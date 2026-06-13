#!/usr/bin/env python3
"""Unit tests for scripts/rewrite_refs.py — the @ref rewriting step.

Proves the two safety invariants:
  1. only @ref values are rewritten, standOff xml:id are left intact
     (the mapping is many-to-one: two NER UUIDs can map to the same register id;
      rewriting xml:id would create duplicate ids -> invalid XML);
  2. refs absent from the mapping are kept verbatim (unreconciled entities).
Plus: well-formed output, and idempotence (a second pass changes nothing).

Pure stdlib, offline. Run directly or via tests/run_tests.sh.
"""
import os
import subprocess
import sys
import tempfile
import xml.dom.minidom as minidom

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, "scripts", "rewrite_refs.py")
FIXTURE = os.path.join(ROOT, "examples", "sample", "data", "sample_reconciled.tei.xml")

# many-to-one on purpose: pers-0001 AND pers-0002 -> person-000001
MAPPING = """ner_uuid,register_id,type
pers-0001,person-000001,person
pers-0002,person-000001,person
place-0001,place-000001,place
org-0001,org-000001,org
work-0001,work-000001,work
event-0001,event-000001,event
mat-0001,material-000001,material
tech-0001,technique-000001,technique
date-0001,date-000001,date
artwork-0001,artwork-000001,artwork
"""

failures = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        failures.append(name)


def run(args):
    return subprocess.run([sys.executable, SCRIPT] + args,
                          capture_output=True, text=True, check=True).stdout


def main():
    print("test_rewrite_refs:")
    with tempfile.TemporaryDirectory() as tmp:
        mapping = os.path.join(tmp, "id_mapping.csv")
        with open(mapping, "w", encoding="utf-8") as f:
            f.write(MAPPING)

        # rewrite into an output dir, originals untouched
        out = os.path.join(tmp, "out")
        os.makedirs(out)
        stdout = run(["--mapping", mapping, "--out", out, FIXTURE])
        result = os.path.join(out, os.path.basename(FIXTURE))
        text = open(result, encoding="utf-8").read()

        check("reports a non-zero COUNT", "COUNT 0" not in stdout)
        # inline @ref rewritten (keep the leading '#')
        check("person ref rewritten", 'ref="#person-000001"' in text)
        check("place ref rewritten", 'ref="#place-000001"' in text)
        check("technique ref (via rs) rewritten", 'ref="#technique-000001"' in text)
        check("material ref rewritten", 'ref="#material-000001"' in text)
        check("date ref rewritten", 'ref="#date-000001"' in text)
        # no old UUID ref survives among the mapped ones
        check("no mapped UUID ref left", 'ref="#pers-0001"' not in text
              and 'ref="#place-0001"' not in text)
        # INVARIANT 1: standOff xml:id untouched (no duplicate id from many-to-one)
        check("standOff xml:id pers-0001 intact", 'xml:id="pers-0001"' in text)
        check("standOff xml:id pers-0002 intact", 'xml:id="pers-0002"' in text)
        check("no register id leaked into xml:id", 'xml:id="person-000001"' not in text)
        # INVARIANT 2: unmapped ref kept verbatim
        check("unmapped ref kept", 'ref="#pers-9999"' in text)
        # well-formed
        try:
            minidom.parseString(text.encode("utf-8"))
            check("output is well-formed XML", True)
        except Exception as e:  # noqa
            check("output is well-formed XML (%s)" % e, False)

        # idempotence: rewriting the result again changes nothing
        out2 = os.path.join(tmp, "out2")
        os.makedirs(out2)
        stdout2 = run(["--mapping", mapping, "--out", out2, result])
        check("second pass is idempotent (COUNT 0)", "COUNT 0" in stdout2)

    print("  -> %d failure(s)" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
