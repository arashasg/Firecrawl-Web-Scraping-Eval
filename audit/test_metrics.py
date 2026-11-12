"""Regression tests for the repository's ORIGINAL evaluation metrics.

PASS  = behavior the current implementation already handles correctly.
XFAIL = a demonstrated limitation we want improved metrics to address.

These tests use short synthetic examples motivated by the dataset audit. They do
not claim to verify the underlying web pages or the accuracy of ground truth.
No API requests are made.

From the repo root:
    uv run pytest tests/test_metrics.py -v -rx
    uv run pytest tests/test_metrics.py --runxfail --tb=short  # show actual failures
"""

import pytest

from eval.llm_judge import PROMPT
from eval.metrics import (
    aggregate_latency,
    compute_completeness,
    compute_error_rate,
    compute_f0_5,
    compute_f1,
)


# 1. Baseline: what the original snippet F-scores already do correctly.

@pytest.mark.parametrize("metric", [compute_f1, compute_f0_5])
def test_identical_long_content_scores_one(metric):
    text = "The laptop includes a powerful processor and excellent battery life."
    assert metric(text, text) == pytest.approx(1.0)


def test_partial_content_has_expected_f_scores():
    first = "The laptop includes a powerful processor and excellent battery life."
    second = "Returns are accepted within thirty calendar days."
    reference = first + "\n\n" + second

    assert compute_f1(first, reference) == pytest.approx(2 / 3)
    assert compute_f0_5(first, reference) == pytest.approx(5 / 6)


@pytest.mark.parametrize("metric", [compute_f1, compute_f0_5])
def test_empty_output_scores_zero_against_nonempty_reference(metric):
    reference = "The laptop includes a powerful processor and excellent battery life."
    assert metric("", reference) == 0.0


# OBSERVED PATTERN: C sometimes omits whole methodology paragraphs from PDFs.
# PASS: The original F1 *already detects* an omitted long paragraph.
def test_missing_pdf_methodology_paragraph_lowers_existing_f1():
    summary = "The document presents findings from a comprehensive technical investigation."
    methodology = (
        "Our methodology draws on 526 data points collected over 18 months "
        "using validated measurement frameworks."
    )
    reference = summary + "\n\n" + methodology
    output = summary

    assert compute_f1(output, reference) == pytest.approx(2 / 3)


# 2. Known snippet-matching limitations (expected failures in original code).

@pytest.mark.xfail(reason="Short price fields are discarded (<5 words)", strict=True)
@pytest.mark.parametrize("metric", [compute_f1, compute_f0_5])
def test_missing_short_price_must_lower_score(metric):
    reference = (
        "Price: $1200\n\n"
        "The laptop includes a powerful processor and excellent battery life."
    )
    output = "The laptop includes a powerful processor and excellent battery life."
    assert metric(output, reference) < 1.0


@pytest.mark.xfail(reason="Jaccard threshold accepts a wrong price", strict=True)
@pytest.mark.parametrize("metric", [compute_f1, compute_f0_5])
def test_numeric_corruption_must_lower_score(metric):
    assert metric("The laptop costs 120 dollars", "The laptop costs 1200 dollars") < 1.0


# NEW, PDF-motivated example: even TWO changed numeric facts can be considered
# an exact snippet match because enough ordinary words are shared.
@pytest.mark.xfail(reason="Changed methodology figures still pass Jaccard >= 0.6", strict=True)
@pytest.mark.parametrize("metric", [compute_f1, compute_f0_5])
def test_changed_pdf_methodology_numbers_must_lower_score(metric):
    reference = (
        "Our methodology draws on 526 data points collected over 18 months "
        "using validated measurement frameworks."
    )
    output = (
        "Our methodology draws on 397 data points collected over 15 months "
        "using validated measurement frameworks."
    )
    assert metric(output, reference) < 1.0


@pytest.mark.xfail(reason="One reference snippet may match several duplicate output snippets", strict=True)
@pytest.mark.parametrize("metric", [compute_f1, compute_f0_5])
def test_duplicated_paragraph_must_lower_precision(metric):
    reference = "The laptop includes a powerful processor and excellent battery life."
    output = reference + "\n\n" + reference
    assert metric(output, reference) < 1.0


