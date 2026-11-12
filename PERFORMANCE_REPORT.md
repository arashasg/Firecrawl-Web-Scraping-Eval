# Performance report

Cached evaluation results for Engines A, B, and C. Scores are per-page macro-averages, rounded to four decimals; higher content scores mean closer reference agreement.

Source: [corrected report](eval/results/leaderboard_corrected.json). Historical baseline: [leaderboard_original.json](eval/results/leaderboard_original.json). Legacy cache comparisons below were recalculated from the same stored Markdown and reference-bearing URLs as the corrected scores.

Ground truth derives from Engine A. Its perfect corrected scores reflect reference construction and do not independently establish factual accuracy. “Navigation/footer removed” means the verified `Site Navigation` and `Footer` blocks are removed symmetrically; article headings and other headers remain.

## Coverage

| Split | URLs per engine | With reference | Without reference | PDF URLs | PDF references |
| --- | --- | --- | --- | --- | --- |
| Evaluation | 500 | 300 | 200 | 40 | 24 |
| Production | 100 | 0 | 100 | 30 | 0 |

All three engines have 600 nonempty cached outputs each (1,800 total), with no missing or empty files. Content scores below cover the 300 evaluation URLs with references. Production content scores, including production PDF scores, are unavailable because none of the 100 production URLs has a reference.

## Corrected scores with and without navigation/footer

Token scores use all 300 references per engine in each variant.

| Engine | Navigation/footer | Token precision | Token recall | Token F1 | Token F0.5 |
| --- | --- | --- | --- | --- | --- |
| A | Retained | 1.0000 | 0.8773 | 0.9338 | 0.9722 |
| A | Removed | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| B | Retained | 0.7933 | 0.8851 | 0.8360 | 0.8097 |
| B | Removed | 0.7862 | 1.0000 | 0.8786 | 0.8204 |
| C | Retained | 0.7935 | 0.9762 | 0.8724 | 0.8223 |
| C | Removed | 0.7831 | 0.9757 | 0.8634 | 0.8117 |

Numeric scores retain currency and percentage markers. Numeric sample counts change when removing footer years makes references number-free; numeric values across variants therefore do not always use the same pages.

| Engine | Navigation/footer | Numeric precision | Numeric recall | Numeric F1 | Numeric samples (P/R/F1) |
| --- | --- | --- | --- | --- | --- |
| A | Retained | 0.8167 | 0.6787 | 0.7344 | 300/300/300 |
| A | Removed | 1.0000 | 1.0000 | 1.0000 | 245/245/245 |
| B | Retained | 0.6609 | 0.6787 | 0.6498 | 300/300/300 |
| B | Removed | 0.8093 | 1.0000 | 0.8834 | 245/245/245 |
| C | Retained | 0.8333 | 0.9570 | 0.8772 | 300/300/300 |
| C | Removed | 0.8093 | 0.9469 | 0.8460 | 245/245/245 |

Paired removal effect (removed minus retained, in score points). Each difference uses only pages applicable in both variants; numeric differences need not equal differences between the full-cohort means above.

| Engine | Δ token F1 | Paired token pages | Δ numeric F1 | Paired numeric pages |
| --- | --- | --- | --- | --- |
| A | +0.0662 | 300 | +0.1007 | 245 |
| B | +0.0426 | 300 | +0.0877 | 245 |
| C | -0.0090 | 300 | -0.0035 | 245 |

## Corrected versus legacy metrics

The historical baseline uses legacy snippet matching. The cache recomputation applies those same legacy formulas to raw cached Markdown for the 300 reference-bearing URLs; corrected token metrics use occurrence counts with navigation/footer removed. Differences combine metric and preprocessing changes, rather than measuring engine improvements between runs.

| Engine | Historical legacy F1 | Historical legacy F0.5 | Cached legacy F1 | Cached legacy F0.5 | Corrected token F1 | Corrected token F0.5 |
| --- | --- | --- | --- | --- | --- | --- |
| A | 0.8933 | 0.9535 | 0.8933 | 0.9535 | 1.0000 | 1.0000 |
| B | 0.8011 | 0.7958 | 0.8011 | 0.7958 | 0.8786 | 0.8204 |
| C | 0.8768 | 0.8304 | 0.8768 | 0.8304 | 0.8634 | 0.8117 |

Legacy heading completeness has no direct corrected equivalent; corrected token recall measures occurrence coverage instead.

| Engine | Historical heading completeness | Cached heading completeness | Corrected token recall |
| --- | --- | --- | --- |
| A | 0.6181 | 0.6181 | 1.0000 |
| B | 0.6181 | 0.6181 | 1.0000 |
| C | 0.9950 | 0.9950 | 0.9757 |

