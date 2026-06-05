"""Background runner that walks downloads/ and processes new PDFs."""
from __future__ import annotations

import json
import queue
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from intelligence.database import BoardPaper, get_session
from intelligence.pipeline import PipelineRunner


@dataclass
class IntelligenceJob:
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "pending"  # pending | running | done | error
    total: int = 0
    processed: int = 0
    skipped: int = 0
    errors: int = 0
    log_queue: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=500))

    def log(self, event: str, **kwargs: Any) -> None:
        try:
            self.log_queue.put_nowait({"event": event, **kwargs})
        except queue.Full:
            pass

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "total": self.total,
            "processed": self.processed,
            "skipped": self.skipped,
            "errors": self.errors,
        }


_jobs: dict[str, IntelligenceJob] = {}
_jobs_lock = threading.Lock()


def get_job(job_id: str) -> IntelligenceJob | None:
    with _jobs_lock:
        return _jobs.get(job_id)


def _slug_to_trust_name(slug: str) -> str:
    """Convert directory slug back to approximate trust name for DB lookup.

    The actual trust name is read from the metadata.json sidecar if present;
    this is a fallback.
    """
    return slug.replace("-", " ").title()


def _trust_name_from_metadata(pdf_path: Path) -> tuple[str, str | None]:
    """Read trust name and URL from the sidecar .metadata.json if it exists."""
    meta_path = pdf_path.with_suffix("").with_suffix(".metadata.json")
    if meta_path.exists():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            name = data.get("trust_name") or data.get("trust") or ""
            url = data.get("trust_url") or data.get("url")
            if name:
                return name, url
        except Exception:
            pass
    # Fallback: derive from directory slug (parent of year directory)
    slug = pdf_path.parent.parent.name
    return _slug_to_trust_name(slug), None


def scan_downloads(
    gemini_api_key: str,
    downloads_dir: Path,
    db_path: Path | None = None,
) -> IntelligenceJob:
    """Start a background job that processes all new PDFs in downloads_dir."""
    job = IntelligenceJob()
    with _jobs_lock:
        _jobs[job.job_id] = job

    def _run() -> None:
        job.status = "running"
        runner = PipelineRunner(gemini_api_key, db_path)

        with get_session(db_path) as session:
            already_ingested = {
                row.file_path
                for row in session.query(BoardPaper.file_path).all()
            }

        pdf_files = sorted(downloads_dir.rglob("*.pdf"))
        new_pdfs = [p for p in pdf_files if str(p) not in already_ingested]
        job.total = len(new_pdfs)
        job.log("scan_complete", total=job.total, already_ingested=len(already_ingested))

        for pdf_path in new_pdfs:
            if not pdf_path.is_file():
                continue
            trust_name, trust_url = _trust_name_from_metadata(pdf_path)
            job.log("processing", file=str(pdf_path), trust=trust_name)
            try:
                runner.process_paper(pdf_path, trust_name, trust_url)
                job.processed += 1
                job.log("done", file=pdf_path.name, trust=trust_name, processed=job.processed)
            except Exception as exc:
                job.errors += 1
                job.log("error", file=pdf_path.name, trust=trust_name, error=str(exc))

        job.status = "done"
        job.log("complete", processed=job.processed, errors=job.errors, skipped=job.skipped)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return job
