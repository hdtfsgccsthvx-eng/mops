"""Input checks and safe HTML output for Telegram."""
from __future__ import annotations
import html
import re
from urllib.parse import urlsplit

USER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")


def username(value: str) -> str:
    val = value.strip().removeprefix("@").lower()
    if not USER_RE.fullmatch(val):
        raise ValueError("Укажите корректный @username Telegram (от 5 до 32 символов).")
    return val


def evidence_url(value: str) -> str:
    v = value.strip()
    parts = urlsplit(v)
    if parts.scheme != "https" or not parts.hostname or len(v) > 500:
        raise ValueError("Нужна действующая ссылка на доказательства, начинающаяся с https://")
    if parts.username or parts.password:
        raise ValueError("Ссылка не должна содержать логин или пароль.")
    return v


def safe(t: object) -> str:
    return html.escape(str(t or ""), quote=True)


def mention(username: str) -> str:
    u = username.strip("@").lower()
    return f'<a href="https://t.me/{u}">@{safe(u)}</a>'


def parse_amount(raw: str) -> int:
    if not raw.strip().isdigit():
        raise ValueError("Введите сумму целым числом в рублях, например: 500")
    n = int(raw.strip())
    if not 50 <= n <= 100_000_000:
        raise ValueError("Допустимая сумма: от 50 до 100 000 000 ₽.")
    return n


def get_admin_ids(raw: str) -> set[int]:
    out = set()
    for item in raw.replace(";", ",").split(","):
        if item.strip():
            out.add(int(item.strip()))
    return out