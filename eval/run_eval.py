"""Run the eval pipeline against all three engines and produce leaderboard.json."""
import argparse
import json
import math
import os
import pathlib
import time
from typing import Callable

import httpx

from eval.metrics import (
    compute_completeness,
    compute_error_rate,
    compute_token_metrics,
    compute_numeric_metrics,
    compute_f0_5,
    compute_f1,
    aggregate_latency,
)
from eval.llm_judge import judge as llm_judge

DEFAULT_DATA_DIR = pathlib.Path("data")
DEFAULT_LEADERBOARD = pathlib.Path("eval/results/leaderboard.json")
DEFAULT_CORRECTED_REPORT = pathlib.Path("eval/results/leaderboard_corrected.json")
DEFAULT_CACHE = pathlib.Path("eval/judge_cache.json")


def _latency_value(value) -> tuple[float | None, str]:
    if value is None:
        return None, "missing"
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        return None, "invalid"
    return float(value), "zero" if value == 0 else "positive"


def _latency_summary(rows: list[dict], source: str) -> dict:
    values = [row["latency"][source] for row in rows
              if row["latency"][source] is not None]
    states = [row["latency"][source + "_state"] for row in rows]
    return {
        "samples": len(values),
        "missing": states.count("missing"),
        "invalid": states.count("invalid"),
        "zero": states.count("zero"),
        "positive": states.count("positive"),
        **(aggregate_latency(values) if values else {"p50": None, "p95": None}),
    }


def _summarize_corrected(rows: list[dict]) -> dict:
    """Aggregate the exported per-URL records, including failed requests."""
    referenced = [row for row in rows if row["has_reference"]]
    content = {}
    for variant in ("chrome_excluded", "chrome_included"):
        summary = {}
        for name, keys in (("token", ("precision", "recall", "f1", "f0_5")),
                           ("numeric", ("precision", "recall", "f1"))):
            means, counts = {}, {}
            for key in keys:
                values = [row["content"][variant][name][key] for row in referenced
                          if row["content"][variant][name][key] is not None]
                means[key] = sum(values) / len(values) if values else None
                counts[key] = len(values)
            summary[name] = means
            summary[name + "_samples"] = counts
        content[variant] = summary

    # Paired differences use only URLs where both variants are applicable.
    deltas = {}
    for name, keys in (("token", ("precision", "recall", "f1", "f0_5")),
                       ("numeric", ("precision", "recall", "f1"))):
        means, counts = {}, {}
        for key in keys:
            pairs = [(row["content"]["chrome_included"][name][key],
                      row["content"]["chrome_excluded"][name][key]) for row in referenced]
            values = [included - excluded for included, excluded in pairs
                      if included is not None and excluded is not None]
            means[key] = sum(values) / len(values) if values else None
            counts[key] = len(values)
        deltas[name] = means
        deltas[name + "_samples"] = counts

    observed = [row for row in rows if row["failed"] is not None]
    failures = sum(row["failed"] for row in observed)
    excluded = content["chrome_excluded"]
    return {
        "counts": {
            "samples": len(rows), "requests": len(observed),
            "successes": len(observed) - failures, "unknown_outcomes": len(rows) - len(observed),
            "failures": failures, "references": len(referenced),
            "missing_references": len(rows) - len(referenced),
            "failures_with_reference": sum(row["failed"] is True for row in referenced),
            "token_samples": excluded["token_samples"],
            "numeric_samples": excluded["numeric_samples"],
        },
        "error_rate": failures / len(observed) if observed else None,
        "cache": {
            "available": sum(row.get("cache_state") == "available" for row in rows),
            "empty": sum(row.get("cache_state") == "empty" for row in rows),
            "missing": sum(row.get("cache_state") == "missing" for row in rows),
        },
        # Retain the original corrected report's chrome-excluded aliases.
        "token": excluded["token"], "numeric": excluded["numeric"],
        "content": content,
        "chrome_delta_included_minus_excluded": deltas,
        "latency": {source: _latency_summary(rows, source)
                    for source in ("measured_ms", "engine_reported_ms")},
    }


