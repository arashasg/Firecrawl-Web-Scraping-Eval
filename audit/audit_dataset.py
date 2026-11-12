"""Summarize the composition and integrity of Firecrawl's stored evaluation dataset.

Usage from the solid-octo-train repository root:
    uv run python -m audit.audit_dataset
    uv run python -m audit.audit_dataset --root /path/to/solid-octo-train
    python -m audit.audit_dataset --validate

Inputs:
    data/seed_urls.json and data/prod_sample_urls.json
    data/ground_truth/*.md and data/cache_{a,b,c}/*.md

Output:
    audit_results/dataset_audit.json (plus a concise terminal summary)
    --validate: check all B/C pairs and GT/A matches, save two CSV files
                and dataset_validation.json in audit_results/.

This is a read-only, offline audit of *stored* inputs and responses. It never
calls the scrape engines or LLM judge and does not score engine quality.
The audit uses simple heuristics; findings are leads for investigation, not
proof of factual errors or data leakage.
"""

import argparse
import csv
import difflib
import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

# Recognize Markdown headings and straightforward integer/decimal/currency tokens.
# These patterns are deliberately limited: they are not full Markdown parsers
# or locale-aware detectors for dates, phone numbers, or scientific notation.
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
NUMBER = re.compile(r"(?<!\w)(?:[$€£]\s*)?-?\d+(?:,\d{3})*(?:\.\d+)?")

# Only explicitly named Markdown sections are treated as site chrome.
CHROME = {"site navigation", "footer"}


def norm(text):
    """Normalize case and whitespace for *exact* content comparison.

    Different punctuation, word order, or revised text still count as changes.
    This function is used for duplicate detection, not semantic similarity.
    """
    return " ".join(text.casefold().split())


def chrome_labels(text):
    """Return any explicitly labeled navigation/footer headings in Markdown.

    The result is a set such as {"footer", "site navigation"}. Unlabeled
    boilerplate is not detected, and a legitimate section bearing one of
    these headings will also be identified as chrome.
    """
    result = set()

    for line in text.splitlines():
        match = HEADING.match(line.strip())

        if match is None:
            continue

        heading = match.group(2).casefold().strip()

        if heading in CHROME:
            result.add(heading)

    return result


def remove_chrome(text):
    """Drop labeled nav/footer sections until the next peer/higher heading.

    Heading levels control section boundaries: a skipped ``## Footer``
    continues until the next ``##`` or ``#`` heading, or end of file. This
    heuristic will not remove unlabeled boilerplate or distinguish mislabeled
    main content from genuine navigation/footer material.
    """
    result, skip_level = [], None
    for line in text.splitlines():
        m = HEADING.match(line.strip())
        if m:
            level, label = len(m.group(1)), m.group(2).casefold().strip()
            # A heading at the same or higher level ends a skipped section.
            if skip_level is not None and level <= skip_level:
                skip_level = None
            # Start skipping if the new heading introduces a chrome section.
            if label in CHROME:
                skip_level = level
        if skip_level is None:
            result.append(line)
    return "\n".join(result)


def numeric_values(text):
    """Count normalized numeric tokens; this measures disagreement, not errors."""
    counts = Counter()

    for match in NUMBER.finditer(text):
        value = match.group()

        # Remove currency symbols, commas, and whitespace
        normalized = re.sub(r"[,$€£\s]", "", value)

        # Count occurrences
        counts[normalized] += 1

    return counts


def percentage(n, total):
    """Express a count as a percentage, returning 0.0 for empty samples."""
    return round(100 * n / total, 1) if total else 0.0


def duplicates(texts, records):
    """Group distinct URL hashes with identical normalized Markdown content.

    Only entries listed in ``records`` are considered. ``extra_copies`` counts
    every instance beyond the first member of each group. This function does
    *not* detect paraphrases, near-duplicates, or different versions of a page.
    """
    groups = defaultdict(list)
    for h, text in texts.items():
        if h in records:
            groups[norm(text)].append(h)
    # Exclude unique documents; sort largest duplicate groups first.
    groups = sorted((g for g in groups.values() if len(g) > 1), key=len, reverse=True)
    return {
        "groups": len(groups),
        "extra_copies": sum(len(g) - 1 for g in groups),
        "examples": [[records[h]["url"] for h in g[:5]] for g in groups[:5]],
    }



# Independent, deliberately narrow check of the actual stored chrome format.
# Unlike remove_chrome(), this never consumes an arbitrary section body.
RAW_CHROME = re.compile(
    r"^## (?P<label>Site Navigation|Footer)\n[ \t]*\n"
    r"(?P<body>[^\n]+)(?:\n|$)", re.MULTILINE
)


