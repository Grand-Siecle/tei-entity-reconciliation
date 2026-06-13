#!/usr/bin/env bash
# OPTIONAL live test : confirms the Wikidata API is reachable and the reconciler
# still finds an obvious match. Hits the network -> only run on demand.
#   RUN_NETWORK_TESTS=1 tests/run_tests.sh      (or run this file directly)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
FIXTURE="$ROOT/examples/sample/data/sample_reconciled.tei.xml"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
fail=0
chk(){ if [ "$1" = 0 ]; then echo "  ok   $2"; else echo "  FAIL $2"; fail=1; fi; }

echo "test_wikidata_smoke (network):"
export WIKIDATA_CONTACT="${WIKIDATA_CONTACT:-ci@example.org}"

NER_OUT="$TMP/ner" TEI_GLOB="$FIXTURE" python3 "$ROOT/scripts/extract_entities.py" >/dev/null 2>&1
NER_OUT="$TMP/ner" python3 "$ROOT/scripts/reconcile_wikidata.py" --types person --limit 5 >/dev/null 2>"$TMP/r.log"
chk $? "reconcile_wikidata runs against the live API"

matched=$(python3 -c "
import json,sys
try: d=json.load(open('$TMP/ner/entities_reconciled.json'))
except Exception: sys.exit(print('0') or 0)
print(sum(1 for r in d if r.get('wikidata_qid')))" 2>/dev/null)
chk $([ "${matched:-0}" -ge 1 ] && echo 0 || echo 1) "at least one person reconciled to a QID (got ${matched:-0})"

echo "  -> $([ $fail = 0 ] && echo PASS || echo FAIL)"
exit $fail
