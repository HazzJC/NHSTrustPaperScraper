from __future__ import annotations

import asyncio
import datetime as dt

from scraper.constants import REPORT_TYPES
from scraper.models import Candidate, Trust
from scraper.extraction import document_extension, extract_date


class JSFallbackFetcher:
    """
    Wraps AdvancedCrawler (crawl4ai + Selenium) to handle JS-rendered NHS sites.
    Returns Candidate objects with minimal scoring — JS fallback gives URLs,
    not scored candidates with keyword intelligence.
    """

    def fetch(self, trust: Trust, *, selected_types: set[str]) -> list[Candidate]:
        from crawler.crawler import AdvancedCrawler

        start_url = trust.start_urls[0] if trust.start_urls else trust.url
        if not start_url:
            return []

        crawler = AdvancedCrawler()
        raw_results = asyncio.run(crawler.deep_crawl(start_url))

        candidates: list[Candidate] = []
        default_type = next(iter(selected_types)) if selected_types else "board"

        for item in raw_results:
            url = item.get("url", "")
            if not url:
                continue

            title = item.get("title") or url.rsplit("/", 1)[-1]
            found_date, date_source = extract_date(f"{title} {url}")
            ext = document_extension(url)

            candidates.append(
                Candidate(
                    url=url,
                    source_page=start_url,
                    link_text=title,
                    date=found_date,
                    date_source=date_source,
                    report_type=default_type,
                    title=title,
                    extension=ext,
                    score=REPORT_TYPES[default_type]["bonus"],
                )
            )

        return candidates
