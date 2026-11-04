#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Import des decisions de relecture dans curation.csv.

Accepte les lots de export_lots.py edites dans un tableur (separateur
`,` `;` ou tabulation, BOM accepte) et les exports OpenRefine (recette de fin
de lot : colonnes qid_final, qid_juge, supprimer, doute). Ordre de decision
(voir decide_row) :
  1. etoile (`starred`/`supprimer` vrai) ou decision `supprimer` -> supprimer ;
  2. decision `fusionner` (ou `fusion_vers` rempli sans decision) -> fusionner ;
  3. drapeau (`flagged`/`doute` vrai) -> doute (l'emporte sur tout ok deduit ;
     un QID de qid_final different du QID implique est garde dans qid_corrige,
     pour information) ;
  4. `qid_final` (recette OpenRefine : "" = pas de correspondance, "AUCUN" =
     match rejete, QID = correspondance) ; QID implique = qid_corrige si la
     decision est corriger, sinon qid_propose : "" -> aucune information ;
     AUCUN -> corriger sans QID si un QID etait implique ; QID different du
     QID implique -> corriger avec ce QID ; egal a qid_propose sans decision
     -> ok seulement si le jugement est explicite : colonne `qid_juge`
     presente et differente de ""/"mass" (OpenRefine : "single"/"similar" =
     jugee a la main ; sans qid_juge, ancienne recette = non touche), OU lot
     declare complet (--lot-complet, ou "complet"
     dans le nom du fichier) et confiance high/medium ; sinon rien ;
  5. decision explicite ok|corriger|doute (qid_corrige seul -> corriger) ;
  6. `label_corrige`/`type_corrige`/`commentaire` seuls : ligne gardee,
     decision vide.
(vrai = true|1|oui|x|yes|vrai). Une ligne `ok` inscrit le QID accepte dans
qid_corrige (le build ne l'asserte que s'il est encore la proposition).
Les lignes d'un meme type partageant un `label_corrige` non vide (sur
l'ensemble des fichiers importes) sont fusionnees dans celle qui a le plus de
mentions (sauf decision explicite) ; une ligne sans label_corrige rejoint le
groupe dont le label_corrige est egal a son propre `label`.
Un id present dans plusieurs fichiers : la derniere ligne portant une
decision non vide l'emporte (avertissement) ; sinon la derniere ligne.
Apres l'ecriture, l'empreinte sha256 de chaque fichier importe est notee dans
le .lots_written.json de son repertoire (garde-fou de export_lots.py).

Usage :
  python3 scripts/import_curation.py lot1.csv [lot2.csv ...]
      [--curation PATH] [--qui NOM] [--known-ids id_registry.csv]
      [--lot-complet] [--dry-run]

stdlib uniquement.
"""
import argparse
import csv
import datetime
import os
import re
import sys
import unicodedata
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import curation as curation_mod  # noqa: E402
import export_lots  # noqa: E402

CURATION_CSV = curation_mod.default_curation_path()

TRUTHY = {"true", "1", "oui", "x", "yes", "vrai"}
QID_RE = re.compile(r"Q\d+", re.I)
CONTENT_FIELDS = [k for k in curation_mod.COLUMNS
                  if k not in ("type", "qui", "date", "source")]


def read_any_csv(path):
    """Read a CSV whatever its delimiter (`,` `;` tab) and BOM; keys and
    values stripped, empty-key columns dropped, blank rows skipped."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            delim = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
        except csv.Error:
            delim = ","
        rows = []
        for raw in csv.DictReader(f, delimiter=delim):
            row = {}
            for k, v in raw.items():
                if k is None or not k.strip():
                    continue
                row[k.strip()] = "" if v is None else str(v).strip()
            if any(row.values()):
                rows.append(row)
    return rows


def truthy(v):
    return v is not None and str(v).strip().lower() in TRUTHY


def _qid(v):
    m = QID_RE.search(v or "")
    return m.group(0).upper() if m else ""


