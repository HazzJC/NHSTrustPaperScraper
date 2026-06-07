"""Tests for scraper/failure_cache.py — FailureCache."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scraper.failure_cache import FailureCache


def _cache(tmp_path: Path) -> FailureCache:
    return FailureCache(path=tmp_path / "data" / "failure_cache.json")


# ── mark_failed ───────────────────────────────────────────────────────────────

def test_mark_failed_creates_entry(tmp_path):
    fc = _cache(tmp_path)
    fc.mark_failed("Trust A")
    assert "Trust A" in fc.get_failed()


def test_mark_failed_increments_consecutive(tmp_path):
    fc = _cache(tmp_path)
    fc.mark_failed("Trust A")
    fc.mark_failed("Trust A")
    entries = {e["name"]: e for e in fc.get_all()}
    assert entries["Trust A"]["consecutive"] == 2


def test_mark_failed_stores_reason(tmp_path):
    fc = _cache(tmp_path)
    fc.mark_failed("Trust B", reason="error")
    entries = {e["name"]: e for e in fc.get_all()}
    assert entries["Trust B"]["reason"] == "error"


def test_mark_failed_default_reason_is_no_results(tmp_path):
    fc = _cache(tmp_path)
    fc.mark_failed("Trust C")
    entries = {e["name"]: e for e in fc.get_all()}
    assert entries["Trust C"]["reason"] == "no_results"


# ── mark_succeeded ────────────────────────────────────────────────────────────

def test_mark_succeeded_removes_entry(tmp_path):
    fc = _cache(tmp_path)
    fc.mark_failed("Trust A")
    fc.mark_succeeded("Trust A")
    assert "Trust A" not in fc.get_failed()


def test_mark_succeeded_on_missing_entry_is_safe(tmp_path):
    fc = _cache(tmp_path)
    fc.mark_succeeded("Never Failed Trust")  # should not raise
    assert fc.get_all() == []


# ── get_failed ────────────────────────────────────────────────────────────────

def test_get_failed_returns_set_of_names(tmp_path):
    fc = _cache(tmp_path)
    fc.mark_failed("Trust X")
    fc.mark_failed("Trust Y")
    failed = fc.get_failed()
    assert failed == {"Trust X", "Trust Y"}


def test_get_failed_excludes_succeeded(tmp_path):
    fc = _cache(tmp_path)
    fc.mark_failed("Trust X")
    fc.mark_failed("Trust Y")
    fc.mark_succeeded("Trust X")
    assert fc.get_failed() == {"Trust Y"}


# ── get_all ───────────────────────────────────────────────────────────────────

def test_get_all_empty(tmp_path):
    fc = _cache(tmp_path)
    assert fc.get_all() == []


def test_get_all_contains_required_fields(tmp_path):
    fc = _cache(tmp_path)
    fc.mark_failed("Trust A")
    entries = fc.get_all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["name"] == "Trust A"
    assert "consecutive" in entry
    assert "reason" in entry
    assert "failed_at" in entry


# ── remove ────────────────────────────────────────────────────────────────────

def test_remove_existing_returns_true(tmp_path):
    fc = _cache(tmp_path)
    fc.mark_failed("Trust A")
    assert fc.remove("Trust A") is True


def test_remove_existing_deletes_entry(tmp_path):
    fc = _cache(tmp_path)
    fc.mark_failed("Trust A")
    fc.remove("Trust A")
    assert "Trust A" not in fc.get_failed()


def test_remove_missing_returns_false(tmp_path):
    fc = _cache(tmp_path)
    assert fc.remove("Nonexistent Trust") is False


# ── clear_all ────────────────────────────────────────────────────────────────

def test_clear_all_returns_count(tmp_path):
    fc = _cache(tmp_path)
    fc.mark_failed("Trust A")
    fc.mark_failed("Trust B")
    assert fc.clear_all() == 2


def test_clear_all_empties_cache(tmp_path):
    fc = _cache(tmp_path)
    fc.mark_failed("Trust A")
    fc.mark_failed("Trust B")
    fc.clear_all()
    assert fc.get_all() == []


def test_clear_all_on_empty_returns_zero(tmp_path):
    fc = _cache(tmp_path)
    assert fc.clear_all() == 0


# ── persistence ──────────────────────────────────────────────────────────────

def test_data_persists_across_instances(tmp_path):
    path = tmp_path / "data" / "fc.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    fc1 = FailureCache(path=path)
    fc1.mark_failed("Trust A")
    fc1.mark_failed("Trust B")

    fc2 = FailureCache(path=path)
    assert fc2.get_failed() == {"Trust A", "Trust B"}


def test_consecutive_persists_across_instances(tmp_path):
    path = tmp_path / "data" / "fc.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    fc1 = FailureCache(path=path)
    fc1.mark_failed("Trust A")
    fc1.mark_failed("Trust A")

    fc2 = FailureCache(path=path)
    entries = {e["name"]: e for e in fc2.get_all()}
    assert entries["Trust A"]["consecutive"] == 2


def test_corrupted_file_loads_empty(tmp_path):
    path = tmp_path / "data" / "fc.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not valid json", encoding="utf-8")

    fc = FailureCache(path=path)
    assert fc.get_all() == []
