"""Intelligence extraction pipeline.

Reads a downloaded PDF, extracts full text, segments it into chunks, sends
each chunk to Gemini for structured extraction (opportunities, procurement
signals, timeline events, strategic priorities, challenges), deduplicates
across chunks, and persists everything to the SQLite database.

Uses gemini-2.5-flash throughout:
  - thinking_budget=0 for per-chunk passes (speed/cost)
  - thinking_budget=1024 for trust profile synthesis (quality)
"""
from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types as genai_types
from pypdf import PdfReader

from intelligence.database import (
    BoardPaper,
    ExtractedInsight,
    Opportunity,
    ProcurementSignal,
    TimelineEvent,
    TrustProfile,
    TrustRecord,
    get_or_create_trust,
    get_session,
)

_CHUNK_CHARS = 3_000
_MODEL = "gemini-2.5-flash"
_EXTRACT_SYSTEM = textwrap.dedent("""\
    You are an expert NHS procurement intelligence analyst. Extract structured
    intelligence from NHS board paper excerpts. Return ONLY valid JSON.
""")
_CHUNK_PROMPT = textwrap.dedent("""\
    Extract intelligence from this NHS board paper excerpt (pages {pages}).
    Return JSON with these keys (use empty lists/arrays if nothing found):
    {{
      "opportunities": [
        {{"category": "Digital Transformation|Workforce|AI|Infrastructure|Estates|Patient Flow|Other",
          "description": "...", "evidence_quote": "...", "confidence": 0-100}}
      ],
      "procurement_signals": [
        {{"signal_type": "high_intent|medium_intent|early_stage",
          "description": "...", "evidence_quote": "...", "confidence": 0-100}}
      ],
      "timeline_events": [
        {{"date_text": "...", "programme": "...", "milestone": "...",
          "confidence": 0-100, "evidence_quote": "..."}}
      ],
      "priorities": ["..."],
      "challenges": ["..."],
      "digital_initiatives": ["..."],
      "financial_items": ["..."]
    }}

    EXCERPT:
    {text}
""")
_PROFILE_PROMPT = textwrap.dedent("""\
    You are an NHS procurement intelligence analyst writing a Trust intelligence
    profile for sales teams. Given the aggregated intelligence below, write
    concise paragraphs (3-5 sentences each) for each section.
    Return JSON with keys:
    {{
      "digital_summary": "...",
      "priorities_summary": "...",
      "challenges_summary": "...",
      "financial_summary": "...",
      "ai_opportunities_summary": "...",
      "procurement_summary": "..."
    }}

    TRUST: {trust_name}

    OPPORTUNITIES (top 20):
    {opportunities}

    PROCUREMENT SIGNALS:
    {signals}

    STRATEGIC PRIORITIES:
    {priorities}

    CHALLENGES:
    {challenges}

    DIGITAL INITIATIVES:
    {digital}

    FINANCIAL ITEMS:
    {financial}
""")


def _extract_text_by_page(pdf_path: Path) -> list[tuple[int, str]]:
    """Return list of (page_number, page_text) for every page in the PDF."""
    reader = PdfReader(str(pdf_path))
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
            pages.append((i, text))
        except Exception:
            pages.append((i, ""))
    return pages


def _chunk_pages(pages: list[tuple[int, str]]) -> list[tuple[list[int], str]]:
    """Group pages into ~CHUNK_CHARS character chunks. Returns (page_nums, text)."""
    chunks: list[tuple[list[int], str]] = []
    current_pages: list[int] = []
    current_text = ""
    for page_num, text in pages:
        if current_text and len(current_text) + len(text) > _CHUNK_CHARS:
            chunks.append((current_pages[:], current_text))
            current_pages = []
            current_text = ""
        current_pages.append(page_num)
        current_text += text
    if current_text:
        chunks.append((current_pages, current_text))
    return chunks


