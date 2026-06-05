"""
CLI entry point for the NHS Trust paper scraper.

All scraping logic lives in the scraper/ package. This file is a thin
wrapper that parses command-line arguments and delegates to ScraperEngine.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scraper.cache import DiscoveryCache
from scraper.constants import REPORT_TYPES
from scraper.discovery import apply_last_modified_dates, discover_candidates, selected_candidates
from scraper.downloader import download_candidate, slugify, safe_filename
from scraper.engine import ScraperEngine, load_trusts, parse_types
from scraper.extraction import extract_date
from scraper.models import Trust, Candidate
from scraper.session import build_session


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
        default="downloads",
        type=Path,
        help="Folder where downloaded files will be stored.",
    )
    parser.add_argument(
        "--types",
        default="board",
        help=(
            "Comma-separated report types to download: "
            "board, supplementary, strategy, digital_strategy, or all."
        ),
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
        help="Number of latest documents to download per report type.",
    )
    parser.add_argument(
        "--max-pages",
        default=60,
        type=int,
        help="Maximum pages to scan per trust.",
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
        help="Verify SSL certificates (disabled by default — some NHS sites have incomplete chains).",
    )
    parser.add_argument(
        "--crawl-delay",
        default=0.5,
        type=float,
        help="Seconds to wait between page requests (default: 0.5).",
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
    cache = DiscoveryCache()
    failures = 0

    print(f"Loaded {len(trusts)} trusts from {args.trusts}")
    print(f"Report types: {', '.join(sorted(selected_types))}")
    print(f"Output folder: {args.output}")

    for index, trust in enumerate(trusts, start=1):
        print(f"\n[{index}/{len(trusts)}] {trust.name}")
        try:
            cached = cache.get_pages(trust.name, selected_types)
            if cached:
                print(f"  Using {len(cached)} cached source page(s) from previous run")
            candidates = discover_candidates(
                session,
                trust,
                max_pages=args.max_pages,
                timeout=args.timeout,
                crawl_delay=args.crawl_delay,
                selected_types=selected_types,
                cached_pages=cached or None,
            )
            cache.update(trust.name, candidates)
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
