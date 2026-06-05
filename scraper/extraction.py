from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse
from urllib.parse import unquote

from bs4 import BeautifulSoup

from scraper.constants import DOCUMENT_EXTENSIONS, MONTHS, SKIP_EXTENSIONS


def domain_for(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def site_allowed(url: str, allowed_domains: set[str]) -> bool:
    return domain_for(url) in allowed_domains


def looks_like_document(url: str) -> bool:
    return Path(urlparse(url).path.lower()).suffix in DOCUMENT_EXTENSIONS


def looks_like_document_link(url: str, text: str) -> bool:
    haystack = f"{text} {url}".lower()
    return (
        looks_like_document(url)
        or "/download" in urlparse(url).path.lower()
        or "download_file" in urlparse(url).path.lower()
        or "[pdf" in haystack
        or " pdf" in haystack
        or ".pdf" in haystack
    )


def document_extension(url: str) -> str:
    suffix = Path(urlparse(url).path.lower()).suffix
    return suffix if suffix in DOCUMENT_EXTENSIONS else ".pdf"


def looks_like_html_page(url: str) -> bool:
    path = urlparse(url).path.lower()
    return not path.endswith(SKIP_EXTENSIONS) and not looks_like_document(url)


def nav_links_from_html(html: str, page_url: str) -> list[str]:
    """Return internal HTML page URLs found inside structural nav/header/menu elements.

    Used on the trust homepage to ensure every top-level navigation link gets
    queued even when its link text contains no board-paper keywords.
    """
    soup = BeautifulSoup(html, "html.parser")
    base_domain = domain_for(page_url)
    seen: dict[str, None] = {}  # ordered dedup

    def _collect(element) -> None:
        for anchor in element.find_all("a", href=True):
            href = anchor.get("href", "").strip()
            if not href or href.startswith(("mailto:", "tel:", "javascript:")):
                continue
            url = urljoin(page_url, href).split("#", 1)[0]
            if domain_for(url) == base_domain and looks_like_html_page(url):
                seen[url] = None

    for el in soup.find_all(["nav", "header"]):
        _collect(el)
    for el in soup.find_all("ul", class_=re.compile(r"menu|nav|navigation", re.I)):
        _collect(el)

    return list(seen)


def iter_links(html: str, page_url: str) -> Iterable[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(page_url, href).split("#", 1)[0]
        text = " ".join(anchor.get_text(" ", strip=True).split())
        yield url, text


def extract_date(text: str) -> tuple[dt.date | None, str]:
    compact = unquote(text).replace("_", " ").replace("-", " ")

    patterns = [
        (r"\b(20\d{2})[./ -](0?[1-9]|1[0-2])[./ -](0?[1-9]|[12]\d|3[01])\b", "ymd"),
        (r"\b(0?[1-9]|[12]\d|3[01])[./ -](0?[1-9]|1[0-2])[./ -](20\d{2})\b", "dmy"),
        (r"\b(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b", "ymd_compact"),
    ]
    for pattern, source in patterns:
        match = re.search(pattern, compact)
        if not match:
            continue
        try:
            if source.startswith("ymd"):
                year, month, day = (int(part) for part in match.groups())
            else:
                day, month, year = (int(part) for part in match.groups())
            return dt.date(year, month, day), source
        except ValueError:
            pass

    month_names = "|".join(MONTHS)
    month_patterns = [
        rf"\b(0?[1-9]|[12]\d|3[01])\s+({month_names})\s+(20\d{{2}})\b",
        rf"\b({month_names})\s+(0?[1-9]|[12]\d|3[01]),?\s+(20\d{{2}})\b",
        rf"\b({month_names})\s+(20\d{{2}})\b",
    ]
    for index, pattern in enumerate(month_patterns):
        match = re.search(pattern, compact, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            parts = match.groups()
            if index == 0:
                day = int(parts[0])
                month = MONTHS[parts[1].lower()]
                year = int(parts[2])
            elif index == 1:
                month = MONTHS[parts[0].lower()]
                day = int(parts[1])
                year = int(parts[2])
            else:
                month = MONTHS[parts[0].lower()]
                day = 1
                year = int(parts[1])
            return dt.date(year, month, day), "month_name"
        except ValueError:
            pass

    return None, "not_found"
