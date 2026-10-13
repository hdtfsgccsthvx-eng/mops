"""Opt-in, neutral 48h payment-confirmation watch.

This is an administrative self-report, *not* a scam verdict or proof of
payment. No automatic reputation changes, global broadcast or editing
third-party messages. Only consenting users receive notifications.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

log = logging.getLogger(__name__)
WATCH_ID = 1
DURATION = timedelta(hours=48)


def utcnow():
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class PaymentWatch(Base):
    __tablename__ = "payment_watches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class PaymentSubscriber(Base):
    __tablename__ = "payment_watch_subscribers"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_status: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    subscribed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


def configured() -> bool:
    # A long secret is kept only in Railway Variables, never in the repository.
    return len(os.getenv("PAYMENT_CONFIRM_CODE", "").strip()) >= 24


async def get_watch(session: AsyncSession) -> PaymentWatch | None:
    return await session.get(PaymentWatch, WATCH_ID)


async def start_watch(session: AsyncSession, now: datetime | None = None) -> bool:
    """Persist deadline on first activation. Restarting never resets the clock."""
    if not configured():
        return False
    if await get_watch(session) is not None:
        return False
    now = now or utcnow()
    session.add(PaymentWatch(id=WATCH_ID, created_at=now, deadline_at=now + DURATION, status="pending"))
    await session.commit()
    return True


def status_at(watch: PaymentWatch | None, now: datetime | None = None) -> str:
    if watch is None or not configured():
        return "off"
    now = now or utcnow()
    if watch.status == "pending" and now >= aware(watch.deadline_at):
        return "overdue"
    return watch.status


async def expire_watch(session: AsyncSession, now: datetime | None = None) -> bool:
    """Single transition; no duplicate alert event on service restarts."""
    if not configured():
        return False
    watch = await get_watch(session)
    if watch is None or watch.status != "pending":
        return False
    if (now or utcnow()) < aware(watch.deadline_at):
        return False
    watch.status = "overdue"
    await session.commit()
    return True


async def confirm_watch(session: AsyncSession, actor: int, candidate: str,
                        now: datetime | None = None) -> str:
    """Caller also checks admin ID and private chat; secret checked timing-safely."""
    expected = os.getenv("PAYMENT_CONFIRM_CODE", "").strip()
    if len(expected) < 24 or not hmac.compare_digest(candidate, expected):
        raise ValueError("Неверный код подтверждения")
    watch = await get_watch(session)
    if watch is None:
        raise ValueError("Мониторинг ещё не активирован")
    if watch.status in ("confirmed", "confirmed_late"):
        return watch.status
    now = now or utcnow()
    late = now >= aware(watch.deadline_at)
    watch.status = "confirmed_late" if late else "confirmed"
    watch.confirmed_at = now
    watch.confirmed_by = actor
    await session.commit()
    return watch.status


def public_status(watch: PaymentWatch | None, now: datetime | None = None) -> str:
    state = status_at(watch, now)
    if state == "off":
        return "ℹ️ Мониторинг подтверждения оплаты не активирован."
    if state == "pending":
        end = aware(watch.deadline_at).strftime("%d.%m.%Y %H:%M UTC")
        return f"⌛ Ожидается подтверждение оплаты администратором FA база до {end}."
    if state == "overdue":
        return ("⚠️ Администратор FA база не подтвердил оплату в установленный срок (48 часов). "
                "Это не доказательство мошенничества.")
    if state == "confirmed_late":
        return "✅ Администратор FA база отметил подтверждение оплаты после установленного срока."
    return "✅ Администратор FA база отметил подтверждение оплаты в срок."


async def subscribe(session: AsyncSession, user_id: int, now: datetime | None = None) -> str:
    watch = await get_watch(session)
    if watch is None or not configured():
        return "off"
    state = status_at(watch, now)
    entry = await session.get(PaymentSubscriber, user_id)
    if entry is None:
        entry = PaymentSubscriber(user_id=user_id, active=True, last_status=state)
        session.add(entry)
    else:
        entry.active = True
        entry.last_status = state
    await session.commit()
    return state


async def unsubscribe(session: AsyncSession, user_id: int) -> None:
    entry = await session.get(PaymentSubscriber, user_id)
    if entry is not None:
        entry.active = False
        await session.commit()


async def can_start_deal(session: AsyncSession, now: datetime | None = None) -> bool:
    """Existing deals remain intact; only new deals pause after missed deadline."""
    return status_at(await get_watch(session), now) != "overdue"


async def send_pending_notifications(session_factory, bot, *, limit: int = 80) -> int:
    """Send each opt-in update once; late confirmation corrects prior overdue alert."""
    if not configured():
        return 0
    async with session_factory() as session:
        await expire_watch(session)
        watch = await get_watch(session)
        state = status_at(watch)
        if state not in ("overdue", "confirmed_late"):
            return 0
        entries = list((await session.scalars(
            select(PaymentSubscriber).where(
                PaymentSubscriber.active.is_(True),
                PaymentSubscriber.last_status == ("overdue" if state == "confirmed_late" else "pending")
            ).order_by(PaymentSubscriber.user_id).limit(limit)
        )).all())
        successful = 0
        for entry in entries:
            # Nobody is notified unless they previously and explicitly subscribed.
            text = ("🔔 <b>Статус оплаты в FA базе изменился</b>\n\n"
                    + public_status(watch) + "\n\n"
                    "Информация основана на подтверждении администратора, "
                    "а не на независимой проверке платежа.\n"
                    "Отписаться: /payment_alerts_off")
            try:
                await bot.send_message(entry.user_id, text)
                entry.last_status = state
                await session.commit()
                successful += 1
                await asyncio.sleep(0.06)
            except Exception as exc:
                # Blocked bots / user unavailable / rate limits: do not leak and retry later.
                log.warning("Payment opt-in alert delivery failed (%s): %s", entry.user_id, type(exc).__name__)
                await session.rollback()
        return successful


async def watch_loop(session_factory, bot) -> None:
    """Runs only after DB + Telegram startup, inside the bot's existing single-instance lock."""
    while True:
        try:
            await send_pending_notifications(session_factory, bot)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Payment watch loop failed: %s", type(exc).__name__)
        await asyncio.sleep(30)
