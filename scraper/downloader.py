from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from scraper.constants import DOCUMENT_EXTENSIONS, REPORT_TYPES
from scraper.models import Candidate, Trust


def slugify(value: str) -> str:
    value = value.lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "unknown"


def safe_filename(value: str, max_length: int = 180) -> str:
    value = unquote(value).strip().strip('"')
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:max_length].strip() or "downloaded-file"


def title_safe(value: str, max_length: int = 100) -> str:
    """Title-case string with underscores for use in filenames."""
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", " ", value)
    value = re.sub(r"\s+", "_", value.strip())
    value = re.sub(r"[^A-Za-z0-9_\-]+", "", value)
    return value[:max_length].strip("_") or "Unknown_Trust"


def filename_from_response(response: requests.Response, fallback_url: str) -> str:
    disposition = response.headers.get("content-disposition", "")
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disposition)
    if match:
        return safe_filename(match.group(1))
    name = Path(urlparse(fallback_url).path).name
    return safe_filename(name)


def output_path_for(
    trust: Trust,
    candidate: Candidate,
    *,
    output_dir: Path,
    response: requests.Response | None,
) -> Path:
    """
    Produces: downloads/{trust-slug}/{year}/[YYYY-MM-DD]_{Trust_Name}_{report_type}_{Title}{ext}
    """
    date_part = candidate.date.isoformat() if candidate.date else "unknown-date"
    year_part = str(candidate.date.year) if candidate.date else "unknown-year"
    trust_slug = slugify(trust.name)
    trust_title = title_safe(trust.name)

    original_name = (
        filename_from_response(response, candidate.url)
        if response is not None
        else Path(urlparse(candidate.url).path).name
    )
    extension = Path(original_name).suffix or candidate.extension
    if extension.lower() not in DOCUMENT_EXTENSIONS:
        extension = candidate.extension

    title = title_safe(candidate.title or Path(safe_filename(original_name)).stem, max_length=80)

    filename = f"[{date_part}]_{trust_title}_{candidate.report_type}_{title}"
    filename = safe_filename(filename, max_length=200)

    return output_dir / trust_slug / year_part / f"{filename}{extension}"


def write_metadata(
    trust: Trust,
    candidate: Candidate,
    *,
    output_dir: Path,
    file_path: Path,
) -> None:
    metadata = {
        "trust": trust.name,
        "trust_url": trust.url,
        "downloaded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "report_type": candidate.report_type,
        "file_path": str(file_path),
        "source_url": candidate.url,
        "source_page": candidate.source_page,
        "link_text": candidate.link_text,
        "title": candidate.title,
        "date": candidate.date.isoformat() if candidate.date else None,
        "date_source": candidate.date_source,
        "score": candidate.score,
    }

    sidecar = file_path.with_suffix(file_path.suffix + ".metadata.json")
    sidecar.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def download_candidate(
    session: requests.Session,
    trust: Trust,
    candidate: Candidate,
    *,
    output_dir: Path,
    timeout: int,
    dry_run: bool,
) -> tuple[Path | None, bool]:
    """Return (path, already_existed). already_existed=True means the file was skipped."""
    if dry_run:
        path = output_path_for(trust, candidate, output_dir=output_dir, response=None)
        print(f"  Would download [{candidate.report_type}] to {path}")
        return None, False

    expected_path = output_path_for(trust, candidate, output_dir=output_dir, response=None)
    if expected_path.exists():
        print(f"  Already downloaded, skipping: {expected_path.name}")
        return expected_path, True

    response = session.get(candidate.url, stream=True, timeout=timeout)
    response.raise_for_status()
    file_path = output_path_for(
        trust, candidate, output_dir=output_dir, response=response
    )
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with file_path.open("wb") as fh:
        for chunk in response.iter_content(chunk_size=1024 * 64):
            if chunk:
                fh.write(chunk)

    write_metadata(trust, candidate, output_dir=output_dir, file_path=file_path)
    return file_path, False
