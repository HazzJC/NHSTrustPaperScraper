import asyncio
import csv
import datetime
import io
import json
import os
import queue
import threading
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from crawler.crawler import AdvancedCrawler
from scraper.constants import REPORT_TYPES
from scraper.engine import ScraperEngine, parse_types
from scraper.national_engine import NationalFetchEngine
from utils.config import Config
from utils.pdf_analyzer import PDFAnalyzer
from utils.results_store import ResultsStore
from intelligence.database import (
    BoardPaper,
    Opportunity,
    ProcurementSignal,
    TimelineEvent,
    TrustProfile,
    TrustRecord,
    get_session,
)
from intelligence.runner import get_job as get_intel_job, scan_downloads
from intelligence.embeddings import answer_question, index_paper
from intelligence.matching import generate_pitch, match_supplier

app = Flask(__name__)
app.config["SECRET_KEY"] = Config.SECRET_KEY

# ------------------------------------------------------------------
# Core singletons
# ------------------------------------------------------------------
DATA_DIR = Config.DATA_DIR
os.makedirs(DATA_DIR, exist_ok=True)

RESULTS_FILE = os.path.join(DATA_DIR, "board_papers.json")
results_store = ResultsStore(RESULTS_FILE)

GEMINI_API_KEY: str | None = Config.GEMINI_API_KEY
pdf_analyzer: PDFAnalyzer | None = None

scraper_engine = ScraperEngine(
    trusts_path=Path("config/mental_health_trusts.json"),
    icb_path=Path("config/icb_config.json"),
    output_dir=Path("downloads"),
    max_pages=int(os.getenv("MAX_PAGES_PER_SITE", "60")),
    timeout=30,
    crawl_delay=0.5,
    verify_ssl=False,
)

national_engine = NationalFetchEngine(output_dir=Path("downloads"))

# AdvancedCrawler kept for /test-specific-urls (ad-hoc URL crawling)
_adv_crawler = AdvancedCrawler()

# Lock for config file writes (in-process protection; one writer at a time)
_config_lock = threading.Lock()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _get_pdf_analyzer() -> PDFAnalyzer:
    global pdf_analyzer
    if pdf_analyzer is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is required for PDF analysis.")
        pdf_analyzer = PDFAnalyzer(GEMINI_API_KEY)
    return pdf_analyzer


def _apply_metadata(
    paper: dict, results: dict, keys=("title", "date", "organization")
) -> None:
    for k in keys:
        v = results.get(k)
        if v and v != "Unknown":
            paper[k] = v


def _extract_valid_term_data(
    raw_term_data: dict[str, dict[str, Any]],
    analyzer: PDFAnalyzer,
) -> dict[str, dict[str, list[str]]]:
    cleaned = {}
    for term, entry in raw_term_data.items():
        quotes = entry.get("quotes", [])
        if isinstance(quotes, str):
            quotes = [quotes]
        summaries = entry.get("summaries", [])
        if isinstance(summaries, str):
            summaries = [summaries]

        cleaned_quotes = [
            analyzer.clean_repetitive_text(q)
            for q in quotes
            if q and len(q.strip()) > 10
        ]
        cleaned_summaries = [
            analyzer.clean_repetitive_text(s)
            for s in summaries
            if s and len(s.strip()) > 10
        ]

        if cleaned_quotes and cleaned_summaries:
            cleaned[term] = {
                "quotes": cleaned_quotes,
                "summaries": cleaned_summaries,
            }
    return cleaned


def analyze_full_paper(paper: dict, analyzer: PDFAnalyzer) -> dict:
    results = analyzer.analyze_pdf_url(paper["url"])
    print(f"  - Found terms: {', '.join(results.get('terms_found', []))}")
    _apply_metadata(paper, results)
    raw_terms_data = results.get("terms_data", {})
    clean_terms_data = _extract_valid_term_data(raw_terms_data, analyzer)
    valid_terms = list(clean_terms_data)
    return {
        "has_relevant_terms": len(valid_terms) > 0,
        "terms_found": valid_terms,
        "terms_count": len(valid_terms),
        "terms_data": clean_terms_data,
    }


