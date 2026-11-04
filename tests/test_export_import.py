#!/usr/bin/env python3
"""Unit tests for scripts/export_lots.py and scripts/import_curation.py.

Offline, stdlib only:  python3 -m unittest tests/test_export_import.py -v
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import curation  # noqa: E402
import export_lots  # noqa: E402
import import_curation  # noqa: E402

LOT_COLUMNS = (
    "id,type,label,label_moderne,variantes,mentions,nb_docs,docs,cert_ner,"
    "exemple_1,exemple_2,qid_propose,label_wikidata,description_wikidata,"
    "confiance,candidats,lien_wikidata,local_id,isni,naissance,deces,pays,"
    "statut,decision,qid_corrige,fusion_vers,label_corrige,type_corrige,"
    "commentaire,qui,date").split(",")


def cluster(xmlid, typ, label, mentions, rec=None, local_id="", isni=""):
    return {
        "xmlid": xmlid, "type": typ, "label": label,
        "label_norm": label.lower(), "modern_label": label,
        "variants": [label, label + "s"], "mentions": mentions,
        "source_docs": ["LIV0001", "LIV0002"], "cert": "high",
        "examples": ["ex un " + label, "ex deux " + label],
        "local_id": local_id, "isni": isni,
        "local_birth": "1600", "local_death": "1650",
        "rec": rec, "curation": {},
    }


def registered():
    rec = {"wikidata_qid": "Q1", "wikidata_label": "Lomenie",
           "description": "homme d'Etat", "match_confidence": "low",
           "wikidata_alts": ["Q2"], "birth": "1595", "death": "1666",
           "country_qid": "Q142"}
    return [
        cluster("person-000001", "person", "Lomenie", 10, rec=rec),
        cluster("person-000002", "person", "Colbert", 3,
                local_id="PERS0037", isni="0000000121234567"),
        cluster("person-000003", "person", "Rare", 1),
        cluster("place-000001", "place", "Paris", 4),
    ]


def read_csv(path, delimiter=",", encoding="utf-8"):
    with open(path, encoding=encoding, newline="") as f:
        return list(csv.reader(f, delimiter=delimiter))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ner = os.path.join(self.tmp, "ner")
        os.makedirs(self.ner)
        self.cur = os.path.join(self.tmp, "registers", "curation.csv")
        os.makedirs(os.path.dirname(self.cur))
        self.out = os.path.join(self.tmp, "lots")
        self.write_registered(registered())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write_registered(self, data):
        with open(os.path.join(self.ner, "entities_registered.json"), "w",
                  encoding="utf-8") as f:
            json.dump(data, f)

    def env(self):
        e = dict(os.environ)
        e["NER_OUT"] = self.ner
        e["CURATION_CSV"] = self.cur
        return e

    def run_script(self, name, *args):
        p = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, name)] + list(args),
            env=self.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, cwd=self.tmp)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def export(self, *args):
        return self.run_script("export_lots.py", "--out", self.out, *args)

    def lot_rows(self, name):
        with open(os.path.join(self.out, name), encoding="utf-8",
                  newline="") as f:
            return list(csv.DictReader(f))


class TestExport(Base):
    def test_lot_columns_and_order(self):
        self.export()
        rows = read_csv(os.path.join(self.out, "person_lot01.csv"))
        self.assertEqual(rows[0], LOT_COLUMNS)
        self.assertEqual(rows[0], read_csv(
            os.path.join(self.out, "place_lot01.csv"))[0])

    def test_min_mentions_filters_singleton(self):
        self.export()
        ids = [r["id"] for r in self.lot_rows("person_lot01.csv")]
        self.assertEqual(ids, ["person-000001", "person-000002"])

    def test_status_new_then_unchanged_then_modified(self):
        self.export()
        self.assertTrue(os.path.exists(os.path.join(self.ner, "lots_state.json")))
        self.assertEqual({r["statut"] for r in self.lot_rows("person_lot01.csv")},
                         {"nouveau"})
        self.export()
        self.assertEqual({r["statut"] for r in self.lot_rows("person_lot01.csv")},
                         {"inchangé"})
        data = registered()
        data[1]["label"] = "Colbert de Croissy"
        self.write_registered(data)
        self.export()
        st = {r["id"]: r["statut"] for r in self.lot_rows("person_lot01.csv")}
        self.assertEqual(st, {"person-000001": "inchangé",
                              "person-000002": "modifié"})

    def test_only_new(self):
        self.export()
        data = registered()
        data[1]["label"] = "Colbert de Croissy"
        self.write_registered(data)
        self.export("--only-new")
        ids = [r["id"] for r in self.lot_rows("person_lot01.csv")]
        self.assertEqual(ids, ["person-000002"])
        # place has nothing new: its lot from the first export is kept
        self.assertEqual(len(self.lot_rows("place_lot01.csv")), 1)
        self.assertEqual([m["file"] for m in self.lot_rows("lots_manifest.csv")],
                         ["person_lot01.csv", "place_lot01.csv"])

    def test_types_keeps_other_types_lots(self):
        self.export()
        self.export("--types", "person")
        self.assertTrue(os.path.exists(os.path.join(self.out, "place_lot01.csv")))
        self.assertEqual([m["file"] for m in self.lot_rows("lots_manifest.csv")],
                         ["person_lot01.csv", "place_lot01.csv"])

    def touch_later(self, name, seconds=10):
        path = os.path.join(self.out, name)
        t = os.path.getmtime(path) + seconds
        os.utime(path, (t, t))

    def edit_lot(self, name, decision="supprimer"):
        """Simulate a reviewer: set the first row's decision, save in place."""
        path = os.path.join(self.out, name)
        rows = read_csv(path)
        rows[1][rows[0].index("decision")] = decision
        with open(path, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerows(rows)
        self.touch_later(name)
        return path

    def export_refused(self, *args):
        p = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "export_lots.py"),
             "--out", self.out] + list(args),
            env=self.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True)
        self.assertNotEqual(p.returncode, 0)
        return p

    def test_edited_lot_refused_then_forced(self):
        self.export("--max-rows", "1")
        self.edit_lot("person_lot02.csv")
        p = self.export_refused("--max-rows", "1")
        self.assertIn("person_lot02.csv", p.stderr)
        self.assertIn("non importe", p.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.out, "person_lot02.csv")))
        self.export("--max-rows", "2", "--force")
        self.assertFalse(os.path.exists(os.path.join(self.out, "person_lot02.csv")))

    def test_edited_then_imported_lot_replaced_without_force(self):
        self.export()
        path = self.edit_lot("person_lot01.csv")
        self.export_refused()
        self.run_script("import_curation.py", path)
        with open(os.path.join(self.out, ".lots_written.json")) as f:
            side = json.load(f)
        self.assertEqual(side["person_lot01.csv"]["imported"],
                         [export_lots.file_sha256(path)])
        self.export()
        self.assertEqual(self.lot_rows("person_lot01.csv")[0]["decision"],
                         "supprimer")   # pre-filled from curation.csv

    def test_copied_lot_same_content_not_refused(self):
        self.export()
        path = os.path.join(self.out, "person_lot01.csv")
        copy = os.path.join(self.tmp, "copy.csv")
        shutil.copy(path, copy)
        shutil.copy(copy, path)        # same content, new mtime
        self.touch_later("person_lot01.csv")
        self.export()

    def test_edited_other_type_survives_manifest_rewrite(self):
        self.export()
        self.edit_lot("place_lot01.csv")
        self.export("--types", "person")   # rewrites the manifest
        with self.assertRaises(export_lots.LotsEditedError):
            export_lots.write_lots(
                [{"type": "place", "statut": ""}], self.out, 500, False)
        self.assertTrue(os.path.exists(os.path.join(self.out, "place_lot01.csv")))

    def test_unlisted_target_refused(self):
        os.makedirs(self.out)
        with open(os.path.join(self.out, "person_lot01.csv"), "w") as f:
            f.write("mes notes\n")
        with self.assertRaises(export_lots.LotsEditedError):
            export_lots.write_lots(
                [{"type": "person", "statut": ""}], self.out, 500, False)

    def test_split_max_rows(self):
        self.export("--max-rows", "2", "--min-mentions", "1",
                    "--types", "person")
        self.assertEqual(len(self.lot_rows("person_lot01.csv")), 2)
        self.assertEqual(len(self.lot_rows("person_lot02.csv")), 1)
        self.assertFalse(os.path.exists(os.path.join(self.out, "place_lot01.csv")))
        man = self.lot_rows("lots_manifest.csv")
        self.assertEqual([m["file"] for m in man],
                         ["person_lot01.csv", "person_lot02.csv"])
        self.assertEqual(list(man[0].keys()),
                         ["file", "type", "rows", "nouveau", "modifié", "inchangé"])
        self.assertEqual(man[0]["rows"], "2")
        self.assertEqual(man[1]["nouveau"], "1")

    def test_prefilled_decision_from_curation(self):
        curation.save_curation(self.cur, {"person-000001": {
            "id": "person-000001", "type": "person", "decision": "ok",
            "commentaire": "vu", "qui": "anne", "date": "2026-11-01",
            "source": "x.csv"}})
        self.export()
        row = self.lot_rows("person_lot01.csv")[0]
        self.assertEqual(row["id"], "person-000001")
        self.assertEqual(row["decision"], "ok")
        self.assertEqual(row["commentaire"], "vu")
        self.assertEqual(row["qui"], "anne")

    def test_excel_mode(self):
        self.export("--excel")
        path = os.path.join(self.out, "person_lot01.csv")
        with open(path, "rb") as f:
            self.assertTrue(f.read().startswith(b"\xef\xbb\xbf"))
        rows = read_csv(path, ";", "utf-8-sig")
        self.assertEqual(rows[0], LOT_COLUMNS)

    def test_lien_and_candidats(self):
        rows = {r["id"]: r for r in
                export_lots.build_rows(registered(), {}, {"Q142": "France"})}
        r = rows["person-000001"]
        self.assertEqual(r["lien_wikidata"], "https://www.wikidata.org/wiki/Q1")
        self.assertEqual(r["candidats"], "Q2")
        self.assertEqual(r["confiance"], "low")
        self.assertEqual(r["naissance"], "1595")
        self.assertEqual(r["pays"], "France")
        self.assertEqual(r["nb_docs"], 2)
        self.assertEqual(list(r.keys()), LOT_COLUMNS)
        r2 = rows["person-000002"]
        self.assertEqual(r2["lien_wikidata"], "")
        self.assertEqual(r2["qid_propose"], "")
        self.assertEqual(r2["local_id"], "PERS0037")
        self.assertEqual(r2["naissance"], "1600")

    def test_signature_and_status_for(self):
        sig = export_lots.signature(registered()[0])
        self.assertEqual(sig, "Lomenie|Q1")
        self.assertEqual(export_lots.status_for("person-000001", sig, {}),
                         "nouveau")
        st = {"person-000001": {"sig": sig, "exported": "2026-11-04"}}
        self.assertEqual(export_lots.status_for("person-000001", sig, st),
                         "inchangé")
        self.assertEqual(export_lots.status_for("person-000001", "x", st),
                         "modifié")


