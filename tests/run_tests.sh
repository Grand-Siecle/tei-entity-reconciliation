#!/usr/bin/env bash
# Runs the offline test suite. No network, no external deps (Python 3 stdlib).
#   tests/run_tests.sh
# Set RUN_NETWORK_TESTS=1 to also run the live Wikidata smoke test.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
rc=0

python3 "$HERE/test_rewrite_refs.py" || rc=1
echo
bash "$HERE/test_pipeline.sh" || rc=1

if [ "${RUN_NETWORK_TESTS:-0}" = 1 ]; then
  echo
  bash "$HERE/test_wikidata_smoke.sh" || rc=1
fi

echo
echo "==================================="
[ $rc = 0 ] && echo "ALL TESTS PASSED" || echo "SOME TESTS FAILED"
exit $rc