def _build_analysis_response(paper: dict, source: str) -> dict:
    return {
        "url": paper.get("url"),
        "title": paper.get("title", "Unknown"),
        "has_relevant_terms": paper.get("has_relevant_terms", False),
        "terms_found": paper.get("terms_found", []),
        "terms_count": paper.get("terms_count", 0),
        "terms_data": paper.get("terms_data", {}),
        "date": paper.get("date", "Unknown"),
        "organization": paper.get("organization", "Unknown"),
        "analysis_source": source,
    }


def update_paper_analysis(url: str, analysis: dict) -> None:
    for paper in results_store.board_papers:
        if paper["url"] != url:
            continue
        analyzer = _get_pdf_analyzer()
        terms_data = _extract_valid_term_data(analysis.get("terms_data", {}), analyzer)
        paper.update(
            {
                "has_relevant_terms": bool(terms_data),
                "terms_found": list(terms_data),
                "terms_count": len(terms_data),
                "terms_data": terms_data,
            }
        )
        _apply_metadata(paper, analysis)
        paper["sort_date"] = paper.get("date", "")
        break


def create_paper_dict(
    paper: dict[str, Any],
    org_url: str,
    existing_papers: set[tuple[str, str]],
) -> dict[str, Any]:
    if not isinstance(paper, dict) or "url" not in paper:
        print(paper)
        return {}

    title = paper.get("title", "Unknown")
    filename = title if title != "Unknown" else paper["url"].rsplit("/", 1)[-1]
    date = paper.get("date", "Unknown")
    sort_date = date if date != "Unknown" else "9999-99"
    trust = paper.get("trust", org_url)
    organization = paper.get("organization", "Unknown")
    is_new = (paper["url"], title) not in existing_papers

    return {
        "url": paper["url"],
        "filename": filename,
        "title": title,
        "date": date,
        "trust": trust,
        "organization": organization,
        "has_relevant_terms": False,
        "terms_found": [],
        "terms_count": 0,
        "terms_data": {},
        "is_new": is_new,
        "found_date": datetime.datetime.now().isoformat(),
        "sort_date": sort_date,
    }


async def process_organization(
    url: str,
    existing_papers: set[tuple[str, str]],
    scrape_only: bool = False,
) -> list[dict]:
    print(f"\nProcessing organization: {url}")
    papers = await _adv_crawler.deep_crawl(url)
    org_papers = []
    for raw in papers:
        paper = create_paper_dict(raw, url, existing_papers)
        if not paper:
            continue

        if GEMINI_API_KEY and not scrape_only:
            try:
                analyzer = _get_pdf_analyzer()
                date = analyzer.extract_date_only(paper["url"], keep_temp_file=True)
                paper["date"] = date
                if not analyzer.is_from_2024_or_later(date):
                    print(f"Skipping pre-2024 paper: {date}")
                    continue
                analysis = analyze_full_paper(paper, analyzer)
                _apply_metadata(paper, analysis)
                paper.update(
                    {
                        "has_relevant_terms": analysis["has_relevant_terms"],
                        "terms_found": analysis["terms_found"],
                        "terms_count": analysis["terms_count"],
                        "terms_data": analysis["terms_data"],
                    }
                )
            except Exception as exc:
                print(f"  Analysis failed for {paper['url']}: {exc}")

        org_papers.append(paper)

    print(f"Found {len(org_papers)} papers for {url}")
    return org_papers


async def _crawl_urls(urls: list[str], scrape_only: bool = False) -> list[dict]:
    existing_papers = {(p["url"], p["title"]) for p in results_store.board_papers}
    all_papers: list[dict] = []
    for url in urls:
        org_papers = await process_organization(url, existing_papers, scrape_only)
        all_papers.extend(org_papers)
        results_store.update(all_papers)
        print(f"Saved results after processing {url}")
    return all_papers


# ------------------------------------------------------------------
# Existing routes (unchanged)
# ------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", results=results_store.board_papers)


@app.route("/results")
def view_results():
    return jsonify(results_store.board_papers)


