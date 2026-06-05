"""Supplier-to-Trust relevance matching and outreach generation (Phase 3)."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types as genai_types

from intelligence.database import (
    Opportunity,
    SupplierMatch,
    SupplierProfile,
    TrustProfile,
    TrustRecord,
    get_session,
)
from intelligence.embeddings import semantic_search

_MODEL = "gemini-2.5-flash"


def match_supplier(
    capabilities_text: str,
    gemini_api_key: str,
    top_n: int = 10,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Find trusts most relevant to a supplier's capabilities.

    1. Semantic search for chunks that overlap with capabilities
    2. Score trusts by hit count × similarity
    3. Return ranked list with supporting evidence
    """
    hits = semantic_search(capabilities_text, n_results=50)
    if not hits:
        return []

    # Aggregate score per trust
    trust_scores: dict[str, dict[str, Any]] = {}
    for hit in hits:
        name = hit["trust_name"]
        if name not in trust_scores:
            trust_scores[name] = {"score": 0.0, "evidence": [], "count": 0}
        trust_scores[name]["score"] += hit["similarity"]
        trust_scores[name]["count"] += 1
        if len(trust_scores[name]["evidence"]) < 3:
            trust_scores[name]["evidence"].append({
                "quote": hit["text"][:300],
                "date": hit["paper_date"],
            })

    ranked = sorted(
        trust_scores.items(),
        key=lambda kv: kv[1]["score"],
        reverse=True,
    )[:top_n]

    result = []
    with get_session(db_path) as session:
        for trust_name, data in ranked:
            trust = session.query(TrustRecord).filter_by(name=trust_name).first()
            profile = trust.profile if trust else None
            opportunities = (
                session.query(Opportunity)
                .filter_by(trust_id=trust.id)
                .order_by(Opportunity.confidence.desc())
                .limit(5)
                .all()
                if trust
                else []
            )
            result.append({
                "trust_name": trust_name,
                "relevance_score": round(data["score"] / max(data["count"], 1), 3),
                "hit_count": data["count"],
                "supporting_evidence": data["evidence"],
                "top_opportunities": [
                    {"category": o.category, "description": o.description, "confidence": o.confidence}
                    for o in opportunities
                ],
                "digital_summary": profile.digital_summary if profile else None,
            })
    return result


def generate_pitch(
    trust_name: str,
    capabilities_text: str,
    gemini_api_key: str,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Generate a sales pitch and email draft for a specific trust."""
    with get_session(db_path) as session:
        trust = session.query(TrustRecord).filter_by(name=trust_name).first()
        if not trust:
            return {"error": f"Trust '{trust_name}' not found in database."}

        profile = trust.profile
        opportunities = (
            session.query(Opportunity)
            .filter_by(trust_id=trust.id)
            .order_by(Opportunity.confidence.desc())
            .limit(8)
            .all()
        )
        opp_text = "\n".join(f"- [{o.category}] {o.description}" for o in opportunities)
        profile_text = "\n".join(filter(None, [
            f"Digital: {profile.digital_summary}" if profile and profile.digital_summary else "",
            f"Priorities: {profile.priorities_summary}" if profile and profile.priorities_summary else "",
            f"Procurement: {profile.procurement_summary}" if profile and profile.procurement_summary else "",
        ]))

    prompt = textwrap.dedent(f"""\
        You are an NHS sales consultant. Generate sales assets for a supplier
        approaching {trust_name}.

        SUPPLIER CAPABILITIES:
        {capabilities_text}

        TRUST INTELLIGENCE:
        {profile_text}

        IDENTIFIED OPPORTUNITIES:
        {opp_text}

        Return JSON with:
        {{
          "sales_pitch": "3-4 sentence value proposition referencing specific trust priorities",
          "email_subject": "Concise email subject line",
          "email_body": "Professional outreach email (150-200 words) referencing board paper evidence",
          "discovery_questions": ["question 1", "question 2", "question 3"],
          "matched_themes": ["theme 1", "theme 2"],
          "recommended_approach": "Brief strategy note"
        }}
    """)

    client = genai.Client(api_key=gemini_api_key)
    try:
        response = client.models.generate_content(
            model=_MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                thinking_config=genai_types.ThinkingConfig(thinking_budget=1024),
            ),
        )
        import re
        text = response.text or "{}"
        text = re.sub(r"^```(?:json)?\s*", "", text.strip())
        text = re.sub(r"\s*```$", "", text.strip())
        return json.loads(text)
    except Exception as exc:
        return {"error": str(exc)}
