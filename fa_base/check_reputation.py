"""Stable Telegram check links and deduplicated reputation scores.

A Telegram bot cannot resolve an arbitrary @username to a numeric ID.
When ID is unavailable, a username deep link is offered with that limitation.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from .db import ReputationVote, resolve_user


def subject_keys(username: str | None, telegram_id: int | None) -> tuple[str, ...]:
    keys = []
    if telegram_id is not None:
        keys.append(f"id:{telegram_id}")
    if username:
        keys.append(f"username:{username.lower().lstrip('@')}")
    return tuple(keys)


def check_link(bot_username: str, username: str | None, telegram_id: int | None) -> str:
    if telegram_id is not None:
        payload = f"check_{telegram_id}"
    elif username:
        payload = f"checku_{username.lower().lstrip('@')}"
    else:
        raise ValueError("Check link needs username or Telegram ID")
    return f"https://t.me/{bot_username.lstrip('@')}?start={payload}"


def rating_link(bot_username: str, username: str | None, telegram_id: int | None) -> str:
    if telegram_id is not None:
        payload = f"rate_i_{telegram_id}"
    elif username:
        payload = f"rate_u_{username.lower().lstrip('@')}"
    else:
        raise ValueError("Rating needs username or Telegram ID")
    return f"https://t.me/{bot_username.lstrip('@')}?start={payload}"


async def reputation_counts(session, username: str | None, telegram_id: int | None) -> tuple[int, int]:
    """Count one vote per voter, including prior username-only votes."""
    keys = subject_keys(username, telegram_id)
    if not keys:
        return (0, 0)
    votes = list((await session.scalars(
        select(ReputationVote).where(ReputationVote.subject_key.in_(keys))
        .order_by(ReputationVote.created_at.desc(), ReputationVote.id.desc())
    )).all())
    by_voter = {}
    for vote in votes:
        if vote.voter_id not in by_voter:
            by_voter[vote.voter_id] = vote.score
    return (sum(s == 1 for s in by_voter.values()),
            sum(s == -1 for s in by_voter.values()))


async def persist_vote(session, *, voter_id: int, score: int,
                       username: str | None, telegram_id: int | None) -> tuple[int, int]:
    """Upsert and collapse legacy aliases; do not allow a voter to inflate totals."""
    if score not in (1, -1):
        raise ValueError("Invalid score")
    keys = subject_keys(username, telegram_id)
    if not keys:
        raise ValueError("No subject")
    if telegram_id == voter_id:
        raise ValueError("Нельзя оценивать себя")
    if username and telegram_id is None:
        user = await resolve_user(session, username)
        if user is not None and user.id == voter_id:
            raise ValueError("Нельзя оценивать себя")
    votes = list((await session.scalars(select(ReputationVote).where(
        ReputationVote.voter_id == voter_id, ReputationVote.subject_key.in_(keys)))).all())
    primary = next((v for v in votes if v.subject_key == keys[0]), None)
    if primary is None and votes:
        primary = votes[0]
        primary.subject_key = keys[0]
    if primary is None:
        primary = ReputationVote(voter_id=voter_id, subject_key=keys[0], score=score)
        session.add(primary)
    primary.score = score
    primary.created_at = datetime.now(timezone.utc)
    for other in votes:
        if other is not primary:
            await session.delete(other)
    await session.commit()
    return await reputation_counts(session, username, telegram_id)
