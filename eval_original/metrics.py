import re
import numpy as np


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
    if not responses:
        return 0.0
    errors = sum(
        1 for r in responses
        if r.get("status", 200) != 200 or not r.get("markdown", "").strip()
    )
    return errors / len(responses)
