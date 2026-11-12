import hashlib
import json
import os
import pathlib

import anthropic

PROMPT = """You are evaluating the quality of a web scraping engine's output.
Below is the scraper's markdown output for a webpage.
Rate the overall quality on a scale of 1-5, where 5 is excellent.
Consider completeness, readability, and usefulness.

Output:
---
{output}
---

Respond with only a single digit (1-5)."""

MODEL = "claude-sonnet-4-6"
DEFAULT_CACHE = pathlib.Path("eval/judge_cache.json")


def _cache_key(engine: str, url: str) -> str:
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    return f"{engine}:{url_hash}"


def _load_cache(cache_path: str) -> dict:
    p = pathlib.Path(cache_path)
    if p.exists():
        return json.loads(p.read_text())
    return {}


def _save_cache(cache: dict, cache_path: str) -> None:
    pathlib.Path(cache_path).write_text(json.dumps(cache, indent=2))


def judge(
    output: str,
    engine: str,
    url: str,
    cache_path: str = str(DEFAULT_CACHE),
    fresh: bool = False,
) -> int:
    key = _cache_key(engine, url)
    cache = _load_cache(cache_path)

    if not fresh and key in cache:
        return cache[key]

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model=MODEL,
        max_tokens=16,
        temperature=1.0,
        messages=[{"role": "user", "content": PROMPT.format(output=output)}],
    )
    score_str = message.content[0].text.strip()
    score = int(score_str)
    score = max(1, min(5, score))

    cache[key] = score
    _save_cache(cache, cache_path)
    return score
