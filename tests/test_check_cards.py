import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from fa_base.check_cards import card_role, card_caption, safe_evidence_url, safe_telegram_url
from fa_base.check_banners import check_banner_path, reviews_word


class CardsTests(unittest.TestCase):
    def test_roles_are_based_on_records_not_votes(self):
        g = SimpleNamespace(active=True, channel_url="https://t.me/proofs", fee="0₽")
        scam = SimpleNamespace(kind="scam", reason="test", evidence_url="https://t.me/test/1")
        self.assertEqual(card_role([], None), "neutral")
        self.assertEqual(card_role([], g), "garant")
        self.assertEqual(card_role([scam], g), "scam")

    def test_html_caption_and_proofs(self):
        scam = SimpleNamespace(kind="scam", reason="<b>not markup</b>"*40,
                               evidence_url="javascript:alert(1)")
        caption, extra = card_caption(name="<bad>", telegram_id=8782556216,
            records=[scam]*8, garant=None, positive=2, negative=3,
            checks=20, bot_username="fa_baza_test",
            checked_at=datetime(2026, 10, 13, tzinfo=timezone.utc))
        self.assertLessEqual(len(caption), 1024)
        self.assertIn("&lt;bad&gt;", caption)
        self.assertIsNotNone(extra)
        self.assertNotIn('href="javascript:', extra)
        self.assertEqual(safe_evidence_url("https://example.com/proof"), "https://example.com/proof")
        self.assertIsNone(safe_telegram_url("https://example.com"))

    def test_neutral_and_guarant(self):
        neutral, _ = card_caption(name="test", telegram_id=None, records=[], garant=None,
            positive=0, negative=0, checks=10, bot_username="fabot")
        self.assertIn("Пользователя нет в базе", neutral)
        guarant = SimpleNamespace(active=True, channel_url="https://t.me/proofs", fee="0₽")
        txt, _ = card_caption(name="test", telegram_id=456, records=[], garant=guarant,
            positive=24, negative=0, checks=100, bot_username="fabot")
        self.assertIn("5.0/5 (24)", txt)
        self.assertIn("Канал гаранта", txt)
        suspicious = SimpleNamespace(kind="suspicious", reason="Вопрос", evidence_url=None)
        txt, _ = card_caption(name="test", telegram_id=456, records=[suspicious], garant=guarant,
            positive=24, negative=0, checks=100, bot_username="fabot")
        self.assertIn("Также есть записи", txt)

    def test_images_change_with_opinions_and_role(self):
        from PIL import Image
        p0 = check_banner_path("neutral", 0)
        p10 = check_banner_path("neutral", 10)
        psc = check_banner_path("scam", 10)
        self.assertNotEqual(Image.open(p0).tobytes(), Image.open(p10).tobytes())
        self.assertNotEqual(Image.open(p10).tobytes(), Image.open(psc).tobytes())
        self.assertEqual(reviews_word(21), "оценка")
        self.assertEqual(reviews_word(22), "оценки")
        self.assertEqual(reviews_word(11), "оценок")


if __name__ == "__main__":
    unittest.main()