def _explicit(row):
    """Decision taken by hand in the input row (decision column, fusion_vers,
    star or flag). Such rows are never turned into `fusionner` by a
    label_corrige group; rows whose decision only comes from qid_final are."""
    return bool((row.get("decision") or "").strip()
                or (row.get("fusion_vers") or "").strip()) or any(
        truthy(row.get(k)) for k in ("supprimer", "starred", "doute", "flagged"))


MASS_JUDGEMENTS = ("", "mass")
CONFIDENT = ("high", "medium")
COMPLETE_RE = re.compile(r"(?<!in)complet", re.I)


def is_complete_file(path):
    """A reviewer file named with "complet" (not "incomplet") declares a fully
    reviewed lot: untouched proposals of high/medium confidence count as ok."""
    return bool(COMPLETE_RE.search(os.path.basename(path)))


def _judged(row):
    """True when the reviewer judged qid_final explicitly: the `qid_juge`
    column exists and holds an OpenRefine judgement other than the recipe's
    mass match ("single" / "similar"). Without `qid_juge` (export of an older
    end recipe) a qid_final equal to the proposal counts as untouched."""
    if "qid_juge" not in row:
        return False
    return (row.get("qid_juge") or "").strip().lower() not in MASS_JUDGEMENTS


def _decide(row, out, complete=False):
    """Decision for one input row, controller ruling order (see module doc)."""
    g = lambda k: (row.get(k) or "").strip()  # noqa: E731
    explicit = g("decision").lower()
    # 1. star / supprimer
    if explicit == "supprimer" or truthy(row.get("starred")) \
            or truthy(row.get("supprimer")):
        return "supprimer"
    # 2. fusionner
    if explicit == "fusionner" or (not explicit and out["fusion_vers"]):
        return "fusionner"
    proposed = _qid(g("qid_propose"))
    if explicit == "corriger":
        implied = out["qid_corrige"]
    elif explicit in ("ok", "doute", ""):
        implied = proposed   # a proposal stands while in doubt
    else:
        implied = ""
    has_final = "qid_final" in row
    raw = g("qid_final")
    final = _qid(raw)
    # 3. flag: beats any inferred ok; a changed QID is kept for information
    if truthy(row.get("flagged")) or truthy(row.get("doute")):
        if has_final and final and final != implied:
            out["qid_corrige"] = final
        return "doute"
    # 4. qid_final (OpenRefine: "" no match, AUCUN rejected, QID matched)
    if has_final:
        if raw.upper() == "AUCUN":
            if implied:
                out["qid_corrige"] = ""
                return "corriger"
        elif final and final != implied:
            out["qid_corrige"] = final
            return "corriger"
        elif final and final == proposed and not explicit:
            if _judged(row) or (complete and g("confiance").lower() in CONFIDENT):
                return "ok"
    # 5. explicit ok / corriger / doute ; qid_corrige alone -> corriger
    if explicit in ("ok", "corriger", "doute"):
        return explicit
    if out["qid_corrige"]:
        return "corriger"
    return ""


