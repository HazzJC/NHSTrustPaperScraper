"""Failure cache — tracks trusts that produced 0 candidates on their last run.

On the next run, previously-failing trusts are given a fast-check pass using
only their known-good cached pages. If that passes finds candidates, great —
no time wasted re-crawling dead start_urls. If it finds nothing, a full crawl
is run as a fallback.

Format of data/failure_cache.json:
{
  "Trust Name": {
    "failed_at": "2026-06-05",
    "consecutive": 2,
    "reason": "no_results"
  },
  ...
}
"""
from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path

_DEFAULT_PATH = Path("data/failure_cache.json")


class FailureCache:
    def __init__(self, path: Path | None = None):
        self._path = path or _DEFAULT_PATH
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._load()

    def get_failed(self) -> set[str]:
        """Return the set of trust names that produced 0 results on their last run."""
        with self._lock:
            return set(self._data.keys())

    def mark_failed(self, trust_name: str, reason: str = "no_results") -> None:
        """Record that this trust produced 0 results or errored."""
        with self._lock:
            existing = self._data.get(trust_name, {})
            self._data[trust_name] = {
                "failed_at": dt.date.today().isoformat(),
                "consecutive": existing.get("consecutive", 0) + 1,
                "reason": reason,
            }
            self._save()

    def mark_succeeded(self, trust_name: str) -> None:
        """Remove this trust from the failure cache after a successful run."""
        with self._lock:
            if trust_name in self._data:
                del self._data[trust_name]
                self._save()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
