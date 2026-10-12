import unittest
from fa_base.policies import identity_matches, can_cancel_deal, can_finish_deal, deal_status_text

class DealPoliciesTest(unittest.TestCase):
    def test_prefer_immutable_id(self):
        self.assertTrue(identity_matches(111, 'old_tag', 111, 'new_tag', 222))
        self.assertFalse(identity_matches(111, 'old_tag', 333, 'old_tag', 222))
        self.assertFalse(identity_matches(111, 'old_tag', 222, 'old_tag', 222))

    def test_resolve_username_only_when_id_missing(self):
        self.assertTrue(identity_matches(None, 'mops_garant', 111, 'MOPS_GARANT', 222))
        self.assertFalse(identity_matches(None, 'mops_garant', 111, 'other_name', 222))
        self.assertFalse(identity_matches(None, 'mops_garant', 111, None, 222))

    def test_status_policy(self):
        self.assertTrue(can_cancel_deal('pending_target'))
        self.assertFalse(can_cancel_deal('completed'))
        self.assertTrue(can_finish_deal('disputed'))
        self.assertFalse(can_finish_deal('pending_target'))
        self.assertIn('ожидает', deal_status_text('accepted').lower())

if __name__ == '__main__': unittest.main()