def decide_row(row, complete=False):
    """Turn one edited lot / OpenRefine row into a curation row (keys =
    curation.COLUMNS), or None when nothing was decided (no decision and no
    label_corrige / type_corrige / commentaire). `complete`: the row comes
    from a lot declared fully reviewed (see _decide).
    An `ok` row records the accepted QID in qid_corrige (from qid_final, else
    qid_propose) when it is empty.
    Raises ValueError on an unknown decision value."""
    out = dict.fromkeys(curation_mod.COLUMNS, "")
    for k in ("id", "type", "qid_corrige", "fusion_vers", "label_corrige",
              "type_corrige", "commentaire", "qui"):
        out[k] = (row.get(k) or "").strip()
    if out["qid_corrige"]:
        out["qid_corrige"] = _qid(out["qid_corrige"]) or out["qid_corrige"]
    decision = (row.get("decision") or "").strip().lower()
    if decision not in curation_mod.DECISIONS:
        raise ValueError("decision inconnue %r pour %s" % (decision, out["id"]))
    out["decision"] = _decide(row, out, complete)
    if out["decision"] == "ok":
        final = _qid(row.get("qid_final"))
        if final and _judged(row):
            # an explicit re-judgement replaces a stale pre-filled QID
            out["qid_corrige"] = final
        elif not out["qid_corrige"]:
            out["qid_corrige"] = final or _qid(row.get("qid_propose"))
    if out["decision"] or out["label_corrige"] or out["type_corrige"] \
            or out["commentaire"]:
        return out
    return None


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.lower().split())


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def merge_label_groups(decided, rows_in):
    """Rows of the same type sharing a non-empty label_corrige are merged
    into the one with most mentions: the others become `fusionner` -> target
    unless they carry an explicit decision. A row with an empty label_corrige
    joins a group by its own `label` when the group has a member whose
    label_corrige equals that label (normalized). Returns the full decided
    list (sorted by id), including the generated fusionner rows."""
    by_id = {r["id"]: dict(r) for r in decided if r}
    groups = {}
    unlabelled = []
    for r in rows_in:
        rid, lab = (r.get("id") or "").strip(), (r.get("label_corrige") or "")
        if not rid:
            continue
        typ = (r.get("type_corrige") or "").strip() or (r.get("type") or "").strip()
        if _norm(lab):
            groups.setdefault((typ, _norm(lab)), {})[rid] = r
        elif _norm(r.get("label")):
            unlabelled.append(((typ, _norm(r.get("label"))), rid, r))
    for key, rid, r in unlabelled:
        if key in groups and rid not in groups[key]:
            groups[key][rid] = r
    for members in groups.values():
        if len(members) < 2:
            continue
        target = sorted(members.values(),
                        key=lambda r: (-_int(r.get("mentions")),
                                       r.get("id", "").strip()))[0]
        tid = target["id"].strip()
        for rid, r in members.items():
            if rid == tid or _explicit(r):
                continue
            row = by_id.get(rid) or decide_row(r) or dict.fromkeys(
                curation_mod.COLUMNS, "")
            row.update(id=rid, decision="fusionner", fusion_vers=tid,
                       qid_corrige="")
            by_id[rid] = row
    return [by_id[k] for k in sorted(by_id)]


def merge_into_curation(existing, new_rows, source, qui, date):
    """New rows replace existing ones with the same id (whole row); other
    existing rows are kept untouched. A new row whose decision content is
    identical to the existing one (a pre-filled lot re-imported as is) keeps
    the existing row, so its qui/date/source attribution survives.
    Returns a new dict."""
    res = {k: dict(v) for k, v in existing.items()}
    for r in new_rows:
        row = dict.fromkeys(curation_mod.COLUMNS, "")
        row.update({k: r.get(k, "") or "" for k in curation_mod.COLUMNS})
        old = existing.get(row["id"])
        if old is not None:
            if not row["type"]:
                row["type"] = old.get("type", "")
            if all(row[k] == (old.get(k) or "") for k in CONTENT_FIELDS):
                continue
        row["qui"] = qui or row["qui"]
        row["date"] = date
        row["source"] = source
        res[row["id"]] = row
    return res


