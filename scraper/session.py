from __future__ import annotations

import time

import requests
import urllib3


def build_session(*, verify_ssl: bool) -> requests.Session:
    if not verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    session = requests.Session()
    session.verify = verify_ssl
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            )
        }
    )
    return session


def request_page(
    session: requests.Session,
    url: str,
    timeout: int,
    crawl_delay: float = 0.0,
    max_retries: int = 3,
) -> tuple[str, str] | None:
    if crawl_delay:
        time.sleep(crawl_delay)
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = session.get(url, timeout=timeout)
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < max_retries:
                time.sleep(2 ** attempt)
            continue
        if response.status_code == 429:
            wait = int(response.headers.get("Retry-After", min(60, 5 * (2 ** attempt))))
            print(f"  429 rate-limited — waiting {wait}s (attempt {attempt + 1}/{max_retries}): {url}")
            time.sleep(wait)
            continue
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            print(f"  Could not fetch page: {url} ({exc})")
            return None
        content_type = response.headers.get("content-type", "").lower()
        if "text/html" not in content_type and "<html" not in response.text[:500].lower():
            return None
        return response.text, response.url
    print(f"  Could not fetch page after {max_retries} retries: {url} ({last_exc})")
    return None
