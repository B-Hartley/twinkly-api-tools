"""Offline checks for conservative, cloud-untouched recovery after mixed state."""
import copy
import unittest
from unittest.mock import patch

import recover_office_standalone as recovery
from test_cloud_group_cycle_reverse import plan, standalone
from cloud_group_preflight import FILES


def mixed():
    data = standalone()
    for i, role in enumerate(("master", "slave")):
        entry = data[FILES[i + 1]]
        entry["group"]["group"].update({
            "mode": role, "size": 4, "offset": i * 2,
            "uid": "12345678-1234-1234-1234-123456789abc"})
        entry["layout"]["source"] = "2d"
    return data


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.config = plan()
        self.data = mixed()
        self.refs_3d = [
            copy.deepcopy(self.config["members"][0]["standalone_layout"]),
            copy.deepcopy(self.config["members"][1]["standalone_layout"])]
        self.refs_2d = [
            copy.deepcopy(self.config["members"][0]["area_layout"]),
            copy.deepcopy(self.config["members"][1]["area_layout"])]

    def check(self):
        with patch("recover_office_standalone.archived_layouts",
                   return_value=self.refs_3d), \
             patch("recover_office_standalone.archived_group_reference",
                   return_value=({"id": 30}, self.refs_2d)):
            return recovery.validate_mixed(self.data, self.config)

    def test_eligible_mixed_state_requires_unchanged_cloud_3d(self):
        result, _ = self.check()
        self.assertEqual(result, self.refs_3d)

    def test_cloud_area_duplicate_blocks(self):
        duplicate = copy.deepcopy(self.data[FILES[0]]["objects"][0])
        duplicate["id"] = 99
        self.data[FILES[0]]["objects"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "count/identity changed"):
            self.check()

    def test_local_2d_mapping_changed_blocks(self):
        self.data[FILES[1]]["layout"]["coordinates"][0]["x"] += 0.4
        with self.assertRaisesRegex(ValueError, "local 2D layout differs"):
            self.check()

    def test_cloud_3d_mapping_changed_blocks(self):
        self.refs_3d[0]["coordinates"][0]["x"] += 0.4
        with self.assertRaisesRegex(ValueError, "Cloud 3D mapping"):
            self.check()

    def test_wrong_local_role_blocks(self):
        self.data[FILES[2]]["group"]["group"]["mode"] = "none"
        with self.assertRaisesRegex(ValueError, "local grouped role changed"):
            self.check()


if __name__ == "__main__":
    unittest.main()
