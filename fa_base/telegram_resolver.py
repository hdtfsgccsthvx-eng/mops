"""Resolve public Telegram usernames through optional bot-authenticated MTProto.

Only Telegram API_ID/API_HASH + existing BOT_TOKEN are used. No user account
session, no third-party ID lookup, and no user credentials are required.
The ordinary Bot API continues to handle all messages independently.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

log = logging.getLogger(__name__)
_client = None
_lock = asyncio.Lock()
_last_request = 0.0
_cooldown_until = 0.0
_cache: dict[str, tuple[int, float]] = {}
_CACHE_SECONDS = 120.0


def mtproto_configured() -> bool:
    return bool(os.getenv("TELEGRAM_API_ID", "").strip().isdigit()
                and os.getenv("TELEGRAM_API_HASH", "").strip()
                and os.getenv("BOT_TOKEN", "").strip())


async def resolve_public_id(handle: str) -> int | None:
    """Return verified ID when Telegram resolves a public user; else None.

    A result for a channel, group, unoccupied name, request timeout or API
    rate limit never gets returned as a person. Telegram may refuse requests.
    """
    global _client, _last_request, _cooldown_until
    if not mtproto_configured():
        return None
    name = handle.lstrip("@").lower()
    if not name or not name.replace("_", "").isalnum():
        return None
    async with _lock:
        now = time.monotonic()
        cached = _cache.get(name)
        if cached and cached[1] > now:
            return cached[0]
        if now < _cooldown_until:
            return None
        try:
            from telethon import TelegramClient, functions, types, errors
            from telethon.sessions import MemorySession

            if _client is None:
                _client = TelegramClient(
                    MemorySession(),
                    int(os.environ["TELEGRAM_API_ID"]),
                    os.environ["TELEGRAM_API_HASH"],
                    receive_updates=False,
                    request_retries=1,
                    connection_retries=2,
                    flood_sleep_threshold=0,
                    timeout=10,
                )
            if not _client.is_connected():
                await asyncio.wait_for(
                    _client.start(bot_token=os.environ["BOT_TOKEN"]), timeout=25)
            wait = 2 - (now - _last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            _last_request = time.monotonic()
            resolved = await asyncio.wait_for(
                _client(functions.contacts.ResolveUsernameRequest(username=name)),
                timeout=15)
            if not isinstance(resolved.peer, types.PeerUser):
                return None
            uid = int(resolved.peer.user_id)
            if not any(int(u.id) == uid for u in resolved.users):
                return None
            _cache[name] = (uid, time.monotonic() + _CACHE_SECONDS)
            return uid
        except Exception as exc:
            try:
                from telethon.errors import FloodWaitError, UsernameNotOccupiedError, UsernameInvalidError
                if isinstance(exc, FloodWaitError):
                    _cooldown_until = time.monotonic() + min(int(exc.seconds), 3600)
                if isinstance(exc, (UsernameNotOccupiedError, UsernameInvalidError)):
                    return None
            except ImportError:
                pass
            log.warning("Telegram public username lookup unavailable: %s", type(exc).__name__)
            return None


async def shutdown_resolver() -> None:
    global _client
    if _client is not None:
        try:
            await _client.disconnect()
        except Exception:
            log.debug("MTProto disconnect failed", exc_info=True)
        finally:
            _client = None
