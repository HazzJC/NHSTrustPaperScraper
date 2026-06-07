from __future__ import annotations

import datetime as dt
import email.utils
import re
import threading
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse

import requests

from scraper.constants import COMMON_PATHS

_GENERIC_LINK_TEXTS = frozenset({
    "view pdf", "download pdf", "download", "view", "open", "read more",
    "click here", "here", "pdf", "link", "document", "file",
})

# Year patterns to detect year-organised archive URLs
_YEAR_RANGE_RE = re.compile(r'/(20\d{2})[-/](20?\d{2})/', re.I)
_YEAR_ONLY_RE  = re.compile(r'/(20\d{2})/', re.I)

# Pagination link text patterns — follow these at higher priority than nav links
_PAGINATION_RE = re.compile(
    r'\b(next|older|previous year|previous meetings?|more meetings?|earlier|archive)\b',
    re.I,
)
from scraper.extraction import (
    document_extension,
    domain_for,
    extract_date,
    iter_links,
    looks_like_document_link,
    looks_like_html_page,
    nav_links_from_html,
    site_allowed,
)
from scraper.models import Candidate, Trust
from scraper.navigation import discover_official_site, sitemap_urls
from scraper.scoring import (
    add_queue_url,
    candidate_score,
    classify_report_type,
    page_relevance_score,
)
from scraper.session import request_page
from scraper.downloader import safe_filename


def _infer_year_siblings(url: str) -> list[str]:
    """For a URL containing a year segment, generate adjacent-year variants.

    Given /board-papers/2025-26/ → also produces /board-papers/2024-25/ and
    /board-papers/2026-27/ so the scraper proactively discovers year archives
    without relying on explicit links to every year.
    """
    current_year = dt.date.today().year
    results: list[str] = []

    m = _YEAR_RANGE_RE.search(url)
    if m:
        start_yr = int(m.group(1))
        sep = "-" if "-" in m.group(0)[1:] else "/"
        for yr in (start_yr - 1, start_yr + 1):
            if yr < 2020 or yr > current_year + 1:
                continue
            short_next = str(yr + 1)[-2:]
            replacement = f"/{yr}{sep}{short_next}/"
            new_url = _YEAR_RANGE_RE.sub(replacement, url, count=1)
            if new_url != url:
                results.append(new_url)
        return list(dict.fromkeys(results))

    m = _YEAR_ONLY_RE.search(url)
    if m:
        start_yr = int(m.group(1))
        for yr in (start_yr - 1, start_yr + 1):
            if yr < 2020 or yr > current_year + 1:
                continue
            new_url = _YEAR_ONLY_RE.sub(f"/{yr}/", url, count=1)
            if new_url != url:
                results.append(new_url)
        return list(dict.fromkeys(results))

    return []


