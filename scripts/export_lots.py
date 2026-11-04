#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Export des lots de relecture (OpenRefine / tableur) depuis
<NER_OUT>/entities_registered.json.

Sorties (dans --out, defaut <NER_OUT>/lots) :
  <type>_lotNN.csv     une ligne par entite, colonnes LOT_COLUMNS
  lots_manifest.csv    file,type,rows,nouveau,modifié,inchangé
et met a jour <NER_OUT>/lots_state.json ({id: {"sig", "exported"}}) pour les
seules lignes ecrites : `statut` (nouveau|modifié|inchangé) se calcule contre
cet etat.

Les colonnes de decision sont pre-remplies depuis curation.csv (CURATION_CSV).

Usage :
  python3 scripts/export_lots.py [--out DIR] [--types person,place]
      [--min-mentions 2] [--max-rows 500] [--only-new] [--excel]

stdlib uniquement.
"""
import argparse
import csv
import datetime
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import curation as curation_mod  # noqa: E402

ROOT = os.path.dirname(HERE)
OUT_DIR = os.environ.get("NER_OUT") or os.path.join(ROOT, "build", "ner")
CURATION_CSV = os.environ.get("CURATION_CSV") or os.path.join(
    ROOT, "data", "registers", "curation.csv")

LOT_COLUMNS = [
    "id", "type", "label", "label_moderne", "variantes", "mentions",
    "nb_docs", "docs", "cert_ner", "exemple_1", "exemple_2", "qid_propose",
    "label_wikidata", "description_wikidata", "confiance", "candidats",
    "lien_wikidata", "local_id", "isni", "naissance", "deces", "pays",
    "statut", "decision", "qid_corrige", "fusion_vers", "label_corrige",
    "type_corrige", "commentaire", "qui", "date"]
CURATION_FIELDS = ["decision", "qid_corrige", "fusion_vers", "label_corrige",
                   "type_corrige", "commentaire", "qui", "date"]
STATUTS = ["nouveau", "modifié", "inchangé"]
MANIFEST_COLUMNS = ["file", "type", "rows"] + STATUTS
LOT_RE = re.compile(r"_lot\d{2,}\.csv$")


def _s(v):
    return "" if v is None else str(v)


def signature(c):
    """Change signature of a cluster: label|qid|mentions."""
    rec = c.get("rec") or {}
    return "%s|%s|%s" % (_s(c.get("label")), _s(rec.get("wikidata_qid")),
                         _s(c.get("mentions")))


def status_for(xmlid, sig, state):
    prev = state.get(xmlid)
    if not prev:
        return "nouveau"
    return "inchangé" if prev.get("sig") == sig else "modifié"


def build_rows(registered, curation, labels):
    """One dict per cluster, keys in LOT_COLUMNS order. `statut` is left
    empty (computed by the caller against lots_state.json). Sorted by
    (type, -mentions, label)."""
    rows = []
    for c in registered:
        rec = c.get("rec") or {}
        qid = _s(rec.get("wikidata_qid"))
        examples = list(c.get("examples") or [])[:2] + ["", ""]
        country = _s(rec.get("country_qid"))
        cur = curation.get(c.get("xmlid"), {}) or {}
        row = {
            "id": _s(c.get("xmlid")),
            "type": _s(c.get("type")),
            "label": _s(c.get("label")),
            "label_moderne": _s(c.get("modern_label")),
            "variantes": "|".join(_s(v) for v in (c.get("variants") or [])[:12]),
            "mentions": c.get("mentions") or 0,
            "nb_docs": len(c.get("source_docs") or []),
            "docs": "|".join(_s(d) for d in (c.get("source_docs") or [])),
            "cert_ner": _s(c.get("cert")),
            "exemple_1": _s(examples[0]),
            "exemple_2": _s(examples[1]),
            "qid_propose": qid,
            "label_wikidata": _s(rec.get("wikidata_label")),
            "description_wikidata": _s(rec.get("description")),
            "confiance": _s(rec.get("match_confidence")),
            "candidats": "|".join(_s(q) for q in (rec.get("wikidata_alts") or [])),
            "lien_wikidata": ("https://www.wikidata.org/wiki/" + qid) if qid else "",
            "local_id": _s(c.get("local_id")),
            "isni": _s(c.get("isni")),
            "naissance": _s(rec.get("birth")) or _s(c.get("local_birth")),
            "deces": _s(rec.get("death")) or _s(c.get("local_death")),
            "pays": _s(labels.get(country, country)) if country else "",
            "statut": "",
        }
        for k in CURATION_FIELDS:
            row[k] = _s(cur.get(k))
        rows.append({k: row[k] for k in LOT_COLUMNS})
    rows.sort(key=lambda r: (r["type"], -int(r["mentions"] or 0), r["label"]))
    return rows


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name) or "untyped"


def write_lots(rows, out_dir, max_rows, excel):
    """Write <type>_lotNN.csv files (max_rows each) and lots_manifest.csv.
    Previous lot files in out_dir are removed first. Returns lot paths."""
    os.makedirs(out_dir, exist_ok=True)
    for old in glob.glob(os.path.join(out_dir, "*_lot*.csv")):
        if LOT_RE.search(os.path.basename(old)):
            os.remove(old)
    delim, enc = (";", "utf-8-sig") if excel else (",", "utf-8")
    max_rows = max(1, int(max_rows))
    by_type = {}
    for r in rows:
        by_type.setdefault(r["type"], []).append(r)
    paths, manifest = [], []
    for typ in sorted(by_type):
        trows = by_type[typ]
        for n, start in enumerate(range(0, len(trows), max_rows), 1):
            chunk = trows[start:start + max_rows]
            name = "%s_lot%02d.csv" % (_safe(typ), n)
            path = os.path.join(out_dir, name)
            with open(path, "w", encoding=enc, newline="") as f:
                w = csv.DictWriter(f, fieldnames=LOT_COLUMNS, delimiter=delim)
                w.writeheader()
                w.writerows(chunk)
            paths.append(path)
            m = {"file": name, "type": typ, "rows": len(chunk)}
            for s in STATUTS:
                m[s] = sum(1 for r in chunk if r.get("statut") == s)
            manifest.append(m)
    with open(os.path.join(out_dir, "lots_manifest.csv"), "w", encoding=enc,
              newline="") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS, delimiter=delim)
        w.writeheader()
        w.writerows(manifest)
    return paths


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", default=os.path.join(OUT_DIR, "lots"))
    ap.add_argument("--types", default="",
                    help="types a exporter, separes par des virgules")
    ap.add_argument("--min-mentions", type=int, default=2)
    ap.add_argument("--max-rows", type=int, default=500)
    ap.add_argument("--only-new", action="store_true",
                    help="n'exporter que les lignes nouveau/modifié")
    ap.add_argument("--excel", action="store_true",
                    help="separateur ';' et BOM utf-8 (Excel FR)")
    a = ap.parse_args(argv)

    reg_path = os.path.join(OUT_DIR, "entities_registered.json")
    if not os.path.exists(reg_path):
        sys.exit("ERREUR : %s introuvable (lancer build_registers.py)" % reg_path)
    registered = _load_json(reg_path, [])
    labels = _load_json(os.path.join(OUT_DIR, "wd_labels.json"), {})
    state_path = os.path.join(OUT_DIR, "lots_state.json")
    state = _load_json(state_path, {})
    cur = curation_mod.load_curation(CURATION_CSV)

    types = {t.strip() for t in a.types.split(",") if t.strip()}
    selected = [c for c in registered
                if (not types or c.get("type") in types)
                and int(c.get("mentions") or 0) >= a.min_mentions]
    sigs = {c.get("xmlid"): signature(c) for c in selected}

    rows = build_rows(selected, cur, labels)
    for r in rows:
        r["statut"] = status_for(r["id"], sigs[r["id"]], state)
    if a.only_new:
        rows = [r for r in rows if r["statut"] != "inchangé"]

    paths = write_lots(rows, a.out, a.max_rows, a.excel)

    today = datetime.date.today().isoformat()
    for r in rows:
        state[r["id"]] = {"sig": sigs[r["id"]], "exported": today}
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)

    counts = {s: sum(1 for r in rows if r["statut"] == s) for s in STATUTS}
    print("%d lignes exportees dans %d lot(s) -> %s" % (len(rows), len(paths), a.out))
    print("  " + ", ".join("%s: %d" % (s, counts[s]) for s in STATUTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
