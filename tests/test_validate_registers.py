#!/usr/bin/env python3
"""Unit tests for tests/validate_registers.py — the --docs and --curation checks.

  * --curation: `id` is accepted when it is a live register xml:id OR any xml_id
    of id_registry.csv (merged/deleted entities are gone from the register XML);
    `fusion_vers` must be a LIVE register id; `decision` must be valid.
  * --docs: duplicate xml:id inside a document FAILs; header PERSnnnn refs are
    ignored by default (--ignore-ref).

Pure stdlib, offline. Run directly.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "validate_registers.py")

REGISTER = ('<listPerson xmlns="http://www.tei-c.org/ns/1.0">'
            '<person xml:id="person-000001"/><person xml:id="person-000002"/>'
            '</listPerson>\n')
MAPPING = "ner_uuid,register_id,type\npers-0001,person-000001,person\n"
REGISTRY = """type,label_norm,xml_id,status,ner_ids,created,updated
person,platon,person-000001,active,pers-0001,2026-11-04,2026-11-04
person,aristote,person-000002,active,pers-0002,2026-11-04,2026-11-04
person,socrate,person-000003,deleted,pers-0003,2026-11-04,2026-11-04
person,platoun,person-000004,merged,pers-0004,2026-11-04,2026-11-04
"""
CUR_HEAD = ("id,type,decision,qid_corrige,fusion_vers,label_corrige,type_corrige,"
            "commentaire,qui,date,source\n")
CUR_OK = CUR_HEAD + (
    "person-000003,person,supprimer,,,,,,rb,2026-11-04,lot\n"
    "person-000004,person,FUSIONNER,,person-000001,,,,rb,2026-11-04,lot\n"
    "person-000002,person,,,,,,,rb,2026-11-04,lot\n")

failures = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        failures.append(name)


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def run(args):
    p = subprocess.run([sys.executable, SCRIPT] + args, capture_output=True, text=True)
    return p.returncode, p.stdout


def main():
    print("test_validate_registers:")
    with tempfile.TemporaryDirectory() as tmp:
        reg = os.path.join(tmp, "registers")
        os.makedirs(reg)
        write(os.path.join(reg, "persons.xml"), REGISTER)
        write(os.path.join(reg, "id_mapping.csv"), MAPPING)
        write(os.path.join(reg, "id_registry.csv"), REGISTRY)

        def cur(name, text):
            path = os.path.join(tmp, name)
            write(path, text)
            return run([reg, "--curation", path])

        rc, out = cur("ok.csv", CUR_OK)
        check("supprimer/fusionner rows whose id exists only in id_registry.csv pass",
              rc == 0 and "3 row(s) checked" in out)

        rc, out = cur("unknown.csv", CUR_HEAD + "person-000099,person,ok,,,,,,,,\n")
        check("unknown curation id FAILs", rc == 1 and "person-000099" in out)

        rc, out = cur("deadtarget.csv",
                      CUR_HEAD + "person-000002,person,fusionner,,person-000004,,,,,,\n")
        check("fusion_vers to a merged (not live) id FAILs",
              rc == 1 and "fusion_vers 'person-000004'" in out)

        rc, out = cur("baddecision.csv", CUR_HEAD + "person-000001,person,garder,,,,,,,,\n")
        check("invalid decision FAILs", rc == 1 and "invalid decision" in out)

        # without id_registry.csv, only live register ids are known
        os.remove(os.path.join(reg, "id_registry.csv"))
        rc, out = cur("noreg.csv", CUR_HEAD + "person-000003,person,supprimer,,,,,,,,\n")
        check("without id_registry.csv a deleted id is unknown (FAIL)", rc == 1)

        # --docs: duplicate xml:id and default --ignore-ref
        doc_ok = os.path.join(tmp, "ok.tei.xml")
        write(doc_ok, '<TEI><teiHeader><author ref="#PERS0037"/>'
                      '<person xml:id="PERS0037"/></teiHeader>'
                      '<text><p><persName ref="#person-000001"/></p></text></TEI>\n')
        rc, out = run([reg, "--docs", doc_ok])
        check("header PERSnnnn ref ignored by default, no duplicate -> pass", rc == 0)
        rc, out = run([reg, "--docs", doc_ok, "--ignore-ref", "^$"])
        check("--ignore-ref '^$' makes the PERS ref dangling (FAIL)",
              rc == 1 and "PERS0037" in out)
        doc_dup = os.path.join(tmp, "dup.tei.xml")
        write(doc_dup, '<TEI><standOff><person xml:id="person-000001"/>'
                       '<person xml:id="person-000001"/></standOff></TEI>\n')
        rc, out = run([reg, "--docs", doc_dup])
        check("duplicate xml:id inside a document FAILs",
              rc == 1 and "duplicate xml:id" in out)

    print("  -> %d failure(s)" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