def _call_gemini_json(
    client: genai.Client,
    prompt: str,
    *,
    thinking_budget: int = 0,
) -> dict[str, Any]:
    config_kwargs: dict[str, Any] = {
        "system_instruction": _EXTRACT_SYSTEM,
        "response_mime_type": "application/json",
    }
    if thinking_budget >= 0:
        config_kwargs["thinking_config"] = genai_types.ThinkingConfig(
            thinking_budget=thinking_budget
        )
    try:
        response = client.models.generate_content(
            model=_MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(**config_kwargs),
        )
        text = response.text or "{}"
        # Strip markdown fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text.strip())
        text = re.sub(r"\s*```$", "", text.strip())
        return json.loads(text)
    except Exception:
        return {}


def _dedup_list(items: list[dict], key: str = "description") -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for item in items:
        k = (item.get(key) or "").strip().lower()[:120]
        if k and k not in seen:
            seen.add(k)
            result.append(item)
    return result


def _dedup_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        k = item.strip().lower()[:120]
        if k and k not in seen:
            seen.add(k)
            result.append(item)
    return result


class PipelineRunner:
    def __init__(self, gemini_api_key: str, db_path: Path | None = None):
        self._client = genai.Client(api_key=gemini_api_key)
        self._db_path = db_path

    def _session(self):
        return get_session(self._db_path)

    def process_paper(self, pdf_path: Path, trust_name: str, trust_url: str | None = None) -> int:
        """Extract and persist intelligence from one PDF. Returns the paper DB id."""
        with self._session() as session:
            existing = session.query(BoardPaper).filter_by(file_path=str(pdf_path)).first()
            if existing and existing.full_text:
                return existing.id

            trust = get_or_create_trust(session, trust_name, trust_url)
            paper = existing or BoardPaper(
                trust_id=trust.id,
                file_path=str(pdf_path),
                title=pdf_path.stem,
            )
            if not existing:
                session.add(paper)
            session.flush()
            paper_id = paper.id

            pages = _extract_text_by_page(pdf_path)
            full_text = "\n".join(t for _, t in pages)
            paper.full_text = full_text
            paper.page_count = len(pages)
            session.commit()

        self._extract_intelligence(pdf_path, paper_id, pages, trust_name)
        self._generate_trust_profile(trust_name)
        return paper_id

    def _extract_intelligence(
        self,
        pdf_path: Path,
        paper_id: int,
        pages: list[tuple[int, str]],
        trust_name: str,
    ) -> None:
        chunks = _chunk_pages(pages)
        all_opportunities: list[dict] = []
        all_signals: list[dict] = []
        all_timelines: list[dict] = []
        all_priorities: list[str] = []
        all_challenges: list[str] = []
        all_digital: list[str] = []
        all_financial: list[str] = []

        for page_nums, chunk_text in chunks:
            page_label = f"{page_nums[0]}-{page_nums[-1]}" if len(page_nums) > 1 else str(page_nums[0])
            prompt = _CHUNK_PROMPT.format(pages=page_label, text=chunk_text[:_CHUNK_CHARS])
            result = _call_gemini_json(self._client, prompt, thinking_budget=0)
            first_page = page_nums[0] if page_nums else None

            for opp in result.get("opportunities", []):
                opp["_page"] = first_page
                all_opportunities.append(opp)
            for sig in result.get("procurement_signals", []):
                sig["_page"] = first_page
                all_signals.append(sig)
            for evt in result.get("timeline_events", []):
                evt["_page"] = first_page
                all_timelines.append(evt)
            all_priorities.extend(result.get("priorities", []))
            all_challenges.extend(result.get("challenges", []))
            all_digital.extend(result.get("digital_initiatives", []))
            all_financial.extend(result.get("financial_items", []))

        with self._session() as session:
            trust = session.query(TrustRecord).filter_by(name=trust_name).first()
            trust_id = trust.id if trust else None

            for opp in _dedup_list(all_opportunities):
                session.add(Opportunity(
                    paper_id=paper_id,
                    trust_id=trust_id,
                    category=opp.get("category", "Other"),
                    description=opp.get("description", ""),
                    confidence=float(opp.get("confidence", 0)) / 100.0,
                    budget_confidence=0.0,
                    urgency=0.0,
                    evidence_quote=opp.get("evidence_quote"),
                    page_ref=opp.get("_page"),
                ))
            for sig in _dedup_list(all_signals):
                session.add(ProcurementSignal(
                    paper_id=paper_id,
                    trust_id=trust_id,
                    signal_type=sig.get("signal_type", "early_stage"),
                    description=sig.get("description", ""),
                    confidence=float(sig.get("confidence", 0)) / 100.0,
                    evidence_quote=sig.get("evidence_quote"),
                    page_ref=sig.get("_page"),
                ))
            for evt in _dedup_list(all_timelines, key="milestone"):
                session.add(TimelineEvent(
                    paper_id=paper_id,
                    trust_id=trust_id,
                    date_text=evt.get("date_text"),
                    programme=evt.get("programme"),
                    milestone=evt.get("milestone", ""),
                    confidence=float(evt.get("confidence", 0)) / 100.0,
                    evidence_quote=evt.get("evidence_quote"),
                ))
            for cat, items in [
                ("priorities", _dedup_strings(all_priorities)),
                ("challenges", _dedup_strings(all_challenges)),
                ("digital", _dedup_strings(all_digital)),
                ("financial", _dedup_strings(all_financial)),
            ]:
                for item in items:
                    session.add(ExtractedInsight(
                        paper_id=paper_id,
                        category=cat,
                        summary=item,
                        confidence=0.7,
                    ))
            session.commit()

    def _generate_trust_profile(self, trust_name: str) -> None:
        with self._session() as session:
            trust = session.query(TrustRecord).filter_by(name=trust_name).first()
            if not trust:
                return

            opps = (
                session.query(Opportunity)
                .filter_by(trust_id=trust.id)
                .order_by(Opportunity.confidence.desc())
                .limit(20)
                .all()
            )
            sigs = (
                session.query(ProcurementSignal)
                .filter_by(trust_id=trust.id)
                .order_by(ProcurementSignal.confidence.desc())
                .limit(10)
                .all()
            )
            insights = session.query(ExtractedInsight).join(BoardPaper).filter(
                BoardPaper.trust_id == trust.id
            ).all()

            priorities = [i.summary for i in insights if i.category == "priorities"][:20]
            challenges = [i.summary for i in insights if i.category == "challenges"][:10]
            digital = [i.summary for i in insights if i.category == "digital"][:15]
            financial = [i.summary for i in insights if i.category == "financial"][:10]

            prompt = _PROFILE_PROMPT.format(
                trust_name=trust_name,
                opportunities="\n".join(f"- [{o.category}] {o.description}" for o in opps),
                signals="\n".join(f"- [{s.signal_type}] {s.description}" for s in sigs),
                priorities="\n".join(f"- {p}" for p in priorities),
                challenges="\n".join(f"- {c}" for c in challenges),
                digital="\n".join(f"- {d}" for d in digital),
                financial="\n".join(f"- {f}" for f in financial),
            )

        result = _call_gemini_json(self._client, prompt, thinking_budget=1024)
        if not result:
            return

        with self._session() as session:
            trust = session.query(TrustRecord).filter_by(name=trust_name).first()
            if not trust:
                return
            profile = trust.profile or TrustProfile(trust_id=trust.id)
            if not trust.profile:
                session.add(profile)
            profile.digital_summary = result.get("digital_summary")
            profile.priorities_summary = result.get("priorities_summary")
            profile.challenges_summary = result.get("challenges_summary")
            profile.financial_summary = result.get("financial_summary")
            profile.ai_opportunities_summary = result.get("ai_opportunities_summary")
            profile.procurement_summary = result.get("procurement_summary")
            session.commit()
