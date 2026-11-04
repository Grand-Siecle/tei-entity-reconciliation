#!/usr/bin/env python3
"""Tests de scripts/extract_entities.py sur des TEI de forme reelle (orig/reg).

    python3 -m unittest tests/test_extract.py -v
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPT = os.path.join(REPO, "scripts", "extract_entities.py")
SAMPLE = os.path.join(REPO, "examples", "sample")
ORIGREG = os.path.join(SAMPLE, "data", "sample_origreg.tei.xml")
DEMO = os.path.join(SAMPLE, "data", "sample_reconciled.tei.xml")
ENTITIES_DIR = os.path.join(SAMPLE, "entities")
PERSONS_CSV = os.path.join(SAMPLE, "metadata_personne_sample.csv")


def load_module():
    spec = importlib.util.spec_from_file_location("extract_entities", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_extract(tei_glob, out_dir, **extra_env):
    env = dict(os.environ)
    env.update({"TEI_GLOB": tei_glob, "NER_OUT": out_dir,
                "ENTITIES_DIR": ENTITIES_DIR, "PERSONS_CSV": PERSONS_CSV})
    env.update(extra_env)
    proc = subprocess.run([sys.executable, SCRIPT], env=env, cwd=tempfile.gettempdir(),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)
    clusters = None
    path = os.path.join(out_dir, "entities_clusters.json")
    if proc.returncode == 0 and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            clusters = json.load(f)
    return proc, clusters


def by_ner(clusters, ner_id):
    for c in clusters:
        if ner_id in c["ner_ids"]:
            return c
    raise AssertionError("no cluster for %s" % ner_id)


class OrigRegExtraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()
        cls.tmp = tempfile.mkdtemp()
        cls.proc, cls.clusters = run_extract(ORIGREG, cls.tmp)
        if cls.proc.returncode != 0:
            raise AssertionError("extract failed:\n" + cls.proc.stderr)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_mentions_exclude_reg_copies(self):
        c = by_ner(self.clusters, "pers-aaaa0001")
        self.assertEqual(c["mentions"], 2)
        self.assertEqual(c["mentions_raw"], 4)
        b = by_ner(self.clusters, "pers-bbbb0002")
        self.assertEqual((b["mentions"], b["mentions_raw"]), (1, 2))
        m = by_ner(self.clusters, "mat-cccc0003")
        self.assertEqual((m["mentions"], m["mentions_raw"]), (1, 1))

    def test_modern_label_and_forms(self):
        c = by_ner(self.clusters, "pers-aaaa0001")
        self.assertEqual(c["modern_label"], "JOANNEM DU BRAY")
        self.assertIn("JOANNEM DU BRAY", c["modern_forms"])
        m = by_ner(self.clusters, "mat-cccc0003")
        self.assertEqual(m["modern_label"], "")
        self.assertEqual(m["modern_forms"], [])

    def test_word_parts_joined(self):
        c = by_ner(self.clusters, "pers-bbbb0002")
        self.assertEqual(c["label"], "Lepidus")
        self.assertNotIn("Lepi dus", c["variants"])

    def test_doc_id_from_root_xmlid(self):
        for c in self.clusters:
            self.assertEqual(c["source_docs"], ["SAMPLE2"])

    def test_doc_id_strips_suffix(self):
        root = ET.fromstring('<TEI xmlns="http://www.tei-c.org/ns/1.0"/>')
        self.assertEqual(self.mod.doc_id("x/LIV0010.tei.xml", root), "LIV0010")
        self.assertEqual(self.mod.doc_id("x/LIV0010_reconciled.tei.xml", root), "LIV0010")
        self.assertEqual(self.mod.doc_id("x/LIV0010_t1.xml", root), "LIV0010_t1")
        rid = ET.fromstring('<TEI xmlns="http://www.tei-c.org/ns/1.0" '
                            'xml:id="LIV0099"/>')
        self.assertEqual(self.mod.doc_id("x/whatever.tei.xml", rid), "LIV0099")

    def test_cert_majority(self):
        c = by_ner(self.clusters, "pers-aaaa0001")
        self.assertEqual(c["cert_counts"], {"high": 1, "medium": 1})
        self.assertEqual(c["cert"], "high")
        self.assertEqual(self.mod.majority_cert({}), "")
        self.assertEqual(self.mod.majority_cert({"low": 3, "high": 1}), "low")

    def test_examples_from_orig_sentence(self):
        c = by_ner(self.clusters, "pers-aaaa0001")
        self.assertEqual(len(c["examples"]), 2)
        self.assertIn("IOANNEM DV BRAY", c["examples"][0])
        self.assertTrue(all(len(x) <= 300 for x in c["examples"]))
        b = by_ner(self.clusters, "pers-bbbb0002")
        self.assertEqual(b["examples"][0], "vir Lepidus fuit")

    def test_local_authority_fields(self):
        c = by_ner(self.clusters, "pers-aaaa0001")
        self.assertEqual(c["local_ids"], ["PERS0001"])
        self.assertEqual(c["local_id"], "PERS0001")
        self.assertEqual(c["isni"], "0000000121296144")
        self.assertTrue(c["bnf_ark"].startswith("ark:"))
        self.assertEqual(c["local_label"], "Jean Du Bray")
        self.assertEqual(c["local_birth"], "")
        self.assertEqual(c["local_death"], "1629")
        b = by_ner(self.clusters, "pers-bbbb0002")
        self.assertEqual((b["local_ids"], b["local_id"], b["isni"]), ([], "", ""))

    def test_year_of(self):
        y = self.mod.year_of
        self.assertEqual(y("1596/03/31"), "1596")
        self.assertEqual(y("15.."), "")
        self.assertEqual(y("1629?"), "1629")
        self.assertEqual(y(""), "")

    def test_csv_has_new_columns(self):
        with open(os.path.join(self.tmp, "entities.csv"), encoding="utf-8") as f:
            header = f.readline().strip().split(",")
        self.assertEqual(header[-3:], ["cert", "local_id", "isni"])

    def test_summary_line(self):
        self.assertIn("Mentions (hors copies <reg>) : 4 | brutes : 7", self.proc.stdout)


class OtherInputs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_file_without_ner(self):
        tei = os.path.join(self.tmp, "LIV9999.tei.xml")
        with open(tei, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader/>'
                    '<text><body><p>rien</p></body></text></TEI>\n')
        out = os.path.join(self.tmp, "out")
        proc, clusters = run_extract(tei, out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(clusters, [])
        self.assertIn("Fichiers traites : 1", proc.stdout)

    def test_demo_fixture_unchanged_behaviour(self):
        out = os.path.join(self.tmp, "out")
        proc, clusters = run_extract(DEMO, out, ENTITIES_DIR="", PERSONS_CSV="")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len({c["type"] for c in clusters}), 9)
        c = by_ner(clusters, "pers-0001")
        self.assertEqual(c["mentions"], 2)
        self.assertEqual(c["mentions_raw"], 2)
        self.assertTrue(c["examples"])
        self.assertEqual(c["source_docs"], ["sample"])


if __name__ == "__main__":
    unittest.main()