@app.route("/analyze-papers", methods=["POST"])
def analyze_papers() -> Response:
    data = request.get_json() or {}
    urls = data.get("urls", [])
    if not urls:
        return jsonify({"error": "No URLs provided", "success": False}), 400
    try:
        analyzer = _get_pdf_analyzer()
    except RuntimeError as exc:
        return jsonify({"error": str(exc), "success": False}), 503

    current_papers = {p["url"]: p for p in results_store.board_papers}
    results = []

    for url in urls:
        paper = current_papers.get(url)
        try:
            if paper and paper.get("terms_count", 0) > 0:
                results.append(_build_analysis_response(paper, "cached"))
                print(f"Using cached analysis for {url}")
                continue

            print(f"Checking date for: {url}")
            date = analyzer.extract_date_only(url, keep_temp_file=True)
            if not analyzer.is_from_2024_or_later(date):
                print(f"Skipping pre-2024 paper: {date}")
                continue

            print(f"Analyzing {url} for healthcare terms")
            analysis = analyze_full_paper({"url": url, **(paper or {})}, analyzer)

            if paper:
                update_paper_analysis(url, analysis)
                updated = paper
            else:
                new_paper = {"url": url, **analysis}
                _apply_metadata(new_paper, analysis)
                new_paper["sort_date"] = new_paper.get("date", "")
                results_store.board_papers.append(new_paper)
                current_papers[url] = new_paper
                updated = new_paper

            results_store.update(results_store.board_papers)
            results.append(_build_analysis_response(updated, "new"))

        except Exception:
            err = paper or {}
            results.append(
                {
                    "url": url,
                    "title": err.get("title", "Unknown"),
                    "has_relevant_terms": False,
                    "terms_found": [],
                    "terms_count": 0,
                    "terms_data": {},
                    "date": err.get("date", "Unknown"),
                    "organization": err.get("organization", "Unknown"),
                    "analysis_source": "error",
                }
            )

    return jsonify({"results": results, "success": True})


@app.route("/test-specific-urls", methods=["POST"])
def test_specific_urls():
    data = request.get_json()
    urls = data.get("urls", [])
    scrape_only = data.get("scrape_only", False)
    try:
        loop = asyncio.new_event_loop()
        papers = loop.run_until_complete(_crawl_urls(urls, scrape_only=scrape_only))
        loop.close()
    except RuntimeError as exc:
        return jsonify({"error": str(exc), "status": "error"}), 503
    return jsonify(results={"board_papers": papers}, status="success")


# ------------------------------------------------------------------
# New scraper routes
# ------------------------------------------------------------------
@app.route("/scrape/trusts")
def scrape_trusts():
    return jsonify(scraper_engine.list_trusts())


@app.route("/scrape/icbs")
def scrape_icbs():
    return jsonify(scraper_engine.list_icbs())


@app.route("/scrape/start", methods=["POST"])
def scrape_start():
    data = request.get_json() or {}
    trust_names = data.get("trust_names")  # None = all 47
    raw_types = data.get("types", ["board"])
    all_matches = bool(data.get("all_matches", False))
    limit_per_type = int(data.get("limit_per_type", 1))
    dry_run = bool(data.get("dry_run", False))
    parallel_trusts = max(1, min(int(data.get("parallel_trusts", 5)), 10))
    max_pages = max(10, min(int(data.get("max_pages", 60)), 200))
    crawl_delay = max(0.0, min(float(data.get("crawl_delay", 0.5)), 5.0))
    ignore_cache = bool(data.get("ignore_cache", False))
    verbose = bool(data.get("verbose", False))

    # date_filters: {type: months_lookback, 0=no_limit}
    raw_filters = data.get("date_filters") or {}
    date_filters = {k: int(v) for k, v in raw_filters.items() if isinstance(v, (int, float, str))}

    if isinstance(raw_types, list):
        selected_types = set(raw_types)
    else:
        selected_types = {t.strip() for t in str(raw_types).split(",") if t.strip()}

    unknown = selected_types - set(REPORT_TYPES.keys())
    if unknown:
        return jsonify({"error": f"Unknown types: {unknown}", "success": False}), 400

    source = data.get("source", "trust")
    if source not in ("trust", "icb"):
        return jsonify({"error": "source must be 'trust' or 'icb'"}), 400

    job = scraper_engine.start_job(
        trust_names=trust_names,
        selected_types=selected_types,
        all_matches=all_matches,
        limit_per_type=limit_per_type,
        dry_run=dry_run,
        date_filters=date_filters,
        parallel_trusts=parallel_trusts,
        max_pages=max_pages,
        crawl_delay=crawl_delay,
        ignore_cache=ignore_cache,
        verbose=verbose,
        source=source,
    )
    return jsonify({"job_id": job.job_id, "status": job.status, "trust_count": job.total_trusts})


