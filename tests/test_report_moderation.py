"""Report lifecycle regression tests. No Telegram token or real database needed."""
import unittest

from sqlalchemy import select
from fa_base.db import build_db, initialize, Record, Report, ReportRecord
from fa_base.moderation import review_report, ModerationError


class ReportModerationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.Sessions = build_db("sqlite+aiosqlite:///:memory:")
        await initialize(self.engine)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def create(self, status="pending"):
        async with self.Sessions() as s:
            report = Report(author_id=900, target_username="target_user",
                            reason="Reported transfer failed despite receipt being provided",
                            evidence_url="https://t.me/exampleproof", status=status)
            s.add(report)
            await s.commit()
            return report.id

    async def test_approve_reopen_reapprove(self):
        rid = await self.create()
        async with self.Sessions() as s:
            result = await review_report(s, rid, "approve", 1874285560)
            self.assertEqual(result.status, "approved")
            await s.commit()
        async with self.Sessions() as s:
            link = await s.get(ReportRecord, rid)
            self.assertIsNotNone(link)
            self.assertTrue((await s.get(Record, link.record_id)).active)
            result = await review_report(s, rid, "reopen", 1874285560)
            self.assertEqual(result.status, "pending")
            await s.commit()
        async with self.Sessions() as s:
            linked = await s.get(ReportRecord, rid)
            self.assertFalse((await s.get(Record, linked.record_id)).active)
            result = await review_report(s, rid, "reject", 1874285560)
            self.assertEqual(result.status, "rejected")
            await s.commit()

    async def test_reject_then_reopen(self):
        rid = await self.create()
        async with self.Sessions() as s:
            await review_report(s, rid, "reject", 1874285560)
            await s.commit()
            await review_report(s, rid, "reopen", 1874285560)
            await s.commit()
            self.assertEqual((await s.get(Report, rid)).status, "pending")

    async def test_old_approved_reversal_hides_exact_record(self):
        rid = await self.create(status="approved")
        async with self.Sessions() as s:
            own = Record(username="target_user", kind="scam",
                         reason="Reported transfer failed despite receipt being provided",
                         evidence_url="https://t.me/exampleproof", created_by=100)
            other = Record(username="target_user", kind="scam",
                           reason="Unrelated previous incident, independently investigated",
                           evidence_url="https://t.me/otherproof", created_by=200)
            s.add_all([own, other])
            await s.commit()
            own_id, other_id = own.id, other.id
            await review_report(s, rid, "reopen", 1874285560)
            await s.commit()
        async with self.Sessions() as s:
            self.assertFalse((await s.get(Record, own_id)).active)
            self.assertTrue((await s.get(Record, other_id)).active)

    async def test_double_action_is_blocked(self):
        rid = await self.create()
        async with self.Sessions() as s:
            await review_report(s, rid, "approve", 1874285560)
            await s.commit()
            with self.assertRaises(ModerationError):
                await review_report(s, rid, "approve", 1874285560)

    async def test_complaint_author_can_withdraw_pending_only(self):
        rid = await self.create()
        async with self.Sessions() as s:
            with self.assertRaises(ModerationError):
                await review_report(s, rid, "withdraw", 123)
            r = await review_report(s, rid, "withdraw", 900)
            self.assertEqual(r.status, "withdrawn")
            await s.commit()

if __name__ == "__main__":
    unittest.main()
