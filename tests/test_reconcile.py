#!/usr/bin/env python3
"""Unit tests for scripts/reconcile_wikidata.py — ISNI-first lookup,
modern-label query stage, re-reconciliation of grown low-confidence clusters.

Pure stdlib, offline: api_get / load_cache / save_cache are monkeypatched.
Run: python3 -m unittest tests/test_reconcile.py -v
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import reconcile_wikidata as rw  # noqa: E402


def _snak(value):
    return {"mainsnak": {"datavalue": {"value": value}}}


DESCARTES = {
    "labels": {"fr": {"language": "fr", "value": "René Descartes"}},
    "descriptions": {"fr": {"language": "fr", "value": "philosophe français"}},
    "claims": {
        "P31": [_snak({"id": "Q5"})],
        "P213": [_snak("0000 0001 2129 6144")],
    },
    "sitelinks": {"frwiki": {}, "enwiki": {}},
}


class FakeApi:
    """Dispatches on action/list, records every params dict."""

    def __init__(self, isni_hits=None, entities=None):
        self.calls = []
        self.isni_hits = isni_hits or []
        self.entities = entities or {}

    def __call__(self, params, ck):
        self.calls.append(dict(params))
        action = params["action"]
        if action == "query" and params.get("list") == "search":
            return {"query": {"search": [{"title": q} for q in self.isni_hits]}}
        if action == "wbgetentities":
            ids = params["ids"].split("|")
            return {"entities": {q: self.entities[q] for q in ids if q in self.entities}}
        if action == "wbsearchentities":
            return {"search": []}
        raise AssertionError("unexpected call %r" % params)

    def searches(self):
        return [c["search"] for c in self.calls if c["action"] == "wbsearchentities"]


class ReconcileTest(unittest.TestCase):
    def setUp(self):
        self._orig = (rw.api_get, rw.load_cache, rw.save_cache)
        rw.load_cache = lambda: None
        rw.save_cache = lambda: None
        rw._cache = {}

    def tearDown(self):
        rw.api_get, rw.load_cache, rw.save_cache = self._orig
        rw._cache = {}

    def test_isni_wd_format(self):
        self.assertEqual(rw.isni_wd_format("0000000121296144"), "0000 0001 2129 6144")
        self.assertEqual(rw.isni_wd_format("0000 0001 2129 6144"), "0000 0001 2129 6144")

    def test_wbsearch_isni_query(self):
        fake = FakeApi(isni_hits=["Q9191"])
        rw.api_get = fake
        self.assertEqual(rw.wbsearch_isni("0000000121296144"), ["Q9191"])
        p = fake.calls[0]
        self.assertEqual(p["action"], "query")
        self.assertEqual(p["list"], "search")
        self.assertEqual(p["srsearch"], 'haswbstatement:"P213=0000 0001 2129 6144"')

    def test_reconcile_one_isni_path(self):
        fake = FakeApi(isni_hits=["Q9191"], entities={"Q9191": DESCARTES})
        rw.api_get = fake
        cl = {"type": "person", "label": "Descartes", "label_norm": "descartes",
              "mentions": 3, "isni": "0000000121296144"}
        rec = rw.reconcile_one(cl)
        self.assertEqual(rec["wikidata_qid"], "Q9191")
        self.assertEqual(rec["match_confidence"], "high")
        self.assertEqual(rec["match_method"], "isni")
        self.assertEqual(fake.searches(), [])

    def test_isni_hit_rejected_falls_through_to_search(self):
        # entity found by ISNI fails accept_place -> normal search stages still run
        fake = FakeApi(isni_hits=["Q9191"], entities={"Q9191": DESCARTES})
        rw.api_get = fake
        cl = {"type": "place", "label": "Descartes", "label_norm": "descartes",
              "mentions": 1, "isni": "0000000121296144"}
        self.assertIsNone(rw.reconcile_one(cl))
        self.assertEqual(fake.searches()[0], "Descartes")

    def test_modern_label_stage(self):
        fake = FakeApi()
        rw.api_get = fake
        cl = {"type": "person", "label": "CLAVDIVS CRAMOISY",
              "label_norm": "clavdivs cramoisy", "modern_label": "CLAUDIUS CRAMOISY",
              "mentions": 2, "isni": ""}
        self.assertIsNone(rw.reconcile_one(cl))
        s = fake.searches()
        self.assertEqual(s[0], "CLAVDIVS CRAMOISY")
        self.assertEqual(s[1], "CLAUDIUS CRAMOISY")

    def test_select_pending_redo_grown(self):
        key = ("person", "x")
        work = [{"type": "person", "label_norm": "x", "mentions": 7}]
        done_low = {key: {"mentions": 2, "match_confidence": "low"}}
        self.assertEqual(rw.select_pending(work, done_low), work)
        done_high = {key: {"mentions": 2, "match_confidence": "high"}}
        self.assertEqual(rw.select_pending(work, done_high), [])
        self.assertEqual(rw.select_pending(work, {}), work)
        done_big = {key: {"mentions": 6, "match_confidence": "low"}}
        self.assertEqual(rw.select_pending(work, done_big), [])


if __name__ == "__main__":
    unittest.main()
