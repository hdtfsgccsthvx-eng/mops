import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from fa_base.db import Base
from fa_base.payment_watch import (
    PaymentWatch, PaymentSubscriber, DURATION, start_watch, get_watch,
    status_at, expire_watch, confirm_watch, public_status, aware,
    subscribe, unsubscribe, send_pending_notifications, can_start_deal,
)


class DummyBot:
    def __init__(self):
        self.messages = []
    async def send_message(self, user_id, message):
        self.messages.append((user_id, message))


class PaymentWatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict("os.environ", {"PAYMENT_CONFIRM_CODE": "real_secret_code_which_is_not_in_git_1234567"})
        self.env.start()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.env.stop()

    async def test_persisted_deadline_48h_and_no_restart_reset(self):
        async with self.Sessions() as s:
            self.assertTrue(await start_watch(s, self.now))
            self.assertFalse(await start_watch(s, self.now + timedelta(hours=10)))
            gate = await get_watch(s)
            self.assertEqual(aware(gate.deadline_at), self.now + DURATION)
            self.assertEqual(status_at(gate, self.now + timedelta(hours=47)), "pending")
            self.assertEqual(status_at(gate, self.now + DURATION), "overdue")
            self.assertTrue(await can_start_deal(s, self.now + timedelta(hours=47)))
            self.assertFalse(await can_start_deal(s, self.now + DURATION))
            self.assertEqual(gate.status, "pending") # virtual expiry doesn't overwrite manually

    async def test_confirmation_prevents_expiry(self):
        async with self.Sessions() as s:
            await start_watch(s, self.now)
            with self.assertRaisesRegex(ValueError, "Неверный"):
                await confirm_watch(s, 100, "wrong", self.now)
            self.assertEqual(await confirm_watch(s, 100, "real_secret_code_which_is_not_in_git_1234567",
                                                 self.now + timedelta(hours=10)), "confirmed")
            self.assertFalse(await expire_watch(s, self.now + timedelta(days=3)))
            self.assertTrue(await can_start_deal(s, self.now + timedelta(days=3)))

    async def test_opt_in_only_one_alert_and_late_correction(self):
        bot = DummyBot()
        async with self.Sessions() as s:
            await start_watch(s, self.now - timedelta(days=3))
            await subscribe(s, 5, self.now - timedelta(days=2, hours=12))
            await subscribe(s, 7, self.now - timedelta(days=2, hours=12))
            await unsubscribe(s, 7)
            self.assertTrue(await expire_watch(s, self.now - timedelta(hours=1)))
            self.assertIn("не подтвердил", public_status(await get_watch(s), self.now))
        self.assertEqual(await send_pending_notifications(self.Sessions, bot), 1)
        self.assertEqual(await send_pending_notifications(self.Sessions, bot), 0)
        self.assertEqual([uid for uid, _ in bot.messages], [5])
        async with self.Sessions() as s:
            result = await confirm_watch(s, 100, "real_secret_code_which_is_not_in_git_1234567", self.now)
            self.assertEqual(result, "confirmed_late")
            self.assertTrue(await can_start_deal(s, self.now))
        self.assertEqual(await send_pending_notifications(self.Sessions, bot), 1)
        self.assertEqual(await send_pending_notifications(self.Sessions, bot), 0)
        self.assertEqual([uid for uid, _ in bot.messages], [5, 5])
        self.assertIn("не доказательство мошенничества", bot.messages[0][1].lower())
        self.assertIn("после установленного срока", bot.messages[1][1])

    async def test_status_off_without_key_even_after_existing_gate(self):
        async with self.Sessions() as s:
            await start_watch(s, self.now)
            with patch.dict("os.environ", {"PAYMENT_CONFIRM_CODE": ""}):
                self.assertEqual(status_at(await get_watch(s), self.now + timedelta(days=3)), "off")
                self.assertTrue(await can_start_deal(s, self.now + timedelta(days=3)))
                self.assertFalse(await expire_watch(s, self.now + timedelta(days=3)))
                self.assertEqual(await subscribe(s, 50, self.now), "off")


if __name__ == "__main__":
    unittest.main()
