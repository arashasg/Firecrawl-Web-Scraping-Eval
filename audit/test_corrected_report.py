"""Offline integration checks for corrected reporting and baseline preservation."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from eval.run_eval import run, _corrected_engine_report, _summarize_corrected, _default_scrape, _production_weighted


class CorrectedReportTests(unittest.TestCase):
    def test_reports_failures_groups_and_metric_denominators(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ground_truth").mkdir()
            seed = [
                {"url": "one", "hash": "one", "category": "shop"},
                {"url": "two", "hash": "two", "category": "shop"},
                {"url": "three", "hash": "three", "category": "docs"},
            ]
            production = [{"url": "four", "hash": "four", "category": "shop"}]
            (root / "seed_urls.json").write_text(json.dumps(seed))
            (root / "prod_sample_urls.json").write_text(json.dumps(production))
            (root / "ground_truth/one.md").write_text("Price: $1200")
            (root / "ground_truth/two.md").write_text("Price: $1200")
            (root / "ground_truth/three.md").write_text("Plain words")

            def scrape(engine, url, engine_url, bearer):
                if url == "two":
                    # Missing status and nonempty text must still count as failure.
                    return {"markdown": "Price: $1200", "latency_ms": 0}
                text = {"one": "Price: $1200", "three": "Plain words", "four": "No reference"}[url]
                return {"status": 200, "markdown": text, "latency_ms": 10}

            baseline = root / "baseline.json"
            corrected = root / "corrected.json"
            with patch("eval.run_eval.time.perf_counter", side_effect=[0, .01, 1, 1.02, 2, 2.03, 3, 3.04]):
                result = run(
                    data_dir=str(root), engine_urls={"a": "unused"},
                    scrape_fn=scrape, judge_fn=lambda *args, **kwargs: 4,
                    judge_cache_path=str(root / "judge.json"),
                    leaderboard_path=str(baseline), corrected_report_path=str(corrected),
                )
            self.assertEqual(json.loads(baseline.read_text()), result)
            self.assertNotIn("token", result["a"])
            report = json.loads(corrected.read_text())["engines"]["a"]
            overall = report["overall"]
            self.assertEqual(overall["counts"]["requests"], 4)
            self.assertEqual(overall["counts"]["failures"], 1)
            self.assertEqual(overall["counts"]["references"], 3)
            self.assertEqual(overall["counts"]["failures_with_reference"], 1)
            self.assertEqual(overall["counts"]["numeric_samples"]["f1"], 2)
            self.assertAlmostEqual(overall["token"]["f1"], 2 / 3)
            self.assertEqual(overall["numeric"]["f1"], 0.5)
            self.assertEqual(overall["error_rate"], 0.25)
            self.assertEqual(_summarize_corrected(report["per_url"]), overall)
            failed = report["per_url"][1]
            self.assertEqual(failed["failure_reason"], "missing_status")
            self.assertAlmostEqual(failed["latency"]["measured_ms"], 20)
            self.assertEqual(overall["latency"]["measured_ms"]["samples"], 4)
            self.assertEqual(overall["latency"]["engine_reported_ms"]["zero"], 1)
            shop = report["by_split"]["evaluation"]["by_category"]["shop"]
            self.assertEqual(shop["token"]["f1"], 0.5)
            weighted = report["production_weighted"]
            self.assertEqual(weighted["category_weights"], {"shop": 1.0})
            for variant in ("chrome_included", "chrome_excluded"):
                self.assertEqual(weighted["content"][variant]["token"]["f1"], 0.5)
                self.assertEqual(weighted["coverage"][variant]["numeric"]["f1"]["evaluation_samples_by_category"], {"shop": 2})
            prod = report["by_split"]["production"]["overall"]
            self.assertEqual(prod["counts"]["missing_references"], 1)
            self.assertIsNone(prod["token"]["f1"])
            self.assertIsNone(prod["numeric"]["f1"])

    def test_chrome_variants_and_missing_latency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body = "The product costs $1200 and includes a warranty."
            chrome = "\n\n## Footer\n\n© 2024 Example. | Privacy\n"
            (root / "page.md").write_text(body + chrome)
            responses = [
                {"url": "pdf", "hash": "page", "split": "evaluation", "category": "pdf",
                 "status": 200, "markdown": body, "measured_ms": 0},
                {"url": "bad", "hash": "absent", "split": "production", "category": "pdf",
                 "status": 500, "markdown": "", "latency_ms": -1, "measured_ms": 15},
                {"url": "invalid", "hash": "absent", "split": "production", "category": "docs",
                 "status": 200, "markdown": body, "latency_ms": float("nan")},
                {"url": "string", "hash": "absent", "split": "production", "category": "docs",
                 "status": 200, "markdown": body, "latency_ms": "slow"},
            ]
            report = _corrected_engine_report(responses, root)
            page = report["per_url"][0]
            self.assertEqual(page["content"]["chrome_excluded"]["token"]["f1"], 1)
            self.assertLess(page["content"]["chrome_included"]["token"]["f1"], 1)
            self.assertLess(page["content"]["chrome_included"]["numeric"]["recall"], 1)
            overall = report["overall"]
            delta = overall["chrome_delta_included_minus_excluded"]
            self.assertLess(delta["token"]["f1"], 0)
            self.assertEqual(delta["token_samples"]["f1"], 1)
            latency = overall["latency"]
            self.assertEqual(latency["engine_reported_ms"]["missing"], 1)
            self.assertEqual(latency["engine_reported_ms"]["invalid"], 3)
            self.assertIsNone(latency["engine_reported_ms"]["p50"])
            self.assertEqual(latency["measured_ms"]["zero"], 1)
            self.assertEqual(latency["measured_ms"]["p50"], 7.5)
            self.assertEqual(latency["measured_ms"]["missing"], 2)
            self.assertIsNone(report["per_url"][1]["content"]["chrome_included"])
            self.assertEqual(_summarize_corrected(report["per_url"]), overall)
            json.dumps(report, allow_nan=False)

    def test_default_scraper_records_missing_latency_and_skip_header(self):
        with patch("eval.run_eval.httpx.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"markdown": "text"}
            response = _default_scrape("a", "page", "https://engine", "token", skip_latency=False)
            self.assertIsNone(response["latency_ms"])
            self.assertEqual(post.call_args.kwargs["headers"]["X-Skip-Latency"], "false")
            _default_scrape("a", "page", "https://engine", "token")
            self.assertEqual(post.call_args.kwargs["headers"]["X-Skip-Latency"], "true")

    def test_cached_only_never_calls_engines_or_judge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ground_truth").mkdir()
            (root / "cache_a").mkdir()
            entries = [{"url": key, "hash": key, "category": "pdf"}
                       for key in ("present", "empty", "missing")]
            (root / "seed_urls.json").write_text(json.dumps(entries))
            (root / "prod_sample_urls.json").write_text("[]")
            for entry in entries:
                (root / "ground_truth" / (entry["hash"] + ".md")).write_text("Price $1200")
            (root / "cache_a/present.md").write_text("Price $1200")
            (root / "cache_a/empty.md").write_text("  ")
            baseline = root / "baseline.json"
            baseline.write_text("baseline sentinel")
            judge_cache = root / "judge.json"
            judge_cache.write_text("judge sentinel")
            with patch("eval.run_eval.httpx.post", side_effect=AssertionError("Network forbidden")), \
                 patch("eval.run_eval.llm_judge", side_effect=AssertionError("Judge forbidden")):
                result = run(
                    data_dir=str(root), cached_only=True, engine_urls={"a": "unused"},
                    leaderboard_path=str(baseline), judge_cache_path=str(judge_cache),
                    corrected_report_path=str(root / "corrected.json"),
                )
            self.assertEqual(baseline.read_text(), "baseline sentinel")
            self.assertEqual(judge_cache.read_text(), "judge sentinel")
            self.assertEqual(json.loads((root / "corrected.json").read_text()), result)
            report = result["engines"]["a"]
            overall = report["overall"]
            self.assertAlmostEqual(overall["token"]["f1"], 1 / 3)
            self.assertIsNone(overall["error_rate"])
            self.assertEqual(overall["counts"]["requests"], 0)
            self.assertEqual(overall["counts"]["unknown_outcomes"], 3)
            self.assertEqual(overall["cache"], {"available": 1, "empty": 1, "missing": 1})
            self.assertEqual(overall["latency"]["measured_ms"]["samples"], 0)
            self.assertEqual(overall["latency"]["engine_reported_ms"]["missing"], 3)
            self.assertEqual(_summarize_corrected(report["per_url"]), overall)
            for row in report["per_url"]:
                self.assertIsNone(row["status"])
                self.assertIsNone(row["failed"])
            with self.assertRaises(ValueError):
                run(data_dir=str(root), cached_only=True, fresh_judge=True)

    def test_production_weights_and_missing_metric_coverage(self):
        rows = [{"split": "production", "category": category}
                for category in ("pdf", "pdf", "pdf", "docs")]
        categories = {}
        for category, score in (("pdf", .2), ("docs", 1.0)):
            categories[category] = {"content": {}}
            for variant in ("chrome_excluded", "chrome_included"):
                categories[category]["content"][variant] = {
                    "token": dict.fromkeys(("precision", "recall", "f1", "f0_5"), score),
                    "token_samples": dict.fromkeys(("precision", "recall", "f1", "f0_5"), 2),
                    "numeric": dict.fromkeys(("precision", "recall", "f1"), score if category == "pdf" else None),
                    "numeric_samples": dict.fromkeys(("precision", "recall", "f1"), 2 if category == "pdf" else 0),
                }
        result = _production_weighted(rows, categories)
        self.assertEqual(result["category_weights"], {"docs": .25, "pdf": .75})
        for variant in ("chrome_excluded", "chrome_included"):
            self.assertAlmostEqual(result["content"][variant]["token"]["f1"], .4)
            if variant == "chrome_excluded":
                self.assertAlmostEqual(result["content"][variant]["numeric"]["f1"], .2)
                numeric_coverage = result["coverage"][variant]["numeric"]["f1"]
                self.assertTrue(numeric_coverage["weights_redistributed"])
                self.assertEqual(numeric_coverage["effective_category_weights"], {"pdf": 1.0})
            else:
                self.assertIsNone(result["content"][variant]["numeric"]["f1"])
            coverage = result["coverage"][variant]["numeric"]["f1"]
            self.assertEqual(coverage["covered_production_weight"], .75)
            self.assertEqual(coverage["missing_categories"], ["docs"])
        empty_numeric = _production_weighted([{"split": "production", "category": "docs"}], categories)
        self.assertIsNone(empty_numeric["content"]["chrome_excluded"]["numeric"]["f1"])
        self.assertEqual(empty_numeric["coverage"]["chrome_excluded"]["numeric"]["f1"]["effective_category_weights"], {})
        del categories["docs"]
        result = _production_weighted(rows, categories)
        self.assertIsNone(result["content"]["chrome_excluded"]["token"]["f1"])
        empty = _production_weighted([], categories)
        self.assertEqual(empty["category_weights"], {})
        self.assertIsNone(empty["content"]["chrome_excluded"]["token"]["f1"])


if __name__ == "__main__":
    unittest.main()
