# Firecrawl Scrape Engine Eval

We're evaluating three candidate scrape engines — A, B, and C — before deciding which to ship to production. Your job is to assess the eval methodology, understand the results, and form an opinion on whether we should ship Engine C.

A teammate ran the eval and left their writeup in `RESULTS.md`. Take a look at the numbers, dig into the code, and come ready to discuss.

## The situation

We have three scrape engines deployed to Vercel. Each one takes a URL and returns a markdown representation of the page. We've run them against a 500-URL eval set and a 100-URL production sample. We have ground truth annotations for 300 of the eval URLs.

The results are in `RESULTS.md`. We're inclined to ship Engine C based on those numbers, but we want a second opinion before we do.

## Repo layout

```
├── data/
│   ├── seed_urls.json              # 500 eval URLs with category tags
│   ├── prod_sample_urls.json       # 100 production sample URLs
│   ├── cache_a/                    # Pre-generated Engine A outputs
│   ├── cache_b/                    # Pre-generated Engine B outputs
│   ├── cache_c/                    # Pre-generated Engine C outputs
│   └── ground_truth/               # 300 ground truth .md files
├── eval/
│   ├── metrics.py                  # F1, F0.5, completeness, latency, error rate
│   ├── llm_judge.py                # LLM-based quality scoring
│   ├── run_eval.py                 # Main eval runner
│   ├── judge_cache.json            # Pre-populated judge scores
│   └── results/leaderboard.json   # Pre-committed results (drives RESULTS.md)
└── data/ground_truth_generator.py  # Script used to generate GT files
```

## Setup

```bash
cp .env.example .env
# Fill in ENGINE_A_URL, ENGINE_B_URL, ENGINE_C_URL, BEARER_TOKEN
# (values provided separately — ANTHROPIC_API_KEY is only needed for make eval-fresh, see below)
pip install -r requirements.txt
```

## Running the eval

```bash
make eval
```

This calls the three Vercel engines live and scores against the pre-committed judge cache — no Anthropic API calls needed.

Each run also writes `eval/results/leaderboard_corrected.json`, separately from
the legacy leaderboard. Override its location with
`python -m eval.run_eval --corrected-report <path>`.

The corrected report contains per-engine totals and results by split
(`evaluation`/`production`) and category within each split. Token precision,
recall, F1 and F0.5 and numeric precision, recall and F1 are macro-averaged over
pages with ground truth, both with and without recognized navigation/footer
blocks. The `content.chrome_excluded` and `content.chrome_included` sections
include separate metric sample counts. `chrome_delta_included_minus_excluded`
reports the mean paired score difference on URLs where both values apply;
its sample counts can differ from the individual variant counts. The existing
`token` and `numeric` fields remain aliases for chrome-excluded averages.
Failed scrapes are scored as empty output instead of being excluded. Reliability
counts include all requests, including pages without ground truth.

Each group includes request, success, failure and reference counts, plus the
sample count for each content metric. Numeric `null` values are excluded per
metric; groups without applicable samples report `null`, not zero. Production
pages without references therefore have reliability results but no content
scores. Ground truth derives from Engine A, so these scores measure reference
agreement rather than independent factual accuracy. Legacy scoring and its
output format remain unchanged.

Each engine also has a separate `production_weighted` section. For each token
and numeric metric, in both chrome variants, it sums evaluation category means
weighted by category shares in `prod_sample_urls.json`. Existing macro averages
and per-URL scores remain unchanged. `category_weights` and
`production_category_counts` describe the target mix; `coverage` gives the
covered production weight, missing categories, and evaluation sample counts for
each metric. For chrome-excluded numeric metrics, weights are renormalized
across categories with applicable scores; coverage preserves the original
covered production weight and lists effective weights and excluded categories.
These numeric scores describe only the covered categories. Other weighted
metrics remain `null` if any production category lacks an applicable score.
An empty production sample or no applicable categories yields null scores. These are estimates under
the production category mix, not direct production accuracy measurements.
The calculation is available in both live and cached-only runs.

Each engine's `per_url` list records URL, hash, split, category, status, failure
reason, reference availability, latency, and both content-score variants.
Aggregates are calculated from these records. Pages without references have
`null` content variants; failed scrapes with references use empty output.

Latency summaries appear in every aggregate group. `measured_ms` uses
`time.perf_counter()` around every scrape call, including failures.
`engine_reported_ms` retains the endpoint's separate, unverified latency.
Both report sample counts, missing/invalid/zero/positive counts, and p50/p95.
Finite nonnegative values, including zeros, enter percentiles; missing or invalid
values remain `null` and are explicitly counted. Per-URL latency values include
a state explaining any absent measurement.

The default HTTP request sends `X-Skip-Latency: true`. Report metadata records
this setting (or marks it unknown for custom scrape functions). Measured wall
time describes endpoint response time under that setting, not verified scraping
time. To send `X-Skip-Latency: false`, run:

```bash
python -m eval.run_eval --no-skip-latency
```

Production scraping latency still requires an endpoint that actually performs
scraping. The legacy leaderboard retains its positive engine-latency policy.

To recalculate corrected metrics entirely offline from `data/cache_a/`,
`data/cache_b/`, and `data/cache_c/`, run:

```bash
python -m eval.run_eval --cached-only
# Or: make eval-cached
```

This writes `eval/results/leaderboard_corrected.json` (override with
`--corrected-report <path>`), without engine or LLM calls. It does not read or
modify the judge cache or overwrite the legacy leaderboard. Both chrome
variants, per-URL records, and split/category aggregates are recalculated.
`--cached-only` cannot be combined with `--fresh-judge`.

Markdown caches do not contain historical HTTP status or latency. Cached rows
therefore have `status: null`, `failed: null`, and null latency values; aggregate
error rates are null and `unknown_outcomes` counts these rows. `samples` counts
all evaluated rows, while `requests` counts observed live outcomes (zero offline).
The `cache` counts distinguish available, empty, and missing files. Missing or
empty caches are scored as empty output on reference-bearing URLs. Local file
read time is never reported as scraping latency.

Offline report checks (no engine or judge calls):

```bash
python -m unittest audit.test_corrected_report audit.test_dataset_validation -v
```

**Note:** The engines only respond to URLs in `data/seed_urls.json` and `data/prod_sample_urls.json`. Requests for other URLs return 404.

**Note:** `eval/results/leaderboard.json` and `eval/judge_cache.json` are pre-committed. `make eval` uses the cached judge scores. `make eval-fresh` re-runs the LLM judge live against all 1800 (engine, URL) pairs — this requires `ANTHROPIC_API_KEY` in `.env`, takes ~2 hours, and is **not required** for the session.

LLM-based judges are inherently noisy: individual scores can shift by ±1 point on re-runs, and aggregate means can vary by ±0.3–0.5 points between runs. Keep that in mind when interpreting the numbers in `RESULTS.md`.

## What to bring to the session

- Your read on the eval methodology
- Any issues you spotted with how the results were computed or presented
- A recommendation on whether to ship Engine C, and why