def load_known_ids(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {(r.get("xml_id") or "").strip() for r in csv.DictReader(f)} - {""}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("files", nargs="+", metavar="FILES")
    ap.add_argument("--curation", default=CURATION_CSV)
    ap.add_argument("--qui", default="", help="nom du relecteur")
    ap.add_argument("--known-ids", default="",
                    help="id_registry.csv : ids inconnus ignores")
    ap.add_argument("--lot-complet", action="store_true",
                    help="lots entierement relus : une proposition high/medium "
                         "laissee telle quelle compte comme ok (implicite si "
                         "le nom du fichier contient \"complet\")")
    ap.add_argument("--dry-run", action="store_true",
                    help="afficher le bilan sans ecrire curation.csv")
    a = ap.parse_args(argv)

    known = load_known_ids(a.known_ids) if a.known_ids else None
    today = datetime.date.today().isoformat()
    cur = curation_mod.load_curation(a.curation)
    before = dict(cur)
    total = Counter()
    errors = 0

    # Rows of all FILES are collected first so label_corrige groups spanning
    # several lots are merged. An id present in several files: the last row
    # that carries a decision wins (a row decided None does not override).
    rows_by_id, source_of, read_count = {}, {}, Counter()
    decided_by_id, files_of = {}, {}
    for path in a.files:
        if not os.path.exists(path):
            print("ERREUR : fichier introuvable : %s" % path, file=sys.stderr)
            return 2
        complete = a.lot_complet or is_complete_file(path)
        for r in read_any_csv(path):
            rid = (r.get("id") or "").strip()
            if not rid:
                continue
            if known is not None and rid not in known:
                print("AVERTISSEMENT : id inconnu ignore : %s (%s)" % (rid, path))
                continue
            fv = (r.get("fusion_vers") or "").strip()
            if known is not None and fv and fv not in known:
                print("AVERTISSEMENT : fusion_vers inconnu %s pour %s" % (fv, rid))
            r["id"] = rid
            read_count[path] += 1
            files_of.setdefault(rid, [])
            if path not in files_of[rid]:
                files_of[rid].append(path)
            try:
                d = decide_row(r, complete)
            except ValueError as e:
                print("AVERTISSEMENT : %s (%s) ligne ignoree" % (e, path))
                errors += 1
                continue
            prev = decided_by_id.get(rid)
            if prev and prev["decision"] and not (d and d["decision"]):
                continue   # only a row with a real decision overrides one
            rows_by_id[rid] = r
            source_of[rid] = path
            decided_by_id[rid] = d
    multi = sorted(rid for rid, fs in files_of.items() if len(fs) > 1)
    if multi:
        print("AVERTISSEMENT : %d id(s) presents dans plusieurs fichiers "
              "(la derniere ligne portant une decision est retenue) : %s"
              % (len(multi), ", ".join(multi)))

    rows_in = list(rows_by_id.values())
    decided = [d for d in decided_by_id.values() if d]
    decided = merge_label_groups(decided, rows_in)

    by_source = {}
    for d in decided:
        by_source.setdefault(source_of[d["id"]], []).append(d)
    for path in a.files:
        rows = by_source.pop(path, [])
        if not rows:
            print("%s : rien (%d ligne(s) lue(s))" % (path, read_count[path]))
            continue
        cur = merge_into_curation(cur, rows, os.path.basename(path),
                                  a.qui, today)
        c = Counter(d["decision"] or "(vide)" for d in rows)
        total.update(c)
        print("%s : %d ligne(s) retenue(s) sur %d -- %s" % (
            path, len(rows), read_count[path],
            ", ".join("%s: %d" % kv for kv in sorted(c.items()))))

    changed = sum(1 for k, v in cur.items() if before.get(k) != v)
    print("Total : " + (", ".join("%s: %d" % kv for kv in sorted(total.items()))
                        or "aucune decision"))
    if errors:
        print("%d ligne(s) ignoree(s) (decision inconnue)" % errors)
    if a.dry_run:
        print("--dry-run : %d entree(s) seraient ecrites dans %s (rien ecrit)"
              % (changed, a.curation))
        return 0
    d = os.path.dirname(os.path.abspath(a.curation))
    os.makedirs(d, exist_ok=True)
    curation_mod.save_curation(a.curation, cur)
    print("%d entree(s) mises a jour -> %s" % (changed, a.curation))
    for path in a.files:
        try:
            export_lots.record_import(path)
        except OSError as e:
            print("AVERTISSEMENT : empreinte de %s non notee (%s) ; "
                  "curation.csv est bien ecrit, mais une nouvelle exportation "
                  "de ce lot demandera --force" % (path, e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
