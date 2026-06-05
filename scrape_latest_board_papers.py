from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import quote_plus, unquote, urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup


DOCUMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")
SKIP_EXTENSIONS = (
    ".css",
    ".gif",
    ".ico",
    ".jpg",
    ".jpeg",
    ".js",
    ".png",
    ".svg",
    ".webp",
    ".zip",
)
COMMON_PATHS = (
    "/about-us/board-papers/",
    "/about-us/board-meetings/",
    "/about-us/trust-board/",
    "/about-us/our-board/",
    "/about-us/board/",
    "/about-us/publications/",
    "/about-us/governance/",
    "/board-papers/",
    "/board-meetings/",
    "/trust-board/",
    "/our-board/",
    "/meetings/",
    "/publications/",
    "/governance/",
    "/strategy/",
    "/digital/",
)
MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

REPORT_TYPES = {
    "board": {
        "folder": "board-papers",
        "page_terms": (
            "board papers",
            "board meetings",
            "board meeting",
            "trust board",
            "board of directors",
            "board packs",
            "board pack",
            "public board",
            "meeting papers",
        ),
        "file_terms": (
            "board paper",
            "board papers",
            "board pack",
            "meeting pack",
            "board agenda",
            "public board",
            "trust board",
            "board of directors",
        ),
        "required_any": ("board",),
        "bonus": 100,
    },
    "supplementary": {
        "folder": "supplementary-material",
        "page_terms": (
            "board papers",
            "board meetings",
            "supplementary",
            "supporting papers",
            "appendix",
            "appendices",
            "enclosure",
        ),
        "file_terms": (
            "supplementary",
            "supporting paper",
            "supporting papers",
            "appendix",
            "appendices",
            "enclosure",
            "additional papers",
            "part 2",
        ),
        "required_any": (),
        "bonus": 70,
    },
    "strategy": {
        "folder": "strategic-reporting",
        "page_terms": (
            "strategy",
            "strategic",
            "annual plan",
            "operational plan",
            "forward plan",
            "corporate plan",
        ),
        "file_terms": (
            "strategy",
            "strategic",
            "annual plan",
            "operational plan",
            "forward plan",
            "corporate plan",
            "integrated performance",
            "quality account",
            "annual report",
        ),
        "required_any": (),
        "bonus": 75,
    },
    "digital_strategy": {
        "folder": "digital-strategy",
        "page_terms": (
            "digital strategy",
            "digital",
            "data strategy",
            "technology",
            "informatics",
        ),
        "file_terms": (
            "digital strategy",
            "data strategy",
            "technology strategy",
            "informatics strategy",
            "digital plan",
            "digital roadmap",
            "digital transformation strategy",
            "electronic patient record",
        ),
        "required_any": (),
        "bonus": 85,
    },
}


@dataclass(frozen=True)
class Trust:
    name: str
    url: str | None = None
    start_urls: tuple[str, ...] = ()
    allowed_domains: tuple[str, ...] = ()
    search_query: str | None = None


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


def slugify(value: str) -> str:
    value = value.lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "unknown"


def safe_filename(value: str, max_length: int = 180) -> str:
    value = unquote(value).strip().strip('"')
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:max_length].strip() or "downloaded-file"


def domain_for(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


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
            )
        )
    return trusts


def site_allowed(url: str, allowed_domains: set[str]) -> bool:
    return domain_for(url) in allowed_domains


def looks_like_document(url: str) -> bool:
    return Path(urlparse(url).path.lower()).suffix in DOCUMENT_EXTENSIONS


def looks_like_document_link(url: str, text: str) -> bool:
    haystack = f"{text} {url}".lower()
    return (
        looks_like_document(url)
        or "/download" in urlparse(url).path.lower()
        or "download_file" in urlparse(url).path.lower()
        or "[pdf" in haystack
        or " pdf" in haystack
        or ".pdf" in haystack
    )


def document_extension(url: str) -> str:
    suffix = Path(urlparse(url).path.lower()).suffix
    return suffix if suffix in DOCUMENT_EXTENSIONS else ".pdf"


