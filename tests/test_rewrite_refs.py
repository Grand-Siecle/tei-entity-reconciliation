#!/usr/bin/env python3
"""Unit tests for scripts/rewrite_refs.py — the @ref rewriting step.

Proves the safety invariants:
  1. in the <text> body only @ref values are rewritten (mention ids untouched);
  2. refs absent from the mapping are kept verbatim (unreconciled entities);
  3. issue #1: the in-document lists (standOff / teiHeader) are rebuilt with the
     register ids, merged UUIDs collapsed into ONE entry (extra names become
     type="variant"), linkGrp targets rewritten, no duplicate xml:id ever;
  4. --no-standoff restores the old behaviour (lists untouched).
Plus: well-formed output, idempotence (a second pass changes nothing, byte for
byte), minified input handled like pretty input, files without NER untouched.

Pure stdlib, offline. Run directly or via tests/run_tests.sh.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.dom.minidom as minidom
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, "scripts", "rewrite_refs.py")
FIXTURE = os.path.join(ROOT, "examples", "sample", "data", "sample_reconciled.tei.xml")
TEI_NS = "http://www.tei-c.org/ns/1.0"
XMLID_RE = re.compile(r'xml:id="([^"]+)"')

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

# collapse / linkGrp case: pers-0001 and pers-0003 both -> person-000001
MAPPING_COLLAPSE = """ner_uuid,register_id,type
pers-0001,person-000001,person
pers-0003,person-000001,person
"""

COLLAPSE_DOC = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc><titleStmt><title>t</title></titleStmt></fileDesc>
  </teiHeader>
  <text>
    <body>
      <p><persName ref="#pers-0001" xml:id="pers-0001-m1">Platon</persName> et
      <persName ref="#pers-0003" xml:id="pers-0003-m1">Platoun</persName></p>
    </body>
  </text>
  <standOff>
    <listPerson>
      <person xml:id="pers-0001" cert="low"><persName>Platon</persName></person>
      <person xml:id="pers-0003" cert="high"><persName>Platoun</persName></person>
    </listPerson>
    <linkGrp type="mentions">
      <link target="#pers-0001 #pers-0001-m1"/>
      <link target="#pers-0003 #pers-0003-m1"/>
    </linkGrp>
  </standOff>
</TEI>
"""

NO_NER_DOC = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:id="LIV9999">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>Sans NER</title><author ref="#PERS0037">X</author></titleStmt>
    </fileDesc>
    <profileDesc><particDesc><listPerson>
      <person xml:id="PERS0037"><persName>X</persName></person>
    </listPerson></particDesc></profileDesc>
  </teiHeader>
  <text><body><p>Rien a reconcilier.</p></body></text>
