"""Unit tests for Telegram and Web report moderation, without live credentials."""
import asyncio
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from fa_base.db import Base, Record, Report, ReportRecord
from fa_base.moderation import ModerationError, review_report


class AsyncSyncBridge:
    """AsyncSession subset backed by a transient synchronous SQLite connection."""
    def __init__(self, session):
        self.session = session

    async def get(self, model, key, **kwargs):
        return self.session.get(model, key)

    async def scalar(self, statement):
        return self.session.scalar(statement)

    async def scalars(self, statement):
        return self.session.scalars(statement)

    async def flush(self):
        self.session.flush()

    def add(self, obj):
        self.session.add(obj)


class ReportModerationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.bridge = AsyncSyncBridge(self.session)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def new_report(self, username="example", author=123, reason="Transaction disputed"):
        report = Report(author_id=author, target_username=username, reason=reason,
                        evidence_url="https://t.me/proofs/5")
        self.session.add(report)
        self.session.commit()
        return report

    def act(self, report, action, actor=1874285560):
        result = asyncio.run(review_report(self.bridge, report.id, action, actor))
        self.session.commit()
        return result

    def test_approval_reversal_and_reapprove(self):
        r = self.new_report()
        self.act(r, "approve")
        old = self.session.scalar(select(Record).where(Record.active.is_(True)))
        self.assertIsNotNone(old)
        self.assertEqual(r.status, "approved")
        self.assertEqual(self.session.get(ReportRecord, r.id).record_id, old.id)
        self.act(r, "reopen")
        self.assertFalse(self.session.get(Record, old.id).active)
        self.assertEqual(r.status, "pending")
        self.act(r, "approve")
        records = list(self.session.scalars(select(Record).order_by(Record.id)))
        self.assertEqual(len(records), 2)
        self.assertFalse(records[0].active)
        self.assertTrue(records[1].active)
        self.assertEqual(self.session.get(ReportRecord, r.id).record_id, records[1].id)

    def test_reject_reopen(self):
        r = self.new_report()
        self.act(r, "reject")
        self.assertEqual(r.status, "rejected")
        self.assertIsNone(self.session.scalar(select(Record)))
        self.act(r, "reopen")
        self.assertEqual(r.status, "pending")

    def test_cannot_approve_twice(self):
        r = self.new_report()
        self.act(r, "approve")
        with self.assertRaises(ModerationError):
            self.act(r, "approve")
        self.assertEqual(len(list(self.session.scalars(select(Record)))), 1)

    def test_author_can_withdraw_only_pending(self):
        r = self.new_report()
        with self.assertRaises(ModerationError):
            self.act(r, "withdraw", actor=222)
        self.act(r, "withdraw", actor=123)
        self.assertEqual(r.status, "withdrawn")
        with self.assertRaises(ModerationError):
            self.act(r, "approve")

    def test_legacy_approved_reversal_unambiguous(self):
        r = self.new_report()
        r.status = "approved"
        record = Record(username=r.target_username, reason=r.reason, evidence_url=r.evidence_url,
                        kind="scam", created_by=0)
        self.session.add(record)
        self.session.commit()
        self.act(r, "reopen")
        self.assertFalse(record.active)
        self.assertEqual(self.session.get(ReportRecord, r.id).record_id, record.id)

    def test_legacy_approved_multiple_records_requires_manual_review(self):
        r = self.new_report()
        r.status = "approved"
        for i in (1, 2):
            self.session.add(Record(username=r.target_username, reason=r.reason,
                                    evidence_url=r.evidence_url, kind="scam", created_by=i))
        self.session.commit()
        with self.assertRaises(ModerationError):
            self.act(r, "reopen")
        self.assertEqual(r.status, "approved")
        self.assertEqual(len(list(self.session.scalars(select(Record).where(
            Record.active.is_(True))))), 2)


if __name__ == "__main__":
    unittest.main()
