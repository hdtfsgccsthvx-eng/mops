import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from fa_base.db import Base, ResolvedIdentity, User
from fa_base.identity_lookup import identify_username, username_from_id, remember_identity
from fa_base.check_reputation import check_link, rating_link
from fa_base.check_cards import card_caption
from fa_base import telegram_resolver


class PublicIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_resolve_external_user_and_open_id_link(self):
        async def verified(handle):
            self.assertEqual(handle, "mtmh_world")
            return 8824452828

        async with self.Session() as s:
            result = await identify_username(s, "MTMH_World", live_lookup=verified)
            self.assertEqual(result, 8824452828)
            row = await s.get(ResolvedIdentity, "mtmh_world")
            self.assertEqual(row.telegram_id, 8824452828)
            self.assertEqual(await username_from_id(s, result), "mtmh_world")
            self.assertEqual(check_link("mops_garant_bot", "mtmh_world", result),
                             "https://t.me/mops_garant_bot?start=check_8824452828")
            self.assertEqual(rating_link("mops_garant_bot", "mtmh_world", result),
                             "https://t.me/mops_garant_bot?start=rate_i_8824452828")
            caption, _ = card_caption(
                name="mtmh_world", telegram_id=result, records=[], garant=None,
                positive=0, negative=0, checks=1, bot_username="mops_garant_bot")
            self.assertIn("8824452828", caption)
            self.assertNotIn("неизвестен", caption)

    async def test_live_mapping_updates_username_to_changed_owner(self):
        async with self.Session() as s:
            await remember_identity(s, "alice", 123)
            async def new_owner(handle):
                return 456
            self.assertEqual(await identify_username(s, "alice", live_lookup=new_owner), 456)
            self.assertEqual((await s.get(ResolvedIdentity, "alice")).telegram_id, 456)
            self.assertIsNone(await username_from_id(s, 123))

    async def test_known_users_work_without_mtproto_credentials(self):
        async with self.Session() as s:
            s.add(User(id=777, username="known_user", first_name="Known"))
            await s.commit()
            async def not_configured(handle):
                return None
            self.assertEqual(await identify_username(s, "known_user", live_lookup=not_configured), 777)
            self.assertEqual(await username_from_id(s, 777), "known_user")

    async def test_identity_from_observed_message(self):
        async with self.Session() as s:
            await remember_identity(s, "alice", 111)
            async def no_live(handle):
                return None
            self.assertEqual(await identify_username(s, "alice", live_lookup=no_live), 111)


class MtprotoResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_absent_credentials_are_safe_no_network(self):
        with patch.dict(os.environ, {"TELEGRAM_API_ID": "", "TELEGRAM_API_HASH": ""}):
            self.assertFalse(telegram_resolver.mtproto_configured())
            self.assertIsNone(await telegram_resolver.resolve_public_id("mtmh_world"))

    async def test_user_only_and_bot_authorization_not_user_login(self):
        from telethon import types
        class FakeClient:
            def is_connected(self):
                return True
            async def __call__(self, query):
                return SimpleNamespace(peer=types.PeerUser(user_id=8824452828),
                                       users=[SimpleNamespace(id=8824452828)])
        fake = FakeClient()
        with patch.dict(os.environ, {"TELEGRAM_API_ID": "12345",
                                     "TELEGRAM_API_HASH": "f"*32,
                                     "BOT_TOKEN": "123:test"}):
            with patch.object(telegram_resolver, "_client", fake):
                with patch.object(telegram_resolver, "_last_request", time.monotonic()-10):
                    telegram_resolver._cache.clear()
                    result = await telegram_resolver.resolve_public_id("mtmh_world")
                    self.assertEqual(result, 8824452828)
        telegram_resolver._cache.clear()