@app.route("/scrape/status/<job_id>")
def scrape_status(job_id: str):
    job = scraper_engine.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job.to_status_dict())


@app.route("/scrape/stream/<job_id>")
def scrape_stream(job_id: str):
    job = scraper_engine.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    def generate():
        while True:
            try:
                msg = job.log_queue.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            yield f"data: {json.dumps(msg)}\n\n"
            if msg.get("event") in ("done", "error", "cancelled"):
                break

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@app.route("/scrape/cancel/<job_id>", methods=["DELETE"])
def scrape_cancel(job_id: str):
    cancelled = scraper_engine.cancel_job(job_id)
    if not cancelled:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({"job_id": job_id, "status": "cancelled"})


@app.route("/scrape/results/<job_id>")
def scrape_results(job_id: str):
    job = scraper_engine.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job.summary_table())


@app.route("/scrape/export/<job_id>")
def scrape_export(job_id: str):
    """Download a CSV summary of a completed scrape job."""
    from scraper.constants import REPORT_TYPES
    job = scraper_engine.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    rows = job.summary_table()
    if not rows:
        return jsonify({"error": "No results to export"}), 404

    type_labels = {
        "board": "Board Papers",
        "supplementary": "Supplementary",
        "strategy": "Strategic Reporting",
        "digital_strategy": "Digital Strategy",
        "quality_account": "Quality Account",
        "annual_report": "Annual Report",
        "cqc_report": "CQC Report",
        "joint_forward_plan": "Joint Forward Plan",
        "icb_mh_strategy": "ICB MH Strategy",
        "integrated_care_strategy": "Integrated Care Strategy",
    }
    all_keys = list(REPORT_TYPES.keys())
    active_cols = [k for k in all_keys if any(isinstance(r.get(k), list) and r[k] for r in rows)]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Organisation"] + [type_labels.get(k, k) for k in active_cols] + ["Total Found"])
    for row in rows:
        total = sum(len(row[k]) for k in active_cols if isinstance(row.get(k), list))
        cells = [row["trust"]]
        for k in active_cols:
            v = row.get(k)
            cells.append("; ".join(v) if isinstance(v, list) and v else "—")
        cells.append(str(total))
        writer.writerow(cells)

    output.seek(0)
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="scrape-summary-{date_str}.csv"'},
    )


@app.route("/scrape/failures")
def scrape_failures():
    """Return failure cache entries enriched with source type (trust/icb)."""
    trust_names = {t.name for t in scraper_engine._trusts}
    icb_names = {t.name for t in scraper_engine._icbs}
    entries = []
    for entry in scraper_engine._failure_cache.get_all():
        name = entry["name"]
        if name in trust_names:
            entry["source"] = "trust"
        elif name in icb_names:
            entry["source"] = "icb"
        else:
            entry["source"] = "unknown"
        entries.append(entry)
    entries.sort(key=lambda x: -x.get("consecutive", 0))
    return jsonify(entries)


@app.route("/scrape/failures/clear-all", methods=["DELETE"])
def scrape_failures_clear_all():
    count = scraper_engine._failure_cache.clear_all()
    return jsonify({"success": True, "cleared": count})


@app.route("/scrape/failures/<path:org_name>", methods=["DELETE"])
def scrape_failure_delete(org_name: str):
    removed = scraper_engine._failure_cache.remove(org_name)
    if not removed:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"success": True, "name": org_name})


# ------------------------------------------------------------------
# Config / org editor routes
# ------------------------------------------------------------------