def _production_weighted(rows: list[dict], evaluation_categories: dict) -> dict:
    """Apply production category shares to evaluation category macro means."""
    counts = {}
    for row in rows:
        if row["split"] == "production":
            category = row["category"]
            counts[category] = counts.get(category, 0) + 1
    total = sum(counts.values())
    weights = {category: count / total for category, count in sorted(counts.items())}
    content, coverage = {}, {}
    for variant in ("chrome_excluded", "chrome_included"):
        content[variant], coverage[variant] = {}, {}
        for family, keys in (("token", ("precision", "recall", "f1", "f0_5")),
                             ("numeric", ("precision", "recall", "f1"))):
            content[variant][family], coverage[variant][family] = {}, {}
            for key in keys:
                weighted_sum, covered_weight = 0.0, 0.0
                missing, samples = [], {}
                for category, weight in weights.items():
                    summary = evaluation_categories.get(category)
                    metric = summary["content"][variant] if summary else None
                    value = metric[family][key] if metric else None
                    samples[category] = metric[family + "_samples"][key] if metric else 0
                    if value is None:
                        missing.append(category)
                    else:
                        weighted_sum += weight * value
                        covered_weight += weight
                redistribute = variant == "chrome_excluded" and family == "numeric"
                if redistribute:
                    score = weighted_sum / covered_weight if covered_weight else None
                else:
                    score = weighted_sum if total and not missing else None
                content[variant][family][key] = score
                effective_weights = {
                    category: weight / covered_weight if redistribute else weight
                    for category, weight in weights.items()
                    if category not in missing and covered_weight
                }
                coverage[variant][family][key] = {
                    "weights_redistributed": redistribute and bool(missing) and covered_weight > 0,
                    "effective_category_weights": effective_weights,
                    "covered_production_weight": covered_weight,
                    "missing_categories": missing,
                    "evaluation_samples_by_category": samples,
                }
    return {
        "method": "Production category shares times evaluation category macro means; chrome-excluded numeric weights renormalized over applicable categories.",
        "limitation": "Estimates reference agreement, not measured production accuracy. Renormalized numeric scores describe only categories with applicable numeric references.",
        "production_samples": total,
        "production_category_counts": dict(sorted(counts.items())),
        "category_weights": weights,
        "content": content,
        "coverage": coverage,
    }


def _corrected_engine_report(responses: list[dict], gt_dir: pathlib.Path) -> dict:
    rows = []
    for response in responses:
        reasons = []
        cached = response.get("source") == "cache"
        if cached:
            if response["cache_state"] != "available":
                reasons.append("cache_" + response["cache_state"])
        elif response.get("status") is None:
            reasons.append("missing_status")
        elif response["status"] != 200:
            reasons.append("request_error" if response["status"] == 0 else "http_error")
        if not response.get("markdown", "").strip():
            reasons.append("empty_markdown")
        reference = gt_dir / f"{response['hash']}.md"
        has_reference = reference.exists()
        content = {"chrome_excluded": None, "chrome_included": None}
        if has_reference:
            ground_truth = reference.read_text()
            output = "" if reasons else response["markdown"]
            for variant, exclude in (("chrome_excluded", True), ("chrome_included", False)):
                content[variant] = {
                    "token": compute_token_metrics(output, ground_truth, exclude_chrome=exclude),
                    "numeric": compute_numeric_metrics(output, ground_truth, exclude_chrome=exclude),
                }
        latency = {}
        for source, value in (("measured_ms", response.get("measured_ms")),
                              ("engine_reported_ms", response.get("latency_ms"))):
            latency[source], latency[source + "_state"] = _latency_value(value)
        rows.append({
            **{key: response[key] for key in ("url", "hash", "split", "category")},
            "status": response.get("status"), "failed": None if cached else bool(reasons),
            "source": "cache" if cached else "scrape",
            "cache_state": response.get("cache_state"),
            "failure_reason": ",".join(reasons) if reasons else None,
            "has_reference": has_reference, "latency": latency, "content": content,
        })

    splits = {}
    for split in ("evaluation", "production"):
        subset = [row for row in rows if row["split"] == split]
        splits[split] = {
            "overall": _summarize_corrected(subset),
            "by_category": {
                category: _summarize_corrected([
                    row for row in subset if row["category"] == category
                ]) for category in sorted({row["category"] for row in subset})
            },
        }
    return {
        "overall": _summarize_corrected(rows), "by_split": splits, "per_url": rows,
        "production_weighted": _production_weighted(rows, splits["evaluation"]["by_category"]),
    }


def _default_scrape(engine: str, url: str, engine_url: str, bearer: str,
                    skip_latency: bool = True) -> dict:
    if not engine_url:
        raise SystemExit(
            f"ENGINE_{engine.upper()}_URL is not set. "
            "Copy .env.example to .env and fill in the engine URLs."
        )
    try:
        resp = httpx.post(
            f"{engine_url}/scrape",
            json={"url": url},
            headers={"Authorization": f"Bearer {bearer}", "X-Skip-Latency": "true" if skip_latency else "false"},
            timeout=30,
        )
    except httpx.RequestError as e:
        return {"status": 0, "markdown": "", "latency_ms": None, "url": url}
    if resp.status_code != 200:
        return {"status": resp.status_code, "markdown": "", "latency_ms": None, "url": url}
    body = resp.json()
    return {
        "status": 200,
        "markdown": body.get("markdown", ""),
        "latency_ms": body.get("latency_ms"),
        "url": url,
    }


