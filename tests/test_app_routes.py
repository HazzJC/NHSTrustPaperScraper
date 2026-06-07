"""Flask route integration tests for app.py.

These tests use the Flask test client — no real HTTP requests or scraping occur.
Heavy scrape jobs are not triggered; tests only verify routing, validation,
and response shapes.

If the app cannot be imported (e.g. missing optional dependencies in CI),
all tests in this file are automatically skipped.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Skip entire module if app can't be imported (missing heavy deps) ──────────

try:
    _project_root = Path(__file__).parent.parent
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
    import app as _app_module  # noqa: F401
    _CAN_IMPORT = True
except Exception as _import_err:
    _CAN_IMPORT = False
    _import_err_msg = str(_import_err)

pytestmark = pytest.mark.skipif(
    not _CAN_IMPORT,
    reason=f"app.py could not be imported: {_import_err_msg if not _CAN_IMPORT else ''}",
)


@pytest.fixture(scope="module")
def client():
    import app as flask_app
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as c:
        yield c


# ── Index ─────────────────────────────────────────────────────────────────────

def test_index_returns_200(client):
    resp = client.get("/")
    assert resp.status_code == 200


# ── Trust / ICB lists ─────────────────────────────────────────────────────────

def test_scrape_trusts_returns_list(client):
    resp = client.get("/scrape/trusts")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert "name" in data[0]


def test_scrape_icbs_returns_list(client):
    resp = client.get("/scrape/icbs")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert "name" in data[0]


# ── /scrape/start validation ──────────────────────────────────────────────────

def test_scrape_start_unknown_type_returns_400(client):
    resp = client.post(
        "/scrape/start",
        data=json.dumps({"types": ["faketype"], "source": "trust"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_scrape_start_bad_source_returns_400(client):
    resp = client.post(
        "/scrape/start",
        data=json.dumps({"types": ["board"], "source": "unknown"}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_scrape_start_valid_trust_payload_returns_job_id(client):
    import app as flask_app
    from scraper.engine import ScrapeJob

    mock_job = MagicMock(spec=ScrapeJob)
    mock_job.job_id = "mock-job-id"
    mock_job.status = "running"
    mock_job.total_trusts = 5

    with patch.object(flask_app.scraper_engine, "start_job", return_value=mock_job):
        resp = client.post(
            "/scrape/start",
            data=json.dumps({"types": ["board"], "source": "trust", "trust_names": ["Example Trust"]}),
            content_type="application/json",
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "job_id" in data
    assert data["job_id"] == "mock-job-id"


def test_scrape_start_valid_icb_payload_returns_job_id(client):
    import app as flask_app
    from scraper.engine import ScrapeJob

    mock_job = MagicMock(spec=ScrapeJob)
    mock_job.job_id = "mock-icb-job"
    mock_job.status = "running"
    mock_job.total_trusts = 3

    with patch.object(flask_app.scraper_engine, "start_job", return_value=mock_job):
        resp = client.post(
            "/scrape/start",
            data=json.dumps({"types": ["joint_forward_plan"], "source": "icb"}),
            content_type="application/json",
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["job_id"] == "mock-icb-job"


# ── /scrape/status ────────────────────────────────────────────────────────────

def test_scrape_status_not_found_returns_404(client):
    resp = client.get("/scrape/status/nonexistent-job-id")
    assert resp.status_code == 404


# ── /scrape/failures ──────────────────────────────────────────────────────────

def test_failures_endpoint_returns_list(client):
    resp = client.get("/scrape/failures")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)


def test_failures_clear_all(client):
    resp = client.delete("/scrape/failures/clear-all")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("success") is True


def test_failures_clear_nonexistent_org(client):
    resp = client.delete("/scrape/failures/Nonexistent%20Trust%20That%20Does%20Not%20Exist")
    # Should return 404 since the org isn't in the cache
    assert resp.status_code == 404


# ── /national/sources ────────────────────────────────────────────────────────

def test_national_sources_returns_list(client):
    resp = client.get("/national/sources")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) > 0
    first = data[0]
    assert "key" in first
    assert "display_name" in first


def test_national_sources_has_eight_datasets(client):
    resp = client.get("/national/sources")
    data = resp.get_json()
    assert len(data) == 8


# ── /config/org ───────────────────────────────────────────────────────────────

def test_config_org_get_missing_name_returns_400(client):
    resp = client.get("/config/org?source=trust")
    assert resp.status_code == 400


def test_config_org_get_nonexistent_trust_returns_404(client):
    resp = client.get("/config/org?name=Nonexistent+Trust+XYZ&source=trust")
    assert resp.status_code == 404


def test_config_org_get_existing_trust_returns_200(client):
    import app as flask_app
    # Get the first trust name from the engine
    trusts = flask_app.scraper_engine.list_trusts()
    if not trusts:
        pytest.skip("No trusts loaded")
    first_name = trusts[0]["name"]
    resp = client.get(f"/config/org?name={first_name}&source=trust")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["name"] == first_name
    assert "url" in data
    assert "start_urls" in data


def test_config_org_get_existing_icb_returns_200(client):
    import app as flask_app
    icbs = flask_app.scraper_engine.list_icbs()
    if not icbs:
        pytest.skip("No ICBs loaded")
    first_name = icbs[0]["name"]
    resp = client.get(f"/config/org?name={first_name}&source=icb")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["name"] == first_name
