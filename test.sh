#!/usr/bin/env bash
# Run all unit test scripts manually, one module at a time.
# Usage: ./test.sh [python_executable]

set -u

cd "$(dirname "$0")"

PYTHON="${1:-python}"
PASS=0
FAIL=0
FAILED_MODULES=()

for test_file in tests/test_*.py; do
    module="tests.$(basename "$test_file" .py)"
    echo "======================================================================"
    echo "Running $module"
    echo "======================================================================"
    if "$PYTHON" -m unittest "$module" -v; then
        PASS=$((PASS + 1))
    else
        FAIL=$((FAIL + 1))
        FAILED_MODULES+=("$module")
    fi
    echo
done

echo "======================================================================"
echo "Summary: $PASS module(s) passed, $FAIL module(s) failed"
if [ "$FAIL" -gt 0 ]; then
    echo "Failed modules:"
    for module in "${FAILED_MODULES[@]}"; do
        echo "  - $module"
    done
    exit 1
fi
