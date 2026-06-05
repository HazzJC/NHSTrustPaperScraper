"""SQLAlchemy models for the NHS Intelligence Platform."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TrustRecord(Base):
    __tablename__ = "trusts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    url: Mapped[str | None] = mapped_column(String(512))
    last_scraped_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    papers: Mapped[list[BoardPaper]] = relationship(back_populates="trust", cascade="all, delete-orphan")
    profile: Mapped[TrustProfile | None] = relationship(back_populates="trust", cascade="all, delete-orphan", uselist=False)
    opportunities: Mapped[list[Opportunity]] = relationship(back_populates="trust", cascade="all, delete-orphan")
    procurement_signals: Mapped[list[ProcurementSignal]] = relationship(back_populates="trust", cascade="all, delete-orphan")
    timeline_events: Mapped[list[TimelineEvent]] = relationship(back_populates="trust", cascade="all, delete-orphan")
    supplier_matches: Mapped[list[SupplierMatch]] = relationship(back_populates="trust", cascade="all, delete-orphan")


class BoardPaper(Base):
    __tablename__ = "board_papers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trust_id: Mapped[int] = mapped_column(ForeignKey("trusts.id"), nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    url: Mapped[str | None] = mapped_column(String(1024))
    title: Mapped[str | None] = mapped_column(String(512))
    paper_date: Mapped[str | None] = mapped_column(String(32))
    report_type: Mapped[str | None] = mapped_column(String(64))
    full_text: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    ingested_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    trust: Mapped[TrustRecord] = relationship(back_populates="papers")
    opportunities: Mapped[list[Opportunity]] = relationship(back_populates="paper", cascade="all, delete-orphan")
    procurement_signals: Mapped[list[ProcurementSignal]] = relationship(back_populates="paper", cascade="all, delete-orphan")
    timeline_events: Mapped[list[TimelineEvent]] = relationship(back_populates="paper", cascade="all, delete-orphan")
    insights: Mapped[list[ExtractedInsight]] = relationship(back_populates="paper", cascade="all, delete-orphan")


class ExtractedInsight(Base):
    __tablename__ = "extracted_insights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("board_papers.id"), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(64))  # priorities | challenges | digital | financial
    summary: Mapped[str] = mapped_column(Text)
    evidence_quote: Mapped[str | None] = mapped_column(Text)
    page_ref: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    paper: Mapped[BoardPaper] = relationship(back_populates="insights")


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("board_papers.id"), nullable=False, index=True)
    trust_id: Mapped[int] = mapped_column(ForeignKey("trusts.id"), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    budget_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    urgency: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_quote: Mapped[str | None] = mapped_column(Text)
    page_ref: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    paper: Mapped[BoardPaper] = relationship(back_populates="opportunities")
    trust: Mapped[TrustRecord] = relationship(back_populates="opportunities")


class ProcurementSignal(Base):
    __tablename__ = "procurement_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("board_papers.id"), nullable=False, index=True)
    trust_id: Mapped[int] = mapped_column(ForeignKey("trusts.id"), nullable=False, index=True)
    signal_type: Mapped[str] = mapped_column(String(64))  # high_intent | medium_intent | early_stage
    description: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_quote: Mapped[str | None] = mapped_column(Text)
    page_ref: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    paper: Mapped[BoardPaper] = relationship(back_populates="procurement_signals")
    trust: Mapped[TrustRecord] = relationship(back_populates="procurement_signals")


class TimelineEvent(Base):
    __tablename__ = "timeline_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("board_papers.id"), nullable=False, index=True)
    trust_id: Mapped[int] = mapped_column(ForeignKey("trusts.id"), nullable=False, index=True)
    date_text: Mapped[str | None] = mapped_column(String(128))
    programme: Mapped[str | None] = mapped_column(String(255))
    milestone: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_quote: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    paper: Mapped[BoardPaper] = relationship(back_populates="timeline_events")
    trust: Mapped[TrustRecord] = relationship(back_populates="timeline_events")


class TrustProfile(Base):
    __tablename__ = "trust_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trust_id: Mapped[int] = mapped_column(ForeignKey("trusts.id"), unique=True, nullable=False)
    digital_summary: Mapped[str | None] = mapped_column(Text)
    priorities_summary: Mapped[str | None] = mapped_column(Text)
    challenges_summary: Mapped[str | None] = mapped_column(Text)
    financial_summary: Mapped[str | None] = mapped_column(Text)
    ai_opportunities_summary: Mapped[str | None] = mapped_column(Text)
    procurement_summary: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    trust: Mapped[TrustRecord] = relationship(back_populates="profile")


class SupplierProfile(Base):
    __tablename__ = "supplier_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    capabilities_text: Mapped[str] = mapped_column(Text)
    target_categories: Mapped[str | None] = mapped_column(Text)  # JSON list
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    matches: Mapped[list[SupplierMatch]] = relationship(back_populates="supplier", cascade="all, delete-orphan")


class SupplierMatch(Base):
    __tablename__ = "supplier_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("supplier_profiles.id"), nullable=False, index=True)
    trust_id: Mapped[int] = mapped_column(ForeignKey("trusts.id"), nullable=False, index=True)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    matched_themes: Mapped[str | None] = mapped_column(Text)  # JSON list
    supporting_evidence: Mapped[str | None] = mapped_column(Text)  # JSON
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    supplier: Mapped[SupplierProfile] = relationship(back_populates="matches")
    trust: Mapped[TrustRecord] = relationship(back_populates="supplier_matches")


_DB_PATH = Path(__file__).parent.parent / "data" / "intelligence.db"
_engine = None


def get_engine(db_path: Path | None = None):
    global _engine
    if _engine is None:
        path = db_path or _DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{path}", echo=False)
        Base.metadata.create_all(_engine)
    return _engine


def get_session(db_path: Path | None = None) -> Session:
    return Session(get_engine(db_path))


def get_or_create_trust(session: Session, name: str, url: str | None = None) -> TrustRecord:
    trust = session.query(TrustRecord).filter_by(name=name).first()
    if not trust:
        trust = TrustRecord(name=name, url=url)
        session.add(trust)
        session.flush()
    elif url and not trust.url:
        trust.url = url
    return trust
