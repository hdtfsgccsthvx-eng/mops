"""Pure business rules used by the bot; independently unit-testable."""
from __future__ import annotations

MAX_DEALS_PER_DAY = 3
MAX_REPORTS_PER_DAY = 5


def identity_matches(counterparty_id: int | None, counterparty_username: str, caller_id: int, caller_username: str | None, creator_id: int) -> bool:
    """Prefer immutable Telegram ID once resolved; fallback to Telegram username."""
    if caller_id == creator_id:
        return False
    if counterparty_id is not None:
        return caller_id == counterparty_id
    return bool(caller_username) and caller_username.lower() == counterparty_username.lower()


def deal_status_text(status: str) -> str:
    return {
        'pending_target': 'Ожидает подтверждения второй стороны',
        'accepted': 'Подтверждена, ожидает группы',
        'declined': 'Отклонена второй стороной',
        'cancelled': 'Отменена инициатором',
        'group_created': 'Группа сделки создана',
        'completed': 'Завершена администрацией',
        'disputed': 'Спор, требуется разбор',
    }.get(status, 'Статус уточняется')


def can_cancel_deal(status: str) -> bool:
    return status in {'pending_target', 'accepted'}


def can_finish_deal(status: str) -> bool:
    return status in {'accepted', 'group_created', 'disputed'}
