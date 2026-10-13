import unittest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from fa_base.db import Base, ReputationVote, User
from fa_base.check_reputation import (check_link, rating_link, subject_keys,
                                      persist_vote, reputation_counts)


class LinkTests(unittest.TestCase):
    def test_permanent_id_and_username_fallback(self):
        self.assertEqual(check_link("my_bot", "Alice", 123), "https://t.me/my_bot?start=check_123")
        self.assertEqual(check_link("my_bot", "Alice", None), "https://t.me/my_bot?start=checku_alice")
        self.assertEqual(rating_link("my_bot", "Alice", None), "https://t.me/my_bot?start=rate_u_alice")
        self.assertEqual(rating_link("my_bot", None, 123), "https://t.me/my_bot?start=rate_i_123")
        self.assertEqual(subject_keys("Alice", 123), ("id:123", "username:alice"))


class VoteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_vote_and_change_vote_not_duplicate(self):
        async with self.Session() as s:
            self.assertEqual(await persist_vote(s, voter_id=51, username="alice", telegram_id=None, score=1), (1, 0))
            self.assertEqual(await persist_vote(s, voter_id=51, username="alice", telegram_id=None, score=-1), (0, 1))
            self.assertEqual(await persist_vote(s, voter_id=51, username="alice", telegram_id=777, score=1), (1, 0))
            self.assertEqual(await reputation_counts(s, "alice", 777), (1, 0))
            from sqlalchemy import select
            self.assertEqual(len((await s.scalars(select(ReputationVote))).all()), 1)

    async def test_votes_from_old_username_key_visible_with_id(self):
        async with self.Session() as s:
            s.add_all([ReputationVote(voter_id=10, subject_key="username:alice", score=1),
                       ReputationVote(voter_id=11, subject_key="id:777", score=-1)])
            await s.commit()
            self.assertEqual(await reputation_counts(s, "alice", 777), (1, 1))

    async def test_cannot_rate_self_by_known_id_or_username(self):
        async with self.Session() as s:
            s.add(User(id=777, username="alice", first_name="Alice"))
            await s.commit()
            with self.assertRaisesRegex(ValueError, "Нельзя"):
                await persist_vote(s, voter_id=777, username="alice", telegram_id=777, score=1)
            with self.assertRaisesRegex(ValueError, "Нельзя"):
                await persist_vote(s, voter_id=777, username="alice", telegram_id=None, score=1)
