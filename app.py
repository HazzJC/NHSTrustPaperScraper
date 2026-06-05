import asyncio
import datetime
import json
import os
import queue
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from crawler.crawler import AdvancedCrawler
from scraper.engine import ScraperEngine, parse_types
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
    output_dir=Path("downloads"),
    max_pages=int(os.getenv("MAX_PAGES_PER_SITE", "60")),
    timeout=30,
    crawl_delay=0.5,
    verify_ssl=False,
)

# AdvancedCrawler kept for /test-specific-urls (ad-hoc URL crawling)
_adv_crawler = AdvancedCrawler()


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


@app.route("/scrape/start", methods=["POST"])
def scrape_start():
    data = request.get_json() or {}
    trust_names = data.get("trust_names")  # None = all 47
    raw_types = data.get("types", ["board"])
    all_matches = bool(data.get("all_matches", False))
    limit_per_type = int(data.get("limit_per_type", 1))
    dry_run = bool(data.get("dry_run", False))

    if isinstance(raw_types, list):
        selected_types = set(raw_types)
    else:
        selected_types = {t.strip() for t in str(raw_types).split(",") if t.strip()}

    unknown = selected_types - {"board", "supplementary", "strategy", "digital_strategy"}
    if unknown:
        return jsonify({"error": f"Unknown types: {unknown}", "success": False}), 400

    job = scraper_engine.start_job(
        trust_names=trust_names,
        selected_types=selected_types,
        all_matches=all_matches,
        limit_per_type=limit_per_type,
        dry_run=dry_run,
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