class TestImport(Base):
    def test_read_excel_style_csv(self):
        path = os.path.join(self.tmp, "edited.csv")
        with open(path, "wb") as f:
            f.write("﻿id;type;decision;commentaire;;\r\n".encode("utf-8"))
            f.write(" person-000001 ;person; OK ;très bien;;\r\n".encode("utf-8"))
        rows = import_curation.read_any_csv(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "person-000001")
        self.assertEqual(rows[0]["commentaire"], "très bien")
        self.assertEqual(rows[0]["decision"], "OK")

    def test_truthy(self):
        for v in ("true", "TRUE", "1", "oui", "x", "yes", " Vrai "):
            self.assertTrue(import_curation.truthy(v), v)
        for v in ("", "false", "0", "non", None):
            self.assertFalse(import_curation.truthy(v), v)

    def test_decide_explicit_decision_kept_when_qid_final_agrees(self):
        d = import_curation.decide_row({
            "id": "person-000001", "type": "person", "decision": "corriger",
            "qid_corrige": "Q5", "qid_propose": "Q1", "qid_final": "Q5"})
        self.assertEqual((d["decision"], d["qid_corrige"]), ("corriger", "Q5"))
        self.assertEqual(list(d.keys()), curation.COLUMNS)
        d = import_curation.decide_row({
            "id": "p", "decision": "corriger", "qid_corrige": "Q5",
            "qid_propose": "Q1"})
        self.assertEqual((d["decision"], d["qid_corrige"]), ("corriger", "Q5"))

    def test_decide_qid_final_beats_prefilled_decision(self):
        d = import_curation.decide_row({
            "id": "p", "decision": "corriger", "qid_corrige": "Q5",
            "qid_propose": "Q1", "qid_final": "Q9"})
        self.assertEqual((d["decision"], d["qid_corrige"]), ("corriger", "Q9"))
        d = import_curation.decide_row({
            "id": "p", "decision": "ok", "qid_propose": "Q1",
            "qid_final": "Q7"})
        self.assertEqual((d["decision"], d["qid_corrige"]), ("corriger", "Q7"))
        d = import_curation.decide_row({
            "id": "p", "decision": "ok", "qid_propose": "Q1",
            "qid_final": "Q1"})
        self.assertEqual(d["decision"], "ok")

    def test_decide_doute_keeps_when_qid_final_agrees(self):
        base = {"id": "p", "decision": "doute", "qid_propose": "Q1"}
        d = import_curation.decide_row(dict(base, qid_final="Q1"))
        self.assertEqual(d["decision"], "doute")
        d = import_curation.decide_row(dict(base, qid_final=""))
        self.assertEqual(d["decision"], "doute")
        d = import_curation.decide_row(dict(base, qid_final="AUCUN"))
        self.assertEqual((d["decision"], d["qid_corrige"]), ("corriger", ""))
        d = import_curation.decide_row(dict(base, qid_final="Q7"))
        self.assertEqual((d["decision"], d["qid_corrige"]), ("corriger", "Q7"))

    def test_decide_qid_final_url(self):
        d = import_curation.decide_row({
            "id": "p", "qid_propose": "Q1",
            "qid_final": "https://www.wikidata.org/wiki/Q42"})
        self.assertEqual((d["decision"], d["qid_corrige"]), ("corriger", "Q42"))

    def test_decide_qid_final_equal_is_ok(self):
        d = import_curation.decide_row({"id": "p", "type": "person",
                                        "qid_propose": "Q1", "qid_final": "Q1"})
        self.assertEqual(d["decision"], "ok")

    def test_decide_qid_final_differs_is_corriger(self):
        d = import_curation.decide_row({"id": "p", "type": "person",
                                        "qid_propose": "Q1", "qid_final": "Q7"})
        self.assertEqual((d["decision"], d["qid_corrige"]), ("corriger", "Q7"))

    def test_decide_qid_final_empty_is_no_information(self):
        self.assertIsNone(import_curation.decide_row(
            {"id": "p", "type": "person", "qid_propose": "Q1", "qid_final": ""}))
        d = import_curation.decide_row({"id": "p", "decision": "ok",
                                        "qid_propose": "Q1", "qid_final": ""})
        self.assertEqual(d["decision"], "ok")

    def test_decide_qid_final_aucun(self):
        d = import_curation.decide_row({"id": "p", "qid_propose": "Q1",
                                        "qid_final": "AUCUN"})
        self.assertEqual((d["decision"], d["qid_corrige"]), ("corriger", ""))
        d = import_curation.decide_row({"id": "p", "qid_propose": "Q1",
                                        "qid_final": "aucun"})
        self.assertEqual((d["decision"], d["qid_corrige"]), ("corriger", ""))
        d = import_curation.decide_row({"id": "p", "decision": "corriger",
                                        "qid_corrige": "Q5",
                                        "qid_propose": "Q1",
                                        "qid_final": "AUCUN"})
        self.assertEqual((d["decision"], d["qid_corrige"]), ("corriger", ""))
        # nothing implied: AUCUN brings no information
        self.assertIsNone(import_curation.decide_row(
            {"id": "p", "qid_propose": "", "qid_final": "AUCUN"}))

    def test_decide_star_beats_qid_final_and_decision(self):
        d = import_curation.decide_row({"id": "p", "qid_propose": "Q1",
                                        "qid_final": "Q1", "starred": "true"})
        self.assertEqual(d["decision"], "supprimer")
        d = import_curation.decide_row({"id": "p", "decision": "ok",
                                        "starred": "true"})
        self.assertEqual(d["decision"], "supprimer")

    def test_decide_flag_beats_inferred_ok(self):
        d = import_curation.decide_row({"id": "p", "qid_propose": "Q1",
                                        "qid_final": "Q1", "qid_juge": "single",
                                        "flagged": "true"})
        self.assertEqual((d["decision"], d["qid_corrige"]), ("doute", ""))
        d = import_curation.decide_row({"id": "p", "qid_propose": "Q1",
                                        "qid_final": "", "flagged": "true"})
        self.assertEqual(d["decision"], "doute")
        # a flagged row whose QID was changed keeps that QID as information
        d = import_curation.decide_row({"id": "p", "qid_propose": "Q1",
                                        "qid_final": "Q7", "doute": "true"})
        self.assertEqual((d["decision"], d["qid_corrige"]), ("doute", "Q7"))
        d = import_curation.decide_row({"id": "p", "decision": "ok",
                                        "qid_propose": "Q1", "doute": "true"})
        self.assertEqual(d["decision"], "doute")

    def mass_row(self, confiance, **kw):
        r = {"id": "p", "type": "person", "qid_propose": "Q1",
             "qid_final": "Q1", "qid_juge": "mass", "confiance": confiance,
             "supprimer": "false", "doute": "false"}
        r.update(kw)
        return r

    def test_decide_untouched_mass_row_needs_complete_lot(self):
        for conf in ("high", "medium"):
            self.assertIsNone(import_curation.decide_row(self.mass_row(conf)))
            d = import_curation.decide_row(self.mass_row(conf), complete=True)
            self.assertEqual((d["decision"], d["qid_corrige"]), ("ok", "Q1"))
            # an empty judgement counts as mass (not explicit)
            self.assertIsNone(import_curation.decide_row(
                self.mass_row(conf, qid_juge="")))

    def test_decide_untouched_mass_low_never_ok(self):
        self.assertIsNone(import_curation.decide_row(self.mass_row("low")))
        self.assertIsNone(import_curation.decide_row(self.mass_row("low"),
                                                     complete=True))

    def test_decide_explicit_judgement_equal_to_proposal_is_ok(self):
        for how in ("single", "similar"):
            d = import_curation.decide_row(self.mass_row("low", qid_juge=how))
            self.assertEqual((d["decision"], d["qid_corrige"]), ("ok", "Q1"))

    def test_decide_spreadsheet_without_qid_juge_is_ok(self):
        d = import_curation.decide_row({"id": "p", "qid_propose": "Q1",
                                        "qid_final": "Q1", "confiance": "low"})
        self.assertEqual((d["decision"], d["qid_corrige"]), ("ok", "Q1"))
        d = import_curation.decide_row({"id": "p", "decision": "ok",
                                        "qid_propose": "Q1"})
        self.assertEqual((d["decision"], d["qid_corrige"]), ("ok", "Q1"))
        d = import_curation.decide_row({"id": "p", "decision": "ok"})
        self.assertEqual((d["decision"], d["qid_corrige"]), ("ok", ""))

    def test_is_complete_file(self):
        self.assertTrue(import_curation.is_complete_file(
            "/x/person_lot01_relu_marie_complet.csv"))
        self.assertTrue(import_curation.is_complete_file("LOT_COMPLET.csv"))
        self.assertFalse(import_curation.is_complete_file(
            "person_lot01_relu_marie.csv"))
        self.assertFalse(import_curation.is_complete_file(
            "person_lot01_incomplet.csv"))

    def test_decide_inferred_corriger_and_fusionner(self):
        d = import_curation.decide_row({"id": "p", "qid_corrige": "Q5"})
        self.assertEqual((d["decision"], d["qid_corrige"]), ("corriger", "Q5"))
        d = import_curation.decide_row({"id": "p", "fusion_vers": "person-000009"})
        self.assertEqual((d["decision"], d["fusion_vers"]),
                         ("fusionner", "person-000009"))
        d = import_curation.decide_row({"id": "p", "decision": "fusionner",
                                        "fusion_vers": "x", "qid_propose": "Q1",
                                        "qid_final": "Q7"})
        self.assertEqual(d["decision"], "fusionner")

    def test_decide_star_and_flag(self):
        base = {"id": "p", "type": "person"}
        d = import_curation.decide_row(dict(base, starred="true"))
        self.assertEqual(d["decision"], "supprimer")
        d = import_curation.decide_row(dict(base, flagged="1"))
        self.assertEqual(d["decision"], "doute")
        d = import_curation.decide_row(dict(base, starred="true", flagged="1"))
        self.assertEqual(d["decision"], "supprimer")
        d = import_curation.decide_row(dict(base, supprimer="x"))
        self.assertEqual(d["decision"], "supprimer")
        d = import_curation.decide_row(dict(base, doute="oui"))
        self.assertEqual(d["decision"], "doute")

    def test_decide_label_alone(self):
        d = import_curation.decide_row({"id": "p", "type": "person",
                                        "label_corrige": "Loménie"})
        self.assertEqual(d["decision"], "")
        self.assertEqual(d["label_corrige"], "Loménie")

    def test_decide_nothing_returns_none(self):
        self.assertIsNone(import_curation.decide_row({
            "id": "p", "type": "person", "label": "X", "qid_propose": "",
            "decision": "", "starred": "false", "flagged": ""}))
        self.assertIsNone(import_curation.decide_row(
            {"id": "p", "type": "person", "qid_propose": "Q1"}))

    def test_decide_invalid_decision_raises(self):
        with self.assertRaises(ValueError):
            import_curation.decide_row({"id": "p", "decision": "peut-etre"})

    def test_merge_label_groups(self):
        rows_in = [
            {"id": "person-000005", "type": "person", "mentions": "5",
             "label_corrige": "Loménie"},
            {"id": "person-000002", "type": "person", "mentions": "2",
             "label_corrige": "Loménie"},
            {"id": "place-000001", "type": "place", "mentions": "9",
             "label_corrige": "Loménie"},
        ]
        decided = [import_curation.decide_row(r) for r in rows_in]
        out = {r["id"]: r for r in
               import_curation.merge_label_groups(decided, rows_in)}
        self.assertEqual(out["person-000002"]["decision"], "fusionner")
        self.assertEqual(out["person-000002"]["fusion_vers"], "person-000005")
        self.assertEqual(out["person-000005"]["decision"], "")
        self.assertEqual(out["person-000005"]["label_corrige"], "Loménie")
        self.assertEqual(out["place-000001"]["decision"], "")

    def test_merge_label_groups_keeps_explicit(self):
        rows_in = [
            {"id": "a", "type": "person", "mentions": "5", "label_corrige": "L"},
            {"id": "b", "type": "person", "mentions": "2", "label_corrige": "l ",
             "decision": "supprimer"},
        ]
        decided = [import_curation.decide_row(r) for r in rows_in]
        out = {r["id"]: r for r in
               import_curation.merge_label_groups(decided, rows_in)}
        self.assertEqual(out["b"]["decision"], "supprimer")
        self.assertEqual(out["a"]["decision"], "")

    def test_merge_label_groups_joins_by_own_label(self):
        rows_in = [
            {"id": "person-000001", "type": "person", "label": "Lomenius",
             "mentions": "5", "label_corrige": ""},
            {"id": "person-000002", "type": "person", "label": "Lomenii",
             "mentions": "2", "label_corrige": "Lomenius"},
            {"id": "person-000003", "type": "person", "label": "Colbert",
             "mentions": "9", "label_corrige": ""},
        ]
        decided = [d for d in (import_curation.decide_row(r) for r in rows_in)
                   if d]
        out = {r["id"]: r for r in
               import_curation.merge_label_groups(decided, rows_in)}
        self.assertEqual(out["person-000002"]["decision"], "fusionner")
        self.assertEqual(out["person-000002"]["fusion_vers"], "person-000001")
        self.assertNotIn("person-000001", out)
        self.assertNotIn("person-000003", out)

    def test_merge_into_curation_overrides_and_keeps_others(self):
        existing = {
            "A": dict.fromkeys(curation.COLUMNS, ""),
            "B": dict.fromkeys(curation.COLUMNS, ""),
        }
        existing["A"].update(id="A", type="person", decision="ok")
        existing["B"].update(id="B", type="person", decision="doute",
                             qui="bob", date="2026-01-01", source="old.csv")
        keep_b = dict(existing["B"])
        new = [dict(dict.fromkeys(curation.COLUMNS, ""), id="A",
                    type="person", decision="supprimer")]
        res = import_curation.merge_into_curation(
            existing, new, "lot.csv", "anne", "2026-11-04")
        self.assertEqual(res["A"]["decision"], "supprimer")
        self.assertEqual(res["A"]["source"], "lot.csv")
        self.assertEqual(res["A"]["date"], "2026-11-04")
        self.assertEqual(res["A"]["qui"], "anne")
        self.assertEqual(res["B"], keep_b)

    def test_merge_into_curation_unchanged_keeps_attribution(self):
        old = dict(dict.fromkeys(curation.COLUMNS, ""), id="A", type="person",
                   decision="ok", qui="bob", date="2026-01-01",
                   source="old.csv")
        new = [dict(old, qui="", date="", source="")]
        res = import_curation.merge_into_curation(
            {"A": old}, new, "lot.csv", "anne", "2026-11-04")
        self.assertEqual(res["A"], old)

    def write_lot(self, rows, name="lot.csv", columns=LOT_COLUMNS):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=columns, restval="")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        return path

    def test_cli_dry_run_writes_nothing(self):
        lot = self.write_lot([{"id": "person-000001", "type": "person",
                               "decision": "ok"}])
        p = self.run_script("import_curation.py", lot, "--dry-run",
                            "--qui", "anne")
        self.assertFalse(os.path.exists(self.cur))
        self.assertIn("ok", p.stdout)

    def test_cli_import_writes_curation(self):
        lot = self.write_lot([
            {"id": "person-000001", "type": "person", "decision": "OK",
             "mentions": "10"},
            {"id": "person-000002", "type": "person", "mentions": "3"},
        ])
        self.run_script("import_curation.py", lot, "--qui", "anne")
        cur = curation.load_curation(self.cur)
        self.assertEqual(list(cur), ["person-000001"])
        self.assertEqual(cur["person-000001"]["decision"], "ok")
        self.assertEqual(cur["person-000001"]["qui"], "anne")
        self.assertEqual(cur["person-000001"]["source"], "lot.csv")

    def test_cli_label_groups_across_files(self):
        lot1 = self.write_lot([{"id": "person-000001", "type": "person",
                                "mentions": "10", "label_corrige": "Loménie"}],
                              "person_lot01.csv")
        lot2 = self.write_lot([{"id": "person-000007", "type": "person",
                                "mentions": "2", "label_corrige": "Lomenie"}],
                              "person_lot02.csv")
        self.run_script("import_curation.py", lot1, lot2, "--qui", "anne")
        cur = curation.load_curation(self.cur)
        self.assertEqual(cur["person-000007"]["decision"], "fusionner")
        self.assertEqual(cur["person-000007"]["fusion_vers"], "person-000001")
        self.assertEqual(cur["person-000007"]["source"], "person_lot02.csv")
        self.assertEqual(cur["person-000001"]["source"], "person_lot01.csv")
        self.assertEqual(cur["person-000001"]["label_corrige"], "Loménie")

    def test_cli_known_ids_skips_unknown(self):
        reg = os.path.join(self.tmp, "id_registry.csv")
        with open(reg, "w", encoding="utf-8", newline="") as f:
            f.write("type,label_norm,xml_id,status,ner_ids,created,updated\n")
            f.write("person,lomenie,person-000001,active,,2026-11-01,"
                    "2026-11-01\n")
        lot = self.write_lot([
            {"id": "person-000001", "type": "person", "decision": "ok"},
            {"id": "person-999999", "type": "person", "decision": "ok"},
        ])
        p = self.run_script("import_curation.py", lot, "--known-ids", reg)
        self.assertEqual(list(curation.load_curation(self.cur)),
                         ["person-000001"])
        self.assertIn("person-999999", p.stdout + p.stderr)

    def test_cli_id_in_several_files_last_decided_wins(self):
        lot1 = self.write_lot([
            {"id": "person-000001", "type": "person", "decision": "supprimer"},
            {"id": "person-000002", "type": "person", "decision": "doute"}],
            "person_lot01_relu_anne.csv")
        lot2 = self.write_lot([
            {"id": "person-000001", "type": "person"},
            {"id": "person-000002", "type": "person", "decision": "ok"}],
            "person_lot01_relu_bob.csv")
        p = self.run_script("import_curation.py", lot1, lot2)
        cur = curation.load_curation(self.cur)
        self.assertEqual(cur["person-000001"]["decision"], "supprimer")
        self.assertEqual(cur["person-000001"]["source"],
                         "person_lot01_relu_anne.csv")
        self.assertEqual(cur["person-000002"]["decision"], "ok")
        self.assertIn("plusieurs fichiers", p.stdout)
        self.assertIn("person-000001, person-000002", p.stdout)

    def test_cli_lot_complet_flag_and_file_name(self):
        row = {"id": "person-000001", "type": "person", "qid_propose": "Q1",
               "confiance": "high", "qid_final": "Q1", "qid_juge": "mass"}
        cols = LOT_COLUMNS + ["qid_final", "qid_juge"]
        lot = self.write_lot([row], "person_lot01_relu_marie.csv", cols)
        self.run_script("import_curation.py", lot)
        self.assertEqual(curation.load_curation(self.cur), {})
        self.run_script("import_curation.py", lot, "--lot-complet")
        self.assertEqual(curation.load_curation(self.cur)["person-000001"]
                         ["decision"], "ok")
        os.remove(self.cur)
        lot = self.write_lot([row], "person_lot01_relu_marie_complet.csv", cols)
        self.run_script("import_curation.py", lot)
        r = curation.load_curation(self.cur)["person-000001"]
        self.assertEqual((r["decision"], r["qid_corrige"]), ("ok", "Q1"))

    def test_registers_dir_honoured(self):
        regdir = os.path.join(self.tmp, "regs")
        cur = os.path.join(regdir, "curation.csv")
        curation.save_curation(cur, {"person-000001": dict(
            dict.fromkeys(curation.COLUMNS, ""), id="person-000001",
            type="person", decision="doute")})
        env = dict(os.environ, NER_OUT=self.ner, REGISTERS_DIR=regdir)
        env.pop("CURATION_CSV", None)

        def run(name, *args):
            p = subprocess.run(
                [sys.executable, os.path.join(SCRIPTS, name)] + list(args),
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, cwd=self.tmp)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        run("export_lots.py", "--out", self.out)
        self.assertEqual(self.lot_rows("person_lot01.csv")[0]["decision"],
                         "doute")
        lot = self.write_lot([{"id": "person-000002", "type": "person",
                               "decision": "supprimer"}])
        run("import_curation.py", lot)
        self.assertEqual(curation.load_curation(cur)["person-000002"]
                         ["decision"], "supprimer")
        self.assertFalse(os.path.exists(self.cur))
        old = os.environ.copy()
        try:
            os.environ.pop("CURATION_CSV", None)
            os.environ["REGISTERS_DIR"] = regdir
            self.assertEqual(curation.default_curation_path(), cur)
            os.environ["CURATION_CSV"] = "/x/c.csv"
            self.assertEqual(curation.default_curation_path(), "/x/c.csv")
            os.environ.pop("CURATION_CSV")
            os.environ.pop("REGISTERS_DIR")
            self.assertEqual(curation.default_curation_path(),
                             os.path.join(ROOT, "data", "registers",
                                          "curation.csv"))
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_roundtrip_export_edit_import(self):
        self.export("--excel")
        path = os.path.join(self.out, "person_lot01.csv")
        rows = read_csv(path, ";", "utf-8-sig")
        hdr = rows[0]
        rows[1][hdr.index("decision")] = "supprimer"
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f, delimiter=";").writerows(rows)
        self.run_script("import_curation.py", path)
        cur = curation.load_curation(self.cur)
        self.assertEqual(cur["person-000001"]["decision"], "supprimer")
        self.assertNotIn("person-000002", cur)


