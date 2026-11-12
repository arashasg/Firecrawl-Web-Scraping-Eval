"""Evaluation metrics for scraped Markdown.

The original metrics are retained unchanged for comparison with the published
leaderboard. New, occurrence-aware metrics are provided at the bottom.

All content metrics compare stored Markdown, not the original webpage. In this
benchmark the ground-truth body is derived from Engine A, so reference-based
scores alone cannot establish which engine is factually correct.
"""

import re
from collections import Counter

import numpy as np


# ---------------------------------------------------------------------------
# LEGACY METRICS — unchanged, to reproduce the original evaluation.
# ---------------------------------------------------------------------------


def split_into_snippets(text: str) -> list[str]:
    """Split text into sentence-ish chunks ≥5 tokens."""
    if not text.strip():
        return []
    paragraphs = re.split(r"\n\n+", text.strip())
    snippets = []
    for para in paragraphs:
        sentences = re.split(r"(?<=[.!?])\s+", para.strip())
        for s in sentences:
            s = s.strip()
            if len(s.split()) >= 5:
                snippets.append(s)
    return snippets


def _token_set(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _jaccard(a: str, b: str) -> float:
    sa, sb = _token_set(a), _token_set(b)
    if not sa and not sb:
        return 1.0
    intersection = len(sa & sb)
    union = len(sa | sb)
    return intersection / union if union else 0.0


def _fbeta(output: str, ground_truth: str, beta: float) -> float:
    out_snippets = split_into_snippets(output)
    gt_snippets = split_into_snippets(ground_truth)
    if not out_snippets and not gt_snippets:
        return 1.0
    if not out_snippets or not gt_snippets:
        return 0.0

    matched_output = sum(
        1 for os in out_snippets if any(_jaccard(os, gs) >= 0.6 for gs in gt_snippets)
    )
    matched_gt = sum(
        1 for gs in gt_snippets if any(_jaccard(os, gs) >= 0.6 for os in out_snippets)
    )

    precision = matched_output / len(out_snippets)
    recall = matched_gt / len(gt_snippets)
    if precision + recall == 0:
        return 0.0
    b2 = beta ** 2
    return (1 + b2) * precision * recall / (b2 * precision + recall)


def compute_f1(output: str, ground_truth: str) -> float:
    return _fbeta(output, ground_truth, beta=1.0)


def compute_f0_5(output: str, ground_truth: str) -> float:
    return _fbeta(output, ground_truth, beta=0.5)


def _extract_headings(text: str) -> list[str]:
    return re.findall(r"^#{1,6}\s+(.+)$", text, re.MULTILINE)


def compute_completeness(output: str, ground_truth: str) -> float:
    gt_headings = _extract_headings(ground_truth)
    if not gt_headings:
        return 0.0
    out_headings = _extract_headings(output)

    def matches(gt_h: str) -> bool:
        for oh in out_headings:
            if gt_h == oh:
                return True
            # substring fallback on first 50 chars
            if gt_h[:50] in oh or oh[:50] in gt_h:
                return True
        return False

    matched = sum(1 for gh in gt_headings if matches(gh))
    return matched / len(gt_headings)


def aggregate_latency(latencies: list[int]) -> dict:
    arr = np.array(latencies, dtype=float)
    return {
        "p50": float(np.percentile(arr, 50)),
        "p95": float(np.percentile(arr, 95)),
    }


def compute_error_rate(responses: list[dict]) -> float:
    """LEGACY: missing HTTP status defaults to 200; empty input yields 0.0."""
    if not responses:
        return 0.0
    errors = sum(
        1 for r in responses
        if r.get("status", 200) != 200 or not r.get("markdown", "").strip()
    )
    return errors / len(responses)


# ---------------------------------------------------------------------------
# NEW PREPROCESSING — remove only the verified one-line nav/footer blocks.
# ---------------------------------------------------------------------------

# This pattern matches the exact chrome format observed in the cached files:
# ## Site Navigation\n\nHome | About | ...
# ## Footer\n\n© 2024 Example. | Privacy | ...
# Unlike a broad heading-based stripper, it does not delete an arbitrary
# section or text appearing after the recognized one-line block.
_CHROME_BLOCK = re.compile(
    r"^## (?P<label>Site Navigation|Footer)\n[ \t]*\n"
    r"(?P<body>[^\n]+)(?:\n|$)",
    re.MULTILINE,
)


def remove_chrome(text: str) -> str:
    """Remove recognized nav/footer blocks without modifying other content.

    This intentionally narrow rule matches the dataset audit's verified chrome
    format. Unrecognized boilerplate remains, and meaningful text is preserved.
    """
    def replace(match: re.Match) -> str:
        label, body = match.group("label", "body")
        looks_like_chrome = (
            " | " in body
            and not body.startswith("#")
            and (label == "Site Navigation" or body.startswith("© "))
        )
        return "" if looks_like_chrome else match.group()

    return _CHROME_BLOCK.sub(replace, text)


def _prepare(output: str, ground_truth: str, exclude_chrome: bool):
    """Apply the same optional preprocessing to output and reference."""
    if exclude_chrome:
        return remove_chrome(output), remove_chrome(ground_truth)
    return output, ground_truth


# ---------------------------------------------------------------------------
# NEW CONTENT METRICS — standard occurrence-aware token precision/recall.
# ---------------------------------------------------------------------------


def _f_score(precision: float, recall: float, beta: float) -> float:
    """Compute the standard F-beta measure, including the zero-score case."""
    b2 = beta * beta
    denominator = b2 * precision + recall
    if denominator == 0:
        return 0.0
    return (1 + b2) * precision * recall / denominator


def compute_token_metrics(
    output: str, ground_truth: str, exclude_chrome: bool = True
) -> dict:
    """Return token precision, recall, F1 and F0.5 using occurrence counts.

    Every token (including short fields) is counted. Unlike legacy snippet F1,
    there is no Jaccard threshold and duplicate tokens cannot match one reference
    occurrence repeatedly. Both texts are processed consistently.

    A missing output or reference receives zero scores (never a perfect 1.0).
    Token overlap cannot establish factual correctness or correct word order.
    """
    output, ground_truth = _prepare(output, ground_truth, exclude_chrome)
    out = Counter(re.findall(r"\w+", output.casefold()))
    ref = Counter(re.findall(r"\w+", ground_truth.casefold()))

    matched = sum((out & ref).values())  # Counter intersection uses min(counts).
    precision = matched / sum(out.values()) if out else 0.0
    recall = matched / sum(ref.values()) if ref else 0.0

    return {
        "precision": precision,
        "recall": recall,
        "f1": _f_score(precision, recall, beta=1.0),
        "f0_5": _f_score(precision, recall, beta=0.5),
    }


# ---------------------------------------------------------------------------
# NEW NUMERIC METRICS — exact numeric-occurrence precision/recall/F1.
# ---------------------------------------------------------------------------

# Keep currencies and '%' so '$100', '€100', '100%', and '100' stay distinct.
# This is a simple pattern, not an entity-aware or locale-aware number parser:
# it cannot understand dates, units (42K), or numbers assigned to wrong fields.
_NUMBER = re.compile(
    r"(?<!\w)(?:[$€£]\s*)?-?\d+(?:,\d{3})*(?:\.\d+)?(?:\s*%)?"
)


def _numeric_counts(text: str) -> Counter:
    """Count numeric expressions, ignoring commas and spaces, not units."""
    return Counter(
        re.sub(r"[,\s]", "", match.group())
        for match in _NUMBER.finditer(text)
    )


def compute_numeric_metrics(
    output: str, ground_truth: str, exclude_chrome: bool = True
) -> dict:
    """Return numeric precision/recall/F1 from normalized number occurrences.

    If the reference has no numbers, recall and F1 are None (not applicable),
    so such pages should be excluded from numeric-recall averages. Precision is
    also None when neither text contains numbers; otherwise it is zero if the
    output introduces numbers absent from a number-free reference.

    A mismatch is not necessarily a factual error, especially with references
    constructed from another engine's output.
    """
    output, ground_truth = _prepare(output, ground_truth, exclude_chrome)
    out = _numeric_counts(output)
    ref = _numeric_counts(ground_truth)

    if not ref:
        return {
            "precision": 0.0 if out else None,
            "recall": None,
            "f1": None,
        }

    matched = sum((out & ref).values())
    precision = matched / sum(out.values()) if out else 0.0
    recall = matched / sum(ref.values())
    return {
        "precision": precision,
        "recall": recall,
        "f1": _f_score(precision, recall, beta=1.0),
    }


# ---------------------------------------------------------------------------
# NEW RELIABILITY METRIC — conservative missing-status and empty-list handling.
# ---------------------------------------------------------------------------


def compute_error_rate_corrected(responses: list[dict]) -> float | None:
    """Count status != 200, missing status, and empty Markdown as errors.

    Unlike the legacy function, missing status is *not* assumed to be 200 and
    an empty input returns None (no observations), not a 0% failure rate.
    Nonempty but irrelevant content still counts as a transport-level success;
    assess its usefulness with the content metrics separately.
    """
    if not responses:
        return None
    errors = sum(
        1 for r in responses
        if r.get("status") != 200 or not r.get("markdown", "").strip()
    )
    return errors / len(responses)
