"""Appeals are reviewed privately and never automatically reverse an accusation."""
from __future__ import annotations
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from .db import Appeal, Audit, Record


class AppealError(ValueError):
    pass


async def review_appeal(session: AsyncSession, appeal_id: int, approve: bool, actor_id: int) -> Appeal:
    appeal = await session.get(Appeal, appeal_id, with_for_update=True)
    if appeal is None:
        raise AppealError("Апелляция не найдена.")
    if appeal.status != "pending":
        raise AppealError("Эта апелляция уже рассмотрена.")
    record = await session.get(Record, appeal.record_id, with_for_update=True)
    if record is None:
        raise AppealError("Исходная запись отсутствует.")
    if approve:
        record.active = False
        appeal.status = "approved"
    else:
        appeal.status = "rejected"
    appeal.reviewed_by = actor_id
    session.add(Audit(actor_id=actor_id, action="review_appeal",
                      detail=f"#{appeal.id}:{appeal.status}:record={record.id}"))
    return appeal


async def can_create_appeal(session: AsyncSession, record_id: int, author_id: int) -> bool:
    existing = await session.scalar(select(Appeal.id).where(
        Appeal.record_id == record_id,
        Appeal.author_id == author_id,
        Appeal.status == "pending").limit(1))
    return existing is None
