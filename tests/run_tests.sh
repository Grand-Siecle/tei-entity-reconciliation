#!/usr/bin/env bash
# Runs the offline test suite. No network, no external deps (Python 3 stdlib).
#   tests/run_tests.sh
# Set RUN_NETWORK_TESTS=1 to also run the live Wikidata smoke test.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
rc=0
# any accidental Wikidata call fails fast (closed local port) instead of
# reaching the network; the live smoke test below restores the real endpoint
export WIKIDATA_API=http://127.0.0.1:9/

echo "unittest (extract, reconcile, idregistry, curation, export/import):"
(cd "$HERE/.." && python3 -m unittest tests/test_extract.py tests/test_reconcile.py \
  tests/test_idregistry.py tests/test_curation.py tests/test_export_import.py) || rc=1
echo
python3 "$HERE/test_rewrite_refs.py" || rc=1
echo
python3 "$HERE/test_validate_registers.py" || rc=1
echo
bash "$HERE/test_pipeline.sh" || rc=1

if [ "${RUN_NETWORK_TESTS:-0}" = 1 ]; then
  echo
  env -u WIKIDATA_API bash "$HERE/test_wikidata_smoke.sh" || rc=1
fi

echo
echo "==================================="
[ $rc = 0 ] && echo "ALL TESTS PASSED" || echo "SOME TESTS FAILED"
exit $rc
