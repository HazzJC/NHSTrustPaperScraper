from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import requests

from scraper.extraction import domain_for, iter_links
from scraper.models import Trust
from scraper.scoring import page_relevance_score
from scraper.session import request_page


def discover_official_site(
    session: requests.Session, trust: Trust, timeout: int
) -> str | None:
    query = quote_plus(trust.search_query or f"{trust.name} official website")
    search_url = f"https://duckduckgo.com/html/?q={query}"
    result = request_page(session, search_url, timeout)
    if not result:
        return None

    html, _ = result
    for url, text in iter_links(html, search_url):
        parsed = urlparse(url)
        if "duckduckgo.com" in parsed.netloc:
            uddg = parse_qs(parsed.query).get("uddg", [None])[0]
            if uddg:
                parsed = urlparse(uddg)
            else:
                continue
        if ".nhs.uk" in parsed.netloc.lower():
            return f"{parsed.scheme}://{parsed.netloc}/"
    return None


def _parse_sitemap_locs(
    session: requests.Session,
    sitemap_url: str,
    *,
    timeout: int,
    selected_types: set[str],
    candidates: list[tuple[int, str]],
    depth: int = 0,
) -> None:
    try:
        response = session.get(sitemap_url, timeout=timeout)
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except (requests.RequestException, ET.ParseError):
        return

    is_index = root.tag.endswith("sitemapindex")
    sub_sitemap_urls: list[str] = []

    for loc in root.iter():
        if not loc.tag.endswith("loc") or not loc.text:
            continue
        url = loc.text.strip()
        if is_index and depth == 0:
            sub_sitemap_urls.append(url)
        else:
            score = page_relevance_score(url, selected_types)
            if score:
                candidates.append((score, url))

    for sub_url in sub_sitemap_urls[:10]:
        _parse_sitemap_locs(
            session, sub_url, timeout=timeout,
            selected_types=selected_types, candidates=candidates, depth=depth + 1,
        )


def sitemap_urls(
    session: requests.Session,
    base_url: str,
    *,
    timeout: int,
    selected_types: set[str],
    limit: int = 200,
) -> list[str]:
    candidates: list[tuple[int, str]] = []
    for sitemap_path in ("/sitemap.xml", "/sitemap_index.xml"):
        _parse_sitemap_locs(
            session,
            urljoin(base_url, sitemap_path),
            timeout=timeout,
            selected_types=selected_types,
            candidates=candidates,
        )
    candidates.sort(reverse=True)
    return [url for _, url in candidates[:limit]]
