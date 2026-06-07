"""Tests for scraper/engine.py — parse_types, _compute_cutoffs, load_trusts, ScrapeJob."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from scraper.constants import REPORT_TYPES
from scraper.engine import ScrapeJob, _compute_cutoffs, load_trusts, parse_types


# ── parse_types ───────────────────────────────────────────────────────────────

def test_parse_types_single():
    assert parse_types("board") == {"board"}


def test_parse_types_comma_separated():
    result = parse_types("board,annual_report")
    assert result == {"board", "annual_report"}


def test_parse_types_strips_whitespace():
    assert parse_types(" board , annual_report ") == {"board", "annual_report"}


def test_parse_types_all_returns_every_report_type():
    result = parse_types("all")
    assert result == set(REPORT_TYPES.keys())


def test_parse_types_all_case_insensitive():
    assert parse_types("ALL") == set(REPORT_TYPES.keys())


def test_parse_types_unknown_raises_value_error():
    with pytest.raises(ValueError, match="Unknown report type"):
        parse_types("faketype")


def test_parse_types_include_supplementary_flag():
    result = parse_types("board", include_supplementary=True)
    assert "supplementary" in result


def test_parse_types_include_strategy_flag():
    result = parse_types("board", include_strategy=True)
    assert "strategy" in result
    assert "digital_strategy" in result


# ── _compute_cutoffs ──────────────────────────────────────────────────────────

def test_compute_cutoffs_zero_months_excluded():
    result = _compute_cutoffs({"board": 0, "annual_report": 12})
    assert "board" not in result
    assert "annual_report" in result


def test_compute_cutoffs_nonzero_returns_past_date():
    result = _compute_cutoffs({"board": 12})
    today = dt.date.today()
    cutoff = result["board"]
    # cutoff should be roughly a year ago (within a day or two)
    assert (today - cutoff).days >= 355
    assert (today - cutoff).days <= 375


def test_compute_cutoffs_empty_input():
    assert _compute_cutoffs({}) == {}


def test_compute_cutoffs_all_zero():
    filters = {k: 0 for k in REPORT_TYPES}
    assert _compute_cutoffs(filters) == {}


# ── load_trusts ───────────────────────────────────────────────────────────────

def test_load_trusts_valid_file(tmp_path):
    config = [
        {"name": "Example Trust", "url": "https://example.nhs.uk", "start_urls": []},
        {"name": "Another Trust", "url": "https://another.nhs.uk", "start_urls": ["https://another.nhs.uk/docs/"]},
    ]
    p = tmp_path / "trusts.json"
    p.write_text(json.dumps(config), encoding="utf-8")

    trusts = load_trusts(p)
    assert len(trusts) == 2
    assert trusts[0].name == "Example Trust"
    assert trusts[1].start_urls == ("https://another.nhs.uk/docs/",)


def test_load_trusts_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_trusts(tmp_path / "nonexistent.json")


def test_load_trusts_missing_name_raises(tmp_path):
    config = [{"url": "https://example.nhs.uk"}]  # no "name" key
    p = tmp_path / "trusts.json"
    p.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError):
        load_trusts(p)


def test_load_trusts_optional_fields_have_defaults(tmp_path):
    config = [{"name": "Minimal Trust"}]
    p = tmp_path / "trusts.json"
    p.write_text(json.dumps(config), encoding="utf-8")
    trusts = load_trusts(p)
    assert trusts[0].url is None
    assert trusts[0].start_urls == ()
    assert trusts[0].allowed_domains == ()
    assert trusts[0].js_render is False


def test_load_trusts_js_render_flag(tmp_path):
    config = [{"name": "JS Trust", "url": "https://jstrust.nhs.uk", "js_render": True}]
    p = tmp_path / "trusts.json"
    p.write_text(json.dumps(config), encoding="utf-8")
    trusts = load_trusts(p)
    assert trusts[0].js_render is True


# ── ScrapeJob.summary_table ───────────────────────────────────────────────────

def _job_with_results(results: list[dict]) -> ScrapeJob:
    job = ScrapeJob(
        job_id="test-job",
        trust_names=[],
        selected_types={"board", "annual_report"},
    )
    job.results = results
    return job


def test_summary_table_empty_results():
    job = _job_with_results([])
    assert job.summary_table() == []


def test_summary_table_single_trust_single_type():
    job = _job_with_results([
        {"trust": "Trust A", "trust_name": "Trust A", "report_type": "board", "date": "2026-06-01"},
    ])
    rows = job.summary_table()
    assert len(rows) == 1
    assert rows[0]["trust"] == "Trust A"
    assert rows[0]["board"] == ["2026-06-01"]


def test_summary_table_groups_multiple_docs_for_same_type():
    job = _job_with_results([
        {"trust": "Trust A", "trust_name": "Trust A", "report_type": "board", "date": "2026-06-01"},
        {"trust": "Trust A", "trust_name": "Trust A", "report_type": "board", "date": "2026-05-01"},
    ])
    rows = job.summary_table()
    assert len(rows) == 1
    assert len(rows[0]["board"]) == 2


def test_summary_table_multiple_trusts_separate_rows():
    job = _job_with_results([
        {"trust": "Trust A", "trust_name": "Trust A", "report_type": "board", "date": "2026-06-01"},
        {"trust": "Trust B", "trust_name": "Trust B", "report_type": "annual_report", "date": "2026-03-01"},
    ])
    rows = job.summary_table()
    names = [r["trust"] for r in rows]
    assert "Trust A" in names
    assert "Trust B" in names
    row_b = next(r for r in rows if r["trust"] == "Trust B")
    assert row_b["annual_report"] == ["2026-03-01"]
    assert row_b["board"] is None  # Trust B had no board papers


def test_summary_table_rows_sorted_alphabetically():
    job = _job_with_results([
        {"trust": "Zeppelin Trust", "trust_name": "Zeppelin Trust", "report_type": "board", "date": "2026-06-01"},
        {"trust": "Alpha Trust", "trust_name": "Alpha Trust", "report_type": "board", "date": "2026-06-01"},
    ])
    rows = job.summary_table()
    assert rows[0]["trust"] == "Alpha Trust"
    assert rows[1]["trust"] == "Zeppelin Trust"


def test_summary_table_ignores_missing_date():
    job = _job_with_results([
        {"trust": "Trust A", "trust_name": "Trust A", "report_type": "board", "date": None},
    ])
    rows = job.summary_table()
    # Entry with no date should not populate the type column
    assert rows[0]["board"] is None


def test_summary_table_all_types_initialised_to_none():
    job = _job_with_results([
        {"trust": "Trust A", "trust_name": "Trust A", "report_type": "board", "date": "2026-06-01"},
    ])
    rows = job.summary_table()
    for key in REPORT_TYPES:
        if key != "board":
            assert rows[0][key] is None
