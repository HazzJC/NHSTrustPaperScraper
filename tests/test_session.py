"""Tests for scraper/session.py — request_page() retry and backoff logic."""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest
import requests

from scraper.session import request_page


HTML_BODY = "<html><body>Hello NHS</body></html>"


def _session(side_effects) -> requests.Session:
    """Build a mock session whose .get() returns the given side_effects in order."""
    sess = MagicMock(spec=requests.Session)
    sess.get.side_effect = side_effects
    return sess


def _ok_response(body: str = HTML_BODY, url: str = "https://nhs.example/page") -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 200
    resp.url = url
    resp.text = body
    resp.headers = requests.structures.CaseInsensitiveDict({"Content-Type": "text/html; charset=utf-8"})
    resp.raise_for_status.return_value = None
    return resp


def _status_response(status: int, headers: dict | None = None) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.url = "https://nhs.example/page"
    resp.text = ""
    resp.headers = requests.structures.CaseInsensitiveDict(headers or {})
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(
            f"HTTP {status}", response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


# ── happy path ────────────────────────────────────────────────────────────────

def test_returns_text_and_url_on_200():
    sess = _session([_ok_response()])
    with patch("scraper.session.time.sleep"):
        result = request_page(sess, "https://nhs.example/page", timeout=10)
    assert result is not None
    text, url = result
    assert "Hello NHS" in text
    assert url == "https://nhs.example/page"


def test_returns_none_on_non_html_content_type():
    resp = _ok_response(body="binary data")
    resp.headers = requests.structures.CaseInsensitiveDict({"Content-Type": "application/pdf"})
    resp.text = "binary data"
    sess = _session([resp])
    with patch("scraper.session.time.sleep"):
        result = request_page(sess, "https://nhs.example/doc.pdf", timeout=10)
    assert result is None


def test_returns_text_when_content_type_missing_but_has_html_tag():
    resp = _ok_response(body="<html><body>page</body></html>")
    resp.headers = requests.structures.CaseInsensitiveDict({})
    sess = _session([resp])
    with patch("scraper.session.time.sleep"):
        result = request_page(sess, "https://nhs.example/page", timeout=10)
    assert result is not None


# ── HTTP errors ───────────────────────────────────────────────────────────────

def test_returns_none_on_404():
    sess = _session([_status_response(404)])
    with patch("scraper.session.time.sleep"):
        result = request_page(sess, "https://nhs.example/missing", timeout=10, max_retries=0)
    assert result is None


def test_returns_none_on_500():
    sess = _session([_status_response(500)])
    with patch("scraper.session.time.sleep"):
        result = request_page(sess, "https://nhs.example/error", timeout=10, max_retries=0)
    assert result is None


# ── network errors with retry ─────────────────────────────────────────────────

def test_retries_on_connection_error_then_succeeds():
    sess = _session([requests.ConnectionError("timeout"), _ok_response()])
    with patch("scraper.session.time.sleep") as mock_sleep:
        result = request_page(sess, "https://nhs.example/page", timeout=10, max_retries=3)
    assert result is not None
    # exponential backoff sleep should have been called for the first failure
    mock_sleep.assert_any_call(1)  # 2^0


def test_exhausts_retries_on_persistent_connection_error():
    sess = _session([requests.ConnectionError("timeout")] * 4)
    with patch("scraper.session.time.sleep"):
        result = request_page(sess, "https://nhs.example/page", timeout=10, max_retries=3)
    assert result is None
    assert sess.get.call_count == 4  # 1 original + 3 retries


# ── 429 handling ──────────────────────────────────────────────────────────────

def test_429_waits_for_retry_after_header_then_succeeds():
    resp_429 = _status_response(429, headers={"Retry-After": "7"})
    sess = _session([resp_429, _ok_response()])
    with patch("scraper.session.time.sleep") as mock_sleep:
        result = request_page(sess, "https://nhs.example/page", timeout=10, max_retries=3)
    assert result is not None
    mock_sleep.assert_any_call(7)  # exact Retry-After value


def test_429_uses_default_wait_when_no_retry_after_header():
    resp_429 = _status_response(429)  # no Retry-After header
    sess = _session([resp_429, _ok_response()])
    with patch("scraper.session.time.sleep") as mock_sleep:
        result = request_page(sess, "https://nhs.example/page", timeout=10, max_retries=3)
    assert result is not None
    # default: min(60, 5 * 2^0) = 5
    mock_sleep.assert_any_call(5)


def test_429_exhausts_max_retries_returns_none():
    sess = _session([_status_response(429)] * 5)
    with patch("scraper.session.time.sleep"):
        result = request_page(sess, "https://nhs.example/page", timeout=10, max_retries=3)
    assert result is None
    # 4 attempts total (max_retries=3 means 0,1,2,3 → 4 get calls)
    assert sess.get.call_count == 4


# ── crawl delay ───────────────────────────────────────────────────────────────

def test_crawl_delay_sleeps_before_first_request():
    sess = _session([_ok_response()])
    with patch("scraper.session.time.sleep") as mock_sleep:
        request_page(sess, "https://nhs.example/page", timeout=10, crawl_delay=1.5)
    # First sleep call should be the crawl delay
    assert mock_sleep.call_args_list[0] == call(1.5)


def test_no_sleep_when_crawl_delay_is_zero():
    sess = _session([_ok_response()])
    with patch("scraper.session.time.sleep") as mock_sleep:
        request_page(sess, "https://nhs.example/page", timeout=10, crawl_delay=0.0)
    mock_sleep.assert_not_called()
