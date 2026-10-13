"""Safe, fact-based Telegram check-card captions."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from urllib.parse import urlsplit


def html(value) -> str:
    return escape(str(value or ""), quote=True)


def safe_evidence_url(value: str | None) -> str | None:
    if not value:
        return None
    url = value.strip()
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password:
            return url
    except ValueError:
        pass
    return None


def safe_telegram_url(value: str | None) -> str | None:
    url = safe_evidence_url(value)
    if not url:
        return None
    parsed = urlsplit(url)
    return url if parsed.scheme.lower() == "https" and (parsed.hostname or "").lower() in ("t.me", "telegram.me") else None


def record_details(records: list) -> str:
    """A bounded, HTML-escaped proof list. Never imply an accusation is a court finding."""
    parts = ["\n📌 <b>Причины добавления:</b>\n"]
    shown = 0
    for record in records[:8]:
        reason = (getattr(record, "reason", "") or "Без описания")[:300]
        part = f"• {html(reason)}\n"
        evidence = safe_evidence_url(getattr(record, "evidence_url", None))
        if evidence and len(evidence) <= 450:
            part += f'🔗 <a href="{html(evidence)}">Доказательства</a>\n'
        if sum(len(p) for p in parts) + len(part) > 3000:
            break
        parts.append(part)
        shown += 1
    if shown < len(records):
        parts.append(f"И ещё {len(records)-shown} записей.\n")
    return "".join(parts).rstrip() + "\n"


def card_role(records, garant) -> str:
    """One negative opinion cannot give someone the status 'scammer'."""
    if any(getattr(record, "kind", None) == "scam" for record in records):
        return "scam"
    if garant is not None and getattr(garant, "active", True):
        return "garant"
    return "neutral"


def card_caption(*, name: str | None, telegram_id: int | None, records: list,
                 garant, positive: int, negative: int, checks: int,
                 bot_username: str, checked_at: datetime | None = None):
    """Return (safe Telegram photo caption <= 1024 chars, optional details message)."""
    checked_at = checked_at or datetime.now(timezone.utc)
    role = card_role(records, garant)
    shown_name = f"@{html(name.lstrip('@'))}" if name else "Не указан"
    shown_id = f"[{telegram_id}]" if telegram_id is not None else "[неизвестен]"
    lead = (
        f"👤 <b>Имя:</b> {shown_name}\n"
        f"🪪 <b>id:</b> <code>{shown_id}</code>\n\n"
        "⚙️ <b>Проверка в базе данных...</b>\n\n"
        "📎 <b>Роль пользователя...</b>\n"
    )
    details = ""
    if role == "scam":
        scam_count = sum(getattr(r, "kind", None) == "scam" for r in records)
        lead += f"➤ <b>Мошенник ⚠️</b>\n👁 <b>Количество записей о мошенничестве:</b> {scam_count}\n"
        details = record_details(records)
    elif role == "garant":
        lead += "➤ <b>Гарант FA база 💎</b>\n"
        url = safe_telegram_url(getattr(garant, "channel_url", None))
        if url:
            lead += f'📣 <b>Канал гаранта:</b> <a href="{html(url)}">Перейти</a>\n'
        votes = positive + negative
        if votes:
            rating = positive / votes * 5
            stars = "★" * round(rating) + "☆" * (5 - round(rating))
            lead += f"📈 <b>Оценка сообщества:</b> {rating:.1f}/5 ({votes}) {stars}\n"
        lead += f'💰 <b>Стоимость сделки:</b> {html(getattr(garant, "fee", "По договорённости"))}\n'
        if records:
            lead += f"⚠️ <b>Также есть записи в базе:</b> {len(records)}\n"
            details = record_details(records)
    elif records:
        lead += f"➤ <b>Есть записи о сомнительных услугах ⚠️</b> ({len(records)})\n"
        details = record_details(records)
    else:
        lead += "➤ <b>Пользователя нет в базе ✅</b>\n"
    footer = (
        f"\n👍 <b>Положительных оценок:</b> {positive} | 👎 <b>Предостережений:</b> {negative}\n"
        "<i>Оценки — мнения пользователей, не подтверждённые факты.</i>\n\n"
        f"🔎 <b>Пользователя искали:</b> {checks} раз\n"
        f"✅ <b>Проверено в @{html(bot_username.lstrip('@'))}</b>\n"
        f"🗓 <b>Дата проверки:</b> [{checked_at.astimezone(timezone.utc):%d.%m.%Y} UTC]"
    )
    if len(lead) + len(footer) > 970:
        raise ValueError("Card headline too long to render safely")
    if details and len(lead) + len(details) + len(footer) <= 1024:
        return lead + details + footer, None
    if details:
        return lead + "\n📎 <b>Подробности — в сообщении ниже.</b>\n" + footer, details
    return lead + footer, None
