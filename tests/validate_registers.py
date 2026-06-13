#!/usr/bin/env python3
"""Health-check / QA for generated registers + (optionally) the TEI documents.

Checks:
  * every registers/*.xml is well-formed XML;
  * no duplicate xml:id inside a register (the cardinal sin to avoid);
  * every register_id in id_mapping.csv actually exists as an xml:id;
  * (optional, --docs GLOB) every @ref in the documents either resolves to a
    register xml:id or is a still-unreconciled NER UUID — never a dangling id.

Exit code 0 if all good, 1 otherwise. Pure stdlib.

Usage:
  tests/validate_registers.py [REGISTERS_DIR] [--docs "data/*.tei.xml"]
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


def check_docs(docs_glob, ids):
    files = sorted(glob.glob(docs_glob))
    if not files:
        fail("no document matched --docs %r" % docs_glob)
        return
    dangling = 0
    for path in files:
        with open(path, encoding="utf-8") as f:
            data = f.read()
        for val in REF_RE.findall(data):
            for tok in val.split():
                if not tok.startswith("#"):
                    continue
                key = tok[1:]
                if key in ids:
                    continue
                if key.split("-")[0] in NER_PREFIXES and re.search(r"-[0-9a-f]{8}-", key):
                    continue  # still-unreconciled NER UUID: tolerated
                dangling += 1
                if dangling <= 5:
                    fail("%s: ref %r resolves to nothing" % (os.path.basename(path), tok))
    if dangling == 0:
        ok("documents: every @ref resolves (or is an unreconciled UUID)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reg_dir", nargs="?", default=os.path.join("data", "registers"))
    ap.add_argument("--docs", help='glob of TEI docs to check @ref resolution')
    a = ap.parse_args()

    print("validate_registers (%s):" % a.reg_dir)
    if not os.path.isdir(a.reg_dir):
        print("  FAIL registers dir not found: %s" % a.reg_dir)
        return 1
    ids = collect_register_ids(a.reg_dir)
    check_mapping(a.reg_dir, ids)
    if a.docs:
        check_docs(a.docs, ids)

    print("  -> %d problem(s)" % len(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
