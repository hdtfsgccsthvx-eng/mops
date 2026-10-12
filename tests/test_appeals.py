"""Appeal moderation regression tests, isolated from production Telegram and Railway."""
import unittest

from fa_base.db import Appeal, Record, build_db, initialize
from fa_base.appeals import AppealError, review_appeal, can_create_appeal


class AppealFlowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.Sessions = build_db("sqlite+aiosqlite:///:memory:")
        await initialize(self.engine)
        async with self.Sessions() as s:
            a = Record(username="person_one", kind="suspicious",
                       reason="Unresolved trading conflict for manual review",
                       evidence_url="https://t.me/proof_demo", created_by=42)
            b = Record(username="person_two", kind="scam",
                       reason="Different account and unrelated evidence",
                       evidence_url="https://t.me/unrelated", created_by=42)
            s.add_all([a, b])
            await s.flush()
            self.record_id, self.other_id = a.id, b.id
            ap = Appeal(record_id=a.id, author_id=300, reason="I have a complete chat log",
                        evidence_url="https://t.me/appeal_demo")
            s.add(ap)
            await s.commit()
            self.appeal_id = ap.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_accept_hides_only_target(self):
        async with self.Sessions() as s:
            self.assertFalse(await can_create_appeal(s, self.record_id, 300))
            self.assertTrue(await can_create_appeal(s, self.record_id, 301))
            result = await review_appeal(s, self.appeal_id, True, actor_id=1874285560)
            self.assertEqual(result.status, "approved")
            await s.commit()
        async with self.Sessions() as s:
            self.assertFalse((await s.get(Record, self.record_id)).active)
            self.assertTrue((await s.get(Record, self.other_id)).active)
            self.assertTrue(await can_create_appeal(s, self.record_id, 300))
            with self.assertRaises(AppealError):
                await review_appeal(s, self.appeal_id, True, 1874285560)

    async def test_reject_does_not_hide(self):
        async with self.Sessions() as s:
            result = await review_appeal(s, self.appeal_id, False, actor_id=1874285560)
            self.assertEqual(result.status, "rejected")
            await s.commit()
        async with self.Sessions() as s:
            self.assertTrue((await s.get(Record, self.record_id)).active)


if __name__ == "__main__":
    unittest.main()
