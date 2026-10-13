"""Bind numeric Telegram IDs to public usernames using real observations."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from sqlalchemy import select

from .db import CheckEvent, ResolvedIdentity, User, resolve_user
from .telegram_resolver import resolve_public_id

RECENT_MAPPING = timedelta(days=7)


def as_aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


async def remember_identity(session, handle: str | None, telegram_id: int | None) -> None:
    if not handle or telegram_id is None:
        return
    handle = handle.lower().lstrip("@")
    row = await session.get(ResolvedIdentity, handle)
    if row is None:
        row = ResolvedIdentity(username=handle, telegram_id=int(telegram_id))
        session.add(row)
    row.telegram_id = int(telegram_id)
    row.verified_at = datetime.now(timezone.utc)
    await session.commit()


async def identify_username(session, handle: str, *, live_lookup=None) -> int | None:
    """Ask Telegram directly first, then use known pairs from this bot."""
    handle = handle.lower().lstrip("@")
    resolver = live_lookup or resolve_public_id
    verified_id = await resolver(handle)
    if verified_id is not None:
        await remember_identity(session, handle, verified_id)
        return verified_id

    cached = await session.get(ResolvedIdentity, handle)
    if cached and datetime.now(timezone.utc) - as_aware(cached.verified_at) <= RECENT_MAPPING:
        return int(cached.telegram_id)

    user = await resolve_user(session, handle)
    if user:
        return int(user.id)

    recent = await session.scalar(select(CheckEvent).where(
        CheckEvent.subject_username == handle,
        CheckEvent.subject_id.is_not(None),
        CheckEvent.at >= datetime.now(timezone.utc) - RECENT_MAPPING,
    ).order_by(CheckEvent.at.desc()).limit(1))
    return int(recent.subject_id) if recent else None


async def username_from_id(session, telegram_id: int) -> str | None:
    """Restore username when the permanent ID link is opened."""
    user = await session.get(User, telegram_id)
    if user and user.username:
        return user.username
    mapping = await session.scalar(select(ResolvedIdentity).where(
        ResolvedIdentity.telegram_id == telegram_id,
        ResolvedIdentity.verified_at >= datetime.now(timezone.utc) - RECENT_MAPPING
    ).order_by(ResolvedIdentity.verified_at.desc()).limit(1))
    return mapping.username if mapping else None
