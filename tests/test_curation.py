#!/usr/bin/env python3
"""Tests for scripts/curation.py (curation overlay). Offline, stdlib only."""
import csv
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from curation import (COLUMNS, DECISIONS, apply_curation, load_curation,  # noqa: E402
                      resolve_fusions, save_curation)
from idregistry import IdRegistry  # noqa: E402


def mk(xid, typ, label, mentions, ner_ids, rec=None):
    return {"type": typ, "label": label, "label_norm": label.lower(), "xmlid": xid,
            "mentions": mentions, "ner_ids": list(ner_ids), "source_docs": ["d1"],
            "variants": [], "examples": ["ex " + label], "rec": rec}


def stub_fetch(typ, qid):
    return {"wikidata_qid": qid, "wikidata_label": "L" + qid, "description": "d"}


def row(xid, **kw):
    r = {k: "" for k in COLUMNS}
    r["id"] = xid
    r.update(kw)
    return r


class CurationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reg = IdRegistry(os.path.join(self.tmp.name, "id_registry.csv"))
        self.reg.load()

    def tearDown(self):
        self.tmp.cleanup()

    def cl(self, typ, label, mentions, ner_ids, rec=None):
        xid = self.reg.assign(typ, label.lower(), ner_ids)
        return mk(xid, typ, label, mentions, ner_ids, rec)

    def apply(self, clusters, rows):
        return apply_curation(clusters, rows, self.reg, stub_fetch)

    def status(self, xid):
        p = os.path.join(self.tmp.name, "id_registry.csv")
        self.reg.save()
        with open(p, encoding="utf-8") as f:
            return {r["xml_id"]: r["status"] for r in csv.DictReader(f)}[xid]

    def test_columns_and_decisions(self):
        self.assertEqual(COLUMNS, ["id", "type", "decision", "qid_corrige", "fusion_vers",
                                   "label_corrige", "type_corrige", "commentaire", "qui",
                                   "date", "source"])
        self.assertEqual(set(DECISIONS), {"ok", "corriger", "fusionner", "supprimer", "doute", ""})

    def test_load_missing_file_is_empty(self):
        self.assertEqual(load_curation(os.path.join(self.tmp.name, "nope.csv")), {})

    def test_load_strips_and_lowercases_decision(self):
        p = os.path.join(self.tmp.name, "curation.csv")
        with open(p, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(COLUMNS)
            w.writerow([" person-000001 ", "person", " OK ", "", "", "", "", "", "ana", "", ""])
        rows = load_curation(p)
        self.assertEqual(list(rows), ["person-000001"])
        self.assertEqual(rows["person-000001"]["decision"], "ok")
        self.assertEqual(rows["person-000001"]["qui"], "ana")

    def test_save_then_load_roundtrip_sorted(self):
        p = os.path.join(self.tmp.name, "curation.csv")
        save_curation(p, {"place-000002": row("place-000002", decision="doute"),
                          "person-000001": row("person-000001", decision="ok")})
        with open(p, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], ",".join(COLUMNS))
        self.assertTrue(lines[1].startswith("person-000001"))
        self.assertEqual(load_curation(p)["place-000002"]["decision"], "doute")

    def test_resolve_fusions_chain(self):
        rows = {"A": row("A", decision="fusionner", fusion_vers="B"),
                "B": row("B", decision="fusionner", fusion_vers="C")}
        m, errs = resolve_fusions(rows, {"A", "B", "C"})
        self.assertEqual(m, {"A": "C", "B": "C"})
        self.assertEqual(errs, [])

    def test_resolve_fusions_cycle(self):
        rows = {"A": row("A", decision="fusionner", fusion_vers="B"),
                "B": row("B", decision="fusionner", fusion_vers="A")}
        m, errs = resolve_fusions(rows, {"A", "B"})
        self.assertEqual(m, {})
        self.assertEqual(len(errs), 1)
        self.assertIn("A", errs[0])
        self.assertIn("B", errs[0])

    def test_resolve_fusions_unknown_target_reported(self):
        rows = {"person-000001": row("person-000001", decision="fusionner",
                                     fusion_vers="person-999999")}
        m, errs = resolve_fusions(rows, {"person-000001"})
        self.assertEqual(m, {})
        self.assertEqual(len(errs), 1)
        self.assertIn("person-999999", errs[0])

    def test_resolve_fusions_self_reported(self):
        rows = {"A": row("A", decision="fusionner", fusion_vers="A")}
        m, errs = resolve_fusions(rows, {"A"})
        self.assertEqual(m, {})
        self.assertEqual(len(errs), 1)

    def test_ok_asserts_low_match(self):
        c = self.cl("person", "Platon", 3, ["u1"],
                    {"wikidata_qid": "Q859", "match_confidence": "low"})
        out, errs = self.apply([c], {c["xmlid"]: row(c["xmlid"], decision="ok", qui="ana")})
        self.assertEqual(errs, [])
        self.assertEqual(out[0]["rec"]["match_confidence"], "curated")
        self.assertEqual(out[0]["rec"]["wikidata_qid"], "Q859")
        self.assertEqual(out[0]["curation"]["decision"], "ok")
        self.assertEqual(out[0]["curation"]["qui"], "ana")
        self.assertEqual(c["rec"]["match_confidence"], "low")  # input not mutated

    def test_corriger_with_qid_fetches(self):
        c = self.cl("person", "Platon", 3, ["u1"],
                    {"wikidata_qid": "Q1", "match_confidence": "high"})
        out, errs = self.apply([c], {c["xmlid"]: row(c["xmlid"], decision="corriger",
                                                     qid_corrige="Q42")})
        self.assertEqual(errs, [])
        self.assertEqual(out[0]["rec"]["wikidata_qid"], "Q42")
        self.assertEqual(out[0]["rec"]["wikidata_label"], "LQ42")
        self.assertEqual(out[0]["rec"]["match_confidence"], "curated")
        self.assertEqual(out[0]["curation_note"], "")

    def test_corriger_qid_fetch_failure_keeps_bare_qid(self):
        c = self.cl("person", "Platon", 3, ["u1"])
        out, errs = apply_curation([c], {c["xmlid"]: row(c["xmlid"], decision="corriger",
                                                         qid_corrige="Q42")},
                                   self.reg, lambda t, q: None)
        self.assertEqual(out[0]["rec"], {"wikidata_qid": "Q42", "match_confidence": "curated"})
        self.assertEqual(len(errs), 1)

    def test_corriger_without_qid_rejects(self):
        c = self.cl("person", "Platon", 3, ["u1"],
                    {"wikidata_qid": "Q1", "match_confidence": "high"})
        out, errs = self.apply([c], {c["xmlid"]: row(c["xmlid"], decision="corriger")})
        self.assertIsNone(out[0]["rec"])
        self.assertEqual(out[0]["curation_note"], "rejected")

    def test_supprimer_drops_cluster(self):
        a = self.cl("person", "Platon", 3, ["u1"])
        b = self.cl("person", "Bruit", 1, ["u2"])
        out, errs = self.apply([a, b], {b["xmlid"]: row(b["xmlid"], decision="supprimer")})
        self.assertEqual([c["xmlid"] for c in out], [a["xmlid"]])
        self.assertEqual(self.status(b["xmlid"]), "deleted")

    def test_fusionner_merges_counts_and_ids(self):
        tgt = self.cl("person", "Lomenie", 5, ["u1", "u2"],
                      {"wikidata_qid": "Q7", "match_confidence": "high"})
        tgt["mentions_raw"] = 6
        src = self.cl("person", "Lomenius", 2, ["u3"],
                      {"wikidata_qid": "Q8", "match_confidence": "high"})
        src["mentions_raw"] = 2
        src["source_docs"] = ["d2"]
        out, errs = self.apply([tgt, src], {src["xmlid"]: row(src["xmlid"], decision="fusionner",
                                                              fusion_vers=tgt["xmlid"])})
        self.assertEqual(errs, [])
        self.assertEqual(len(out), 1)
        m = out[0]
        self.assertEqual(m["xmlid"], tgt["xmlid"])
        self.assertEqual(m["mentions"], 7)
        self.assertEqual(m["mentions_raw"], 8)
        self.assertEqual(m["ner_ids"], ["u1", "u2", "u3"])
        self.assertEqual(m["source_docs"], ["d1", "d2"])
        self.assertIn("Lomenius", m["variants"])
        self.assertEqual(m["examples"], ["ex Lomenie", "ex Lomenius"])
        self.assertEqual(m["rec"]["wikidata_qid"], "Q7")
        self.assertEqual(self.status(src["xmlid"]), "merged")

    def test_fusion_into_deleted_target_skipped(self):
        tgt = self.cl("person", "Lomenie", 5, ["u1"])
        src = self.cl("person", "Lomenius", 2, ["u3"])
        out, errs = self.apply([tgt, src], {
            tgt["xmlid"]: row(tgt["xmlid"], decision="supprimer"),
            src["xmlid"]: row(src["xmlid"], decision="fusionner", fusion_vers=tgt["xmlid"])})
        self.assertEqual([c["xmlid"] for c in out], [src["xmlid"]])
        self.assertEqual(out[0]["mentions"], 2)
        self.assertEqual(len(errs), 1)
        self.assertIn(tgt["xmlid"], errs[0])
        self.assertEqual(self.status(src["xmlid"]), "active")

    def test_cross_type_fusion_skipped(self):
        tgt = self.cl("place", "Rome", 5, ["u1"])
        src = self.cl("person", "Rome", 2, ["u3"])
        out, errs = self.apply([tgt, src], {
            src["xmlid"]: row(src["xmlid"], decision="fusionner", fusion_vers=tgt["xmlid"])})
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["mentions"], 5)
        self.assertEqual(len(errs), 1)
        self.assertIn("type", errs[0])
        self.assertEqual(self.status(src["xmlid"]), "active")

    def test_doute_note(self):
        rec = {"wikidata_qid": "Q1", "match_confidence": "medium"}
        c = self.cl("person", "Platon", 3, ["u1"], rec)
        out, errs = self.apply([c], {c["xmlid"]: row(c["xmlid"], decision="doute")})
        self.assertEqual(out[0]["curation_note"], "doute")
        self.assertEqual(out[0]["rec"], rec)

    def test_label_and_type_corrige(self):
        c = self.cl("org", "Platon", 3, ["u1"])
        old = c["xmlid"]
        out, errs = self.apply([c], {old: row(old, label_corrige="Platon d'Athenes",
                                               type_corrige="person")})
        self.assertEqual(errs, [])
        o = out[0]
        self.assertEqual(o["label"], "Platon d'Athenes")
        self.assertEqual(o["type"], "person")
        self.assertTrue(o["xmlid"].startswith("person-"))
        self.assertEqual(o["orig_xmlid"], old)
        self.assertEqual(self.status(old), "deleted")
        self.assertEqual(self.status(o["xmlid"]), "active")

    def test_retype_onto_existing_key_merges_instead_of_duplicating(self):
        p = self.cl("person", "Platon", 4, ["u1"])
        o = self.cl("org", "Platon", 1, ["u2"])
        out, errs = self.apply([p, o], {o["xmlid"]: row(o["xmlid"], type_corrige="person")})
        self.assertEqual([c["xmlid"] for c in out], [p["xmlid"]])
        self.assertEqual(out[0]["mentions"], 5)
        self.assertEqual(out[0]["ner_ids"], ["u1", "u2"])

    def test_unknown_decision_reported_and_ignored(self):
        rec = {"wikidata_qid": "Q1", "match_confidence": "low"}
        c = self.cl("person", "Platon", 3, ["u1"], rec)
        out, errs = self.apply([c], {c["xmlid"]: row(c["xmlid"], decision="yolo",
                                                     label_corrige="X")})
        self.assertEqual(len(errs), 1)
        self.assertIn("yolo", errs[0])
        self.assertEqual(out[0]["label"], "Platon")
        self.assertEqual(out[0]["rec"], rec)
        self.assertEqual(out[0]["curation"]["decision"], "")

    def test_unknown_id_reported(self):
        c = self.cl("person", "Platon", 3, ["u1"])
        out, errs = self.apply([c], {"person-000099": row("person-000099", decision="ok")})
        self.assertEqual(len(out), 1)
        self.assertEqual(len(errs), 1)
        self.assertIn("person-000099", errs[0])


if __name__ == "__main__":
    unittest.main()
