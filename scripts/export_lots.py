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

Les lots sont edites sur place par les relecteurs : une nouvelle exportation
ne remplace que les lots listes dans le manifeste precedent pour les types
reecrits, et refuse (sauf --force) si l'un d'eux a ete modifie sans avoir ete
importe (son empreinte sha256 differe de celle notee a l'ecriture ET de toutes
celles notees par import_curation.py), ou si un fichier cible existe sans
figurer au manifeste.
<out>/.lots_written.json (fichier technique : {lot: {"written": sha256,
"imported": [sha256...]}}) peut etre supprime sans risque ; pour un lot sans
empreinte, le controle se rabat sur la mtime du manifeste (moins fiable).

Les colonnes de decision sont pre-remplies depuis curation.csv (CURATION_CSV,
sinon <REGISTERS_DIR>/curation.csv, sinon data/registers/curation.csv).

Usage :
  python3 scripts/export_lots.py [--out DIR] [--types person,place]
      [--min-mentions 2] [--max-rows 500] [--only-new] [--excel] [--force]

stdlib uniquement.
"""
import argparse
import csv
import datetime
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import curation as curation_mod  # noqa: E402

ROOT = os.path.dirname(HERE)
OUT_DIR = os.environ.get("NER_OUT") or os.path.join(ROOT, "build", "ner")
CURATION_CSV = curation_mod.default_curation_path()

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
WRITTEN_SIDECAR = ".lots_written.json"


def _s(v):
    return "" if v is None else str(v)


def signature(c):
    """Change signature of a cluster: label|qid (`modifié` = the label or the
    proposal changed; a new mention alone does not count)."""
    rec = c.get("rec") or {}
    return "%s|%s" % (_s(c.get("label")), _s(rec.get("wikidata_qid")))


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


class LotsEditedError(Exception):
    """A lot listed in the previous manifest was edited after the export."""


def _read_manifest(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            delim = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
        except csv.Error:
            delim = ","
        return [{(k or "").strip(): (v or "").strip() for k, v in r.items()}
                for r in csv.DictReader(f, delimiter=delim)]


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_sidecar(path):
    """{lot: {"written": sha, "imported": [sha]}}; entries of an older format
    (or an unreadable file) are dropped."""
    try:
        data = _load_json(path, {})
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def _save_sidecar(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def record_import(path):
    """Note the sha256 of an imported file in the .lots_written.json of its
    directory (created if missing): a lot edited then imported may be
    replaced by the next export without --force."""
    d = os.path.dirname(os.path.abspath(path))
    side = os.path.join(d, WRITTEN_SIDECAR)
    data = _load_sidecar(side)
    entry = data.setdefault(os.path.basename(path), {})
    imported = list(entry.get("imported") or [])
    h = file_sha256(path)
    if h not in imported:
        imported.append(h)
    entry["imported"] = imported
    _save_sidecar(side, data)


def write_lots(rows, out_dir, max_rows, excel, force=False):
    """Write <type>_lotNN.csv files (max_rows each) and lots_manifest.csv.

    Lots are edited in place by reviewers, so only the files listed in the
    previous lots_manifest.csv, and only for the types written now, are
    replaced; lots of other types are kept and stay in the manifest.
    Raises LotsEditedError (nothing written) when a file to replace was
    modified and not imported (its sha256 differs from the one recorded at
    write time in .lots_written.json and from every hash recorded there by
    import_curation.py; without a record, its mtime is compared with the
    manifest's), or when a target file exists but is not listed in the
    manifest, unless force=True.
    Returns the written lot paths."""
    os.makedirs(out_dir, exist_ok=True)
    man_path = os.path.join(out_dir, "lots_manifest.csv")
    old_manifest = _read_manifest(man_path)
    man_mtime = os.path.getmtime(man_path) if old_manifest else 0
    written_path = os.path.join(out_dir, WRITTEN_SIDECAR)
    written = _load_sidecar(written_path)
    max_rows = max(1, int(max_rows))
    by_type = {}
    for r in rows:
        by_type.setdefault(r["type"], []).append(r)
    targets = {"%s_lot%02d.csv" % (_safe(typ), n + 1): typ
               for typ, trows in by_type.items()
               for n in range((len(trows) + max_rows - 1) // max_rows)}
    listed = {m.get("file", ""): m for m in old_manifest}
    to_delete = [f for f, m in listed.items()
                 if f and m.get("type") in by_type
                 and os.path.exists(os.path.join(out_dir, f))]
    if not force:
        problems = []
        for f in to_delete:
            fpath = os.path.join(out_dir, f)
            entry = written.get(f) or {}
            if entry.get("written"):
                h = file_sha256(fpath)
                edited = (h != entry["written"]
                          and h not in (entry.get("imported") or []))
            else:
                edited = os.path.getmtime(fpath) > man_mtime
            if edited:
                problems.append("%s modifie depuis l'export et non importe" % f)
        for f in targets:
            if f not in listed and os.path.exists(os.path.join(out_dir, f)):
                problems.append("%s existe mais n'est pas dans le manifeste" % f)
        if problems:
            raise LotsEditedError(
                "lots non importes dans %s : %s (importer d'abord, ou --force)"
                % (out_dir, "; ".join(sorted(problems))))
    for f in to_delete:
        os.remove(os.path.join(out_dir, f))
        written.pop(f, None)
    delim, enc = (";", "utf-8-sig") if excel else (",", "utf-8")
    paths = []
    manifest = [{k: m.get(k, "") for k in MANIFEST_COLUMNS}
                for m in old_manifest
                if m.get("type") not in by_type and m.get("file")
                and os.path.exists(os.path.join(out_dir, m["file"]))]
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
            written[name] = {"written": file_sha256(path)}
            m = {"file": name, "type": typ, "rows": len(chunk)}
            for s in STATUTS:
                m[s] = sum(1 for r in chunk if r.get("statut") == s)
            manifest.append(m)
    manifest.sort(key=lambda m: (m["type"], m["file"]))
    with open(man_path, "w", encoding=enc, newline="") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS, delimiter=delim)
        w.writeheader()
        w.writerows(manifest)
    listed_now = {m["file"] for m in manifest}
    _save_sidecar(written_path,
                  {k: v for k, v in written.items() if k in listed_now})
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
    ap.add_argument("--force", action="store_true",
                    help="remplacer les lots meme s'ils ont ete modifies")
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

    try:
        paths = write_lots(rows, a.out, a.max_rows, a.excel, force=a.force)
    except LotsEditedError as e:
        print("ERREUR : %s" % e, file=sys.stderr)
        return 1

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
