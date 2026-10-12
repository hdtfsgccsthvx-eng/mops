"""Offline regression tests for FA база validators and DB schema."""
import unittest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from fa_base.db import Base, Record, ReputationVote
from fa_base.utils import username, evidence_url, parse_amount, parse_telegram_id, get_admin_ids, safe, mention

class ValidatorsTest(unittest.TestCase):
    def test_usernames(self):
        self.assertEqual(username(' @MoPs_Garant '), 'mops_garant')
        for bad in ('', '@ab', '../admin', 'username<script>', 'http://example.com'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                username(bad)

    def test_evidence_urls(self):
        self.assertEqual(evidence_url(' https://t.me/abc '), 'https://t.me/abc')
        for bad in ('http://example.org', 'file:///etc/passwd', 'https:///missing-host',
                    'https://user:pass@host.tld/a'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                evidence_url(bad)

    def test_deal_amount(self):
        self.assertEqual(parse_amount(' 50 '), 50)
        for bad in ('49', '-5', '0', 'hello', '100000001', '99.50'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_amount(bad)

    def test_telegram_id_validation(self):
        self.assertEqual(parse_telegram_id('1874285560'), 1874285560)
        for bad in ('0', '-1', 'abc', '1000000000000000000000000000'):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                parse_telegram_id(bad)

    def test_admin_ids(self):
        self.assertEqual(get_admin_ids('1874285560, 8517242609;1874285560'),
                         {1874285560, 8517242609})

    def test_html_escape(self):
        self.assertEqual(safe('<script>'), '&lt;script&gt;')
        self.assertIn('https://t.me/mops_garant', mention('@mops_garant'))

class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite+pysqlite:///:memory:')
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_records_are_not_deleted_by_default(self):
        with Session(self.engine) as s:
            s.add(Record(username='example', kind='suspicious', reason='For manual review',
                         evidence_url='https://t.me/post/1', created_by=1))
            s.commit()
            rec = s.scalar(select(Record).where(Record.username == 'example'))
            self.assertTrue(rec.active)
            rec.active = False
            s.commit()
            self.assertFalse(s.scalar(select(Record).where(Record.username == 'example')).active)

    def test_reputation_vote_is_unique_per_voter_and_subject(self):
        with Session(self.engine) as s:
            s.add(ReputationVote(voter_id=12, subject_key='username:mops_garant', score=1))
            s.commit()
        with self.assertRaises(IntegrityError), Session(self.engine) as s:
            s.add(ReputationVote(voter_id=12, subject_key='username:mops_garant', score=-1))
            s.commit()

if __name__ == '__main__':
    unittest.main()
