"""Environment configuration. NEVER commit bot tokens."""
from __future__ import annotations
import os
from dataclasses import dataclass
from .utils import get_admin_ids


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    database_url: str
    channel_url: str
    report_url: str
    garant_chat_url: str
    appeal_url: str

    @classmethod
    def load(cls):
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("BOT_TOKEN не задан. Создайте бота через @BotFather и укажите токен в Railway Variables.")
        admins = get_admin_ids(os.getenv("ADMIN_IDS", ""))
        if not admins:
            raise RuntimeError("ADMIN_IDS не задан: нужен хотя бы один Telegram user ID администратора.")
        return cls(
            bot_token=token,
            admin_ids=frozenset(admins),
            database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./fa_baza.db"),
            channel_url=os.getenv("CHANNEL_URL", "https://t.me/mops_garant"),
            report_url=os.getenv("REPORT_URL", "https://t.me/mops_garant"),
            garant_chat_url=os.getenv("GARANT_CHAT_URL", "https://t.me/mops_garant"),
            appeal_url=os.getenv("APPEAL_URL", "https://t.me/mops_garant"),
        )