</TEI>
"""

failures = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        failures.append(name)


def run(args):
    return subprocess.run([sys.executable, SCRIPT] + args,
                          capture_output=True, text=True, check=True).stdout


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def no_dup_ids(text):
    ids = XMLID_RE.findall(text)
    return len(ids) == len(set(ids))


def canon(el):
    """Whitespace-insensitive serialization of an element (for comparisons)."""
    el = ET.fromstring(ET.tostring(el))
    for e in el.iter():
        if e.text is not None and not e.text.strip():
            e.text = None
        if e.tail is not None and not e.tail.strip():
            e.tail = None
    return ET.tostring(el)


def main():
    print("test_rewrite_refs:")
    with tempfile.TemporaryDirectory() as tmp:
        mapping = os.path.join(tmp, "id_mapping.csv")
        write(mapping, MAPPING)

        # rewrite into an output dir, originals untouched
        out = os.path.join(tmp, "out")
        os.makedirs(out)
        stdout = run(["--mapping", mapping, "--out", out, FIXTURE])
        result = os.path.join(out, os.path.basename(FIXTURE))
        text = read(result)

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
        # ISSUE #1: standOff rebuilt with register ids, merged UUIDs collapsed
        check("standOff person pers-0001 now has xml:id person-000001 (the mapped id)",
              'xml:id="person-000001"' in text and 'xml:id="pers-0001"' not in text)
        check("standOff pers-0002 (merged into person-000001) collapsed, no xml:id left",
              'xml:id="pers-0002"' not in text
              and text.count('xml:id="person-000001"') == 1)
        check("standOff place rebuilt (xml:id place-000001)", 'xml:id="place-000001"' in text)
        check("no duplicate xml:id in output", no_dup_ids(text))
        # INVARIANT 2: unmapped ref kept verbatim
        check("unmapped ref kept", 'ref="#pers-9999"' in text)
        # well-formed
        try:
            minidom.parseString(text.encode("utf-8"))
            check("output is well-formed XML", True)
        except Exception as e:  # noqa
            check("output is well-formed XML (%s)" % e, False)
        check("TEI namespace not prefixed (no ns0:)", "ns0:" not in text)

        # idempotence: rewriting the result again changes nothing
        out2 = os.path.join(tmp, "out2")
        os.makedirs(out2)
        stdout2 = run(["--mapping", mapping, "--out", out2, result])
        check("second pass is idempotent (COUNT 0)", "COUNT 0" in stdout2)
        # in place: second pass is a no-op, byte for byte
        inplace = os.path.join(tmp, "inplace.tei.xml")
        shutil.copy(result, inplace)
        before = read_bytes(inplace)
        stdout3 = run(["--mapping", mapping, inplace])
        check("second pass is a no-op (COUNT 0 and file identical)",
              "COUNT 0" in stdout3 and read_bytes(inplace) == before)

        # unmapped standOff entry keeps its xml:id
        extra = read(FIXTURE).replace(
            '<person xml:id="pers-0002"><persName>Aristote</persName></person>',
            '<person xml:id="pers-0002"><persName>Aristote</persName></person>\n'
            '      <person xml:id="pers-9998"><persName>Inconnu</persName></person>')
        extra_path = os.path.join(tmp, "extra.tei.xml")
        write(extra_path, extra)
        run(["--mapping", mapping, extra_path])
        etext = read(extra_path)
        check("unmapped standOff entry keeps xml:id pers-9998",
              'xml:id="pers-9998"' in etext and no_dup_ids(etext))

        # collapse + linkGrp on an inline fixture
        map2 = os.path.join(tmp, "map2.csv")
        write(map2, MAPPING_COLLAPSE)
        cpath = os.path.join(tmp, "collapse.tei.xml")
        write(cpath, COLLAPSE_DOC)
        run(["--mapping", map2, cpath])
        ctext = read(cpath)
        check("two UUIDs mapped to the same id collapse into one entry",
              ctext.count('xml:id="person-000001"') == 1
              and 'xml:id="pers-0003"' not in ctext and no_dup_ids(ctext))
        check("collapsed entry's extra name is <persName type=\"variant\">",
              '<persName type="variant">Platoun</persName>' in ctext)
        check("collapsed entry keeps the highest cert (high)",
              re.search(r'<person (?=[^>]*xml:id="person-000001")(?=[^>]*cert="high")',
                        ctext) is not None)
        check("linkGrp link/@target first token rewritten, mention token (-m1) untouched",
              'target="#person-000001 #pers-0001-m1"' in ctext
              and 'target="#person-000001 #pers-0003-m1"' in ctext)
        check("body mention xml:id (-m1) untouched",
              'xml:id="pers-0001-m1"' in ctext and 'xml:id="pers-0003-m1"' in ctext)
        try:
            ET.fromstring(ctext.encode("utf-8"))
            check("collapsed output is well-formed XML", True)
        except ET.ParseError as e:
            check("collapsed output is well-formed XML (%s)" % e, False)

        # --no-standoff: previous behaviour exactly
        out3 = os.path.join(tmp, "out3")
        os.makedirs(out3)
        run(["--mapping", mapping, "--no-standoff", "--out", out3, FIXTURE])
        ntext = read(os.path.join(out3, os.path.basename(FIXTURE)))
        check("--no-standoff keeps old behaviour (xml:id pers-0001 intact)",
              'xml:id="pers-0001"' in ntext and 'xml:id="pers-0002"' in ntext
              and 'xml:id="person-000001"' not in ntext
              and 'ref="#person-000001"' in ntext)

        # minified fixture: whole standOff on one line with the preceding </text>
        src = read(FIXTURE)
        so = re.search(r"\s*<standOff>.*?</standOff>", src, re.S)
        so_min = re.sub(r">\s+<", "><", so.group(0).strip())
        mini = src[:so.start()] + src[so.end():]
        mini = mini.replace("</text>\n", "</text>" + so_min + "\n")
        mpath = os.path.join(tmp, "mini.tei.xml")
        write(mpath, mini)
        run(["--mapping", mapping, mpath])
        mtext = read(mpath)
        try:
            pretty_root = ET.fromstring(text.encode("utf-8"))
            mini_root = ET.fromstring(mtext.encode("utf-8"))
            q = "{%s}standOff" % TEI_NS
            same = canon(pretty_root.find(q)) == canon(mini_root.find(q))
        except (ET.ParseError, TypeError):
            same = False
        check("minified fixture (standOff on one line with </text>) rebuilt "
              "identically to the pretty one", same and no_dup_ids(mtext))

        # file without standOff and without refs: byte-identical, COUNT 0
        npath = os.path.join(tmp, "noner.tei.xml")
        write(npath, NO_NER_DOC)
        before = read_bytes(npath)
        stdout4 = run(["--mapping", mapping, npath])
        check("file without standOff and without refs is byte-identical (COUNT 0)",
              "COUNT 0" in stdout4 and read_bytes(npath) == before)

    print("  -> %d failure(s)" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
