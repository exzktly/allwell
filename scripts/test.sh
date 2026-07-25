#!/usr/bin/env bash
# Run the fast lane exactly as CI runs it.
#
# CI calls this same script (.github/workflows/ci.yml), so the local and remote
# invocations cannot drift. That drift is the thing this file exists to prevent:
# a hand-rolled `pytest tests/` differs from CI's command in flags (no timeout)
# and, before the config moved into pyproject.toml, in which config file it
# loaded.
#
# Extra arguments are forwarded to pytest, which is how CI adds --junitxml.
#
#   scripts/test.sh                      # the fast lane
#   scripts/test.sh -k threshold         # a subset of it
#   scripts/test.sh tests/test_app_smoke.py -m ""   # one full-app file
#
# NOT covered here: the `slow` full-app tests. Each builds a real WellViewerApp
# whose Qt objects are never reclaimed in-process (~40 MB/test), so a bulk run
# hangs or gets OOM-killed. Run those a file or two at a time, as the last
# example above shows. See ARCHITECTURE.md §10.4.
set -euo pipefail

cd "$(dirname "$0")/.."

# Qt must not look for a display; matplotlib must not look for a GUI backend.
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
export MPLBACKEND="${MPLBACKEND:-Agg}"

exec python -m pytest -m "not slow" -q \
    --timeout=90 --timeout-method=signal \
    "$@"
