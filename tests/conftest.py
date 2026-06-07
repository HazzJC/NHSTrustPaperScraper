"""Shared fixtures for the NHS Evidence Scraper test suite."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests


@pytest.fixture
def tmp_cache_file(tmp_path: Path) -> Path:
    """Return a path inside tmp_path for FailureCache; parent dir is guaranteed to exist."""
    cache_dir = tmp_path / "data"
    cache_dir.mkdir()
    return cache_dir / "failure_cache.json"


def make_response(
    status: int = 200,
    body: str | bytes = b"",
    headers: dict | None = None,
    url: str = "https://example.nhs.uk/doc.pdf",
) -> requests.Response:
    """Build a mock requests.Response."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.url = url
    resp.headers = requests.structures.CaseInsensitiveDict(headers or {})
    if isinstance(body, str):
        body = body.encode()
    resp.content = body
    resp.text = body.decode("utf-8", errors="replace")
    resp.iter_content = lambda chunk_size=65536: iter([body])
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(
            f"{status} Error", response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


@pytest.fixture
def flask_client():
    """Flask test client for app.py integration tests."""
    import sys
    import os
    # Ensure the project root is on sys.path so `import app` works
    project_root = Path(__file__).parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    # Set a test secret key before importing
    os.environ.setdefault("SECRET_KEY", "test-secret-key")

    import app as flask_app
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as client:
        yield client