# NEW, DATASET-MOTIVATED: 527/600 cached B/C pairs differ only because C adds
# recognized navigation/footer blocks. The reference itself often contains
# those blocks, so the original F-score can reward extra chrome.
@pytest.mark.xfail(reason="Reference boilerplate can inflate the snippet F-score", strict=True)
@pytest.mark.parametrize("metric", [compute_f1, compute_f0_5])
def test_adding_only_chrome_must_not_improve_content_fscore(metric):
    body = "The report contains important technical findings and recommendations for future work."
    chrome = (
        "## Site Navigation\nHome | Reports | Products | About | Support\n\n"
        "## Footer\nPrivacy | Terms | Support | Copyright | Legal"
    )
    reference = body + "\n\n" + chrome
    assert metric(reference, reference) == metric(body, reference)


# 3. Completeness: the current implementation counts headings, not body text.

def test_completeness_recognizes_preserved_headings():
    reference = "# Overview\n\nAn introductory section.\n\n## Specifications\n\n32 GB RAM."
    assert compute_completeness("# Overview\n\n## Specifications", reference) == 1.0


def test_completeness_detects_missing_heading():
    reference = "# Overview\n\n## Specifications"
    assert compute_completeness("# Overview", reference) == pytest.approx(0.5)


@pytest.mark.xfail(reason="Missing body text does not affect heading-based completeness", strict=True)
def test_missing_body_text_must_lower_completeness():
    reference = "# Overview\n\nThe product costs $1200.\n\n## Specifications\n\n32 GB RAM."
    output = "# Overview\n\n## Specifications"
    assert compute_completeness(output, reference) < 1.0


# NEW: adding only site navigation and footer should not increase a metric
# intended to measure substantive content coverage.
@pytest.mark.xfail(reason="Navigation/footer headings inflate completeness", strict=True)
def test_adding_only_chrome_must_not_improve_content_completeness():
    body = "# Quarterly Report\n\n## Key Metrics"
    chrome = "\n\n## Site Navigation\n\n## Footer"
    reference = body + chrome
    assert compute_completeness(body + chrome, reference) == compute_completeness(body, reference)


@pytest.mark.xfail(reason="Substring heading matching accepts unrelated headings", strict=True)
def test_unrelated_heading_substring_must_not_match():
    assert compute_completeness("# Artificial Intelligence", "# Art") == 0.0


# 4. Reliability checks (preserved from your existing suite).

def test_latency_percentiles_are_mathematically_correct():
    result = aggregate_latency([1000, 1100, 1200, 4000])
    assert result["p50"] == pytest.approx(1150.0)
    assert result["p95"] == pytest.approx(3580.0)
    assert result["p95"] > result["p50"]


def test_error_rate_counts_http_failures_and_empty_markdown():
    responses = [
        {"status": 200, "markdown": "Useful content"},
        {"status": 500, "markdown": "Still has text"},
        {"status": 200, "markdown": "  \n  "},
        {"status": 0, "markdown": ""},
    ]
    assert compute_error_rate(responses) == pytest.approx(3 / 4)


def test_error_rate_only_measures_transport_and_nonempty_output():
    # Documents current behavior; this is NOT a content-quality metric.
    assert compute_error_rate([{"status": 200, "markdown": "# Footer\n\nHome | Contact"}]) == 0.0


@pytest.mark.xfail(reason="Missing HTTP status defaults to 200", strict=True)
def test_missing_http_status_must_not_count_as_success():
    assert compute_error_rate([{"markdown": "Useful content"}]) == 1.0


# 5. Reference-blind LLM judge (prompt test only, no API request).

@pytest.mark.xfail(reason="Judge prompt contains extracted Markdown but not ground truth", strict=True)
def test_judge_should_include_ground_truth():
    reference = "The laptop costs $1200."
    output = "The laptop costs $120."
    prompt = PROMPT.format(output=output, ground_truth=reference)
    assert reference in prompt