@app.route("/config/org")
def config_org_get():
    name = request.args.get("name", "").strip()
    source = request.args.get("source", "trust")
    if not name:
        return jsonify({"error": "name parameter required"}), 400
    pool = scraper_engine._trusts if source == "trust" else scraper_engine._icbs
    trust = next((t for t in pool if t.name == name), None)
    if not trust:
        return jsonify({"error": f"Org '{name}' not found in {source} list"}), 404
    return jsonify({
        "name": trust.name,
        "url": trust.url,
        "start_urls": list(trust.start_urls),
        "allowed_domains": list(trust.allowed_domains),
        "search_query": trust.search_query,
        "js_render": trust.js_render,
    })


@app.route("/config/org", methods=["PUT"])
def config_org_update():
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    source = data.get("source", "trust")
    if not name:
        return jsonify({"error": "name required"}), 400
    config_path = (
        Path("config/mental_health_trusts.json") if source == "trust"
        else Path("config/icb_config.json")
    )
    with _config_lock:
        entries = json.loads(config_path.read_text(encoding="utf-8"))
        idx = next((i for i, e in enumerate(entries) if e.get("name") == name), None)
        if idx is None:
            return jsonify({"error": f"Org '{name}' not found in {config_path.name}"}), 404
        updated = {
            "name": name,
            "url": data.get("url") or entries[idx].get("url"),
            "start_urls": data.get("start_urls") or entries[idx].get("start_urls", []),
        }
        allowed = data.get("allowed_domains")
        if allowed:
            updated["allowed_domains"] = allowed
        elif entries[idx].get("allowed_domains"):
            updated["allowed_domains"] = entries[idx]["allowed_domains"]
        # Preserve any other fields (e.g. icb_name, search_query, js_render)
        for k, v in entries[idx].items():
            if k not in updated:
                updated[k] = v
        entries[idx] = updated
        config_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    scraper_engine.reload_config()
    return jsonify({"success": True, "name": name})


@app.route("/config/org", methods=["POST"])
def config_org_add():
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    source = data.get("source", "trust")
    url = data.get("url", "").strip()
    start_urls = data.get("start_urls", [])
    if not name:
        return jsonify({"error": "name required"}), 400
    if not url:
        return jsonify({"error": "url required"}), 400
    config_path = (
        Path("config/mental_health_trusts.json") if source == "trust"
        else Path("config/icb_config.json")
    )
    with _config_lock:
        entries = json.loads(config_path.read_text(encoding="utf-8"))
        if any(e.get("name") == name for e in entries):
            return jsonify({"error": f"Org '{name}' already exists"}), 409
        new_entry: dict = {"name": name, "url": url, "start_urls": start_urls}
        allowed = data.get("allowed_domains")
        if allowed:
            new_entry["allowed_domains"] = allowed
        entries.append(new_entry)
        config_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    scraper_engine.reload_config()
    return jsonify({"success": True, "name": name}), 201


# ------------------------------------------------------------------
# National dataset routes
# ------------------------------------------------------------------
@app.route("/national/sources")
def national_sources():
    return jsonify(national_engine.list_sources())


@app.route("/national/fetch", methods=["POST"])
def national_fetch():
    data = request.get_json() or {}
    source_keys = data.get("source_keys") or None  # None = fetch all
    try:
        job = national_engine.start_job(source_keys=source_keys)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"job_id": job.job_id, "status": job.status})


@app.route("/national/status/<job_id>")
def national_status(job_id: str):
    job = national_engine.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "job_id": job.job_id,
        "status": job.status,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "results": [r.to_dict() for r in job.results],
    })