def recipe_columns():
    """Columns of a lot after the end recipe (fin_de_lot.json): supprimer and
    doute inserted at index 1, qid_final and qid_juge after qid_propose."""
    cols = list(LOT_COLUMNS)
    with open(os.path.join(ROOT, "relecteurs", "recettes", "fin_de_lot.json"),
              encoding="utf-8") as f:
        for op in json.load(f):
            if op["op"] == "core/column-addition":
                cols.insert(op["columnInsertIndex"], op["newColumnName"])
    return cols


class TestRecipeRoundtrip(Base):
    """A lot exported by OpenRefine after the end recipe, imported as is."""

    def rows(self):
        base = {"type": "person", "supprimer": "false", "doute": "false",
                "qid_juge": "", "qid_final": "", "confiance": "high"}

        def r(rid, mentions, label, **kw):
            d = dict(base, id=rid, mentions=str(mentions), label=label)
            d.update(kw)
            return d
        return [
            # flagged row with a proposal, matched by the start recipe
            r("person-000001", 50, "Lomenius", qid_propose="Q1",
              qid_final="Q1", qid_juge="mass", doute="true"),
            # untouched, high confidence
            r("person-000002", 40, "Colbert", qid_propose="Q2",
              qid_final="Q2", qid_juge="mass"),
            # untouched, low confidence
            r("person-000003", 30, "Rare", qid_propose="Q3", qid_final="Q3",
              qid_juge="mass", confiance="low"),
            # judged by hand (a candidate ticked), equal to the proposal
            r("person-000004", 20, "Platon", qid_propose="Q4",
              qid_final="Q4", qid_juge="similar", confiance="low"),
            # rejected
            r("person-000005", 10, "Comedie", qid_propose="Q5",
              qid_final="AUCUN", qid_juge="single"),
            # another QID chosen
            r("person-000006", 9, "Apollonius", qid_propose="Q6",
              qid_final="Q66", qid_juge="single"),
            # starred
            r("person-000007", 8, "bello", qid_propose="Q7", qid_final="Q7",
              qid_juge="mass", supprimer="true"),
            # grouped onto person-000001's label
            r("person-000008", 2, "Lomenii", label_corrige="Lomenius"),
        ]

    def run_import(self, name, *args):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=recipe_columns(), restval="")
            w.writeheader()
            w.writerows(self.rows())
        if os.path.exists(self.cur):
            os.remove(self.cur)
        self.run_script("import_curation.py", path, *args)
        return {k: (v["decision"], v["qid_corrige"], v["fusion_vers"])
                for k, v in curation.load_curation(self.cur).items()}

    def test_recipe_columns(self):
        cols = recipe_columns()
        i = cols.index("qid_final")
        self.assertEqual(cols[i - 1:i + 2], ["qid_propose", "qid_final", "qid_juge"])
        self.assertIn("supprimer", cols)
        self.assertIn("doute", cols)

    def expected(self):
        return {
            "person-000001": ("doute", "", ""),
            "person-000004": ("ok", "Q4", ""),
            "person-000005": ("corriger", "", ""),
            "person-000006": ("corriger", "Q66", ""),
            "person-000007": ("supprimer", "", ""),
            "person-000008": ("fusionner", "", "person-000001"),
        }

    def test_partial_lot(self):
        self.assertEqual(self.run_import("person_lot01_relu_marie.csv"),
                         self.expected())

    def test_complete_lot(self):
        exp = self.expected()
        exp["person-000002"] = ("ok", "Q2", "")
        self.assertEqual(self.run_import("person_lot01_relu_marie_complet.csv"),
                         exp)
        self.assertEqual(self.run_import("person_lot01_relu_marie.csv",
                                         "--lot-complet"), exp)


if __name__ == "__main__":
    unittest.main()