def verify_raw_chrome(text):
    """Remove only one-line pipe-separated navigation or copyright footers.

    Preserve case, punctuation, numbers and internal whitespace. Unexpected
    labeled sections remain in place and cannot silently become matches.
    """
    removed = []

    def replace(match):
        label, body = match.group("label", "body")
        valid = (" | " in body and not body.startswith("#") and
                 (label == "Site Navigation" or body.startswith("© ")))
        if not valid:
            return match.group()
        removed.append(match.group().strip())
        return ""
    return RAW_CHROME.sub(replace, text), removed


def strict_body(text):
    """Remove blank lines, but otherwise preserve each line exactly."""
    return "\n".join(line for line in text.splitlines() if line.strip())


def validate_findings(root):
    """Double-check all B/C pairs and GT/A matches using stricter chrome removal.

    The original audit removes entire labeled sections. This check only removes
    the recognizable, one-line navigation/footer blocks in the cached data.
    Text left behind is compared line-by-line, ignoring blank lines only.
    """
    data = root / "data"
    entries = []
    for split, filename in [("evaluation", "seed_urls.json"),
                            ("production", "prod_sample_urls.json")]:
        for item in json.loads((data / filename).read_text(encoding="utf-8")):
            entries.append({**item, "split": split})

    counts = Counter()
    by_category = defaultdict(Counter)
    rows = []
    examples = {}

    # Compare the stored Markdown for every URL, not just selected examples.
    for entry in entries:
        h = entry["hash"]
        paths = {name: data / folder / f"{h}.md" for name, folder in
                 [("a", "cache_a"), ("b", "cache_b"),
                  ("c", "cache_c"), ("gt", "ground_truth")]}
        raw = {name: path.read_text(encoding="utf-8")
               for name, path in paths.items() if path.exists()}
        if not all(name in raw for name in "abc"):
            raise ValueError(f"Missing cache for {entry['url']}")

        # Compare the original broad method with the narrower, stricter one.
        b_clean, _ = verify_raw_chrome(raw["b"])
        c_clean, c_chrome = verify_raw_chrome(raw["c"])
        original_equal = norm(remove_chrome(raw["b"])) == norm(remove_chrome(raw["c"]))
        same_body = strict_body(b_clean) == strict_body(c_clean)
        raw_equal = strict_body(raw["b"]) == strict_body(raw["c"])

        # Check the direction: does C differ from B *only* by added chrome?
        c_adds_chrome = (
            not raw_equal and bool(c_chrome)
            and strict_body(raw["b"]) == strict_body(c_clean)
        )

        counts["bc_compared"] += 1
        counts["bc_same_body"] += same_body
        counts["bc_different_body"] += not same_body
        counts["bc_c_adds_only_verified_chrome"] += c_adds_chrome
        counts["bc_original_audit_equal"] += original_equal
        counts["bc_original_vs_strict_disagreements"] += original_equal != same_body

        category = f"{entry['split']}/{entry['category']}"
        by_category[category]["total"] += 1
        by_category[category]["same_body"] += same_body
        by_category[category]["different_body"] += not same_body

        # The ground truth exists only for some evaluation URLs.
        gt_same_a = ""
        gt_chrome = ""
        if entry["split"] == "evaluation" and "gt" in raw:
            gt_clean, gt_blocks = verify_raw_chrome(raw["gt"])
            a_clean, _ = verify_raw_chrome(raw["a"])
            gt_same_a = strict_body(gt_clean) == strict_body(a_clean)
            gt_chrome = bool(gt_blocks)
            counts["gt_compared"] += 1
            counts["gt_same_as_a"] += gt_same_a
            if entry["category"] == "pdf":
                counts["pdf_gt_compared"] += 1
                counts["pdf_gt_with_verified_chrome"] += gt_chrome

        # Keep a small diff in the CSV for inspecting the 73 differing pairs.
        diff = []
        if not same_body:
            diff = list(difflib.unified_diff(
                strict_body(b_clean).splitlines(),
                strict_body(c_clean).splitlines(),
                fromfile="B", tofile="C", lineterm="",
            ))

        row = {
            "url": entry["url"], "split": entry["split"],
            "category": entry["category"],
            **{f"{name}_path": str(path.relative_to(root)) if path.exists() else ""
               for name, path in paths.items()},
            "bc_original_equal": original_equal,
            "bc_same_body": same_body,
            "c_adds_only_chrome": c_adds_chrome,
            "gt_same_as_a": gt_same_a,
            "gt_has_verified_chrome": gt_chrome,
            "body_diff": " | ".join(diff[:25]),
        }
        rows.append(row)

        # One example per category/split/outcome is enough for a quick review.
        example_key = (category, same_body)
        examples.setdefault(example_key, row)

    # Save one full CSV, a small selection of examples, and summary statistics.
    out = root / "audit_results"
    out.mkdir(parents=True, exist_ok=True)
    # Copy up to two differing pairs per split/category, preserving raw Markdown.
    sample_dir = out / "bc_different_samples"
    sample_dir.mkdir(exist_ok=True)
    sampled = Counter()
    index = ["# B/C differing output samples", "",
             "Raw, unmodified outputs from pairs that still differ after chrome removal.",
             "Up to two pairs per split/category, in dataset order.", ""]
    for row in rows:
        group = (row["split"], row["category"])
        if row["bc_same_body"] or sampled[group] >= 2:
            continue
        sampled[group] += 1
        stem = f"{row['split']}_{row['category']}_{Path(row['b_path']).stem}"
        for engine in "bc":
            shutil.copyfile(root / row[f"{engine}_path"], sample_dir / f"{stem}_{engine}.md")
        gt_link = "GT unavailable"
        if row["gt_path"]:
            shutil.copyfile(root / row["gt_path"], sample_dir / f"{stem}_gt.md")
            gt_link = f"[GT]({stem}_gt.md)"
        index.append(f"- {row['url']} ({row['split']}, {row['category']}): "
                     f"[B]({stem}_b.md) | [C]({stem}_c.md) | {gt_link}")
    (sample_dir / "README.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    for filename, selected in [
        ("dataset_validation_all.csv", rows),
        ("dataset_validation_examples.csv", list(examples.values())),
    ]:
        with (out / filename).open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(selected)

    summary = {
        "counts": dict(counts),
        "by_split_and_category": {k: dict(v) for k, v in sorted(by_category.items())},
        "notes": [
            "Same body means identical nonblank lines after narrow chrome removal.",
            "Different body may include formatting-only differences; inspect the CSV.",
            "Matches and disagreements do not establish which extraction is correct.",
        ],
    }
    (out / "dataset_validation.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print("DATASET VALIDATION (offline)")
    print(f"B/C compared: {counts['bc_compared']}")
    print(f"B/C same body: {counts['bc_same_body']}; "
          f"different body: {counts['bc_different_body']}")
    print(f"C adds only verified chrome: {counts['bc_c_adds_only_verified_chrome']}")
    print(f"Original/strict comparison disagreements: "
          f"{counts['bc_original_vs_strict_disagreements']}")
    print(f"GT/A same body: {counts['gt_same_as_a']}/{counts['gt_compared']}")
    print(f"PDF GT with chrome: {counts['pdf_gt_with_verified_chrome']}/"
          f"{counts['pdf_gt_compared']}")
    print(f"Full results: {out / 'dataset_validation_all.csv'}")
    print(f"Summary: {out / 'dataset_validation.json'}")


def main():
    """Load stored files, calculate dataset indicators, and write a JSON report.

    The sections below cover: integrity/category coverage, numbers, pairwise
    stored-output differences, duplicate content, and GT provenance signals.
    No quality metrics or engine rankings are computed here.
    """
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--validate", action="store_true",
                    help="Check all B/C pairs using stricter chrome removal")
    args = ap.parse_args()
    root = args.root.resolve()
    if args.validate:
        validate_findings(root)
        return
    data = root / "data"
    seed = json.loads((data / "seed_urls.json").read_text())
    prod = json.loads((data / "prod_sample_urls.json").read_text())
    entries = seed + prod
    # URL hashes link each URL record to its ground-truth/cache filename.
    # A dict also collapses duplicate hashes; counts below expose that fact.
    records = {e["hash"]: e for e in entries}
    seed_hashes = {e["hash"] for e in seed}
    prod_hashes = {e["hash"] for e in prod}

    # Load files once: ~300 references and ~600 cached outputs per engine.
    # File stems are URL hashes; contents are stored Markdown, not raw HTML.
    files = {
        label: {p.stem: p.read_text(encoding="utf-8") for p in (data / folder).glob("*.md")}
        for label, folder in [("gt", "ground_truth"), ("a", "cache_a"),
                              ("b", "cache_b"), ("c", "cache_c")]
    }
    # Limit referenced GT to the *evaluation* split, which is the annotated set.
    refs = {h: text for h, text in files["gt"].items() if h in seed_hashes}
    # Work with an optional chrome-stripped view; never change source files.
    clean = {label: {h: remove_chrome(t) for h, t in corpus.items()}
             for label, corpus in files.items()}
    # Compare numbers in the chrome-stripped content, using occurrence counts.
    nums = {label: {h: numeric_values(t) for h, t in corpus.items()}
            for label, corpus in clean.items()}

    # 1. Dataset size, coverage, category mix, and possible cross-split overlap.
    # Annotation coverage is per category; it is not an accuracy measurement.
    seed_categories = Counter(e["category"] for e in seed)
    prod_categories = Counter(e["category"] for e in prod)
    annotated = Counter(e["category"] for e in seed if e["hash"] in refs)
    category_table = {
        cat: {
            "eval": seed_categories[cat], "production": prod_categories[cat],
            "ground_truth": annotated[cat],
            "annotation_coverage_pct": percentage(annotated[cat], seed_categories[cat]),
            "eval_share_pct": percentage(seed_categories[cat], len(seed)),
            "production_share_pct": percentage(prod_categories[cat], len(prod)),
        }
        for cat in sorted(set(seed_categories) | set(prod_categories))
    }
    # Hostname concentration can reveal templated or non-independent samples.
    domains = Counter(urlsplit(e["url"]).netloc for e in entries)
    integrity = {
        "evaluation_urls": len(seed), "production_urls": len(prod),
        "unique_urls": len({e["url"] for e in entries}),
        "unique_hashes": len(records),
        # Verify whether each stored hash really corresponds to its URL.
        "bad_sha256_hashes": sum(hashlib.sha256(e["url"].encode()).hexdigest() != e["hash"]
                                 for e in entries),
        "overlap_between_splits": len(seed_hashes & prod_hashes),
        "ground_truth_files": len(files["gt"]),
        "ground_truth_for_eval_urls": len(refs),
        "orphan_ground_truth": len(set(files["gt"]) - seed_hashes),
        "cache_missing_files": {e: len(set(records) - set(files[e])) for e in "abc"},
        "unique_domains": len(domains),
        "top_domains": domains.most_common(8),
    }

    # 2. How common are numeric tokens in annotated references?
    # Count occurrences and pages separately so repeated numbers aren't lost.
    number_by_category = defaultdict(lambda: {"pages": 0, "pages_with_numbers": 0,
                                               "number_occurrences": 0})
    for h in refs:
        d = number_by_category[records[h]["category"]]
        d["pages"] += 1
        d["pages_with_numbers"] += bool(nums["gt"][h])
        d["number_occurrences"] += sum(nums["gt"][h].values())
    for d in number_by_category.values():
        d["prevalence_pct"] = percentage(d["pages_with_numbers"], d["pages"])
    numbers_in_refs = sum(bool(nums["gt"][h]) for h in refs)

    # 3. Numeric disagreement with GT, only where both files exist.
    # Difference between Counters signals a mismatch, NOT a verified error;
    # references may themselves contain artifacts from a scrape engine.
    against_reference = {}
    for engine in "abc":
        hs = sorted(set(refs) & set(nums[engine]))
        mismatches = [h for h in hs if nums[engine][h] != nums["gt"][h]]
        against_reference[engine] = {
            "compared": len(hs), "different": len(mismatches),
            "different_pct": percentage(len(mismatches), len(hs)),
            "examples": [records[h]["url"] for h in mismatches[:5]],
        }

    # 4. Pairwise comparison on the same URL, without evaluating a winner.
    # Numeric differences use the chrome-stripped versions of both outputs.
    # ``chrome_only`` below is intentionally directional: it checks whether
    # the second engine adds labeled chrome onto the first engine's full text.
    pairs = {}
    for a, b in [("a", "b"), ("a", "c"), ("b", "c")]:
        hs = sorted(set(records) & set(files[a]) & set(files[b]))
        num_diff = sum(nums[a][h] != nums[b][h] for h in hs)
        # A symmetric test: after stripping chrome from *both*, are they equal?
        same_body = sum(norm(clean[a][h]) == norm(clean[b][h]) for h in hs)
        chrome_only = [h for h in hs if norm(files[a][h]) != norm(files[b][h])
                       and chrome_labels(files[b][h])
                       and norm(files[a][h]) == norm(clean[b][h])]
        pairs[f"{a}_vs_{b}"] = {
            "compared": len(hs), "numeric_disagreement": num_diff,
            "numeric_disagreement_pct": percentage(num_diff, len(hs)),
            "same_after_chrome_removal": same_body,
            "only_extra_nav_footer_in_second": len(chrome_only),
            "chrome_only_examples": [records[h]["url"] for h in chrome_only[:5]],
        }

    # 5. Duplicate documents based on exact normalized text.
    # Report raw and chrome-stripped duplicates separately: some documents
    # become identical only after removing labeled nav/footer sections.
    dupes = {
        label: {
            "raw": duplicates(corpus, records),
            "without_nav_footer": duplicates(clean[label], records),
        }
        for label, corpus in files.items()
    }
    body_groups = defaultdict(list)
    for h in refs:
        body_groups[norm(clean["gt"][h])].append(h)
    # A GT chrome variant is a group with equal stripped text but differing
    # raw text. Similarity alone doesn't establish that two URLs are the same page.
    chrome_variants = [group for group in body_groups.values()
                       if len(group) > 1 and len({norm(refs[h]) for h in group}) > 1]

    # 6. GT provenance/contamination indicators.
    # GT matching A's stripped content can suggest reference dependence, but
    # is NOT proof that A is correct or that an annotation was independently made.
    # PDF references containing nav/footer are especially worth inspecting,
    # given the generator's documented PDF exclusion rule.
    gt_matches_a = sum(norm(clean["gt"][h]) == norm(clean["a"][h])
                       for h in refs if h in clean["a"])
    gt_chrome = sum(bool(chrome_labels(refs[h])) for h in refs)
    pdf_refs = [h for h in refs if records[h]["category"] == "pdf"]
    pdf_with_chrome = sum(bool(chrome_labels(refs[h])) for h in pdf_refs)

    result = {
        "integrity": integrity, "categories": category_table,
        "numeric_prevalence_ground_truth": {
            "pages": len(refs), "with_numbers": numbers_in_refs,
            "with_numbers_pct": percentage(numbers_in_refs, len(refs)),
            "by_category": dict(number_by_category),
        },
        "numeric_disagreement_vs_reference": against_reference,
        "pairwise_output_comparison": pairs,
        "duplicates": dupes,
        "ground_truth_chrome_and_provenance": {
            "refs_with_nav_or_footer": gt_chrome,
            "pdf_refs_with_nav_or_footer": pdf_with_chrome,
            "pdf_reference_count": len(pdf_refs),
            "gt_matches_engine_a_after_removing_nav_footer": gt_matches_a,
            "gt_compared_to_engine_a": len(set(refs) & set(files["a"])),
            "gt_variant_groups_differing_only_in_nav_footer": len(chrome_variants),
            "gt_variant_examples": [[records[h]["url"] for h in group[:5]]
                                    for group in chrome_variants[:5]],
        },
        "caveats": [
            "Differences in numbers are discrepancies, not verified factual errors.",
            "Navigation/footer removal detects only explicitly labeled sections.",
            "Duplicates use exact text after case/whitespace normalization; near-duplicates remain for v2.",
            "Ground truth is engine-derived; similarity to an engine is not proof of correctness.",
        ],
    }
    # Persist raw counts, compact examples, and explicit interpretation caveats.
    # Only this generated JSON is written; data/ and eval/ are unchanged.
    out = root / "audit_results" / "dataset_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    # Human-readable preview; full per-section results remain in the JSON.
    print("DATASET AUDIT (offline, no API calls)")
    print(f"Eval: {len(seed)} | Production: {len(prod)} | GT: {len(refs)} "
          f"| Unique domains: {len(domains)}")
    print("\nCategory       Eval  Prod   GT  GT coverage")
    for cat, s in category_table.items():
        print(f"{cat:<14}{s['eval']:>4}{s['production']:>6}{s['ground_truth']:>5}"
              f"{s['annotation_coverage_pct']:>12.1f}%")
    print(f"\nGT pages with numbers: {numbers_in_refs}/{len(refs)} "
          f"({percentage(numbers_in_refs, len(refs))}%)")
    for eng, x in against_reference.items():
        print(f"Numeric disagreement {eng.upper()} vs GT: {x['different']}/{x['compared']}")
    for pair, x in pairs.items():
        print(f"{pair}: numeric disagreement {x['numeric_disagreement']}/{x['compared']}; "
              f"only extra nav/footer in second {x['only_extra_nav_footer_in_second']}")
    print(f"\nDuplicate GT groups: {dupes['gt']['raw']['groups']} raw; "
          f"{dupes['gt']['without_nav_footer']['groups']} without chrome")
    print(f"GT chrome-only variant groups: {len(chrome_variants)}")
    print(f"GT body matching A: {gt_matches_a}/{len(set(refs) & set(files['a']))}")
    print(f"GT with chrome: {gt_chrome}/{len(refs)}; PDF: {pdf_with_chrome}/{len(pdf_refs)}")
    print(f"\nDetails and examples: {out}")
    print("Note: disagreements and duplicate indicators need human interpretation.")


if __name__ == "__main__":
    main()
