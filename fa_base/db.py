"""Async SQLAlchemy database layer, shared by the Telegram bot."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram ID
    username: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    first_name: Mapped[str] = mapped_column(String(150), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Record(Base):
    __tablename__ = "records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True)
    kind: Mapped[str] = mapped_column(String(20))  # scam | suspicious
    reason: Mapped[str] = mapped_column(Text)
    evidence_url: Mapped[str] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Garant(Base):
    __tablename__ = "garants"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(40), unique=True)
    country: Mapped[str] = mapped_column(String(80))
    channel_url: Mapped[Optional[str]] = mapped_column(Text)
    fee: Mapped[str] = mapped_column(String(80), default="По договорённости")
    description: Mapped[str] = mapped_column(String(400), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    author_id: Mapped[int] = mapped_column(BigInteger)
    target_username: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text)
    evidence_url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_by: Mapped[Optional[int]] = mapped_column(BigInteger)


class ReportRecord(Base):
    """Exact link between a moderated report and the public record it created."""
    __tablename__ = "report_records"
    report_id: Mapped[int] = mapped_column(Integer, ForeignKey("reports.id"), primary_key=True)
    record_id: Mapped[int] = mapped_column(Integer, ForeignKey("records.id"), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Appeal(Base):
    """An appeal concerns one public record. Evidence stays with moderators."""
    __tablename__ = "appeals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[int] = mapped_column(Integer, ForeignKey("records.id"), index=True)
    author_id: Mapped[int] = mapped_column(BigInteger, index=True)
    reason: Mapped[str] = mapped_column(Text)
    evidence_url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_by: Mapped[Optional[int]] = mapped_column(BigInteger)


class Deal(Base):
    __tablename__ = "deals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    creator_id: Mapped[int] = mapped_column(BigInteger)
    target_username: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    offered: Mapped[str] = mapped_column(String(40))
    requested: Mapped[str] = mapped_column(String(40))
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="offered")
    group_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Audit(Base):
    __tablename__ = "audits"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(60))
    detail: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReputationVote(Base):
    """Community assessments, not verified facts. One vote per account per subject."""
    __tablename__ = "reputation_votes"
    __table_args__ = (UniqueConstraint("voter_id", "subject_key", name="uq_reputation_vote"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    voter_id: Mapped[int] = mapped_column(BigInteger, index=True)
    subject_key: Mapped[str] = mapped_column(String(48), index=True)
    score: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CheckEvent(Base):
    __tablename__ = "check_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    checker_id: Mapped[int] = mapped_column(BigInteger)
    subject_username: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    subject_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def normalize_url(raw: str) -> str:
    if raw.startswith("postgres://"):
        return "postgresql+asyncpg://" + raw[len("postgres://"):]
    if raw.startswith("postgresql://"):
        return "postgresql+asyncpg://" + raw[len("postgresql://"):]
    return raw


def build_db(url: str):
    engine = create_async_engine(normalize_url(url), pool_pre_ping=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def initialize(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def touch_user(session: AsyncSession, tg_user):
    if not tg_user:
        return
    u = await session.get(User, tg_user.id)
    if u is None:
        u = User(id=tg_user.id)
        session.add(u)
    u.username = (tg_user.username or "").lower() or None
    u.first_name = tg_user.first_name or ""
    await session.commit()


async def audit(session: AsyncSession, actor: int, action: str, detail: str = ""):
    session.add(Audit(actor_id=actor, action=action, detail=detail[:2000]))
    await session.commit()


async def count(session: AsyncSession, cls, *where):
    return (await session.scalar(select(func.count()).select_from(cls).where(*where))) or 0


async def resolve_user(session: AsyncSession, username: str) -> Optional[User]:
    return await session.scalar(select(User).where(User.username == username.lower()).order_by(User.created_at.desc()))