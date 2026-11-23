"""Offline Office cloud/LAN consistency classification tests."""
import unittest

from office_cloud_status import cloud_state, normalized


class OfficeStatusTests(unittest.TestCase):
    def setUp(self):
        self.members = [{"name": "Office Left"}, {"name": "Office Right"}]

    @staticmethod
    def obj(ident, names, leds):
        return {"id": ident, "ledCount": leds, "devices": [
            {"device": {"name": name}} for name in names]}

    def test_joined_group(self):
        objects = [self.obj(1, ["Office Left", "Office Right"], 420)]
        mode, matches = cloud_state(objects, self.members, 420)
        self.assertEqual(mode, "joined")
        self.assertEqual(len(matches), 1)

    def test_unjoined_individuals(self):
        objects = [self.obj(1, ["Office Left"], 210),
                   self.obj(2, ["Office Right"], 210)]
        self.assertEqual(cloud_state(objects, self.members, 420)[0], "unjoined")

    def test_mixed_group_and_individuals_is_ambiguous(self):
        objects = [self.obj(1, ["Office Left", "Office Right"], 420),
                   self.obj(2, ["Office Left"], 210)]
        self.assertEqual(cloud_state(objects, self.members, 420)[0], "ambiguous")

    def test_missing_individual_is_ambiguous(self):
        self.assertEqual(cloud_state([self.obj(1, ["Office Left"], 210)],
                                     self.members, 420)[0], "ambiguous")


if __name__ == "__main__":
    unittest.main()
