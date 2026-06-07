from __future__ import annotations

import concurrent.futures
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
from scraper.failure_cache import FailureCache
from scraper.discovery import (
    apply_last_modified_dates,
    discover_candidates,
    selected_candidates,
)
from scraper.downloader import download_candidate, slugify
from scraper.models import Candidate, DownloadResult, Trust
from scraper.session import build_session


def load_trusts(path: Path) -> list[Trust]:
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}. "
            "Check that the path is correct and the file has been created."
        )
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


def _compute_cutoffs(date_filters: dict[str, int]) -> dict[str, dt.date]:
    """Convert {type: months} to {type: cutoff_date}. Types with 0 months are omitted (no limit)."""
    today = dt.date.today()
    return {
        report_type: today - dt.timedelta(days=months * 30)
        for report_type, months in date_filters.items()
        if months > 0
    }


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
    date_filters: dict[str, int] = field(default_factory=dict)
    parallel_trusts: int = 5
    max_pages: int = 60
    crawl_delay: float = 0.5
    ignore_cache: bool = False
    verbose: bool = False
    failed_trusts: list[dict] = field(default_factory=list)
    stop_event: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def log(self, event: str, **kwargs) -> None:
        try:
            self.log_queue.put_nowait({"event": event, **kwargs})
        except queue.Full:
            pass

    def summary_table(self) -> list[dict]:
        """Return one row per trust with date found per report type (or None)."""
        from scraper.constants import REPORT_TYPES
        rows: dict[str, dict] = {}
        for r in self.results:
            trust = r.get("trust", "") or r.get("trust_name", "")
            rtype = r.get("report_type", "")
            date = r.get("date")
            if trust not in rows:
                rows[trust] = {"trust": trust, **{k: None for k in REPORT_TYPES}}
            if rtype in REPORT_TYPES and date:
                if rows[trust][rtype] is None:
                    rows[trust][rtype] = []
                rows[trust][rtype].append(date)
        return sorted(rows.values(), key=lambda x: x["trust"])

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
        icb_path: Path = Path("config/icb_config.json"),
        output_dir: Path = Path("downloads"),
        max_pages: int = 60,
        timeout: int = 30,
        crawl_delay: float = 0.5,
        verify_ssl: bool = False,
    ) -> None:
        self._trusts_path = trusts_path
        self._icb_path = icb_path
        self._output_dir = output_dir
        self._max_pages = max_pages
        self._timeout = timeout
        self._crawl_delay = crawl_delay
        self._verify_ssl = verify_ssl
        self._trusts: list[Trust] = load_trusts(trusts_path)
        self._icbs: list[Trust] = load_trusts(icb_path) if icb_path.exists() else []
        self._cache = DiscoveryCache()
        self._failure_cache = FailureCache()
        self._jobs: dict[str, ScrapeJob] = {}
        self._lock = threading.Lock()
        self._cache_lock = threading.Lock()

    def list_trusts(self) -> list[dict]:
        return [{"name": t.name, "url": t.url, "js_render": t.js_render} for t in self._trusts]

    def list_icbs(self) -> list[dict]:
        return [{"name": t.name, "url": t.url, "js_render": t.js_render} for t in self._icbs]

    def reload_config(self) -> None:
        """Reload trust and ICB lists from disk (call after config file edits)."""
        with self._lock:
            self._trusts = load_trusts(self._trusts_path)
            self._icbs = load_trusts(self._icb_path) if self._icb_path.exists() else []

    def start_job(
        self,
        trust_names: list[str] | None,
        selected_types: set[str],
        all_matches: bool = False,
        limit_per_type: int = 1,
        dry_run: bool = False,
        date_filters: dict[str, int] | None = None,
        parallel_trusts: int = 5,
        max_pages: int = 60,
        crawl_delay: float = 0.5,
        ignore_cache: bool = False,
        verbose: bool = False,
        source: str = "trust",
    ) -> ScrapeJob:
        if source not in ("trust", "icb"):
            raise ValueError(f"Invalid source: {source!r}. Must be 'trust' or 'icb'.")
        pool = self._icbs if source == "icb" else self._trusts
        if trust_names is None:
            trusts = pool
        else:
            name_set = {n.lower() for n in trust_names}
            trusts = [t for t in pool if t.name.lower() in name_set]

        job = ScrapeJob(
            job_id=uuid.uuid4().hex,
            trust_names=[t.name for t in trusts],
            selected_types=selected_types,
            total_trusts=len(trusts),
            date_filters=date_filters or {},
            parallel_trusts=max(1, min(parallel_trusts, 10)),
            max_pages=max(10, min(max_pages, 200)),
            crawl_delay=max(0.0, min(crawl_delay, 5.0)),
            ignore_cache=ignore_cache,
            verbose=verbose,
        )

        with self._lock:
            self._jobs[job.job_id] = job

        thread = threading.Thread(
            target=self._run_job,
            args=(job, trusts, selected_types, all_matches, limit_per_type, dry_run),
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
        job.stop_event.set()
        job.log("cancelled", message="Job cancelled by user")
        return True

    def _do_discover(
        self,
        session,
        trust: Trust,
        job: ScrapeJob,
        selected_types: set[str],
        cached: list[str] | None,
        fast_check: bool,
    ) -> list[Candidate]:
        on_scan = (lambda url: job.log("page_scan", trust=trust.name, url=url)) if job.verbose else None
        return discover_candidates(
            session,
            trust,
            max_pages=job.max_pages,
            timeout=self._timeout,
            crawl_delay=job.crawl_delay,
            selected_types=selected_types,
            stop_event=job.stop_event,
            cached_pages=cached or None,
            fast_check=fast_check,
            on_page_scan=on_scan,
        )

    def _scrape_trust(
        self,
        job: ScrapeJob,
        index: int,
        trust: Trust,
        selected_types: set[str],
        all_matches: bool,
        limit_per_type: int,
        dry_run: bool,
        cutoff_dates: dict[str, dt.date],
        previously_failed: set[str],
    ) -> None:
        if job.stop_event.is_set() or job.status == "cancelled":
            return

        job.log("trust_start", trust=trust.name, index=index, total=job.total_trusts)
        print(f"\n[{index}/{job.total_trusts}] {trust.name}")

        session = build_session(verify_ssl=self._verify_ssl)
        try:
            if trust.js_render:
                candidates = self._js_fallback(trust, selected_types)
            else:
                with self._cache_lock:
                    cached = None if job.ignore_cache else self._cache.get_pages(trust.name, selected_types)

                is_previously_failed = trust.name in previously_failed

                if is_previously_failed and cached:
                    # Fast-check: only revisit known-good pages to avoid wasting
                    # time on start_urls that have been unproductive before
                    print(f"  [previously failed] Fast-checking {len(cached)} cached page(s) first…")
                    candidates = self._do_discover(session, trust, job, selected_types, cached, fast_check=True)
                    if not candidates:
                        job.log("trust_retry", trust=trust.name,
                                message="Cached pages found nothing — running full crawl")
                        print("  Fast-check found nothing — running full crawl")
                        candidates = self._do_discover(session, trust, job, selected_types, cached, fast_check=False)
                else:
                    if cached and not job.ignore_cache:
                        print(f"  Using {len(cached)} cached source page(s) from previous run")
                    candidates = self._do_discover(session, trust, job, selected_types, cached, fast_check=False)

            with self._cache_lock:
                self._cache.update(trust.name, candidates)
            apply_last_modified_dates(session, candidates, timeout=self._timeout)
            chosen = selected_candidates(
                candidates,
                all_matches=all_matches,
                limit_per_type=limit_per_type,
                cutoff_dates=cutoff_dates or None,
            )

            if not chosen:
                with job._lock:
                    job.failed_trusts.append({"name": trust.name, "error": None, "reason": "no_results"})
                self._failure_cache.mark_failed(trust.name, reason="no_results")
                job.log("trust_done", trust=trust.name, downloaded=0, found=0,
                        message="No matching documents found")
                print("  No matching documents found.")
                with job._lock:
                    job.completed_trusts += 1
                return

            self._failure_cache.mark_succeeded(trust.name)
            downloaded = 0
            for candidate in chosen:
                date_text = candidate.date.isoformat() if candidate.date else "unknown date"
                print(f"  Selected [{candidate.report_type}] {date_text} - {candidate.url}")
                job.log("candidate_found", trust=trust.name,
                        report_type=candidate.report_type,
                        date=date_text, url=candidate.url)

                path, skipped = download_candidate(
                    session,
                    trust,
                    candidate,
                    output_dir=self._output_dir,
                    timeout=self._timeout,
                    dry_run=dry_run,
                )
                if skipped:
                    job.log("candidate_skipped", trust=trust.name,
                            url=candidate.url, path=str(path))
                result = DownloadResult(
                    trust_name=trust.name,
                    candidate=candidate,
                    file_path=str(path) if path else None,
                    success=True,
                )
                with job._lock:
                    job.results.append(result.to_dict())
                if path and not skipped:
                    downloaded += 1
                    print(f"  Downloaded: {path}")

            job.log("trust_done", trust=trust.name, found=len(chosen),
                    downloaded=downloaded)

        except Exception as exc:
            print(f"  Failed: {exc}")
            job.log("trust_error", trust=trust.name, error=str(exc))
            with job._lock:
                job.failed_trusts.append({"name": trust.name, "error": str(exc), "reason": "error"})
            self._failure_cache.mark_failed(trust.name, reason="error")

        with job._lock:
            job.completed_trusts += 1

    def _run_job(
        self,
        job: ScrapeJob,
        trusts: list[Trust],
        selected_types: set[str],
        all_matches: bool,
        limit_per_type: int,
        dry_run: bool,
    ) -> None:
        cutoff_dates = _compute_cutoffs(job.date_filters)
        previously_failed = self._failure_cache.get_failed()

        with concurrent.futures.ThreadPoolExecutor(max_workers=job.parallel_trusts) as executor:
            futures = {
                executor.submit(
                    self._scrape_trust,
                    job, index, trust, selected_types, all_matches,
                    limit_per_type, dry_run, cutoff_dates, previously_failed,
                ): trust
                for index, trust in enumerate(trusts, start=1)
            }
            for future in concurrent.futures.as_completed(futures):
                exc = future.exception()
                if exc:
                    trust = futures[future]
                    print(f"  Unhandled error for {trust.name}: {exc}")

        job.finished_at = dt.datetime.now()
        if job.status not in ("cancelled",):
            job.status = "done"
        failed_names = [f["name"] for f in job.failed_trusts]
        job.log("done",
                total=job.total_trusts,
                completed=job.completed_trusts,
                downloads=sum(1 for r in job.results if r.get("file_path")),
                failures=len(job.failed_trusts),
                failed_names=failed_names)

    def _js_fallback(self, trust: Trust, selected_types: set[str]) -> list[Candidate]:
        try:
            from scraper.js_fallback import JSFallbackFetcher
            fetcher = JSFallbackFetcher()
            return fetcher.fetch(trust, selected_types=selected_types)
        except Exception as exc:
            print(f"  JS fallback failed for {trust.name}: {exc}")
            return []