def discover_candidates(
    session: requests.Session,
    trust: Trust,
    *,
    max_pages: int,
    timeout: int,
    crawl_delay: float,
    selected_types: set[str],
    stop_event: threading.Event | None = None,
    cached_pages: list[str] | None = None,
    fast_check: bool = False,
    on_page_scan: Callable[[str], None] | None = None,
) -> list[Candidate]:
    """Crawl a trust's website and return document candidates.

    fast_check=True: only fetch the cached_pages (skip homepage/start_urls/sitemaps).
    on_page_scan: called with each page URL just before it is fetched (verbose mode).
    """
    if fast_check:
        # Quick pass — only revisit previously-known good pages
        if not cached_pages:
            return []
        queue: list[tuple[int, str]] = [(200, url) for url in cached_pages]
        allowed_domains: set[str] = {domain_for(url) for url in cached_pages}
        allowed_domains.update(
            domain.lower().removeprefix("www.") for domain in trust.allowed_domains
        )
    else:
        trust_url = trust.url or discover_official_site(session, trust, timeout)
        if not trust_url:
            print("  Could not discover official NHS website.")
            return []

        allowed_domains = {domain_for(trust_url)}
        allowed_domains.update(domain_for(url) for url in trust.start_urls)
        allowed_domains.update(
            domain.lower().removeprefix("www.") for domain in trust.allowed_domains
        )
        if cached_pages:
            allowed_domains.update(domain_for(url) for url in cached_pages)

        queue = [(100, trust_url)]
        for url in trust.start_urls:
            queue.append((150, urljoin(trust_url, url)))
        # Previously-successful source pages jump straight to the front of the queue
        for url in (cached_pages or []):
            queue.append((200, url))
        for path in COMMON_PATHS:
            queue.append((60, urljoin(trust_url, path)))
        for url in sitemap_urls(
            session, trust_url, timeout=timeout, selected_types=selected_types
        ):
            queue.append((80 + page_relevance_score(url, selected_types), url))

    queue.sort(reverse=True)

    seen_pages: set[str] = set()
    queued_urls: set[str] = {url for _, url in queue}
    seen_candidates: set[str] = set()
    candidates: list[Candidate] = []

    while queue and len(seen_pages) < max_pages:
        if stop_event and stop_event.is_set():
            break

        _, page_url = queue.pop(0)
        if page_url in seen_pages:
            continue
        if not site_allowed(page_url, allowed_domains):
            continue

        seen_pages.add(page_url)
        print(f"  Scanning {page_url}")
        if on_page_scan:
            on_page_scan(page_url)

        result = request_page(session, page_url, timeout, crawl_delay)
        if not result:
            continue

        html, final_page_url = result
        allowed_domains.add(domain_for(final_page_url))

        # On the homepage, parse structural nav/header elements and queue all
        # internal links at score 40 so intermediate navigation pages (e.g.
        # /corporate-documents/) are explored even when they have no keywords.
        if not fast_check and len(seen_pages) == 1:
            for nav_url in nav_links_from_html(html, final_page_url):
                if nav_url not in seen_pages and nav_url not in queued_urls:
                    queue.append((40, nav_url))
                    queued_urls.add(nav_url)
            queue.sort(reverse=True)

        for link_url, link_text, context_text in iter_links(html, final_page_url):
            if not site_allowed(link_url, allowed_domains):
                continue

            # Include surrounding DOM context so dates/types in <li> wrappers and
            # <h3> headings are available to classification and date extraction.
            combined_text = f"{link_text} {context_text} {link_url}"

            if looks_like_document_link(link_url, link_text):
                report_type, type_score = classify_report_type(combined_text, selected_types)
                if not report_type or link_url in seen_candidates:
                    continue

                found_date, date_source = extract_date(combined_text)
                # Prefer surrounding context over generic labels like "View PDF"
                meaningful_text = (
                    link_text
                    if link_text and link_text.lower().strip() not in _GENERIC_LINK_TEXTS
                    else ""
                )
                if not meaningful_text and context_text:
                    # Use context text (trimmed) as title fallback
                    meaningful_text = context_text[:80].strip()
                title = safe_filename(
                    meaningful_text or Path(urlparse(link_url).path).stem, max_length=90
                )
                seen_candidates.add(link_url)
                candidates.append(
                    Candidate(
                        url=link_url,
                        source_page=final_page_url,
                        link_text=link_text,
                        date=found_date,
                        date_source=date_source,
                        report_type=report_type,
                        title=title,
                        extension=document_extension(link_url),
                        score=candidate_score(
                            url=link_url,
                            text=combined_text,
                            found_date=found_date,
                            type_score=type_score,
                        ),
                    )
                )
                # Infer adjacent year-archive pages from year patterns in the doc URL
                if not fast_check:
                    for yr_url in _infer_year_siblings(link_url):
                        add_queue_url(
                            queue, yr_url, score=130,
                            seen_pages=seen_pages, queued_urls=queued_urls,
                        )
                continue

            if not fast_check and looks_like_html_page(link_url):
                # Pagination links ("next", "older meetings") get higher priority
                # so year archives are explored before exhausting max_pages on nav.
                if _PAGINATION_RE.search(link_text):
                    add_queue_url(
                        queue, link_url, score=140,
                        seen_pages=seen_pages, queued_urls=queued_urls,
                    )
                else:
                    add_queue_url(
                        queue,
                        link_url,
                        score=page_relevance_score(combined_text, selected_types),
                        seen_pages=seen_pages,
                        queued_urls=queued_urls,
                    )

    return candidates


def apply_last_modified_dates(
    session: requests.Session,
    candidates: list[Candidate],
    *,
    timeout: int,
) -> None:
    for candidate in candidates:
        if candidate.date is not None:
            continue
        try:
            response = session.head(candidate.url, allow_redirects=True, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException:
            continue
        last_modified = response.headers.get("last-modified")
        if not last_modified:
            continue
        parsed = email.utils.parsedate_to_datetime(last_modified)
        if parsed:
            candidate.date = parsed.date()
            candidate.date_source = "http_last_modified"
            candidate.score += 5


def selected_candidates(
    candidates: list[Candidate],
    *,
    all_matches: bool,
    limit_per_type: int,
    cutoff_dates: dict[str, dt.date] | None = None,
) -> list[Candidate]:
    if cutoff_dates:
        filtered = []
        for c in candidates:
            cutoff = cutoff_dates.get(c.report_type)
            # Only exclude if we have both a cutoff and a confirmed date older than it
            if cutoff and c.date is not None and c.date < cutoff:
                continue
            filtered.append(c)
        candidates = filtered

    candidates.sort(
        key=lambda item: (
            item.report_type,
            item.date or dt.date.min,
            item.score,
            item.title,
            item.url,
        ),
        reverse=True,
    )
    if all_matches:
        return candidates

    chosen: list[Candidate] = []
    counts: dict[str, int] = {}
    for candidate in candidates:
        count = counts.get(candidate.report_type, 0)
        if count >= limit_per_type:
            continue
        chosen.append(candidate)
        counts[candidate.report_type] = count + 1
    return chosen
