#!/usr/bin/env bash
# The checks worth running before pushing — in one command, in the order that
# fails cheapest first.
#
# Not a substitute for CI, and deliberately not the slow lane (see CLAUDE.md §2:
# CI is the gate, and a full slow run per commit is wasted minutes). This is the
# 90-second pass that catches the things which are embarrassing to learn from a
# red check: a lint error, a broken guardrail, a doc that still names a symbol
# you deleted — and scratch output about to be committed by accident, which is
# how 21 JUnit XMLs once reached main.
#
#   scripts/preflight.sh          # lint + fast lane (+ nanowell / remote lanes when reachable) + tree check
#   scripts/preflight.sh --quick  # skip the fast lane (lint + tree only, ~2s)
#
# What runs follows the change's scope (scripts/change_scope.py, the same
# classifier CI and scripts/test.sh read): a version-only change runs no lint and
# no tests; a docs-only change skips lint and runs the docs lane; anything else
# runs everything above. The working-tree check always runs.
#
# There is no version check: pull requests leave version.py alone, and
# .github/workflows/version-bump.yml bumps it on main after each merge.
#
# Exit status: 0 only when every check passes.
set -uo pipefail

cd "$(dirname "$0")/.."

QUICK=0
[[ "${1:-}" == "--quick" ]] && QUICK=1

failed=0
step() { printf '\n\033[1m── %s\033[0m\n' "$1"; }
fail() { printf '\033[31m   FAILED: %s\033[0m\n' "$1" >&2; failed=$(( failed + 1 )); }

step "change scope"
# stdout is the scope, stderr the reason; a classifier that fails runs everything.
scope_out=$(python scripts/change_scope.py 2>&1) || scope_out="code"
SCOPE=$(printf '%s\n' "$scope_out" | head -1)
[[ "$SCOPE" == version-only || "$SCOPE" == docs-only ]] || SCOPE=code
printf '%s\n' "$scope_out" | sed 's/^/   /' 

step "ruff"
if [[ "$SCOPE" != code ]]; then
    echo "   skipped ($SCOPE: no Python changed beyond the version number)"
elif ruff check .; then
    echo "   clean"
else
    fail "lint errors above"
fi

# Run one lane of scripts/test.sh and gate on its JUnit report, not the exit code — the same
# thing ci.yml does (scripts/junit_gate.py). PySide6 can raise a teardown SIGSEGV *after* a green
# session has finished and written its report, so a 139 is not on its own a failure; a missing or
# non-empty-failure report is.
run_lane() {
    local name="$1"; shift
    local log="/tmp/preflight-pytest-${name}.log"
    local report
    report=$(mktemp -t preflight-junit-XXXXXX.xml)
    scripts/test.sh "$@" --junitxml="$report" > "$log" 2>&1 || true
    local verdict
    verdict=$(python - "$report" <<'PY'
import sys, xml.etree.ElementTree as ET
from pathlib import Path
p = Path(sys.argv[1])
if not p.exists() or p.stat().st_size == 0:
    print("MISSING"); raise SystemExit
try:
    root = ET.parse(p).getroot()
except ET.ParseError:
    print("MISSING"); raise SystemExit
suites = root.findall("testsuite") or ([root] if root.tag == "testsuite" else [])
t = sum(int(s.get("tests", 0)) for s in suites)
f = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
k = sum(int(s.get("skipped", 0)) for s in suites)
print(f"OK {t} passed-ish, {f} failed, {k} skipped" if f == 0 else f"RED {f} of {t}")
PY
)
    rm -f "$report"
    case "$verdict" in
        OK*)      echo "   ${verdict#OK }" ;;
        MISSING)  tail -25 "$log"
                  fail "$name wrote no report — it crashed or hung before finishing (log: $log)" ;;
        *)        grep -E "^(FAILED|ERROR)" "$log" | head -20
                  fail "$name ${verdict#RED } — full log at $log" ;;
    esac
}

if (( QUICK == 0 )) && [[ "$SCOPE" == version-only ]]; then
    step "tests"
    echo "   skipped (version-only: nothing a test reads changed)"
elif (( QUICK == 0 )); then
    if [[ "$SCOPE" == docs-only ]]; then
        step "docs lane (scripts/test.sh narrows itself to the tests that read the documents)"
        run_lane "docs lane"
    else
        step "fast lane (scripts/test.sh, UI tests included)"
        run_lane "fast lane"
    fi

    # The gated lanes (tests/_lanes.py): the nanowell end-to-end and feature tests, and the remote /
    # HPC tests. Each asks its own gate, scripts/lane_needed.py <gate> — the same question CI's jobs
    # ask — and runs only the test files the change can reach, or the whole lane when the change
    # touches what every test receives, or nothing. The selection is computed once and handed to
    # scripts/test.sh as named files, so the import graph is built once per lane.
    gated_lane() {
        local name="$1" flag="$2" gate="$3"
        step "$name (scripts/test.sh $flag)"
        local files=() f status
        while IFS= read -r f; do [[ -n "$f" ]] && files+=("$f"); done \
            < <(python scripts/lane_needed.py "$gate" --files 2> >(sed 's/^/   /' >&2) \
                && echo "__status=0" || echo "__status=$?")
        local last=$(( ${#files[@]} - 1 ))
        status="${files[$last]#__status=}"
        unset "files[$last]"
        if [[ "$status" == "1" ]]; then
            echo "   skipped"
        elif [[ "$status" != "0" || "${files[*]:-}" == "ALL" || ${#files[@]} -eq 0 ]]; then
            ALLWELL_FULL_LANE=1 run_lane "$name" "$flag"
        else
            run_lane "$name" "$flag" "${files[@]}"
        fi
    }
    gated_lane "nanowell lane" --nanowell nanowell-e2e
    gated_lane "nanowell feature lane" --nanowell-features nanowell-features
    gated_lane "remote lane" --remote remote
else
    step "fast lane"
    echo "   skipped (--quick)"
fi

step "working tree"
# Untracked files are the risk: `git add -A` sweeps in whatever a hand-run job
# left lying around. Tracked modifications are just your work, so they are
# listed for a read-through rather than treated as a failure.
untracked=$(git ls-files --others --exclude-standard)
if [[ -n "$untracked" ]]; then
    echo "$untracked" | sed 's/^/   ?? /'
    fail "untracked file(s) above — commit them deliberately, or ignore them; \
do not let 'git add -A' decide"
else
    echo "   no untracked files"
fi

modified=$(git diff --name-only; git diff --cached --name-only)
if [[ -n "$modified" ]]; then
    printf '   staged/modified:\n'
    echo "$modified" | sort -u | sed 's/^/     /'
fi

printf '\n'
if (( failed == 0 )); then
    printf '\033[32mpreflight: all checks passed\033[0m\n'
    exit 0
fi
printf '\033[31mpreflight: %d check(s) failed\033[0m\n' "$failed"
exit 1
