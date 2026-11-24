import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location("archive_import",Path(__file__).resolve().parents[1]/"custom_components/twinkly_area_state/archive_import.py")
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class ArchiveImportTests(unittest.TestCase):
    mac="123456789abc"
    def write(self, root, layout=None):
        layout=layout or {"source":"3d","synthesized":False,"coordinates":[{"x":0,"y":1,"z":2}]}
        path=Path(root)/"by-mac"/self.mac/"reference-3d.json"
        path.parent.mkdir(parents=True,exist_ok=True)
        doc={"schema":1,"mac":self.mac,"led_count":1,"layout":layout,"sha256":mod.digest(layout),"name_at_archive":"Bush"}
        path.write_text(json.dumps(doc))
        return path
    def test_import_and_repeat(self):
        with tempfile.TemporaryDirectory() as root:
            self.write(root)
            incoming=mod.read_archives(root)
            merged,added,unchanged,skipped=mod.merge_archives({},incoming,{self.mac:1})
            self.assertEqual(added,["Bush"])
            self.assertFalse(skipped)
            again,added,unchanged,skipped=mod.merge_archives(merged,incoming,{self.mac:1})
            self.assertEqual(again,merged)
            self.assertEqual(unchanged,["Bush"])
            self.assertFalse(added)
    def test_mismatch(self):
        with tempfile.TemporaryDirectory() as root:
            self.write(root)
            incoming=mod.read_archives(root)
            for identities in ({},{self.mac:2}):
                merged,added,unchanged,skipped=mod.merge_archives({},incoming,identities)
                self.assertFalse(merged)
                self.assertTrue(skipped)
    def test_checksum(self):
        with tempfile.TemporaryDirectory() as root:
            path=self.write(root)
            doc=json.loads(path.read_text())
            doc["layout"]["coordinates"][0]["z"]=4
            path.write_text(json.dumps(doc))
            with self.assertRaises(ValueError):
                mod.read_archives(root)
    def test_conflict(self):
        with tempfile.TemporaryDirectory() as root:
            self.write(root)
            incoming=mod.read_archives(root)
            old={self.mac:{"led_count":1,"layout":{"source":"3d","synthesized":False,"coordinates":[{"x":0,"y":1,"z":7}]}}}
            merged,added,unchanged,skipped=mod.merge_archives(old,incoming,{self.mac:1})
            self.assertEqual(merged,old)
            self.assertTrue(skipped)
