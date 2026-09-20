#!/usr/bin/env bash
#
# Run pyspider unit tests.
#
# Usage:
#   ./test.sh                      # run unit tests (no external services needed)
#   ./test.sh tests/test_utils.py  # run specific test file(s)
#   FULL=1 ./test.sh               # run the whole tests/ directory
#                                  # (requires httpbin, phantomjs, pycurl, ...)
#
# Environment:
#   PYTHON        python interpreter to use (default: python3)
#   PYTEST_OPTS   extra options passed to pytest

set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
PYTEST_OPTS="${PYTEST_OPTS:--v}"

if [ "$#" -gt 0 ]; then
    FILES="$*"
elif [ "${FULL:-0}" = "1" ]; then
    FILES="tests/"
else
    # unit tests which do not require external services
    FILES="tests/test_observability.py
           tests/test_counter.py
           tests/test_utils.py
           tests/test_task_queue.py"
fi

echo "==> running unit tests with $PYTHON"
exec "$PYTHON" -m pytest $PYTEST_OPTS $FILES
