# Failure points in the original evaluation

The baseline exposes **seven failure points**: incorrect, incomplete, or duplicated content can receive perfect scores.

Source: [baseline_tests.txt](baseline_tests.txt) — **10 passed, 11 xfailed**. Exact assertion values appear in [original_failures.txt](original_failures.txt); cases are defined in [test_metrics.py](../eval/test_metrics.py).

## Findings

| Failure | Example | Actual result | Cause and impact |
|---|---|---|---|
| **Short fields are ignored** | Drop `Price: $1200` while keeping the descriptive paragraph. | F1 and F0.5: **1.0** | Chunks under five tokens are discarded. Missing important short fields cannot reduce the score. |
| **Numeric corruption is accepted** | Change “The laptop costs 1200 dollars” to “The laptop costs 120 dollars.” | F1 and F0.5: **1.0** | Token Jaccard similarity is `4/6 ≈ 0.667`, above the `0.6` threshold. A tenfold price error counts as a full match. |
| **Duplication is unpenalized** | Repeat the reference paragraph twice. | F1 and F0.5: **1.0** | Both output snippets can reuse the same reference match. Duplicate content does not reduce precision. |
| **Missing body text is invisible** | Keep `Overview` and `Specifications` headings; remove prices and specifications beneath them. | Completeness: **1.0** | Only headings are checked. The metric measures heading coverage, not content completeness. |
| **Unrelated headings match** | Reference: `Art`. Output: `Artificial Intelligence`. | Completeness: **1.0**, expected **0.0** | Substring matching treats unrelated section names as equivalent. |
| **Missing status counts as success** | Response: `{"markdown": "Useful content"}`. | Error rate: **0.0**, expected **1.0** | Missing status defaults to HTTP 200, potentially hiding incomplete response records. |
| **Judge lacks reference evidence** | Reference price: `$1200`; output price: `$120`. | Reference absent from prompt. | The judge sees only output, so it lacks evidence to verify source fidelity or omissions. |

F1 and F0.5 share the same snippet-matching logic. Emphasizing precision in F0.5 cannot fix information discarded or incorrectly matched before the formula is applied.

## What still works

The passing controls confirm expected scores for identical long text, partial sentence coverage, and empty output; detection of missing headings; correct latency percentiles for the supplied sample; and error counting for HTTP failures and empty Markdown.

A separate passing case also shows that footer-only output receives **0% errors**. Transport success and nonempty text do not establish extraction quality.

## Implications

- Perfect F-scores do not guarantee factual accuracy, field retention, or freedom from duplication.
- Full completeness does not guarantee preserved section content.
- Zero errors does not guarantee a useful scrape.
- The judge test demonstrates missing prompt evidence; it does not test actual model scores.
- These examples do not quantify changes to engine rankings or establish which engine should ship. No latency calculation defect was demonstrated.

Original implementations and results remain the baseline for reviewing improvements one failure point at a time.
