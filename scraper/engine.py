from __future__ import annotations

import datetime as dt
import json
import queue
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from scraper.cache import DiscoveryCache
from scraper.constants import REPORT_TYPES
from scraper.discovery import (
    apply_last_modified_dates,
    discover_candidates,
    selected_candidates,
)
from scraper.downloader import download_candidate, slugify
from scraper.models import Candidate, DownloadResult, Trust
from scraper.session import build_session


def load_trusts(path: Path) -> list[Trust]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    trusts: list[Trust] = []
    for entry in raw:
        name = entry.get("name")
        if not name:
            raise ValueError(f"Invalid trust entry in {path}: {entry!r}")
        trusts.append(
            Trust(
                name=name,
                url=entry.get("url") or None,
                start_urls=tuple(entry.get("start_urls", [])),
                allowed_domains=tuple(entry.get("allowed_domains", [])),
                search_query=entry.get("search_query"),
                js_render=bool(entry.get("js_render", False)),
            )
        )
    return trusts


def parse_types(
    raw_types: str,
    *,
    include_supplementary: bool = False,
    include_strategy: bool = False,
) -> set[str]:
    if raw_types.lower() == "all":
        selected = set(REPORT_TYPES)
    else:
        selected = {item.strip() for item in raw_types.split(",") if item.strip()}
    if include_supplementary:
        selected.add("supplementary")
    if include_strategy:
        selected.update({"strategy", "digital_strategy"})
    unknown = selected - set(REPORT_TYPES)
    if unknown:
        raise ValueError(f"Unknown report type(s): {', '.join(sorted(unknown))}")
    return selected


@dataclass
class ScrapeJob:
    job_id: str
    trust_names: list[str]
    selected_types: set[str]
    status: str = "pending"
    started_at: dt.datetime = field(default_factory=dt.datetime.now)
    finished_at: dt.datetime | None = None
    results: list[dict] = field(default_factory=list)
    log_queue: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=1000))
    total_trusts: int = 0
    completed_trusts: int = 0

    def log(self, event: str, **kwargs) -> None:
        try:
            self.log_queue.put_nowait({"event": event, **kwargs})
        except queue.Full:
            pass

    def to_status_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "trust_count": self.total_trusts,
            "completed": self.completed_trusts,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "results": self.results,
        }


class ScraperEngine:
    def __init__(
        self,
        trusts_path: Path = Path("config/mental_health_trusts.json"),
        output_dir: Path = Path("downloads"),
        max_pages: int = 60,
        timeout: int = 30,
        crawl_delay: float = 0.5,
        verify_ssl: bool = False,
    ) -> None:
        self._trusts_path = trusts_path
        self._output_dir = output_dir
        self._max_pages = max_pages
        self._timeout = timeout
        self._crawl_delay = crawl_delay
        self._verify_ssl = verify_ssl
        self._trusts: list[Trust] = load_trusts(trusts_path)
        self._cache = DiscoveryCache()
        self._jobs: dict[str, ScrapeJob] = {}
        self._lock = threading.Lock()

    def list_trusts(self) -> list[dict]:
        return [{"name": t.name, "url": t.url, "js_render": t.js_render} for t in self._trusts]

    def start_job(
        self,
        trust_names: list[str] | None,
        selected_types: set[str],
        all_matches: bool = False,
        limit_per_type: int = 1,
        dry_run: bool = False,
    ) -> ScrapeJob:
        if trust_names is None:
            trusts = self._trusts
        else:
            name_set = {n.lower() for n in trust_names}
            trusts = [t for t in self._trusts if t.name.lower() in name_set]

        job = ScrapeJob(
            job_id=uuid.uuid4().hex,
            trust_names=[t.name for t in trusts],
            selected_types=selected_types,
            total_trusts=len(trusts),
        )

        stop_event = threading.Event()
        with self._lock:
            self._jobs[job.job_id] = job

        thread = threading.Thread(
            target=self._run_job,
            args=(job, trusts, selected_types, all_matches, limit_per_type, dry_run, stop_event),
            daemon=True,
        )
        thread.start()
        job.status = "running"
        return job

    def get_job(self, job_id: str) -> ScrapeJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel_job(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job:
            return False
        job.status = "cancelled"
        job.log("cancelled", message="Job cancelled by user")
        return True

    def _run_job(
        self,
        job: ScrapeJob,
        trusts: list[Trust],
        selected_types: set[str],
        all_matches: bool,
        limit_per_type: int,
        dry_run: bool,
        stop_event: threading.Event,
    ) -> None:
        session = build_session(verify_ssl=self._verify_ssl)

        for index, trust in enumerate(trusts, start=1):
            if stop_event.is_set() or job.status == "cancelled":
                break

            job.log("trust_start", trust=trust.name, index=index, total=len(trusts))
            print(f"\n[{index}/{len(trusts)}] {trust.name}")

            try:
                if trust.js_render:
                    candidates = self._js_fallback(trust, selected_types)
                else:
                    cached = self._cache.get_pages(trust.name, selected_types)
                    if cached:
                        print(f"  Using {len(cached)} cached source page(s) from previous run")
                    candidates = discover_candidates(
                        session,
                        trust,
                        max_pages=self._max_pages,
                        timeout=self._timeout,
                        crawl_delay=self._crawl_delay,
                        selected_types=selected_types,
                        stop_event=stop_event,
                        cached_pages=cached or None,
                    )

                self._cache.update(trust.name, candidates)
                apply_last_modified_dates(session, candidates, timeout=self._timeout)
                chosen = selected_candidates(
                    candidates, all_matches=all_matches, limit_per_type=limit_per_type
                )

                if not chosen:
                    job.log("trust_done", trust=trust.name, downloaded=0, found=0,
                            message="No matching documents found")
                    print("  No matching documents found.")
                    job.completed_trusts += 1
                    continue

                downloaded = 0
                for candidate in chosen:
                    date_text = candidate.date.isoformat() if candidate.date else "unknown date"
                    print(f"  Selected [{candidate.report_type}] {date_text} - {candidate.url}")
                    job.log("candidate_found", trust=trust.name,
                            report_type=candidate.report_type,
                            date=date_text, url=candidate.url)

                    path = download_candidate(
                        session,
                        trust,
                        candidate,
                        output_dir=self._output_dir,
                        timeout=self._timeout,
                        dry_run=dry_run,
                    )
                    result = DownloadResult(
                        trust_name=trust.name,
                        candidate=candidate,
                        file_path=str(path) if path else None,
                        success=True,
                    )
                    job.results.append(result.to_dict())
                    if path:
                        downloaded += 1
                        print(f"  Downloaded: {path}")

                job.log("trust_done", trust=trust.name, found=len(chosen),
                        downloaded=downloaded)

            except Exception as exc:
                print(f"  Failed: {exc}")
                job.log("trust_error", trust=trust.name, error=str(exc))

            job.completed_trusts += 1

        job.finished_at = dt.datetime.now()
        if job.status not in ("cancelled",):
            job.status = "done"
        job.log("done",
                total=job.total_trusts,
                completed=job.completed_trusts,
                downloads=sum(1 for r in job.results if r.get("file_path")))

    def _js_fallback(self, trust: Trust, selected_types: set[str]) -> list[Candidate]:
        try:
            from scraper.js_fallback import JSFallbackFetcher
            fetcher = JSFallbackFetcher()
            return fetcher.fetch(trust, selected_types=selected_types)
        except Exception as exc:
            print(f"  JS fallback failed for {trust.name}: {exc}")
            return []
