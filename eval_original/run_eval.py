"""Run the eval pipeline against all three engines and produce leaderboard.json."""
import argparse
import json
import os
import pathlib
from typing import Callable

import httpx

from eval.metrics import (
    compute_completeness,
    compute_error_rate,
    compute_f0_5,
    compute_f1,
    aggregate_latency,
)
from eval.llm_judge import judge as llm_judge

DEFAULT_DATA_DIR = pathlib.Path("data")
DEFAULT_LEADERBOARD = pathlib.Path("eval/results/leaderboard.json")
DEFAULT_CACHE = pathlib.Path("eval/judge_cache.json")


def _default_scrape(engine: str, url: str, engine_url: str, bearer: str) -> dict:
    if not engine_url:
        raise SystemExit(
            f"ENGINE_{engine.upper()}_URL is not set. "
            "Copy .env.example to .env and fill in the engine URLs."
        )
    try:
        resp = httpx.post(
            f"{engine_url}/scrape",
            json={"url": url},
            headers={"Authorization": f"Bearer {bearer}", "X-Skip-Latency": "true"},
            timeout=30,
        )
    except httpx.RequestError as e:
        return {"status": 0, "markdown": "", "latency_ms": 0, "url": url}
    if resp.status_code != 200:
        return {"status": resp.status_code, "markdown": "", "latency_ms": 0, "url": url}
    body = resp.json()
    return {
        "status": 200,
        "markdown": body.get("markdown", ""),
        "latency_ms": body.get("latency_ms", 0),
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
) -> dict:
    data = pathlib.Path(data_dir)
    gt_dir = data / "ground_truth"

    seed_urls = json.loads((data / "seed_urls.json").read_text())
    prod_urls = json.loads((data / "prod_sample_urls.json").read_text())
    all_urls = seed_urls + prod_urls

    if engine_urls is None:
        engine_urls = {
            "a": os.environ.get("ENGINE_A_URL", ""),
            "b": os.environ.get("ENGINE_B_URL", ""),
            "c": os.environ.get("ENGINE_C_URL", ""),
        }
    if judge_fn is None:
        judge_fn = llm_judge
    if scrape_fn is None:
        scrape_fn = _default_scrape

    leaderboard = {}

    for engine, eng_url in engine_urls.items():
        responses = []
        for entry in all_urls:
            result = scrape_fn(engine, entry["url"], eng_url, bearer)
            result["hash"] = entry["hash"]
            result["url"] = entry["url"]
            responses.append(result)

        f1_scores, f0_5_scores, completeness_scores = [], [], []
        for resp in responses:
            url_hash = resp["hash"]
            gt_file = gt_dir / f"{url_hash}.md"
            if gt_file.exists() and resp["status"] == 200:
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
            if resp["status"] == 200 and resp["markdown"]:
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

        latency_per_url = {r["hash"]: r["latency_ms"] for r in responses if r["latency_ms"] > 0}
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
    return leaderboard


def main():
    parser = argparse.ArgumentParser(description="Run scrape engine eval")
    parser.add_argument("--fresh-judge", action="store_true", help="Bypass judge cache")
    args = parser.parse_args()

    bearer = os.environ.get("BEARER_TOKEN", "")
    results = run(fresh_judge=args.fresh_judge, bearer=bearer)

    print("\n=== Leaderboard ===")
    for engine, metrics in results.items():
        print(f"\nEngine {engine.upper()}:")
        for k, v in metrics.items():
            print(f"  {k}: {v:.3f}" if isinstance(v, float) else f"  {k}: {v}")


if __name__ == "__main__":
    main()
