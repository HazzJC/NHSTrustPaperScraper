from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Trust:
    name: str
    url: str | None = None
    start_urls: tuple[str, ...] = ()
    allowed_domains: tuple[str, ...] = ()
    search_query: str | None = None
    js_render: bool = False


@dataclass
class Candidate:
    url: str
    source_page: str
    link_text: str
    date: dt.date | None
    date_source: str
    report_type: str
    title: str
    extension: str
    score: int


@dataclass
class DownloadResult:
    trust_name: str
    candidate: Candidate
    file_path: str | None
    success: bool
    error: str | None = None

    def to_dict(self) -> dict:
        c = self.candidate
        return {
            "trust": self.trust_name,
            "url": c.url,
            "title": c.title,
            "date": c.date.isoformat() if c.date else None,
            "report_type": c.report_type,
            "file_path": self.file_path,
            "success": self.success,
            "error": self.error,
        }
