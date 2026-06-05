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
    session: requests.Session, url: str, timeout: int, crawl_delay: float = 0.0
) -> tuple[str, str] | None:
    if crawl_delay:
        time.sleep(crawl_delay)
    try:
        response = session.get(url, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"  Could not fetch page: {url} ({exc})")
        return None

    content_type = response.headers.get("content-type", "").lower()
    if "text/html" not in content_type and "<html" not in response.text[:500].lower():
        return None
    return response.text, response.url
