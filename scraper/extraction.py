from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse
from urllib.parse import unquote

from bs4 import BeautifulSoup, Tag

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


_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "strong"}
_CONTAINER_TAGS = {"li", "tr", "td", "div", "p", "dt", "dd", "article", "section"}


def _nearest_heading(anchor) -> str:
    """Return the text of the nearest preceding heading above an anchor element.

    Searches previous siblings at the same level, then walks up two parent
    levels and repeats. Returns the first h1-h5 or <strong> text found, capped
    at 120 chars, so it can be included in combined_text for classification.
    """
    def _search_siblings(el) -> str:
        for sibling in el.previous_siblings:
            if not isinstance(sibling, Tag):
                continue
            if sibling.name in _HEADING_TAGS:
                return " ".join(sibling.get_text(" ", strip=True).split())[:120]
            # Also look inside sibling containers for headings
            found = sibling.find(re.compile(r"^(h[1-5]|strong)$"))
            if found:
                return " ".join(found.get_text(" ", strip=True).split())[:120]
        return ""

    # Check immediate siblings
    result = _search_siblings(anchor)
    if result:
        return result
    # Walk up two parent levels
    node = anchor.parent
    for _ in range(2):
        if node is None:
            break
        result = _search_siblings(node)
        if result:
            return result
        node = node.parent
    return ""


def iter_links(html: str, page_url: str) -> Iterable[tuple[str, str, str]]:
    """Yield (url, link_text, context_text) for every anchor on the page.

    context_text combines:
    - The direct parent container's text (the <li>/<tr>/<div> holding the link)
    - The nearest preceding heading above the link

    This allows classification and date extraction to use surrounding DOM
    context rather than relying solely on often-generic anchor text.
    """
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(page_url, href).split("#", 1)[0]
        link_text = " ".join(anchor.get_text(" ", strip=True).split())

        # Parent container text: use the closest meaningful container element
        container_text = ""
        node = anchor.parent
        while node and getattr(node, "name", None) not in _CONTAINER_TAGS:
            node = getattr(node, "parent", None)
        if node:
            container_text = " ".join(node.get_text(" ", strip=True).split())[:200]

        heading_text = _nearest_heading(anchor)
        context_text = f"{container_text} {heading_text}".strip()

        yield url, link_text, context_text


_DATE_MIN_YEAR = 2000
_DATE_MAX_YEAR_OFFSET = 1  # accept up to 1 year in the future


def _year_plausible(year: int) -> bool:
    return _DATE_MIN_YEAR <= year <= dt.date.today().year + _DATE_MAX_YEAR_OFFSET


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
            if not _year_plausible(year):
                continue
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
            if not _year_plausible(year):
                continue
            return dt.date(year, month, day), "month_name"
        except ValueError:
            pass

    return None, "not_found"
