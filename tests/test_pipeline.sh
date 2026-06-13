#!/usr/bin/env bash
# End-to-end OFFLINE test : extract -> build_registers (no Wikidata) -> rewrite -> validate.
# Uses env overrides to write everything into a throwaway temp dir.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
FIXTURE="$ROOT/examples/sample/data/sample_reconciled.tei.xml"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fail=0
chk(){ if [ "$1" = 0 ]; then echo "  ok   $2"; else echo "  FAIL $2"; fail=1; fi; }

echo "test_pipeline (offline):"

# 1) extraction
NER_OUT="$TMP/ner" TEI_GLOB="$FIXTURE" python3 "$ROOT/scripts/extract_entities.py" >/dev/null 2>"$TMP/e.log"
chk $? "extract_entities runs"
[ -f "$TMP/ner/entities_clusters.json" ]; chk $? "clusters json produced"

# 9 distinct types clustered ?
ntypes=$(python3 -c "import json;d=json.load(open('$TMP/ner/entities_clusters.json'));print(len({e['type'] for e in d}))" 2>/dev/null)
chk $([ "$ntypes" = 9 ] && echo 0 || echo 1) "9 entity types clustered (got ${ntypes:-?})"

# 2) build registers with an EMPTY reconciliation (offline, no Wikidata)
echo "[]" > "$TMP/ner/entities_reconciled.json"
NER_OUT="$TMP/ner" REGISTERS_DIR="$TMP/registers" CORPUS_NAME="Test Corpus" \
  python3 "$ROOT/scripts/build_registers.py" >/dev/null 2>"$TMP/b.log"
chk $? "build_registers runs"
nreg=$(ls "$TMP/registers"/*.xml 2>/dev/null | wc -l)
chk $([ "$nreg" = 9 ] && echo 0 || echo 1) "9 register files generated (got $nreg)"
[ -f "$TMP/registers/id_mapping.csv" ]; chk $? "id_mapping.csv generated"

# 3) validate the generated registers
python3 "$HERE/validate_registers.py" "$TMP/registers" >/dev/null 2>&1
chk $? "registers pass validation (well-formed, no dup id, mapping consistent)"

# 4) rewrite the fixture's @ref using the freshly built mapping, then re-validate vs docs
cp "$FIXTURE" "$TMP/doc.tei.xml"
python3 "$ROOT/scripts/rewrite_refs.py" --mapping "$TMP/registers/id_mapping.csv" "$TMP/doc.tei.xml" >/dev/null
chk $? "rewrite_refs runs on built mapping"
python3 "$HERE/validate_registers.py" "$TMP/registers" --docs "$TMP/doc.tei.xml" >/dev/null 2>&1
chk $? "rewritten doc: every @ref resolves to a register id"

echo "  -> $([ $fail = 0 ] && echo PASS || echo FAIL)"
exit $fail
