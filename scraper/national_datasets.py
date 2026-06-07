"""
Fetchers for national NHS mental health datasets.
Each fetcher downloads the latest published version of its dataset to
downloads/national/<subdir>/, skipping if the file already exists.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


@dataclass
class FetchResult:
    source_key: str
    display_name: str
    file_path: Path | None
    url: str | None
    fetched_at: str
    version_label: str
    skipped: bool = False
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "source_key": self.source_key,
            "display_name": self.display_name,
            "file_path": str(self.file_path) if self.file_path else None,
            "url": self.url,
            "fetched_at": self.fetched_at,
            "version_label": self.version_label,
            "skipped": self.skipped,
            "error": self.error,
        }


def _safe_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)[:200]


def _path_ext(href: str) -> str:
    """Return the file extension from a URL path, ignoring query strings."""
    path = urlparse(href).path
    return os.path.splitext(path)[1].lower()


class NationalDatasetFetcher:
    source_key: str = ""
    display_name: str = ""
    output_subdir: str = ""

    def fetch(self, output_dir: Path, session: requests.Session) -> FetchResult:
        raise NotImplementedError

    # ── helpers ──────────────────────────────────────────────────────────────

    def _get_with_retry(
        self,
        session: requests.Session,
        url: str,
        timeout: int,
        stream: bool = False,
        max_retries: int = 3,
    ) -> requests.Response:
        """GET with 429-aware exponential backoff. Raises on unrecoverable errors."""
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                resp = session.get(url, timeout=timeout, stream=stream)
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                continue
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", min(60, 5 * (2 ** attempt))))
                print(f"  429 rate-limited — waiting {wait}s (attempt {attempt + 1}/{max_retries}): {url}")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        if last_exc:
            raise last_exc
        raise requests.HTTPError(f"429 rate-limit persisted after {max_retries} retries: {url}")

    def _download(
        self,
        session: requests.Session,
        url: str,
        output_dir: Path,
        filename: str,
        version_label: str,
    ) -> FetchResult:
        dest = output_dir / self.output_subdir
        dest.mkdir(parents=True, exist_ok=True)
        file_path = dest / _safe_filename(filename)
        if file_path.exists():
            return FetchResult(
                source_key=self.source_key,
                display_name=self.display_name,
                file_path=file_path,
                url=url,
                fetched_at=dt.date.today().isoformat(),
                version_label=version_label,
                skipped=True,
            )
        resp = self._get_with_retry(session, url, timeout=120, stream=True)

        # Reject HTML responses for non-HTML destinations (login redirects)
        content_type = resp.headers.get("Content-Type", "")
        ext = _path_ext(filename)
        if "text/html" in content_type and ext not in (".html", ".htm", ""):
            return self._error(
                f"Server returned HTML instead of {ext or 'data'} — "
                f"possible redirect to login page (URL: {url})"
            )

        try:
            with file_path.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
        except Exception as exc:
            # Remove partial file so future runs retry
            try:
                file_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise exc

        # Reject zero-byte files
        if file_path.stat().st_size == 0:
            file_path.unlink(missing_ok=True)
            return self._error(f"Downloaded file was empty (URL: {url})")

        return FetchResult(
            source_key=self.source_key,
            display_name=self.display_name,
            file_path=file_path,
            url=url,
            fetched_at=dt.date.today().isoformat(),
            version_label=version_label,
        )

    def _error(self, msg: str) -> FetchResult:
        return FetchResult(
            source_key=self.source_key,
            display_name=self.display_name,
            file_path=None,
            url=None,
            fetched_at=dt.date.today().isoformat(),
            version_label="",
            error=msg,
        )

    def _soup(self, session: requests.Session, url: str) -> BeautifulSoup:
        resp = self._get_with_retry(session, url, timeout=30)
        return BeautifulSoup(resp.text, "html.parser")

    def _find_file_link(
        self,
        soup: BeautifulSoup,
        base_url: str,
        extensions: tuple[str, ...],
        prefer_text: str | None = None,
    ) -> tuple[str | None, str | None]:
        """Return (absolute_url, filename) for first matching download link.

        Extension matching uses the URL path only — query strings are ignored
        so links like /download?file=data.csv&token=xyz are matched correctly.
        """
        candidates: list[tuple[str, str, str]] = []
        for a in soup.find_all("a", href=True):
            href: str = a["href"]
            text: str = a.get_text(strip=True)
            if _path_ext(href) in extensions:
                abs_url = urljoin(base_url, href)
                # Derive filename from path component (not raw href with query)
                path_part = urlparse(href).path
                name = path_part.rsplit("/", 1)[-1] or "download"
                candidates.append((abs_url, name, text))
        if not candidates:
            return None, None
        if prefer_text:
            pt = prefer_text.lower()
            for url, name, text in candidates:
                if pt in text.lower():
                    return url, name
        return candidates[0][0], candidates[0][1]


# ── Individual fetchers ────────────────────────────────────────────────────────

_NHSD_BASE = "https://digital.nhs.uk"
_NHSE_BASE = "https://www.england.nhs.uk"


class MHSDSFetcher(NationalDatasetFetcher):
    """NHS Mental Health Services Data Set — monthly selected-measures file."""
    source_key = "mhsds"
    display_name = "NHS MHSDS Monthly Statistics"
    output_subdir = "national/mhsds"
    _series_url = (
        f"{_NHSD_BASE}/data-and-information/publications/statistical"
        "/mental-health-services-monthly-statistics"
    )

    def fetch(self, output_dir: Path, session: requests.Session) -> FetchResult:
        try:
            soup = self._soup(session, self._series_url)
            release_url = version_label = None

            # Primary: find "performance-" release links (current NHS naming)
            for a in soup.find_all("a", href=True):
                href: str = a["href"]
                if "/mental-health-services-monthly-statistics/performance-" in href:
                    release_url = urljoin(_NHSD_BASE, href)
                    version_label = a.get_text(strip=True)
                    break

            # Fallback: any sub-page of the series URL (e.g. "provisional-" releases)
            if not release_url:
                series_path = urlparse(self._series_url).path.rstrip("/")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    parsed = urlparse(urljoin(_NHSD_BASE, href))
                    if (
                        parsed.path.startswith(series_path + "/")
                        and parsed.path.rstrip("/") != series_path
                    ):
                        release_url = parsed.geturl()
                        version_label = a.get_text(strip=True)
                        break

            if not release_url:
                return self._error("Could not find latest MHSDS release on series page")

            soup2 = self._soup(session, release_url)
            # Prefer "selected measures" XLSX/CSV, fall back to any ZIP
            file_url, filename = self._find_file_link(
                soup2, release_url, (".xlsx", ".csv", ".zip"), prefer_text="selected"
            )
            if not file_url:
                file_url, filename = self._find_file_link(
                    soup2, release_url, (".csv", ".zip", ".xlsx")
                )
            if not file_url:
                return self._error(f"No data file found on release page: {release_url}")
            return self._download(
                session, file_url, output_dir, filename, version_label or "latest"
            )
        except Exception as exc:
            return self._error(str(exc))


class OAPFetcher(NationalDatasetFetcher):
    """Out of Area Placements.

    NOTE: The separate OAP publication series was retired in April 2024 —
    OAP data is now embedded in MHSDS v6 monthly statistics. This fetcher
    attempts to download the last available archived release; if the series
    page shows no releases it returns a graceful 'archived' notice rather
    than an error so users understand why there is no new data.
    """
    source_key = "oap"
    display_name = "NHS Out of Area Placements (OAP)"
    output_subdir = "national/oap"
    _series_url = (
        f"{_NHSD_BASE}/data-and-information/publications/statistical"
        "/out-of-area-placements-in-mental-health-services"
    )

    def fetch(self, output_dir: Path, session: requests.Session) -> FetchResult:
        try:
            soup = self._soup(session, self._series_url)
            release_url = version_label = None
            series_path = urlparse(self._series_url).path.rstrip("/")
            for a in soup.find_all("a", href=True):
                href: str = a["href"]
                parsed = urlparse(urljoin(_NHSD_BASE, href))
                if (
                    parsed.path.startswith(series_path + "/")
                    and parsed.path.rstrip("/") != series_path
                ):
                    release_url = parsed.geturl()
                    version_label = a.get_text(strip=True)
                    break

            if not release_url:
                # Series archived — return as skipped with informative message
                return FetchResult(
                    source_key=self.source_key,
                    display_name=self.display_name,
                    file_path=None,
                    url=self._series_url,
                    fetched_at=dt.date.today().isoformat(),
                    version_label="Series archived April 2024 — OAP data now in MHSDS monthly statistics",
                    skipped=True,
                )

            soup2 = self._soup(session, release_url)
            file_url, filename = self._find_file_link(
                soup2, release_url, (".csv", ".xlsx", ".zip")
            )
            if not file_url:
                return self._error(f"No data file on {release_url}")
            return self._download(
                session, file_url, output_dir, filename, version_label or "latest"
            )
        except Exception as exc:
            return self._error(str(exc))


class CQCSurveyFetcher(NationalDatasetFetcher):
    """CQC Community Mental Health Survey — trust-level benchmark file."""
    source_key = "cqc_survey"
    display_name = "CQC Community Mental Health Survey"
    output_subdir = "national/cqc-survey"
    _page_url = "https://www.cqc.org.uk/publications/surveys/community-mental-health-survey"

    def fetch(self, output_dir: Path, session: requests.Session) -> FetchResult:
        try:
            soup = self._soup(session, self._page_url)
            # Trust-level data is in the "benchmark" ODS file
            file_url, filename = self._find_file_link(
                soup, self._page_url, (".ods", ".xlsx", ".csv"), prefer_text="benchmark"
            )
            if not file_url:
                file_url, filename = self._find_file_link(
                    soup, self._page_url, (".ods", ".xlsx", ".csv")
                )
            if not file_url:
                return self._error("No data file found on CQC Community MH Survey page")
            year = dt.date.today().year
            versioned = f"{year}_{filename}"
            return self._download(session, file_url, output_dir, versioned, f"{year} survey")
        except Exception as exc:
            return self._error(str(exc))


class NCAPFetcher(NationalDatasetFetcher):
    """NCAP — National Clinical Audit of Psychosis latest report."""
    source_key = "ncap"
    display_name = "National Clinical Audit of Psychosis (NCAP)"
    output_subdir = "national/ncap"
    _page_url = (
        "https://www.rcpsych.ac.uk/improving-care/ccqi/national-clinical-audits"
        "/national-clinical-audit-of-psychosis/audit-reports"
    )

    def fetch(self, output_dir: Path, session: requests.Session) -> FetchResult:
        try:
            soup = self._soup(session, self._page_url)
            # Prefer "State of the Nation" report
            file_url, filename = self._find_file_link(
                soup, self._page_url, (".pdf",), prefer_text="state of the nation"
            )
            if not file_url:
                file_url, filename = self._find_file_link(
                    soup, self._page_url, (".pdf", ".csv")
                )
            if not file_url:
                return self._error("No report file found on NCAP audit reports page")
            year = dt.date.today().year
            versioned = f"{year}_{filename}"
            return self._download(session, file_url, output_dir, versioned, f"NCAP {year}")
        except Exception as exc:
            return self._error(str(exc))


class FingertipsFetcher(NationalDatasetFetcher):
    """OHID Fingertips Adult Mental Health and Wellbeing profile.

    Scrapes the profile page to find the bulk download CSV link rather than
    using a hardcoded numeric API ID (which may change or return an unverified
    profile). Falls back to indicator-specific API calls for key SMI indicators.
    """
    source_key = "fingertips"
    display_name = "OHID Fingertips Adult MH Profile"
    output_subdir = "national/fingertips"
    _profile_url = "https://fingertips.phe.org.uk/profile/adult-mental-health-wellbeing"
    # Fallback: confirmed SMI-related indicator IDs on Fingertips
    # 848 = QOF SMI register prevalence; 849 = mental health quality indicator
    _fallback_api = (
        "https://fingertips.phe.org.uk/api/all_data/csv/by_indicator_id"
        "?indicator_ids=848,849&area_type_id=102"
    )

    def fetch(self, output_dir: Path, session: requests.Session) -> FetchResult:
        try:
            today = dt.date.today().isoformat()
            # Try to scrape the profile page for a download link first
            try:
                soup = self._soup(session, self._profile_url)
                file_url, filename = self._find_file_link(
                    soup, self._profile_url, (".csv", ".xlsx"),
                    prefer_text="download"
                )
                if not file_url:
                    file_url, filename = self._find_file_link(
                        soup, self._profile_url, (".csv", ".xlsx"),
                        prefer_text="export"
                    )
            except Exception:
                file_url = filename = None

            if file_url:
                dl_filename = f"fingertips_adult_mh_{today}_{filename}"
                return self._download(session, file_url, output_dir, dl_filename, today)

            # Fallback: indicator-level API for confirmed SMI indicators
            fallback_filename = f"fingertips_smi_indicators_{today}.csv"
            return self._download(
                session, self._fallback_api, output_dir, fallback_filename, today
            )
        except Exception as exc:
            return self._error(str(exc))


class QOFFetcher(NationalDatasetFetcher):
    """QOF SMI registers — latest year's mental health XLSX.

    Release URLs use fiscal year format: /2024-25
    """
    source_key = "qof"
    display_name = "NHS QOF SMI Registers"
    output_subdir = "national/qof"
    _base_url = (
        f"{_NHSD_BASE}/data-and-information/publications/statistical"
        "/quality-and-outcomes-framework-achievement-prevalence-and-exceptions-data"
    )

    def fetch(self, output_dir: Path, session: requests.Session) -> FetchResult:
        try:
            soup = self._soup(session, self._base_url)
            release_url = version_label = None
            base_path = urlparse(self._base_url).path.rstrip("/")
            for a in soup.find_all("a", href=True):
                href: str = a["href"]
                parsed = urlparse(urljoin(_NHSD_BASE, href))
                # Fiscal year format: /2024-25 (two 2-digit year segments)
                if (
                    parsed.path.startswith(base_path + "/")
                    and re.search(r"/20\d\d-\d\d$", parsed.path)
                ):
                    release_url = parsed.geturl()
                    version_label = a.get_text(strip=True)
                    break
            if not release_url:
                return self._error("Could not find latest QOF release page")
            soup2 = self._soup(session, release_url)
            # Mental health group XLSX preferred; fall back to ZIP
            file_url, filename = self._find_file_link(
                soup2, release_url, (".xlsx", ".zip"), prefer_text="mental health"
            )
            if not file_url:
                file_url, filename = self._find_file_link(
                    soup2, release_url, (".zip", ".xlsx")
                )
            if not file_url:
                return self._error(f"No data file on {release_url}")
            return self._download(
                session, file_url, output_dir, filename, version_label or "latest"
            )
        except Exception as exc:
            return self._error(str(exc))


class PHSMIFetcher(NationalDatasetFetcher):
    """Physical Health Checks for People with SMI — latest quarterly CSV.

    Release URLs use format: /q1-2024-25, /q2-2024-25, etc.
    """
    source_key = "phsmi"
    display_name = "Physical Health Checks for SMI (PHSMI)"
    output_subdir = "national/phsmi"
    _series_url = (
        f"{_NHSD_BASE}/data-and-information/publications/statistical"
        "/physical-health-checks-for-people-with-severe-mental-illness"
    )

    def fetch(self, output_dir: Path, session: requests.Session) -> FetchResult:
        try:
            soup = self._soup(session, self._series_url)
            release_url = version_label = None
            series_path = urlparse(self._series_url).path.rstrip("/")
            for a in soup.find_all("a", href=True):
                href: str = a["href"]
                parsed = urlparse(urljoin(_NHSD_BASE, href))
                # Quarterly releases: /q1-2024-25, /q2-2024-25, etc.
                if (
                    parsed.path.startswith(series_path + "/q")
                    and parsed.path != series_path
                ):
                    release_url = parsed.geturl()
                    version_label = a.get_text(strip=True)
                    break
            if not release_url:
                return self._error("Could not find latest PHSMI quarterly release page")
            soup2 = self._soup(session, release_url)
            file_url, filename = self._find_file_link(
                soup2, release_url, (".csv", ".xlsx")
            )
            if not file_url:
                return self._error(f"No data file on {release_url}")
            return self._download(
                session, file_url, output_dir, filename, version_label or "latest"
            )
        except Exception as exc:
            return self._error(str(exc))


class OversightFetcher(NationalDatasetFetcher):
    """NHS Oversight Framework segmentation — non-acute league table CSV."""
    source_key = "oversight"
    display_name = "NHS Oversight Framework Segmentation"
    output_subdir = "national/oversight"
    _page_url = f"{_NHSE_BASE}/nhs-oversight-framework/segmentation-and-league-tables/"

    def fetch(self, output_dir: Path, session: requests.Session) -> FetchResult:
        try:
            soup = self._soup(session, self._page_url)
            # Non-acute league table = mental health providers
            file_url, filename = self._find_file_link(
                soup, self._page_url, (".csv", ".xlsx"), prefer_text="non-acute"
            )
            if not file_url:
                # Try broader search including XLS/XLSX since NHS sometimes uses spreadsheets
                file_url, filename = self._find_file_link(
                    soup, self._page_url, (".csv", ".xlsx", ".xls")
                )
            if not file_url:
                return self._error("No data file found on NHS Oversight segmentation page")
            return self._download(session, file_url, output_dir, filename, "latest")
        except Exception as exc:
            return self._error(str(exc))


# ── Registry ──────────────────────────────────────────────────────────────────

ALL_FETCHERS: list[NationalDatasetFetcher] = [
    MHSDSFetcher(),
    OAPFetcher(),
    CQCSurveyFetcher(),
    NCAPFetcher(),
    FingertipsFetcher(),
    QOFFetcher(),
    PHSMIFetcher(),
    OversightFetcher(),
]

FETCHERS_BY_KEY: dict[str, NationalDatasetFetcher] = {
    f.source_key: f for f in ALL_FETCHERS
}
