"""Vector embedding and RAG search using ChromaDB + sentence-transformers.

Called after process_paper() completes to index each chunk for semantic search.
The /intelligence/ask endpoint uses this for natural language querying.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import chromadb
from google import genai
from google.genai import types as genai_types

from intelligence.database import BoardPaper, TrustRecord, get_session

_CHUNK_CHARS = 1_500
_COLLECTION = "board_papers"
_N_RESULTS = 12
_MODEL = "gemini-2.5-flash"
_CHROMA_PATH = Path(__file__).parent.parent / "data" / "chroma"

_chroma_client: chromadb.ClientAPI | None = None
_collection: chromadb.Collection | None = None


def _get_collection() -> chromadb.Collection:
    global _chroma_client, _collection
    if _collection is None:
        _CHROMA_PATH.mkdir(parents=True, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=str(_CHROMA_PATH))
        _collection = _chroma_client.get_or_create_collection(
            name=_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def index_paper(paper_id: int, db_path: Path | None = None) -> int:
    """Chunk BoardPaper.full_text and upsert into ChromaDB. Returns chunks indexed."""
    with get_session(db_path) as session:
        paper = session.get(BoardPaper, paper_id)
        if not paper or not paper.full_text:
            return 0
        trust = session.get(TrustRecord, paper.trust_id)
        trust_name = trust.name if trust else "Unknown"
        full_text = paper.full_text
        paper_date = paper.paper_date or ""
        report_type = paper.report_type or ""
        file_path = paper.file_path

    collection = _get_collection()
    chunks = _make_chunks(full_text)
    documents = []
    metadatas = []
    ids = []

    for i, chunk in enumerate(chunks):
        chunk_id = f"paper_{paper_id}_chunk_{i}"
        # Skip if already indexed
        existing = collection.get(ids=[chunk_id])
        if existing["ids"]:
            continue
        documents.append(chunk)
        metadatas.append({
            "paper_id": paper_id,
            "trust_name": trust_name,
            "paper_date": paper_date,
            "report_type": report_type,
            "file_path": file_path,
            "chunk_index": i,
        })
        ids.append(chunk_id)

    if documents:
        collection.add(documents=documents, metadatas=metadatas, ids=ids)
    return len(documents)


def _make_chunks(text: str) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        current.append(word)
        current_len += len(word) + 1
        if current_len >= _CHUNK_CHARS:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
    if current:
        chunks.append(" ".join(current))
    return chunks


def semantic_search(query: str, n_results: int = _N_RESULTS) -> list[dict[str, Any]]:
    """Return top matching chunks with metadata."""
    collection = _get_collection()
    try:
        results = collection.query(query_texts=[query], n_results=n_results)
    except Exception:
        return []

    hits = []
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    for doc, meta, dist in zip(docs, metas, distances):
        hits.append({
            "text": doc,
            "trust_name": meta.get("trust_name", ""),
            "paper_date": meta.get("paper_date", ""),
            "report_type": meta.get("report_type", ""),
            "file_path": meta.get("file_path", ""),
            "similarity": round(1 - dist, 3),
        })
    return hits


def answer_question(
    question: str,
    gemini_api_key: str,
    n_results: int = _N_RESULTS,
) -> dict[str, Any]:
    """RAG: retrieve relevant chunks then ask Gemini to synthesise an answer."""
    hits = semantic_search(question, n_results)
    if not hits:
        return {
            "answer": "No relevant documents found for that query.",
            "sources": [],
        }

    context = "\n\n".join(
        f"[{h['trust_name']} | {h['paper_date']}]\n{h['text']}" for h in hits
    )
    prompt = textwrap.dedent(f"""\
        You are an NHS procurement intelligence analyst.
        Answer the following question using ONLY the board paper excerpts provided.
        Cite sources (Trust name, date) for each claim.

        QUESTION: {question}

        BOARD PAPER EXCERPTS:
        {context}

        Answer concisely and factually. If the excerpts don't contain enough
        information, say so explicitly.
    """)

    client = genai.Client(api_key=gemini_api_key)
    try:
        response = client.models.generate_content(
            model=_MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                thinking_config=genai_types.ThinkingConfig(thinking_budget=512)
            ),
        )
        answer_text = response.text or "No answer generated."
    except Exception as exc:
        answer_text = f"Error generating answer: {exc}"

    sources = [
        {
            "trust_name": h["trust_name"],
            "paper_date": h["paper_date"],
            "similarity": h["similarity"],
            "file_path": h["file_path"],
        }
        for h in hits
    ]
    return {"answer": answer_text, "sources": sources}
