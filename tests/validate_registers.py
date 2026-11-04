#!/usr/bin/env python3
"""Health-check / QA for generated registers + (optionally) the TEI documents.

Checks:
  * every registers/*.xml is well-formed XML;
  * no duplicate xml:id inside a register (the cardinal sin to avoid);
  * every register_id in id_mapping.csv actually exists as an xml:id;
  * (optional, --docs GLOB) every @ref in the documents either resolves to a
    register xml:id or is a still-unreconciled NER UUID — never a dangling id
    (refs matching --ignore-ref, default ^PERS\d+$ = header persons, skipped);
    and no duplicate xml:id inside each document (issue #1: the rebuilt
    standOff must collapse merged entries);
  * (optional, --curation PATH) every curation.csv `id` is a register xml:id or
    an xml_id of REGISTERS_DIR/id_registry.csv (merged/deleted entities are no
    longer in the register XML); every `fusion_vers` is a live register xml:id;
    every `decision` is ok|corriger|fusionner|supprimer|doute|(empty).

Exit code 0 if all good, 1 otherwise. Pure stdlib.

Usage:
  tests/validate_registers.py [REGISTERS_DIR] [--docs "data/*.tei.xml"]
                              [--ignore-ref REGEX] [--curation curation.csv]
  REGISTERS_DIR default: ./data/registers
"""
import argparse
import csv
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

XMLID = "{http://www.w3.org/XML/1998/namespace}id"
REF_RE = re.compile(r'\sref="([^"]*)"')
XMLID_RE = re.compile(r'xml:id="([^"]+)"')
NER_PREFIXES = ("pers", "place", "org", "work", "event", "artwork", "mat", "tech", "date")

problems = []


def fail(msg):
    problems.append(msg)
    print("  FAIL " + msg)


def ok(msg):
    print("  ok   " + msg)


def collect_register_ids(reg_dir):
    """Return set of all xml:id found across registers, reporting duplicates."""
    ids = set()
    for path in sorted(glob.glob(os.path.join(reg_dir, "*.xml"))):
        name = os.path.basename(path)
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as e:
            fail("%s is not well-formed: %s" % (name, e))
            continue
        ok("%s well-formed" % name)
        seen = set()
        for el in root.iter():
            xid = el.get(XMLID)
            if not xid:
                continue
            if xid in seen:
                fail("%s: duplicate xml:id %r" % (name, xid))
            seen.add(xid)
            ids.add(xid)
    return ids


def check_mapping(reg_dir, ids):
    mp = os.path.join(reg_dir, "id_mapping.csv")
    if not os.path.exists(mp):
        fail("id_mapping.csv missing in %s" % reg_dir)
        return
    missing = 0
    total = 0
    with open(mp, newline="", encoding="utf-8") as f:
        rd = csv.reader(f)
        next(rd, None)
        for row in rd:
            if len(row) < 2 or not row[1]:
                continue
            total += 1
            if row[1] not in ids:
                missing += 1
                if missing <= 5:
                    fail("id_mapping target %r has no matching xml:id" % row[1])
    if missing == 0:
        ok("id_mapping.csv: all %d register ids exist" % total)
    else:
        fail("id_mapping.csv: %d/%d targets missing" % (missing, total))


def check_docs(docs_glob, ids, ignore_ref=None):
    files = sorted(glob.glob(docs_glob))
    if not files:
        fail("no document matched --docs %r" % docs_glob)
        return
    ignore = re.compile(ignore_ref) if ignore_ref else None
    dangling = 0
    dup_docs = 0
    for path in files:
        with open(path, encoding="utf-8") as f:
            data = f.read()
        seen, dups = set(), []
        for xid in XMLID_RE.findall(data):
            if xid in seen and xid not in dups:
                dups.append(xid)
            seen.add(xid)
        if dups:
            dup_docs += 1
            fail("%s: %d duplicate xml:id, e.g. %s"
                 % (os.path.basename(path), len(dups), ", ".join(dups[:5])))
        for val in REF_RE.findall(data):
            for tok in val.split():
                if not tok.startswith("#"):
                    continue
                key = tok[1:]
                if key in ids:
                    continue
                if ignore and ignore.search(key):
                    continue
                if key.split("-")[0] in NER_PREFIXES and re.search(r"-[0-9a-f]{8}-", key):
                    continue  # still-unreconciled NER UUID: tolerated
                dangling += 1
                if dangling <= 5:
                    fail("%s: ref %r resolves to nothing" % (os.path.basename(path), tok))
    if dangling == 0:
        ok("documents: every @ref resolves (or is an unreconciled UUID)")
    if dup_docs == 0:
        ok("documents: no duplicate xml:id")


CURATION_DECISIONS = {"ok", "corriger", "fusionner", "supprimer", "doute", ""}


def load_registry_ids(reg_dir):
    """All xml_id of id_registry.csv (any status: active|merged|deleted), or empty."""
    path = os.path.join(reg_dir, "id_registry.csv")
    out = set()
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            xid = (row.get("xml_id") or "").strip()
            if xid:
                out.add(xid)
    return out


def check_curation(path, ids, registry_ids=frozenset()):
    """curation.csv: `id` must be known (live register xml:id, or any id of
    id_registry.csv: merged / deleted entities are no longer in the register
    XML); `fusion_vers` must be a LIVE register xml:id; `decision` must be one
    of ok|corriger|fusionner|supprimer|doute|(empty)."""
    name = os.path.basename(path)
    if not os.path.exists(path):
        fail("curation file not found: %s" % path)
        return
    known = set(ids) | set(registry_ids)
    bad = 0
    rows = 0

    def report(msg):
        nonlocal bad
        bad += 1
        if bad <= 5:
            fail(msg)

    with open(path, newline="", encoding="utf-8-sig") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            rows += 1
            xid = (row.get("id") or "").strip()
            if xid and xid not in known:
                report("%s line %d: id %r unknown (neither register xml:id nor "
                       "id_registry.csv)" % (name, i, xid))
            target = (row.get("fusion_vers") or "").strip()
            if target and target not in ids:
                report("%s line %d: fusion_vers %r is not a live register xml:id"
                       % (name, i, target))
            decision = (row.get("decision") or "").strip().lower()
            if decision not in CURATION_DECISIONS:
                report("%s line %d: invalid decision %r" % (name, i, row.get("decision")))
    if bad == 0:
        ok("%s: %d row(s) checked, ids / fusion_vers / decisions valid" % (name, rows))
    else:
        fail("%s: %d problem(s) in %d row(s)" % (name, bad, rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reg_dir", nargs="?", default=os.path.join("data", "registers"))
    ap.add_argument("--docs", help='glob of TEI docs to check @ref resolution')
    ap.add_argument("--ignore-ref", default=r"^PERS\d+$",
                    help=r'refs (without #) matching this regex are not checked '
                         r'(default: ^PERS\d+$, header persons)')
    ap.add_argument("--curation",
                    help="curation.csv to check: every id is a register xml:id or "
                         "in id_registry.csv, every fusion_vers a live register "
                         "xml:id, every decision valid")
    a = ap.parse_args()

    print("validate_registers (%s):" % a.reg_dir)
    if not os.path.isdir(a.reg_dir):
        print("  FAIL registers dir not found: %s" % a.reg_dir)
        return 1
    ids = collect_register_ids(a.reg_dir)
    check_mapping(a.reg_dir, ids)
    if a.docs:
        check_docs(a.docs, ids, a.ignore_ref)
    if a.curation:
        check_curation(a.curation, ids, load_registry_ids(a.reg_dir))

    print("  -> %d problem(s)" % len(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