def run(
    data_dir: str = str(DEFAULT_DATA_DIR),
    engine_urls: dict | None = None,
    bearer: str = "",
    judge_fn: Callable | None = None,
    judge_cache_path: str = str(DEFAULT_CACHE),
    fresh_judge: bool = False,
    leaderboard_path: str = str(DEFAULT_LEADERBOARD),
    scrape_fn: Callable | None = None,
    corrected_report_path: str = str(DEFAULT_CORRECTED_REPORT),
    skip_latency: bool = True,
    cached_only: bool = False,
) -> dict:
    data = pathlib.Path(data_dir)
    gt_dir = data / "ground_truth"

    seed_urls = json.loads((data / "seed_urls.json").read_text())
    prod_urls = json.loads((data / "prod_sample_urls.json").read_text())
    all_urls = [dict(entry, split="evaluation") for entry in seed_urls]
    all_urls += [dict(entry, split="production") for entry in prod_urls]

    if cached_only:
        if fresh_judge or scrape_fn is not None:
            raise ValueError("cached_only cannot be combined with fresh_judge or scrape_fn")
        if pathlib.Path(corrected_report_path).resolve() == pathlib.Path(leaderboard_path).resolve():
            raise ValueError("Cached report must not overwrite the legacy leaderboard")
        report = {
            "methodology": {
                "mode": "cached_only",
                "aggregation": "Macro-average per-page scores; null values excluded per metric.",
                "preprocessing": "Both chrome-excluded and chrome-included scores; preprocessing is symmetric.",
                "chrome_delta": "Mean paired included-minus-excluded score; pairs with null values excluded.",
                "cache_policy": "Missing or empty cache files score as empty output when a reference exists.",
                "reliability": "Historical HTTP outcomes are unknown; error rate is null. Cache availability is reported separately.",
                "latency": {
                    "scrape_source": "cached Markdown", "skip_latency": None,
                    "limitation": "Historical latency unavailable; file read time is not scraping latency.",
                },
                "judge": "Skipped; no judge calls or cache changes.",
                "reference_limitation": "Ground truth derives from Engine A; agreement is not independent factual accuracy.",
            },
            "engines": {},
        }
        for engine in (engine_urls if engine_urls is not None else ("a", "b", "c")):
            responses = []
            for entry in all_urls:
                path = data / f"cache_{engine}" / f"{entry['hash']}.md"
                markdown = path.read_text() if path.exists() else ""
                state = "missing" if not path.exists() else "available" if markdown.strip() else "empty"
                responses.append({
                    **entry, "category": entry.get("category", "unknown"),
                    "source": "cache", "cache_state": state,
                    "markdown": markdown, "status": None,
                    "latency_ms": None, "measured_ms": None,
                })
            report["engines"][engine] = _corrected_engine_report(responses, gt_dir)
        output_path = pathlib.Path(corrected_report_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, allow_nan=False))
        return report

    if engine_urls is None:
        engine_urls = {
            "a": os.environ.get("ENGINE_A_URL", ""),
            "b": os.environ.get("ENGINE_B_URL", ""),
            "c": os.environ.get("ENGINE_C_URL", ""),
        }
    if judge_fn is None:
        judge_fn = llm_judge
    custom_scrape = scrape_fn is not None
    if scrape_fn is None:
        def scrape_fn(engine, url, engine_url, bearer):
            return _default_scrape(engine, url, engine_url, bearer, skip_latency=skip_latency)

    leaderboard = {}
    corrected = {
        "methodology": {
            "aggregation": "Macro-average per-page scores; null values excluded per metric.",
            "failed_scrapes": "Scored as empty output on every page with a reference.",
            "preprocessing": "Both chrome-excluded and chrome-included scores; preprocessing is symmetric.",
            "chrome_delta": "Mean paired included-minus-excluded score; pairs with null values excluded.",
            "latency": {
                "measured_ms": "perf_counter duration around each scrape call, including failures; endpoint round-trip, not verified scraping time.",
                "engine_reported_ms": "Unverified engine response latency_ms; finite nonnegative values included, including zero.",
                "skip_latency": None if custom_scrape else skip_latency,
                "scrape_source": "custom callable" if custom_scrape else "HTTP endpoint",
                "limitation": "Production scraping latency requires a real-scraping endpoint with skip latency disabled.",
            },
            "reference_limitation": "Ground truth derives from Engine A; agreement is not independent factual accuracy.",
        },
        "engines": {},
    }

    for engine, eng_url in engine_urls.items():
        responses = []
        for entry in all_urls:
            started = time.perf_counter()
            result = scrape_fn(engine, entry["url"], eng_url, bearer)
            measured_ms = (time.perf_counter() - started) * 1000
            result = dict(result)
            result["measured_ms"] = measured_ms
            result["hash"] = entry["hash"]
            result["url"] = entry["url"]
            result["split"] = entry["split"]
            result["category"] = entry.get("category", "unknown")
            responses.append(result)

        corrected["engines"][engine] = _corrected_engine_report(responses, gt_dir)

        f1_scores, f0_5_scores, completeness_scores = [], [], []
        for resp in responses:
            url_hash = resp["hash"]
            gt_file = gt_dir / f"{url_hash}.md"
            if gt_file.exists() and resp.get("status") == 200:
                gt = gt_file.read_text()
                md = resp["markdown"]
                f1_scores.append(compute_f1(md, gt))
                f0_5_scores.append(compute_f0_5(md, gt))
                completeness_scores.append(compute_completeness(md, gt))

        # LLM judge — check cache first; call judge_fn only on misses
        import hashlib as _hashlib
        cache_data = json.loads(pathlib.Path(judge_cache_path).read_text()) if pathlib.Path(judge_cache_path).exists() else {}
        judge_scores = []
        for resp in responses:
            if resp.get("status") == 200 and resp["markdown"]:
                url_hash = resp["hash"]
                cache_key = f"{engine}:{url_hash}"
                if not fresh_judge and cache_key in cache_data:
                    score = cache_data[cache_key]
                else:
                    score = judge_fn(
                        resp["markdown"],
                        engine=engine,
                        url=resp["url"],
                        cache_path=judge_cache_path,
                        fresh=fresh_judge,
                    )
                    cache_data[cache_key] = score
                    pathlib.Path(judge_cache_path).write_text(json.dumps(cache_data, indent=2))
                judge_scores.append(score)

        latency_per_url = {r["hash"]: r["latency_ms"] for r in responses if _latency_value(r.get("latency_ms"))[1] == "positive"}
        latencies = list(latency_per_url.values())
        lat = aggregate_latency(latencies) if latencies else {"p50": 0, "p95": 0}

        leaderboard[engine] = {
            "llm_judge": sum(judge_scores) / len(judge_scores) if judge_scores else 0.0,
            "f1": sum(f1_scores) / len(f1_scores) if f1_scores else 0.0,
            "f0_5": sum(f0_5_scores) / len(f0_5_scores) if f0_5_scores else 0.0,
            "completeness": sum(completeness_scores) / len(completeness_scores) if completeness_scores else 0.0,
            "latency_p50": lat["p50"],
            "latency_p95": lat["p95"],
            "error_rate": compute_error_rate(responses),
            "latency_per_url": latency_per_url,
        }

    pathlib.Path(leaderboard_path).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(leaderboard_path).write_text(json.dumps(leaderboard, indent=2))
    corrected_path = pathlib.Path(corrected_report_path)
    corrected_path.parent.mkdir(parents=True, exist_ok=True)
    corrected_path.write_text(json.dumps(corrected, indent=2))
    return leaderboard


