from __future__ import annotations

import datetime as dt
import re
from typing import Iterable

from scraper.constants import NAV_PATH_FRAGMENTS, REPORT_TYPES


def term_hits(text: str, terms: Iterable[str]) -> int:
    haystack = text.lower()
    hits = 0
    for term in terms:
        normalized = term.lower()
        if len(normalized) <= 3:
            if re.search(rf"\b{re.escape(normalized)}\b", haystack):
                hits += 1
        elif normalized in haystack:
            hits += 1
    return hits


def _normalise(text: str) -> str:
    """Lowercase and replace URL separators with spaces so 'board-of-directors'
    matches the file_term 'board of directors'."""
    return text.lower().replace("-", " ").replace("_", " ")


def classify_report_type(text: str, selected_types: set[str]) -> tuple[str | None, int]:
    best_type = None
    best_score = 0
    haystack = _normalise(text)

    for report_type in selected_types:
        profile = REPORT_TYPES[report_type]
        if profile["required_any"] and not any(
            term in haystack for term in profile["required_any"]
        ):
            continue
        hits = term_hits(haystack, profile["file_terms"])
        if hits == 0:
            continue
        score = profile["bonus"] + (hits * 20)
        if score > best_score:
            best_type = report_type
            best_score = score

    return best_type, best_score


def page_relevance_score(text: str, selected_types: set[str]) -> int:
    score = 0
    haystack = _normalise(text)
    for report_type in selected_types:
        profile = REPORT_TYPES[report_type]
        score += term_hits(haystack, profile["page_terms"]) * 20
        score += term_hits(haystack, profile["file_terms"]) * 8
    if "board" in haystack:
        score += 15
    if "governance" in haystack or "publication" in haystack:
        score += 10
    return score


def candidate_score(
    *,
    url: str,
    text: str,
    found_date: dt.date | None,
    type_score: int,
) -> int:
    haystack = _normalise(f"{text} {url}")
    score = type_score
    from scraper.extraction import looks_like_document
    if looks_like_document(url):
        score += 50
    if found_date:
        score += 40
    if "draft" in haystack:
        score -= 15
    if "minute" in haystack and "supplementary" not in haystack:
        score -= 25
    return score


def add_queue_url(
    queue: list[tuple[int, str]],
    url: str,
    *,
    score: int,
    seen_pages: set[str],
    queued_urls: set[str],
) -> None:
    if url in seen_pages or url in queued_urls:
        return
    # Even if keyword score is 0, follow pages whose URL path contains a
    # navigation fragment — they're likely intermediate listing pages (e.g.
    # /corporate-documents/) that lead to board papers but carry no keywords.
    if score <= 0:
        url_lower = url.lower()
        if not any(frag in url_lower for frag in NAV_PATH_FRAGMENTS):
            return
        score = 25  # low but non-zero so it enters the queue
    queue.append((score, url))
    queued_urls.add(url)
    queue.sort(reverse=True)