## PDF performance across all three engines

There are 70 PDF URLs per engine: 40 evaluation and 30 production. Scores use only the 24 evaluation PDFs with references; the other 46 PDFs have no reference. All 70 PDF cache files per engine are nonempty. Each PDF metric below uses the same 24 pages in both variants.

| Engine | Navigation/footer | Token precision | Token recall | Token F1 | Token F0.5 | Numeric precision | Numeric recall | Numeric F1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | Retained | 1.0000 | 0.9921 | 0.9959 | 0.9983 | 1.0000 | 0.9950 | 0.9974 |
| A | Removed | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| B | Retained | 0.8951 | 0.9925 | 0.9407 | 0.9127 | 1.0000 | 0.9950 | 0.9974 |
| B | Removed | 0.8948 | 1.0000 | 0.9443 | 0.9139 | 1.0000 | 1.0000 | 1.0000 |
| C | Retained | 0.7054 | 0.7022 | 0.6849 | 0.6885 | 0.7083 | 0.4625 | 0.5417 |
| C | Removed | 0.8570 | 0.6966 | 0.7548 | 0.8052 | 1.0000 | 0.4583 | 0.6190 |

PDF legacy metrics below are recomputed on those same 24 cached PDF/reference pairs with navigation/footer retained; no PDF-specific legacy aggregate is stored in the historical leaderboard.

| Engine | Cached legacy F1 | Cached legacy F0.5 | Cached heading completeness | Corrected token F1 (removed) | Corrected token recall (removed) | Corrected numeric recall (removed) |
| --- | --- | --- | --- | --- | --- | --- |
| A | 0.9933 | 0.9972 | 0.9688 | 1.0000 | 1.0000 | 1.0000 |
| B | 0.8784 | 0.8261 | 0.9688 | 0.9443 | 1.0000 | 1.0000 |
| C | 0.5329 | 0.4988 | 0.9375 | 0.7548 | 0.6966 | 0.4583 |

## Production-weighted evaluation

Evaluation category means weighted by the production sample category mix. These estimates supplement the macro averages; they do not directly measure production accuracy.

| Category | Production URLs | Weight | Evaluation references |
| --- | --- | --- | --- |
| docs | 15 | 15% | 55 |
| ecommerce | 25 | 25% | 87 |
| other | 15 | 15% | 13 |
| pdf | 30 | 30% | 24 |
| spa | 15 | 15% | 14 |

| Engine | Navigation/footer | Token precision | Token recall | Token F1 | Token F0.5 | Numeric precision | Numeric recall | Numeric F1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | Retained | 1.0000 | 0.8769 | 0.9325 | 0.9713 | 0.8500 | 0.7690 | 0.8062 |
| A | Removed | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| B | Retained | 0.8094 | 0.8868 | 0.8450 | 0.8230 | 0.7020 | 0.7690 | 0.7252 |
| B | Removed | 0.8003 | 1.0000 | 0.8860 | 0.8320 | 0.8259 | 1.0000 | 0.8925 |
| C | Retained | 0.7665 | 0.9107 | 0.8233 | 0.7848 | 0.7765 | 0.8387 | 0.7800 |
| C | Removed | 0.7890 | 0.9090 | 0.8292 | 0.7994 | 0.8259 | 0.8088 | 0.7580 |

Token metrics and numeric metrics with navigation/footer retained have 100% production-weight coverage. Chrome-excluded numeric scores redistribute weights over the applicable 85% of the production mix: docs has no applicable numeric references and is excluded. Effective numeric weights are PDF 35.2941%, ecommerce 29.4118%, other 17.6471%, and SPA 17.6471%. These numeric scores apply to those categories only. Original coverage, missing categories, sample counts, and effective weights remain visible under `production_weighted.coverage`. No applicable categories would still produce N/A.

## Reliability, latency, and judge availability

| Engine | Nonempty caches | Missing/empty caches | Historical HTTP outcomes | Measured p50/p95 | Engine-reported p50/p95 | Corrected error rate | New judge score |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | 600 | 0/0 | 600 unknown | N/A | N/A | N/A | Not run |
| B | 600 | 0/0 | 600 unknown | N/A | N/A | N/A | Not run |
| C | 600 | 0/0 | 600 unknown | N/A | N/A | N/A | Not run |

Cached Markdown does not retain HTTP status or latency. Cache availability does not imply a zero scrape error rate. No scraping engines or LLM judge were called for the corrected report; no new production-latency measurements are available.
