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
#   scripts/test.sh --nanowell-features  # the rest of the nanowell tests
#   scripts/test.sh --remote             # the remote / HPC tests
#   scripts/test.sh --ui                 # only the GUI layout / appearance tests
#   scripts/test.sh --no-ui              # the fast lane without them (what CI runs)
#   scripts/test.sh -k threshold         # a subset of it
#   scripts/test.sh tests/test_app_smoke.py -m ""   # one full-app file
#
# The fast lane (the default, and --no-ui) runs only what the change can reach,
# as scripts/change_scope.py classifies it against origin/main:
#   version-only  nothing runs (exit 0): only the __version__ line moved
#   docs-only     the docs lane: just the test files that read the documents
#   code          the whole lane
# ALLWELL_FULL_LANE=1 runs the whole lane whatever changed; CI sets it on pushes
# to main. Naming test files, or choosing another lane, is never narrowed.
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
# end-to-end files instead (tests/_lanes.py NANOWELL_E2E_FILES: rendered tiles through the real
# detection, about 1,075 s of work), which CI runs as a job of its own beside the fast lane. That
# lane distributes by test (`--dist load`) rather than by file: one of its files is 475 s, and kept
# on one worker it would set the lane's wall time. `--nanowell-features` and `--remote` run the
# other two gated rosters (tests/_lanes.py); preflight and CI run all three only when
# `scripts/lane_needed.py` says the change can reach them, and CI always on main. The GUI layout and
# appearance files (UI_LANE_FILES) are part of the local fast lane but not of CI's: CI passes
# `--no-ui` to leave them out, and `--ui` runs them alone.
# Naming test files on the command line selects
# them whichever lane they belong to, so `scripts/test.sh tests/test_nanowell_run.py` runs it.
FAST="not slow and not nanowell_e2e and not nanowell_feature and not remote_lane"
MARK="$FAST"
DIST=loadfile
case "${1:-}" in
    --nanowell)          shift; MARK="nanowell_e2e and not slow"; DIST=load ;;
    --nanowell-features) shift; MARK="nanowell_feature and not slow" ;;
    --remote)            shift; MARK="remote_lane and not slow" ;;
    --ui)                shift; MARK="ui_lane and not slow" ;;
    --no-ui)             shift; MARK="$FAST and not ui_lane" ;;
    *)
        for arg in "$@"; do
            if [[ "$arg" == tests/* || "$arg" == *.py || "$arg" == *.py::* ]]; then
                MARK="not slow"
                break
            fi
        done ;;
esac

# Narrow a gated lane to the test files the change can reach (scripts/lane_needed.py <gate> --files):
# the files a changed definition reaches through its importers, or the whole lane when the change
# touches what every test receives (a harness file, the pytest configuration, a conftest hook). A
# lane nothing can reach runs nothing. Naming test files, or ALLWELL_FULL_LANE=1, runs exactly what
# was asked; CI sets ALLWELL_FULL_LANE on a push to main and on a pull request labelled full-ci.
GATE=""
case "$MARK" in
    "nanowell_e2e and not slow")     GATE=nanowell-e2e ;;
    "nanowell_feature and not slow") GATE=nanowell-features ;;
    "remote_lane and not slow")      GATE=remote ;;
esac
if [[ -n "$GATE" && -z "${ALLWELL_FULL_LANE:-}" ]]; then
    NAMED=""
    for arg in "$@"; do
        if [[ "$arg" == tests/* || "$arg" == *.py || "$arg" == *.py::* ]]; then NAMED=1; break; fi
    done
    if [[ -z "$NAMED" ]]; then
        SELECTED=()   # read line by line, not with mapfile: macOS still ships bash 3.2
        status=0
        while IFS= read -r f; do [[ -n "$f" ]] && SELECTED+=("$f"); done \
            < <(python scripts/lane_needed.py "$GATE" --files && echo "__status=0" || echo "__status=$?")
        last=$(( ${#SELECTED[@]} - 1 ))
        status="${SELECTED[$last]#__status=}"
        unset "SELECTED[$last]"
        if [[ "$status" == "1" ]]; then
            echo "scripts/test.sh: nothing this change touches reaches the $GATE lane — no tests to run" \
                 "(ALLWELL_FULL_LANE=1 runs it anyway)" >&2
            exit 0
        elif [[ "$status" != "0" || "${SELECTED[*]:-}" == "ALL" || ${#SELECTED[@]} -eq 0 ]]; then
            echo "scripts/test.sh: running the whole $GATE lane" >&2
        else
            echo "scripts/test.sh: running the ${#SELECTED[@]} $GATE test files this change can reach" \
                 "(ALLWELL_FULL_LANE=1 runs the whole lane; scripts/lane_needed.py $GATE says why)" >&2
            set -- "${SELECTED[@]}" "$@"
        fi
    fi
fi

# Narrow the fast lane to what the change can reach (scripts/change_scope.py). Only the default lane
# and --no-ui are narrowed, and only when no test files were named: a run that names its files, or
# asks for another lane, gets exactly what it asked for.
if [[ -z "${ALLWELL_FULL_LANE:-}" && ( "$MARK" == "$FAST" || "$MARK" == "$FAST and not ui_lane" ) ]]; then
    SCOPE=$(python scripts/change_scope.py) || SCOPE=code   # a classifier that fails runs everything
    case "$SCOPE" in
        version-only)
            echo "scripts/test.sh: only the version number changed — no tests to run" \
                 "(ALLWELL_FULL_LANE=1 runs the fast lane anyway)" >&2
            exit 0 ;;
        docs-only)
            DOCS_TESTS=()   # read line by line, not with mapfile: macOS still ships bash 3.2
            while IFS= read -r f; do [[ -n "$f" ]] && DOCS_TESTS+=("$f"); done \
                < <(python scripts/change_scope.py --docs-tests)
            echo "scripts/test.sh: documentation-only change — running the docs lane" \
                 "(${#DOCS_TESTS[@]} files; ALLWELL_FULL_LANE=1 runs the whole fast lane)" >&2
            # The docs lane crosses rosters (a gated or UI file that reads a document runs here too), so
            # its marker keeps only the slow exclusion — and CI's --no-ui exclusion when it was asked for.
            # An empty docs lane (nothing reads the documents) leaves the whole lane to run.
            if (( ${#DOCS_TESTS[@]} > 0 )); then
                if [[ "$MARK" == "$FAST" ]]; then MARK="not slow"; else MARK="not slow and not ui_lane"; fi
                set -- "${DOCS_TESTS[@]}" "$@"
            fi ;;
    esac
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
