"""
Engine for running national dataset fetch jobs.
Simpler than ScrapeEngine — sequential, known URLs, skip-if-exists.
"""
from __future__ import annotations

import json
import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from .national_datasets import ALL_FETCHERS, FETCHERS_BY_KEY, FetchResult


@dataclass
class NationalFetchJob:
    job_id: str
    source_keys: list[str]
    output_dir: Path
    status: str = "running"        # running | done | error
    started_at: str = ""
    finished_at: str = ""
    results: list[FetchResult] = field(default_factory=list)
    log_queue: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=200))

    def _emit(self, event: str, data: dict) -> None:
        try:
            self.log_queue.put_nowait({"event": event, "data": data})
        except queue.Full:
            pass

    def iter_sse(self):
        """Generator yielding raw SSE text from the log queue.

        Handles the race condition where the job finishes before the consumer
        connects — in that case a synthetic done event is emitted immediately.
        """
        # Fast path: job already finished and queue is drained
        if self.status in ("done", "error") and self.log_queue.empty():
            summary = [r.to_dict() for r in self.results]
            if self.status == "error":
                yield f"event: error\ndata: {json.dumps({'message': 'Job already finished with error'})}\n\n"
            else:
                yield f"event: done\ndata: {json.dumps({'summary': summary})}\n\n"
            return

        heartbeats_since_msg = 0
        while True:
            try:
                msg = self.log_queue.get(timeout=30)
            except queue.Empty:
                # Check if job finished while we were waiting
                if self.status in ("done", "error") and self.log_queue.empty():
                    summary = [r.to_dict() for r in self.results]
                    if self.status == "error":
                        yield f"event: error\ndata: {json.dumps({'message': 'Job finished with error'})}\n\n"
                    else:
                        yield f"event: done\ndata: {json.dumps({'summary': summary})}\n\n"
                    return
                heartbeats_since_msg += 1
                yield "event: heartbeat\ndata: {}\n\n"
                continue
            heartbeats_since_msg = 0
            yield f"event: {msg['event']}\ndata: {json.dumps(msg['data'])}\n\n"
            if msg["event"] in ("done", "error"):
                break


class NationalFetchEngine:
    def __init__(self, output_dir: Path | None = None) -> None:
        self.output_dir = output_dir or Path("downloads")
        self._jobs: dict[str, NationalFetchJob] = {}
        self._lock = threading.Lock()

    def list_sources(self) -> list[dict]:
        return [
            {"key": f.source_key, "display_name": f.display_name}
            for f in ALL_FETCHERS
        ]

    def start_job(self, source_keys: Optional[list[str]] = None) -> NationalFetchJob:
        keys = source_keys or list(FETCHERS_BY_KEY.keys())
        invalid = [k for k in keys if k not in FETCHERS_BY_KEY]
        if invalid:
            raise ValueError(f"Unknown source keys: {invalid}")
        job = NationalFetchJob(
            job_id=str(uuid.uuid4()),
            source_keys=keys,
            output_dir=self.output_dir,
            started_at=datetime.utcnow().isoformat(),
        )
        with self._lock:
            self._jobs[job.job_id] = job
        thread = threading.Thread(target=self._run, args=(job,), daemon=True)
        thread.start()
        return job

    def get_job(self, job_id: str) -> Optional[NationalFetchJob]:
        return self._jobs.get(job_id)

    def _run(self, job: NationalFetchJob) -> None:
        session = requests.Session()
        session.headers.update({"User-Agent": "NHSTrustScraper/1.0 (research)"})
        try:
            for key in job.source_keys:
                fetcher = FETCHERS_BY_KEY[key]
                job._emit("fetch_start", {"key": key, "name": fetcher.display_name})
                result = fetcher.fetch(job.output_dir, session)
                job.results.append(result)
                if result.error:
                    job._emit(
                        "fetch_error",
                        {
                            "key": key,
                            "name": fetcher.display_name,
                            "error": result.error,
                        },
                    )
                elif result.skipped:
                    job._emit(
                        "fetch_skipped",
                        {
                            "key": key,
                            "name": fetcher.display_name,
                            "file": str(result.file_path),
                            "version": result.version_label,
                        },
                    )
                else:
                    job._emit(
                        "fetch_done",
                        {
                            "key": key,
                            "name": fetcher.display_name,
                            "file": str(result.file_path),
                            "version": result.version_label,
                            "url": result.url,
                        },
                    )
            job.status = "done"
            job.finished_at = datetime.utcnow().isoformat()
            summary = [r.to_dict() for r in job.results]
            job._emit("done", {"summary": summary})
        except Exception as exc:
            job.status = "error"
            job.finished_at = datetime.utcnow().isoformat()
            job._emit("error", {"message": str(exc)})
