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
#   scripts/test.sh --nanowell           # the nanowell end-to-end lane
#   scripts/test.sh -k threshold         # a subset of it
#   scripts/test.sh tests/test_app_smoke.py -m ""   # one full-app file
#
# NOT covered here: the `slow` full-app tests. Each builds a real ReviewApp
# whose Qt objects are never reclaimed in-process (~40 MB/test), so a bulk run
# hangs or gets OOM-killed. Run those a file or two at a time, as the last
# example above shows. See ARCHITECTURE.md §10.4.
set -euo pipefail

cd "$(dirname "$0")/.."

# Qt must not look for a display; matplotlib must not look for a GUI backend.
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
export MPLBACKEND="${MPLBACKEND:-Agg}"

# Coverage is opt-in (ALLWELL_COVERAGE=1), so the default run — which is the gate
# for every push and is run many times a day — pays nothing for it. Both lanes
# read the same variable and write parallel-mode data files that
# scripts/coverage.sh combines; measuring one lane alone reports every
# full-app-only module as dead, which is worse than not measuring at all.
COV=()
if [[ -n "${ALLWELL_COVERAGE:-}" ]]; then
    COV=(--cov --cov-report=)
    # A data file of this lane's own, for two reasons that both cost a whole
    # measurement when they bite. Neither is theoretical; both were reproduced.
    #
    # Left on the default name, this lane writes plain `.coverage` — pytest-cov
    # combines its parallel files at session end and saves under the base name.
    # `coverage combine` then globs `<data_file>.*`, which never matches
    # `.coverage` itself, so the slow lane's `.coverage.slow-*` files combine
    # *without* it and overwrite it. Measured: a module the fast lane covers
    # reported 0% after the documented fast-then-slow-then-combine sequence,
    # and `coverage combine` said "Combined 1 file".
    #
    # Worse in the other order. pytest-cov calls `Coverage.erase()` at session
    # start, and in parallel mode that globs `<data_file>.*` and unlinks every
    # match — so a fast lane run after the slow one deletes every
    # `.coverage.slow-*` on disk. Hours of work gone, and `coverage.sh` then
    # reports the fast lane alone with nothing saying the rest is missing.
    #
    # Under its own name the lane can neither be skipped by the combine nor
    # erase anything but its own files, and run order genuinely stops mattering.
    export COVERAGE_FILE="${COVERAGE_FILE:-.coverage.fast}"
fi

# Parallel by default (pytest-xdist): one worker per CPU, each test FILE kept on one worker
# (``--dist loadfile``) so module-scoped fixtures are built once and tests that run in order
# within a file still do. ALLWELL_TEST_WORKERS=0 runs serially — the right setting for
# ``--durations`` profiling, which a parallel run reports per worker; any other number sets the
# worker count. Without xdist installed the lane runs serially, as it always did.
#
# Two lanes share this script. The default is the fast lane. `--nanowell` runs the nanowell
# end-to-end files instead (tests/conftest.py NANOWELL_E2E_FILES: rendered tiles through the real
# detection, about 1,075 s of work), which CI runs as a job of its own beside the fast lane. That
# lane distributes by test (`--dist load`) rather than by file: one of its files is 475 s, and kept
# on one worker it would set the lane's wall time. Naming test files on the command line selects
# them whichever lane they belong to, so `scripts/test.sh tests/test_nanowell_run.py` runs it.
MARK="not slow and not nanowell_e2e"
DIST=loadfile
if [[ "${1:-}" == "--nanowell" ]]; then
    shift
    MARK="nanowell_e2e and not slow"
    DIST=load
else
    for arg in "$@"; do
        if [[ "$arg" == tests/* || "$arg" == *.py || "$arg" == *.py::* ]]; then
            MARK="not slow"
            break
        fi
    done
fi

XDIST=()
workers="${ALLWELL_TEST_WORKERS:-auto}"
if [[ "$workers" != "0" ]] && python -c "import xdist" 2>/dev/null; then
    XDIST=(-n "$workers" --dist "$DIST")
fi

exec python -m pytest -m "$MARK" -q \
    "${XDIST[@]+"${XDIST[@]}"}" \
    --timeout=90 --timeout-method=signal \
    "${COV[@]+"${COV[@]}"}" \
    "$@"