def main():
    parser = argparse.ArgumentParser(description="Run scrape engine eval")
    parser.add_argument("--fresh-judge", action="store_true", help="Bypass judge cache")
    parser.add_argument("--corrected-report", default=str(DEFAULT_CORRECTED_REPORT),
                        help="Path for the separate corrected metrics report")
    parser.add_argument("--no-skip-latency", action="store_true",
                        help="Send X-Skip-Latency: false (default: true)")
    parser.add_argument("--cached-only", action="store_true",
                        help="Recalculate corrected metrics from cached Markdown; no network or baseline writes")
    args = parser.parse_args()
    if args.cached_only and args.fresh_judge:
        parser.error("--cached-only cannot be combined with --fresh-judge")

    bearer = os.environ.get("BEARER_TOKEN", "")
    results = run(fresh_judge=args.fresh_judge, bearer=bearer,
                  corrected_report_path=args.corrected_report,
                  skip_latency=not args.no_skip_latency, cached_only=args.cached_only)
    print(f"Corrected report: {args.corrected_report}")

    if args.cached_only:
        for engine, report in results["engines"].items():
            overall = report["overall"]
            print(f"Engine {engine.upper()}: token={overall['token']}, cache={overall['cache']}")
        return

    print("\n=== Leaderboard ===")
    for engine, metrics in results.items():
        print(f"\nEngine {engine.upper()}:")
        for k, v in metrics.items():
            print(f"  {k}: {v:.3f}" if isinstance(v, float) else f"  {k}: {v}")


if __name__ == "__main__":
    main()
