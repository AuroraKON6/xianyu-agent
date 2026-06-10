import unittest

from xianyu_agent.models import ListingDraft


class ListingDraftTests(unittest.TestCase):
    def test_listing_draft_has_default_location_for_browser_publish(self):
        draft = ListingDraft()

        self.assertEqual(draft.location, "")


if __name__ == "__main__":
    unittest.main()