def looks_like_html_page(url: str) -> bool:
    path = urlparse(url).path.lower()
    return not path.endswith(SKIP_EXTENSIONS) and not looks_like_document(url)


def term_hits(text: str, terms: Iterable[str]) -> int:
    haystack = text.lower()
    hits = 0
    for term in terms:
        normalized = term.lower()
        if len(normalized) <= 3:
            if re.search(rf"\b{re.escape(normalized)}\b", haystack):
                hits += 1
        elif normalized in haystack:
            hits += 1
    return hits


def classify_report_type(text: str, selected_types: set[str]) -> tuple[str | None, int]:
    best_type = None
    best_score = 0
    haystack = text.lower()

    for report_type in selected_types:
        profile = REPORT_TYPES[report_type]
        if profile["required_any"] and not any(
            term in haystack for term in profile["required_any"]
        ):
            continue
        hits = term_hits(haystack, profile["file_terms"])
        if hits == 0:
            continue
        score = profile["bonus"] + (hits * 20)
        if score > best_score:
            best_type = report_type
            best_score = score

    return best_type, best_score


def page_relevance_score(text: str, selected_types: set[str]) -> int:
    score = 0
    haystack = text.lower()
    for report_type in selected_types:
        profile = REPORT_TYPES[report_type]
        score += term_hits(haystack, profile["page_terms"]) * 20
        score += term_hits(haystack, profile["file_terms"]) * 8
    if "board" in haystack:
        score += 15
    if "governance" in haystack or "publication" in haystack:
        score += 10
    return score


