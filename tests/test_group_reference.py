import importlib.util
from pathlib import Path
import copy
import unittest

spec=importlib.util.spec_from_file_location("group_reference",Path(__file__).resolve().parents[1]/"custom_components/twinkly_area_state/group_reference.py")
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def make(x):
    return {"led_count":2,"members":[
        {"mac":"001122334455","led_count":1,"offset":0,"size":2,"role":"master","layout":{"source":"2d","coordinates":[{"x":x,"y":0,"z":0}]}},
        {"mac":"001122334466","led_count":1,"offset":1,"size":2,"role":"slave","layout":{"source":"2d","coordinates":[{"x":1,"y":1,"z":0}]}}]}

class GroupReferenceTests(unittest.TestCase):
    def test_match_and_preserve_original(self):
        before=make(0); after=make(.2)
        candidate=copy.deepcopy(after)
        candidate["members"].reverse()
        uid,record=mod.match_candidate(candidate,{"uid":after},lambda a,b:a==b)
        self.assertEqual(uid,"uid")
        archive={"groups":{"uid":before},"maps_3d":{"immutable":{"layout":"3d"}}}
        original=copy.deepcopy(archive["maps_3d"])
        mod.replace_reference(archive,uid,record,"stamp")
        self.assertEqual(archive["groups"][uid],after)
        self.assertEqual(archive["group_replacement_backups"]["stamp"][uid],before)
        self.assertEqual(archive["maps_3d"],original)
    def test_reject_mismatch(self):
        for candidate in (make(0),{"led_count":1,"members":make(.2)["members"]}, {"led_count":2,"members":[make(.2)["members"][0]]*2}):
            with self.assertRaises(ValueError):
                mod.match_candidate(candidate,{"uid":make(.2)},lambda a,b:a==b)
        changed=make(.2);changed["members"][1]["offset"]=2
        with self.assertRaises(ValueError):
            mod.match_candidate(changed,{"uid":make(.2)},lambda a,b:a==b)
    def test_refuse_changed_existing_topology(self):
        old=make(0);old["members"][1]["offset"]=3
        archive={"groups":{"uid":old},"maps_3d":{}}
        with self.assertRaises(ValueError):
            mod.replace_reference(archive,"uid",make(.2),"stamp")
        self.assertEqual(archive["groups"]["uid"],old)
