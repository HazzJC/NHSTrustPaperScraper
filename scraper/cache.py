"""Discovery page cache.

Stores the HTML pages where document links were previously found, keyed by
trust name and report type. On the next run those pages are queued at the
highest priority (score 200) so the scraper jumps straight to them instead
of re-crawling from the homepage.

The cache is updated after every trust run, even dry runs — we care about
discovering the page, not downloading the file.

Format of data/discovery_cache.json:
{
  "Birmingham and Solihull Mental Health NHS Foundation Trust": {
    "board": [
      "https://www.bsmhft.nhs.uk/about-us/corporate-documents/trust-board-papers/",
      "https://www.bsmhft.nhs.uk/about-us/news/document-type/trust-board-papers/"
    ]
  },
  ...
}
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from scraper.models import Candidate

_DEFAULT_PATH = Path("data/discovery_cache.json")
_MAX_PAGES_PER_TYPE = 5  # cap stored URLs per trust/type to avoid unbounded growth


class DiscoveryCache:
    def __init__(self, path: Path | None = None):
        self._path = path or _DEFAULT_PATH
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, list[str]]] = {}
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_pages(self, trust_name: str, report_types: set[str]) -> list[str]:
        """Return cached source pages for this trust across all requested types.

        Ordered so the pages that appeared in more types come first (most
        useful listing pages tend to contain multiple report types).
        """
        with self._lock:
            trust_entry = self._data.get(trust_name, {})
            seen: dict[str, int] = {}  # url -> hit count
            for rt in report_types:
                for url in trust_entry.get(rt, []):
                    seen[url] = seen.get(url, 0) + 1
            # Sort by frequency descending so multi-type listing pages are first
            return sorted(seen, key=seen.get, reverse=True)  # type: ignore[arg-type]

    def update(self, trust_name: str, candidates: list[Candidate]) -> None:
        """Add any new source pages found during this run and persist to disk."""
        if not candidates:
            return
        with self._lock:
            trust_entry = self._data.setdefault(trust_name, {})
            changed = False
            for candidate in candidates:
                rt = candidate.report_type
                src = candidate.source_page
                if not rt or not src:
                    continue
                pages = trust_entry.setdefault(rt, [])
                if src not in pages:
                    pages.insert(0, src)  # newest first
                    if len(pages) > _MAX_PAGES_PER_TYPE:
                        pages.pop()
                    changed = True
            if changed:
                self._save()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

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
