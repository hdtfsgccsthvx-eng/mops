"""Centralised review workflow for Telegram and Web admin.

A complaint stays private until approved by an administrator. A reversal hides
only the exact linked public record. Pre-migration decisions are recoverable
only when the matching record is unambiguous.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from .db import Audit, Record, Report, ReportRecord, User


class ModerationError(ValueError):
    pass


async def review_report(session: AsyncSession, report_id: int, decision: str, actor_id: int) -> Report:
    if decision not in ("approve", "reject", "reopen", "withdraw"):
        raise ModerationError("Неизвестное действие.")
    report = await session.get(Report, report_id, with_for_update=True)
    if not report:
        raise ModerationError("Жалоба не найдена.")
    if decision == "withdraw":
        if report.status != "pending" or actor_id != report.author_id:
            raise ModerationError("Отозвать можно только свою жалобу, пока она не рассмотрена.")
        report.status = "withdrawn"
        session.add(Audit(actor_id=actor_id, action="withdraw_report",
                          detail=f"#{report.id}"))
        return report
    if decision == "reopen":
        if report.status not in ("approved", "rejected"):
            raise ModerationError("Отменить можно только уже вынесенное решение.")
        if report.status == "approved":
            link = await session.get(ReportRecord, report.id)
            if not link:
                # One-time repair for approved reports created by older versions.
                candidates = list((await session.scalars(
                    select(Record).where(
                        Record.username == report.target_username,
                        Record.reason == report.reason,
                        Record.evidence_url == report.evidence_url,
                        Record.kind == "scam",
                        Record.active.is_(True)
                    )
                )).all())
                available = []
                for candidate in candidates:
                    claimed = await session.scalar(select(ReportRecord).where(
                        ReportRecord.record_id == candidate.id))
                    if claimed is None:
                        available.append(candidate)
                if len(available) != 1:
                    raise ModerationError(
                        "Для старой жалобы не удалось однозначно найти опубликованную "
                        "запись. Сначала скройте её в «Записи базы», затем обратитесь "
                        "к администратору для ручного изменения статуса."
                    )
                record = available[0]
                session.add(ReportRecord(report_id=report.id, record_id=record.id))
                await session.flush()
            else:
                record = await session.get(Record, link.record_id, with_for_update=True)
                if record is None:
                    raise ModerationError("Связанная запись не найдена. Требуется ручная проверка.")
            record.active = False
        report.status = "pending"
        report.reviewed_by = None
        session.add(Audit(actor_id=actor_id, action="reopen_report",
                          detail=f"#{report.id}"))
        return report

    if report.status != "pending":
        raise ModerationError("Жалоба уже рассмотрена. Сначала отмените прежнее решение.")
    if decision == "approve":
        user = await session.scalar(select(User).where(User.username == report.target_username))
        record = Record(username=report.target_username,
                        telegram_id=user.id if user else None,
                        kind="scam", reason=report.reason,
                        evidence_url=report.evidence_url, created_by=actor_id)
        session.add(record)
        await session.flush()
        link = await session.get(ReportRecord, report.id)
        if link:
            # A reopened report may have an old, now inactive record.
            link.record_id = record.id
        else:
            session.add(ReportRecord(report_id=report.id, record_id=record.id))
        report.status = "approved"
    else:
        report.status = "rejected"
    report.reviewed_by = actor_id
    session.add(Audit(actor_id=actor_id, action="review_report",
                      detail=f"#{report.id}: {report.status}"))
    return report
