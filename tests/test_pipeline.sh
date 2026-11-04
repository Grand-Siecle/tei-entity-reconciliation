#!/usr/bin/env bash
# End-to-end OFFLINE test : extract -> build_registers (no Wikidata) -> rewrite -> validate,
# then the team curation cycle : corpus growth (stable ids) -> curation.csv ->
# export_lots -> import_curation -> rewrite from originals -> validate.
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

# ---------------------------------------------------------------------------
# Team curation workflow (incremental): stable ids, curation overlay, lots.
# Everything stays offline: no reconciliation (empty results), and no curated
# QID needs a Wikidata fetch at build time.
# ---------------------------------------------------------------------------
FIXTURE2="$ROOT/examples/sample/data/sample_origreg.tei.xml"
GLOB_ALL="$ROOT/examples/sample/data/sample_*.tei.xml"
MAP="$TMP/registers/id_mapping.csv"
CUR="$TMP/registers/curation.csv"
regid(){ python3 - "$MAP" "$1" <<'PY'
import csv, sys
m = {r["ner_uuid"]: r["register_id"] for r in csv.DictReader(open(sys.argv[1], encoding="utf-8"))}
print(m.get(sys.argv[2], ""))
PY
}
build(){ NER_OUT="$TMP/ner" REGISTERS_DIR="$TMP/registers" CORPUS_NAME="Test Corpus" \
  python3 "$ROOT/scripts/build_registers.py" >>"$TMP/b.log" 2>&1; }

PLATON=$(regid pers-0001)
ARISTOTE=$(regid pers-0002)
CONCILE=$(regid event-0001)
ATHENES=$(regid place-0001)

# 5) corpus growth: extract + build over both fixtures, ids must not move
NER_OUT="$TMP/ner" TEI_GLOB="$GLOB_ALL" python3 "$ROOT/scripts/extract_entities.py" >/dev/null 2>"$TMP/e2.log"
echo "[]" > "$TMP/ner/entities_reconciled.json"
build
rc=$?
P2=$(regid pers-0001)
chk $([ $rc = 0 ] && [ -n "$PLATON" ] && [ "$P2" = "$PLATON" ] && echo 0 || echo 1) \
  "ids stable across corpus growth ($PLATON -> ${P2:-?})"

# 6) curation overlay: supprimer / fusionner / label_corrige / doute
cat > "$CUR" <<EOF
id,type,decision,qid_corrige,fusion_vers,label_corrige,type_corrige,commentaire,qui,date,source
$CONCILE,event,supprimer,,,,,,test,2026-11-04,
$ARISTOTE,person,fusionner,,$PLATON,,,,test,2026-11-04,
$PLATON,person,,,,Platon (curé),,,test,2026-11-04,
$ATHENES,place,doute,,,,,,test,2026-11-04,
EOF
build
rc=$?
ok=0
[ $rc = 0 ] || ok=1
grep -q "Concile" "$TMP/registers/events.xml" && ok=1
[ "$(regid pers-0002)" = "$PLATON" ] || ok=1
grep -q "Platon (curé)" "$TMP/registers/persons.xml" || ok=1
grep -Eq '<note type="curation"( resp="[^"]*")?>doute</note>' "$TMP/registers/places.xml" || ok=1
chk $ok "curation applied"

# 7) reviewer lots: first export all `nouveau`, second export all `inchangé`
statuts(){ python3 - "$1" <<'PY'
import csv, sys
print(" ".join(sorted({r["statut"] for r in csv.DictReader(open(sys.argv[1], encoding="utf-8-sig"))})))
PY
}
NER_OUT="$TMP/ner" CURATION_CSV="$CUR" python3 "$ROOT/scripts/export_lots.py" \
  --min-mentions 1 --out "$TMP/lots" >/dev/null 2>"$TMP/x.log"
rc=$?
LOT="$TMP/lots/person_lot01.csv"
s1=$([ -f "$LOT" ] && statuts "$LOT")
chk $([ $rc = 0 ] && [ -f "$TMP/lots/lots_manifest.csv" ] && [ "$s1" = "nouveau" ] && echo 0 || echo 1) \
  "lots exported (person statut: ${s1:-?})"
NER_OUT="$TMP/ner" CURATION_CSV="$CUR" python3 "$ROOT/scripts/export_lots.py" \
  --min-mentions 1 --out "$TMP/lots" >/dev/null 2>>"$TMP/x.log"
rc=$?
s2=$([ -f "$LOT" ] && statuts "$LOT")
chk $([ $rc = 0 ] && [ "$s2" = "inchangé" ] && echo 0 || echo 1) \
  "lots re-exported unchanged (person statut: ${s2:-?})"

# 8) import a reviewer file (OpenRefine-style qid_final): dry-run, then for real
printf 'id,qid_final\n%s,Q859\n' "$PLATON" > "$TMP/relecture.csv"
cp "$CUR" "$TMP/cur.before"
CURATION_CSV="$CUR" python3 "$ROOT/scripts/import_curation.py" --dry-run "$TMP/relecture.csv" \
  >/dev/null 2>"$TMP/i.log"
rc=$?
chk $([ $rc = 0 ] && cmp -s "$CUR" "$TMP/cur.before" && echo 0 || echo 1) \
  "import dry-run (curation.csv untouched)"
CURATION_CSV="$CUR" python3 "$ROOT/scripts/import_curation.py" --qui test "$TMP/relecture.csv" \
  >/dev/null 2>>"$TMP/i.log"
rc=$?
got=$(python3 - "$CUR" "$PLATON" <<'PY'
import csv, sys
for r in csv.DictReader(open(sys.argv[1], encoding="utf-8")):
    if r["id"] == sys.argv[2]:
        print(r["decision"], r["qid_corrige"])
PY
)
chk $([ $rc = 0 ] && [ "$got" = "corriger Q859" ] && echo 0 || echo 1) "import applied (Platon: ${got:-?})"

# 9) rewrite the ORIGINAL documents with the curated mapping, validate with curation
mkdir -p "$TMP/final"
cp "$FIXTURE" "$FIXTURE2" "$TMP/final/"
python3 "$ROOT/scripts/rewrite_refs.py" --mapping "$MAP" "$TMP/final/"*.tei.xml >/dev/null 2>"$TMP/r.log"
chk $? "rewrite_refs runs on curated mapping"
# `supprimer` leaves the deleted entity's refs local (#event-0001): ignore it.
python3 "$HERE/validate_registers.py" "$TMP/registers" --docs "$TMP/final/*.tei.xml" \
  --curation "$CUR" --ignore-ref '^(PERS\d+|event-0001)$' >"$TMP/v.log" 2>&1
chk $? "validate_registers --docs --curation passes"

# 10) idempotence: a second rewrite changes nothing
total=$(python3 "$ROOT/scripts/rewrite_refs.py" --mapping "$MAP" "$TMP/final/"*.tei.xml 2>/dev/null \
  | awk '$1=="TOTAL"{print $2}')
chk $([ "$total" = 0 ] && echo 0 || echo 1) "second rewrite: COUNT 0 (TOTAL ${total:-?})"

echo "  -> $([ $fail = 0 ] && echo PASS || echo FAIL)"
exit $fail