@app.route("/national/stream/<job_id>")
def national_stream(job_id: str):
    job = national_engine.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    return Response(
        stream_with_context(job.iter_sse()),
        content_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# Backward-compatible redirect: old /run-crawler button now starts all-trusts scrape
@app.route("/run-crawler", methods=["POST"])
def run_crawler_route():
    job = scraper_engine.start_job(
        trust_names=None,
        selected_types={"board"},
    )
    return jsonify({"success": True, "job_id": job.job_id, "message":
                    "Use /scrape/stream/{job_id} to watch progress."})


# ------------------------------------------------------------------
# Intelligence Platform routes
# ------------------------------------------------------------------

def _require_gemini():
    if not GEMINI_API_KEY:
        return jsonify({"error": "GEMINI_API_KEY is not set"}), 503
    return None


@app.route("/intelligence")
def intelligence_index():
    return render_template("intelligence.html")


@app.route("/intelligence/run", methods=["POST"])
def intelligence_run():
    err = _require_gemini()
    if err:
        return err
    downloads_dir = Path("downloads")
    if not downloads_dir.exists():
        return jsonify({"error": "downloads/ directory not found"}), 404
    job = scan_downloads(GEMINI_API_KEY, downloads_dir)
    return jsonify({"job_id": job.job_id, "status": job.status})


@app.route("/intelligence/status/<job_id>")
def intelligence_status(job_id: str):
    job = get_intel_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job.to_dict())


@app.route("/intelligence/stream/<job_id>")
def intelligence_stream(job_id: str):
    job = get_intel_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    def generate():
        while True:
            try:
                msg = job.log_queue.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            yield f"data: {json.dumps(msg)}\n\n"
            if msg.get("event") in ("complete", "error"):
                break

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@app.route("/intelligence/trusts")
def intelligence_trusts():
    with get_session() as session:
        trusts = session.query(TrustRecord).all()
        return jsonify([
            {
                "id": t.id,
                "name": t.name,
                "url": t.url,
                "paper_count": len(t.papers),
                "opportunity_count": len(t.opportunities),
                "signal_count": len(t.procurement_signals),
                "has_profile": t.profile is not None,
                "last_scraped_at": t.last_scraped_at.isoformat() if t.last_scraped_at else None,
            }
            for t in trusts
        ])


@app.route("/intelligence/trust/<path:trust_name>")
def intelligence_trust(trust_name: str):
    with get_session() as session:
        trust = session.query(TrustRecord).filter_by(name=trust_name).first()
        if not trust:
            return jsonify({"error": "Trust not found"}), 404

        profile = trust.profile
        opportunities = (
            session.query(Opportunity)
            .filter_by(trust_id=trust.id)
            .order_by(Opportunity.confidence.desc())
            .limit(25)
            .all()
        )
        signals = (
            session.query(ProcurementSignal)
            .filter_by(trust_id=trust.id)
            .order_by(ProcurementSignal.confidence.desc())
            .limit(15)
            .all()
        )
        timeline = (
            session.query(TimelineEvent)
            .filter_by(trust_id=trust.id)
            .order_by(TimelineEvent.confidence.desc())
            .limit(15)
            .all()
        )
        papers = (
            session.query(BoardPaper)
            .filter_by(trust_id=trust.id)
            .order_by(BoardPaper.paper_date.desc())
            .all()
        )

        return jsonify({
            "trust": {"id": trust.id, "name": trust.name, "url": trust.url},
            "profile": {
                "digital_summary": profile.digital_summary if profile else None,
                "priorities_summary": profile.priorities_summary if profile else None,
                "challenges_summary": profile.challenges_summary if profile else None,
                "financial_summary": profile.financial_summary if profile else None,
                "ai_opportunities_summary": profile.ai_opportunities_summary if profile else None,
                "procurement_summary": profile.procurement_summary if profile else None,
                "updated_at": profile.updated_at.isoformat() if profile and profile.updated_at else None,
            },
            "opportunities": [
                {
                    "category": o.category,
                    "description": o.description,
                    "confidence": round(o.confidence, 2),
                    "budget_confidence": round(o.budget_confidence, 2),
                    "evidence_quote": o.evidence_quote,
                    "page_ref": o.page_ref,
                }
                for o in opportunities
            ],
            "procurement_signals": [
                {
                    "signal_type": s.signal_type,
                    "description": s.description,
                    "confidence": round(s.confidence, 2),
                    "evidence_quote": s.evidence_quote,
                    "page_ref": s.page_ref,
                }
                for s in signals
            ],
            "timeline": [
                {
                    "date_text": e.date_text,
                    "programme": e.programme,
                    "milestone": e.milestone,
                    "confidence": round(e.confidence, 2),
                    "evidence_quote": e.evidence_quote,
                }
                for e in timeline
            ],
            "papers": [
                {
                    "title": p.title,
                    "paper_date": p.paper_date,
                    "report_type": p.report_type,
                    "page_count": p.page_count,
                    "file_path": p.file_path,
                }
                for p in papers
            ],
        })


