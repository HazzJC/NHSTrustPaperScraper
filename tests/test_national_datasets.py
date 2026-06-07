"""Tests for scraper/national_datasets.py — base class helpers."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from bs4 import BeautifulSoup

from scraper.national_datasets import FetchResult, NationalDatasetFetcher


# ── Minimal concrete subclass for testing base helpers ────────────────────────

class _TestFetcher(NationalDatasetFetcher):
    source_key = "test"
    display_name = "Test Dataset"
    output_subdir = "test"

    def fetch(self, output_dir, session):
        raise NotImplementedError("not used in tests")


@pytest.fixture
def fetcher():
    return _TestFetcher()


def _ok_response(body: bytes = b"file data", content_type: str = "application/octet-stream") -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 200
    resp.headers = requests.structures.CaseInsensitiveDict({"Content-Type": content_type})
    resp.content = body
    resp.text = body.decode("utf-8", errors="replace")
    resp.iter_content = lambda chunk_size=65536: iter([body])
    resp.raise_for_status.return_value = None
    return resp


def _429_response(retry_after: str | None = None) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 429
    headers = {}
    if retry_after:
        headers["Retry-After"] = retry_after
    resp.headers = requests.structures.CaseInsensitiveDict(headers)
    resp.raise_for_status.side_effect = requests.HTTPError("429", response=resp)
    return resp


# ── _get_with_retry ───────────────────────────────────────────────────────────

def test_get_with_retry_returns_on_200(fetcher):
    sess = MagicMock(spec=requests.Session)
    sess.get.return_value = _ok_response()
    with patch("scraper.national_datasets.time.sleep"):
        resp = fetcher._get_with_retry(sess, "https://example.com/file.xlsx", timeout=30)
    assert resp.status_code == 200


def test_get_with_retry_429_retries_with_sleep(fetcher):
    sess = MagicMock(spec=requests.Session)
    sess.get.side_effect = [_429_response(retry_after="3"), _ok_response()]
    with patch("scraper.national_datasets.time.sleep") as mock_sleep:
        resp = fetcher._get_with_retry(sess, "https://example.com/file.xlsx", timeout=30)
    assert resp.status_code == 200
    mock_sleep.assert_any_call(3)


def test_get_with_retry_raises_after_max_retries_exhausted(fetcher):
    sess = MagicMock(spec=requests.Session)
    sess.get.return_value = _429_response()
    with patch("scraper.national_datasets.time.sleep"):
        with pytest.raises(requests.HTTPError):
            fetcher._get_with_retry(sess, "https://example.com/file.xlsx", timeout=30, max_retries=3)
    assert sess.get.call_count == 4  # attempt 0,1,2,3


def test_get_with_retry_raises_on_persistent_connection_error(fetcher):
    sess = MagicMock(spec=requests.Session)
    sess.get.side_effect = requests.ConnectionError("unreachable")
    with patch("scraper.national_datasets.time.sleep"):
        with pytest.raises(requests.ConnectionError):
            fetcher._get_with_retry(sess, "https://example.com/file.xlsx", timeout=30, max_retries=2)


def test_get_with_retry_raises_on_4xx(fetcher):
    bad = MagicMock(spec=requests.Response)
    bad.status_code = 404
    bad.headers = requests.structures.CaseInsensitiveDict({})
    bad.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
    sess = MagicMock(spec=requests.Session)
    sess.get.return_value = bad
    with patch("scraper.national_datasets.time.sleep"):
        with pytest.raises(requests.HTTPError):
            fetcher._get_with_retry(sess, "https://example.com/missing", timeout=30, max_retries=0)


# ── _find_file_link ───────────────────────────────────────────────────────────

def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def test_find_file_link_finds_xlsx(fetcher):
    soup = _soup('<a href="https://data.nhs.uk/report.xlsx">Download</a>')
    url, name = fetcher._find_file_link(soup, "https://data.nhs.uk/", (".xlsx",))
    assert url == "https://data.nhs.uk/report.xlsx"
    assert name == "report.xlsx"


def test_find_file_link_finds_csv(fetcher):
    soup = _soup('<a href="/files/data.csv">Data</a>')
    url, name = fetcher._find_file_link(soup, "https://data.nhs.uk/", (".csv",))
    assert url == "https://data.nhs.uk/files/data.csv"
    assert name == "data.csv"


def test_find_file_link_returns_none_when_no_match(fetcher):
    soup = _soup('<a href="/page.html">Home</a><a href="/about">About</a>')
    url, name = fetcher._find_file_link(soup, "https://data.nhs.uk/", (".xlsx", ".csv"))
    assert url is None
    assert name is None


def test_find_file_link_prefers_text_match(fetcher):
    soup = _soup(
        '<a href="/old.xlsx">Archive Report 2024</a>'
        '<a href="/latest.xlsx">Latest Report 2026</a>'
    )
    url, name = fetcher._find_file_link(
        soup, "https://data.nhs.uk/", (".xlsx",), prefer_text="Latest"
    )
    assert "latest.xlsx" in url


def test_find_file_link_falls_back_to_first_when_no_prefer_match(fetcher):
    soup = _soup(
        '<a href="/first.xlsx">First File</a>'
        '<a href="/second.xlsx">Second File</a>'
    )
    url, name = fetcher._find_file_link(
        soup, "https://data.nhs.uk/", (".xlsx",), prefer_text="nonexistent text"
    )
    assert "first.xlsx" in url


def test_find_file_link_ignores_query_string_when_extension_is_in_path(fetcher):
    """Extension matching uses the URL path only; query strings after ? are stripped."""
    # /data.xlsx?v=2026 → path is /data.xlsx → extension .xlsx matched correctly
    soup = _soup('<a href="/files/data.xlsx?version=2026&token=abc">Download</a>')
    url, name = fetcher._find_file_link(soup, "https://data.nhs.uk/", (".xlsx",))
    assert url is not None
    assert url.startswith("https://data.nhs.uk/files/data.xlsx")
    assert name == "data.xlsx"


def test_find_file_link_multiple_extensions(fetcher):
    soup = _soup('<a href="/data.ods">ODS File</a>')
    url, name = fetcher._find_file_link(soup, "https://data.nhs.uk/", (".xlsx", ".ods", ".csv"))
    assert url is not None
    assert name == "data.ods"


# ── _download ─────────────────────────────────────────────────────────────────

def test_download_skips_if_file_already_exists(fetcher, tmp_path):
    dest = tmp_path / "test"
    dest.mkdir()
    existing = dest / "report.xlsx"
    existing.write_bytes(b"data")
    sess = MagicMock(spec=requests.Session)

    result = fetcher._download(sess, "https://example.com/report.xlsx", tmp_path, "report.xlsx", "v1")
    assert result.skipped is True
    sess.get.assert_not_called()


def test_download_saves_file_on_success(fetcher, tmp_path):
    sess = MagicMock(spec=requests.Session)
    sess.get.return_value = _ok_response(body=b"xlsx content")
    with patch("scraper.national_datasets.time.sleep"):
        result = fetcher._download(sess, "https://example.com/report.xlsx", tmp_path, "report.xlsx", "v1")
    assert result.error is None
    assert result.skipped is False
    assert result.file_path is not None
    assert result.file_path.exists()
    assert result.file_path.read_bytes() == b"xlsx content"


def test_download_rejects_html_response_for_non_html_file(fetcher, tmp_path):
    html_body = b"<html><body>Login required</body></html>"
    resp = _ok_response(body=html_body, content_type="text/html; charset=utf-8")
    sess = MagicMock(spec=requests.Session)
    sess.get.return_value = resp
    with patch("scraper.national_datasets.time.sleep"):
        result = fetcher._download(sess, "https://example.com/report.xlsx", tmp_path, "report.xlsx", "v1")
    assert result.error is not None
    assert "HTML" in result.error or "html" in result.error.lower()


def test_download_rejects_empty_file(fetcher, tmp_path):
    resp = _ok_response(body=b"")
    sess = MagicMock(spec=requests.Session)
    sess.get.return_value = resp
    with patch("scraper.national_datasets.time.sleep"):
        result = fetcher._download(sess, "https://example.com/empty.xlsx", tmp_path, "empty.xlsx", "v1")
    assert result.error is not None
    assert "empty" in result.error.lower()
    # Partial file should be cleaned up
    dest_file = tmp_path / "test" / "empty.xlsx"
    assert not dest_file.exists()
