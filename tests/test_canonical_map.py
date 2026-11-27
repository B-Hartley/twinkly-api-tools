"""Canonical map updater regression checks without device access."""
import copy
import json
from pathlib import Path
import unittest
import update_grouped_2d_map as maps

ROOT=Path(__file__).resolve().parents[1]

class CanonicalMapTests(unittest.TestCase):
    def test_existing_bundle_and_cloud_payload(self):
        bundle=maps.read_bundle(ROOT/"Front_2D_corrected_v3.zip")
        original=copy.deepcopy(bundle[0])
        corrected=maps.prepare(bundle)
        payload=maps.cloud_write_payload(bundle[0],bundle[2],corrected)
        self.assertEqual(bundle[0],original)
        self.assertEqual(payload["layout"]["coords"],bundle[2]["coords"])
        self.assertEqual(json.loads(payload["meta"]),corrected)
        for slot in payload["devices"]:
            self.assertNotIn("type",slot)
            for key in ("id","created","updated"):
                self.assertNotIn(key,slot["device"])
        for key in ("id","objectId","created","updated"):
            self.assertNotIn(key,payload["layout"])

    def test_fixed_decimal_and_layout_classification(self):
        self.assertEqual(maps.fixed_json(-0.00003379),"-0.000034")
        layout={"source":"2d","synthesized":True,"uuid":"test","coordinates":[{"x":0.0,"y":0.5,"z":0.0}]}
        target=copy.deepcopy(layout)
        target["coordinates"][0]["x"]=0.5
        self.assertEqual(maps.classify_layout(layout,layout,target),"ORIGINAL")
        self.assertEqual(maps.classify_layout(target,layout,target),"CORRECTED")
        other=copy.deepcopy(layout)
        other["coordinates"][0]["x"]=0.25
        with self.assertRaises(ValueError):
            maps.classify_layout(other,layout,target)