@app.route("/intelligence/opportunities")
def intelligence_opportunities():
    category = request.args.get("category")
    trust_name = request.args.get("trust")
    min_confidence = float(request.args.get("min_confidence", "0"))

    with get_session() as session:
        q = session.query(Opportunity, TrustRecord.name).join(
            TrustRecord, Opportunity.trust_id == TrustRecord.id
        )
        if category:
            q = q.filter(Opportunity.category.ilike(f"%{category}%"))
        if trust_name:
            q = q.filter(TrustRecord.name.ilike(f"%{trust_name}%"))
        q = q.filter(Opportunity.confidence >= min_confidence)
        q = q.order_by(Opportunity.confidence.desc()).limit(100)

        return jsonify([
            {
                "trust_name": name,
                "category": o.category,
                "description": o.description,
                "confidence": round(o.confidence, 2),
                "evidence_quote": o.evidence_quote,
                "page_ref": o.page_ref,
            }
            for o, name in q.all()
        ])


@app.route("/intelligence/search")
def intelligence_search():
    q_text = request.args.get("q", "").strip()
    if not q_text:
        return jsonify({"error": "q parameter required"}), 400

    # Keyword search across opportunity descriptions and insight summaries
    with get_session() as session:
        opps = (
            session.query(Opportunity, TrustRecord.name)
            .join(TrustRecord, Opportunity.trust_id == TrustRecord.id)
            .filter(Opportunity.description.ilike(f"%{q_text}%"))
            .order_by(Opportunity.confidence.desc())
            .limit(20)
            .all()
        )
        signals = (
            session.query(ProcurementSignal, TrustRecord.name)
            .join(TrustRecord, ProcurementSignal.trust_id == TrustRecord.id)
            .filter(ProcurementSignal.description.ilike(f"%{q_text}%"))
            .order_by(ProcurementSignal.confidence.desc())
            .limit(10)
            .all()
        )

        return jsonify({
            "query": q_text,
            "opportunities": [
                {"trust_name": name, "category": o.category, "description": o.description, "confidence": round(o.confidence, 2)}
                for o, name in opps
            ],
            "procurement_signals": [
                {"trust_name": name, "signal_type": s.signal_type, "description": s.description, "confidence": round(s.confidence, 2)}
                for s, name in signals
            ],
        })


@app.route("/intelligence/ask")
def intelligence_ask():
    err = _require_gemini()
    if err:
        return err
    question = request.args.get("q", "").strip()
    if not question:
        return jsonify({"error": "q parameter required"}), 400
    result = answer_question(question, GEMINI_API_KEY)
    return jsonify(result)


@app.route("/match-supplier", methods=["POST"])
def match_supplier_route():
    err = _require_gemini()
    if err:
        return err
    data = request.get_json() or {}
    capabilities = data.get("capabilities_text", "").strip()
    if not capabilities:
        return jsonify({"error": "capabilities_text required"}), 400
    top_n = int(data.get("top_n", 10))
    results = match_supplier(capabilities, GEMINI_API_KEY, top_n=top_n)
    return jsonify({"matches": results})


@app.route("/generate-pitch", methods=["POST"])
def generate_pitch_route():
    err = _require_gemini()
    if err:
        return err
    data = request.get_json() or {}
    trust_name = data.get("trust_name", "").strip()
    capabilities = data.get("capabilities_text", "").strip()
    if not trust_name or not capabilities:
        return jsonify({"error": "trust_name and capabilities_text required"}), 400
    result = generate_pitch(trust_name, capabilities, GEMINI_API_KEY)
    return jsonify(result)


if __name__ == "__main__":
    if GEMINI_API_KEY:
        print(f"Using Gemini API key starting with: {GEMINI_API_KEY[:8]}...")
    else:
        print("GEMINI_API_KEY is not set; analysis routes will return 503.")
    app.run(debug=True, host="0.0.0.0", port=5002)
