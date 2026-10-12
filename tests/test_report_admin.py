"""Regression tests for the in-bot review workflow and admin menu."""
import unittest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from fa_base.db import Base, Report, Record, ReportRecord
from fa_base.moderation import review_report, ModerationError
from fa_base.bot import report_actions
from fa_base.keyboards import admin_menu


class AdminNavigationTests(unittest.TestCase):
    def test_dashboard_has_required_sections_and_quick_add(self):
        payloads = [button.callback_data for row in admin_menu().inline_keyboard for button in row]
        for required in ("admin:reports", "admin:garants:0", "admin:records:0",
                         "admin:newgarant", "admin:newrecord:scam",
                         "admin:newrecord:suspicious"):
            self.assertIn(required, payloads)

    def test_report_has_actions_for_each_state(self):
        def names(state):
            return [button.callback_data for row in report_actions(1,state).inline_keyboard
                    for button in row]
        self.assertIn("admin:approve:1", names("pending"))
        self.assertIn("admin:reject:1", names("pending"))
        self.assertIn("admin:reopen:1", names("approved"))
        self.assertIn("admin:reopen:1", names("rejected"))
        self.assertNotIn("admin:approve:1", names("approved"))


class ModerationWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def create_report(self, status="pending"):
        async with self.Session() as s:
            r = Report(author_id=123, target_username="test_person",
                       reason="Проверяем обоснованность фактов и доказательств",
                       evidence_url="https://t.me/example/1", status=status)
            s.add(r)
            await s.commit()
            return r.id

    async def test_approve_reopen_reject(self):
        rid = await self.create_report()
        async with self.Session() as s:
            r = await review_report(s, rid, "approve", 77)
            await s.commit()
            self.assertEqual(r.status, "approved")
            link = await s.get(ReportRecord,rid)
            self.assertIsNotNone(link)
            record = await s.get(Record,link.record_id)
            self.assertTrue(record.active)
            await review_report(s,rid,"reopen",77)
            await s.commit()
            self.assertFalse((await s.get(Record, link.record_id)).active)
            self.assertEqual((await s.get(Report,rid)).status,"pending")
            await review_report(s,rid,"reject",77)
            await s.commit()
            self.assertEqual((await s.get(Report,rid)).status,"rejected")

    async def test_legacy_approved_report_restores_and_hides_unique_record(self):
        rid = await self.create_report("approved")
        async with self.Session() as s:
            s.add(Record(username="test_person",kind="scam",
                         reason="Проверяем обоснованность фактов и доказательств",
                         evidence_url="https://t.me/example/1",created_by=77))
            await s.commit()
            await review_report(s,rid,"reopen",77)
            await s.commit()
            self.assertEqual((await s.get(Report,rid)).status,"pending")
            found = (await s.scalars(select(Record))).one()
            self.assertFalse(found.active)

    async def test_old_report_with_two_possible_records_is_not_silently_reversed(self):
        rid = await self.create_report("approved")
        async with self.Session() as s:
            for _ in range(2):
                s.add(Record(username="test_person",kind="scam",
                             reason="Проверяем обоснованность фактов и доказательств",
                             evidence_url="https://t.me/example/1",created_by=77))
            await s.commit()
            with self.assertRaises(ModerationError):
                await review_report(s,rid,"reopen",77)
            await s.rollback()
            self.assertEqual((await s.get(Report,rid)).status,"approved")

if __name__ == "__main__":
    unittest.main()
