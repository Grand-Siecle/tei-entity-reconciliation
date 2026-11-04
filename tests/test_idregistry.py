#!/usr/bin/env python3
"""Tests for scripts/idregistry.py (stable register ids). Offline, stdlib only."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from idregistry import IdRegistry, PREFIX  # noqa: E402


class IdRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "id_registry.csv")

    def tearDown(self):
        self.tmp.cleanup()

    def reg(self):
        r = IdRegistry(self.path)
        r.load()
        return r

    def test_prefixes_cover_nine_types(self):
        self.assertEqual(len(PREFIX), 9)
        self.assertEqual(PREFIX["person"], "person-")
        self.assertEqual(PREFIX["material"], "material-")

    def test_new_ids_sequential_per_type(self):
        r = self.reg()
        self.assertEqual(r.assign("person", "platon", ["u1"]), "person-000001")
        self.assertEqual(r.assign("place", "athenes", ["u2"]), "place-000001")
        self.assertEqual(r.assign("person", "aristote", ["u3"]), "person-000002")

    def test_same_key_same_id_across_reload(self):
        r = self.reg()
        r.assign("person", "zzz", ["u0"])
        xid = r.assign("person", "platon", ["u1"])
        r.save()
        r2 = self.reg()
        self.assertEqual(r2.assign("person", "platon", ["u9"]), xid)
        r2.save()
        with open(self.path, encoding="utf-8") as f:
            body = f.read()
        self.assertIn("u1|u9", body)
        self.assertTrue(body.startswith("type,label_norm,xml_id,status,ner_ids,created,updated"))

    def test_assign_by_uuid_overlap_when_label_changes(self):
        r = self.reg()
        xid = r.assign("person", "lomenius", ["u1", "u2"])
        r.save()
        r2 = self.reg()
        self.assertEqual(r2.assign("person", "lomenie", ["u2", "u3"]), xid)
        self.assertEqual(r2.get("person", "lomenie"), xid)
        self.assertIsNone(r2.get("person", "lomenius"))

    def test_overlap_not_reused_by_two_clusters_in_one_session(self):
        r = self.reg()
        xid = r.assign("person", "lomenius", ["u1", "u2"])
        r.save()
        r2 = self.reg()
        self.assertEqual(r2.assign("person", "lomenie", ["u1"]), xid)
        self.assertNotEqual(r2.assign("person", "lomenie de brienne", ["u2"]), xid)

    def test_ids_never_reused_after_delete(self):
        r = self.reg()
        a = r.assign("person", "a", ["u1"])
        self.assertEqual(a, "person-000001")
        r.mark(a, "deleted")
        self.assertEqual(r.assign("person", "b", ["u2"]), "person-000002")
        self.assertIn(a, r.ids())

    def test_deleted_key_reassigned_reactivates(self):
        r = self.reg()
        a = r.assign("person", "a", ["u1"])
        r.mark(a, "deleted")
        r.save()
        r2 = self.reg()
        self.assertEqual(r2.assign("person", "a", ["u1"]), a)
        r2.save()
        with open(self.path, encoding="utf-8") as f:
            self.assertIn("person,a,person-000001,active,u1", f.read())

    def test_type_prefix_unknown_raises(self):
        r = self.reg()
        with self.assertRaises(ValueError):
            r.assign("unknown", "x", ["u1"])


if __name__ == "__main__":
    unittest.main()