def extract_date(text: str) -> tuple[dt.date | None, str]:
    compact = unquote(text).replace("_", " ").replace("-", " ")

    patterns = [
        (r"\b(20\d{2})[./ -](0?[1-9]|1[0-2])[./ -](0?[1-9]|[12]\d|3[01])\b", "ymd"),
        (r"\b(0?[1-9]|[12]\d|3[01])[./ -](0?[1-9]|1[0-2])[./ -](20\d{2})\b", "dmy"),
        (r"\b(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b", "ymd_compact"),
    ]
    for pattern, source in patterns:
        match = re.search(pattern, compact)
        if not match:
            continue
        try:
            if source.startswith("ymd"):
                year, month, day = (int(part) for part in match.groups())
            else:
                day, month, year = (int(part) for part in match.groups())
            return dt.date(year, month, day), source
        except ValueError:
            pass

    month_names = "|".join(MONTHS)
    month_patterns = [
        rf"\b(0?[1-9]|[12]\d|3[01])\s+({month_names})\s+(20\d{{2}})\b",
        rf"\b({month_names})\s+(0?[1-9]|[12]\d|3[01]),?\s+(20\d{{2}})\b",
        rf"\b({month_names})\s+(20\d{{2}})\b",
    ]
    for index, pattern in enumerate(month_patterns):
        match = re.search(pattern, compact, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            parts = match.groups()
            if index == 0:
                day = int(parts[0])
                month = MONTHS[parts[1].lower()]
                year = int(parts[2])
            elif index == 1:
                month = MONTHS[parts[0].lower()]
                day = int(parts[1])
                year = int(parts[2])
            else:
                month = MONTHS[parts[0].lower()]
                day = 1
                year = int(parts[1])
            return dt.date(year, month, day), "month_name"
        except ValueError:
            pass

    return None, "not_found"


def request_page(
    session: requests.Session, url: str, timeout: int
) -> tuple[str, str] | None:
    try:
        response = session.get(url, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"  Could not fetch page: {url} ({exc})")
        return None

    content_type = response.headers.get("content-type", "").lower()
    if "text/html" not in content_type and "<html" not in response.text[:500].lower():
        return None
    return response.text, response.url


def iter_links(html: str, page_url: str) -> Iterable[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(page_url, href).split("#", 1)[0]
        text = " ".join(anchor.get_text(" ", strip=True).split())
        yield url, text


def discover_official_site(
    session: requests.Session, trust: Trust, timeout: int
) -> str | None:
    query = quote_plus(trust.search_query or f"{trust.name} official website")
    search_url = f"https://duckduckgo.com/html/?q={query}"
    result = request_page(session, search_url, timeout)
    if not result:
        return None

    html, _ = result
    for url, text in iter_links(html, search_url):
        parsed = urlparse(url)
        if "duckduckgo.com" in parsed.netloc:
            continue
        if ".nhs.uk" in parsed.netloc.lower() and "official" in text.lower():
            return f"{parsed.scheme}://{parsed.netloc}/"
        if ".nhs.uk" in parsed.netloc.lower():
            return f"{parsed.scheme}://{parsed.netloc}/"
    return None


def sitemap_urls(
    session: requests.Session,
    base_url: str,
    *,
    timeout: int,
    selected_types: set[str],
    limit: int = 80,
) -> list[str]:
    candidates: list[tuple[int, str]] = []
    for sitemap_path in ("/sitemap.xml", "/sitemap_index.xml"):
        sitemap_url = urljoin(base_url, sitemap_path)
        try:
            response = session.get(sitemap_url, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException:
            continue

        try:
            root = ET.fromstring(response.content)
        except ET.ParseError:
            continue

        for loc in root.iter():
            if not loc.tag.endswith("loc") or not loc.text:
                continue
            url = loc.text.strip()
            score = page_relevance_score(url, selected_types)
            if score:
                candidates.append((score, url))

    candidates.sort(reverse=True)
    return [url for _, url in candidates[:limit]]


def candidate_score(
    *,
    url: str,
    text: str,
    found_date: dt.date | None,
    type_score: int,
) -> int:
    haystack = f"{text} {url}".lower()
    score = type_score
    if looks_like_document(url):
        score += 50
    if found_date:
        score += 40
    if "draft" in haystack:
        score -= 15
    if "minute" in haystack and "supplementary" not in haystack:
        score -= 25
    return score


def add_queue_url(
    queue: list[tuple[int, str]],
    url: str,
    *,
    score: int,
    seen_pages: set[str],
) -> None:
    if score <= 0 or url in seen_pages or any(existing == url for _, existing in queue):
        return
    queue.append((score, url))
    queue.sort(reverse=True)


def discover_candidates(
    session: requests.Session,
    trust: Trust,
    *,
    max_pages: int,
    timeout: int,
    selected_types: set[str],
) -> list[Candidate]:
    trust_url = trust.url or discover_official_site(session, trust, timeout)
    if not trust_url:
        print("  Could not discover official NHS website.")
        return []

    allowed_domains = {domain_for(trust_url)}
    allowed_domains.update(domain_for(url) for url in trust.start_urls)
    allowed_domains.update(domain.lower().removeprefix("www.") for domain in trust.allowed_domains)

    queue: list[tuple[int, str]] = [(100, trust_url)]
    for url in trust.start_urls:
        queue.append((150, urljoin(trust_url, url)))
    for path in COMMON_PATHS:
        queue.append((60, urljoin(trust_url, path)))
    for url in sitemap_urls(
        session, trust_url, timeout=timeout, selected_types=selected_types
    ):
        queue.append((80 + page_relevance_score(url, selected_types), url))
    queue.sort(reverse=True)

    seen_pages: set[str] = set()
    seen_candidates: set[str] = set()
    candidates: list[Candidate] = []

    while queue and len(seen_pages) < max_pages:
        _, page_url = queue.pop(0)
        if page_url in seen_pages:
            continue
        if not site_allowed(page_url, allowed_domains):
            continue

        seen_pages.add(page_url)
        print(f"  Scanning {page_url}")

        result = request_page(session, page_url, timeout)
        if not result:
            continue

        html, final_page_url = result
        allowed_domains.add(domain_for(final_page_url))

        for link_url, link_text in iter_links(html, final_page_url):
            if not site_allowed(link_url, allowed_domains):
                continue

            combined_text = f"{link_text} {link_url}"
            if looks_like_document_link(link_url, link_text):
                report_type, type_score = classify_report_type(
                    combined_text, selected_types
                )
                if not report_type or link_url in seen_candidates:
                    continue

                found_date, date_source = extract_date(combined_text)
                title = safe_filename(
                    link_text or Path(urlparse(link_url).path).stem, max_length=90
                )
                seen_candidates.add(link_url)
                candidates.append(
                    Candidate(
                        url=link_url,
                        source_page=final_page_url,
                        link_text=link_text,
                        date=found_date,
                        date_source=date_source,
                        report_type=report_type,
                        title=title,
                            extension=document_extension(link_url),
                        score=candidate_score(
                            url=link_url,
                            text=combined_text,
                            found_date=found_date,
                            type_score=type_score,
                        ),
                    )
                )
                continue

            if looks_like_html_page(link_url):
                add_queue_url(
                    queue,
                    link_url,
                    score=page_relevance_score(combined_text, selected_types),
                    seen_pages=seen_pages,
                )

    return candidates


def apply_last_modified_dates(
    session: requests.Session,
    candidates: list[Candidate],
    *,
    timeout: int,
) -> None:
    for candidate in candidates:
        if candidate.date is not None:
            continue
        try:
            response = session.head(candidate.url, allow_redirects=True, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException:
            continue
        last_modified = response.headers.get("last-modified")
        if not last_modified:
            continue
        parsed = email.utils.parsedate_to_datetime(last_modified)
        if parsed:
            candidate.date = parsed.date()
            candidate.date_source = "http_last_modified"
            candidate.score += 5


def selected_candidates(
    candidates: list[Candidate],
    *,
    all_matches: bool,
    limit_per_type: int,
) -> list[Candidate]:
    candidates.sort(
        key=lambda item: (
            item.report_type,
            item.date or dt.date.min,
            item.score,
            item.title,
            item.url,
        ),
        reverse=True,
    )
    if all_matches:
        return candidates

    chosen: list[Candidate] = []
    counts: dict[str, int] = {}
    for candidate in candidates:
        count = counts.get(candidate.report_type, 0)
        if count >= limit_per_type:
            continue
        chosen.append(candidate)
        counts[candidate.report_type] = count + 1
    return chosen


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
    date_part = candidate.date.isoformat() if candidate.date else "unknown-date"
    year_part = str(candidate.date.year) if candidate.date else "unknown-year"
    report_folder = REPORT_TYPES[candidate.report_type]["folder"]
    trust_slug = slugify(trust.name)

    original_name = (
        filename_from_response(response, candidate.url)
        if response is not None
        else Path(urlparse(candidate.url).path).name
    )
    extension = Path(original_name).suffix or candidate.extension
    if extension.lower() not in DOCUMENT_EXTENSIONS:
        extension = candidate.extension

    title = safe_filename(candidate.title or Path(original_name).stem, max_length=80)
    filename = safe_filename(
        f"{date_part} - {trust.name} - {candidate.report_type} - {title}",
        max_length=170,
    )
    return output_dir / trust_slug / report_folder / year_part / f"{filename}{extension}"


def write_metadata(
    trust: Trust,
    candidate: Candidate,
    *,
    output_dir: Path,
    file_path: Path,
) -> None:
    trust_dir = output_dir / slugify(trust.name)
    report_dir = trust_dir / REPORT_TYPES[candidate.report_type]["folder"]
    report_dir.mkdir(parents=True, exist_ok=True)

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
    (report_dir / "latest.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )


def download_candidate(
    session: requests.Session,
    trust: Trust,
    candidate: Candidate,
    *,
    output_dir: Path,
    timeout: int,
    dry_run: bool,
) -> Path | None:
    if dry_run:
        path = output_path_for(trust, candidate, output_dir=output_dir, response=None)
        print(f"  Would download [{candidate.report_type}] to {path}")
        return None

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
    return file_path


def build_session(*, verify_ssl: bool) -> requests.Session:
    if not verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    session = requests.Session()
    session.verify = verify_ssl
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            )
        }
    )
    return session


def parse_types(raw_types: str, *, include_supplementary: bool, include_strategy: bool) -> set[str]:
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape board papers and related trust reports."
    )
    parser.add_argument(
        "--trusts",
        default="config/mental_health_trusts.json",
        type=Path,
        help="Path to the trust list JSON file.",
    )
    parser.add_argument(
        "--output",
        default="board_papers",
        type=Path,
        help="Folder where downloaded files will be stored.",
    )
    parser.add_argument(
        "--types",
        default="board",
        help="Comma-separated report types to download: board, supplementary, strategy, digital_strategy, or all.",
    )
    parser.add_argument(
        "--include-supplementary",
        action="store_true",
        help="Also include supplementary board materials.",
    )
    parser.add_argument(
        "--include-strategy",
        action="store_true",
        help="Also include strategic reporting and digital strategy materials.",
    )
    parser.add_argument(
        "--all-matches",
        action="store_true",
        help="Download every matching document instead of only the latest per type.",
    )
    parser.add_argument(
        "--limit-per-type",
        default=1,
        type=int,
        help="Number of latest documents to download per report type when --all-matches is not used.",
    )
    parser.add_argument(
        "--max-pages",
        default=60,
        type=int,
        help="Maximum likely pages to scan per trust.",
    )
    parser.add_argument(
        "--timeout",
        default=30,
        type=int,
        help="HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Find candidates but do not download files.",
    )
    parser.add_argument(
        "--only",
        help="Run only trusts whose name or URL contains this text.",
    )
    parser.add_argument(
        "--verify-ssl",
        action="store_true",
        help="Verify SSL certificates. By default SSL verification is disabled because some NHS sites have incomplete certificate chains.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        selected_types = parse_types(
            args.types,
            include_supplementary=args.include_supplementary,
            include_strategy=args.include_strategy,
        )
    except ValueError as exc:
        print(exc)
        return 2

    trusts = load_trusts(args.trusts)
    if args.only:
        needle = args.only.lower()
        trusts = [
            trust
            for trust in trusts
            if needle in trust.name.lower()
            or (trust.url and needle in trust.url.lower())
        ]
        if not trusts:
            print(f"No trusts matched --only {args.only!r}")
            return 2

    session = build_session(verify_ssl=args.verify_ssl)
    failures = 0

    print(f"Loaded {len(trusts)} trusts from {args.trusts}")
    print(f"Report types: {', '.join(sorted(selected_types))}")
    print(f"Output folder: {args.output}")

    for index, trust in enumerate(trusts, start=1):
        print(f"\n[{index}/{len(trusts)}] {trust.name}")
        try:
            candidates = discover_candidates(
                session,
                trust,
                max_pages=args.max_pages,
                timeout=args.timeout,
                selected_types=selected_types,
            )
            apply_last_modified_dates(session, candidates, timeout=args.timeout)
            chosen = selected_candidates(
                candidates,
                all_matches=args.all_matches,
                limit_per_type=args.limit_per_type,
            )
            if not chosen:
                print("  No matching documents found.")
                failures += 1
                continue

            for candidate in chosen:
                date_text = candidate.date.isoformat() if candidate.date else "unknown date"
                print(
                    f"  Selected [{candidate.report_type}] {date_text} - {candidate.url}"
                )
                path = download_candidate(
                    session,
                    trust,
                    candidate,
                    output_dir=args.output,
                    timeout=args.timeout,
                    dry_run=args.dry_run,
                )
                if path:
                    print(f"  Downloaded: {path}")
        except Exception as exc:
            failures += 1
            print(f"  Failed: {exc}")

    print(f"\nComplete. Trusts without a selected download: {failures}")
    return 1 if failures == len(trusts) else 0


if __name__ == "__main__":
    sys.exit(main())
