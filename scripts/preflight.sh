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
# runs everything above. The working-tree and version-bump checks always run.
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
    # HPC tests. Each runs only when scripts/lane_needed.py says this branch can reach it — the same
    # question CI's jobs ask — because together they cost several minutes.
    step "nanowell lanes (scripts/test.sh --nanowell, --nanowell-features)"
    if python scripts/lane_needed.py nanowell | sed 's/^/   /'; then
        run_lane "nanowell lane" --nanowell
        run_lane "nanowell feature lane" --nanowell-features
    else
        echo "   skipped"
    fi

    step "remote lane (scripts/test.sh --remote)"
    if python scripts/lane_needed.py remote | sed 's/^/   /'; then
        run_lane "remote lane" --remote
    else
        echo "   skipped"
    fi
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

step "version bump"
# CLAUDE.md §6: exactly one bump per pull request — not one per commit, and
# not none. More than one comes from holding a mental model of two pull
# requests while the branch carries one (a second body of work stacked onto a
# branch whose PR is still open). None comes from a bump that collided with a
# PR merged first, or from forgetting it. The verdict is decided by
# scripts/version_delta.py, which tests/test_version_delta.py covers.
#
# A stacked branch measured against origin/main reports more than one
# increment when the branch below carries its own bump. That is a property of
# this comparison, not of the branch: do not "correct" the stacked number to
# satisfy it (CLAUDE.md §6).
base_ref="origin/main"
if ! git rev-parse --verify --quiet "$base_ref^{commit}" >/dev/null; then
    echo "   skipped ($base_ref not fetched)"
else
    bump_commits=$(git rev-list --count "$base_ref..HEAD" -- version.py 2>/dev/null || echo 0)
    base_version=$(git show "$base_ref:version.py" 2>/dev/null \
                   | sed -n 's/^__version__ = "\(.*\)"/\1/p')
    head_version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' version.py)
    # The *net* delta is the rule, not how many commits touched the file: a
    # branch that bumps, over-bumps and then corrects is fine, and that is
    # exactly the history this check was written from.
    verdict=$(python scripts/version_delta.py "$base_version" "$head_version")
    case "$verdict" in
        SAME)  fail "version.py is unchanged from $base_ref ($base_version). Every pull \
request bumps the version once (CLAUDE.md §6) — patch for fixes, docs and tooling. \
When another branch's merge took the number this branch bumped to, bump again" ;;
        ONE)   echo "   $base_version → $head_version, one increment (in $bump_commits commit(s))" ;;
        UNREADABLE) echo "   could not read a version on one side; skipped" ;;
        *)     git log --oneline "$base_ref..HEAD" -- version.py | sed 's/^/     /'
               fail "version.py moved $base_version → $head_version, which is more \
than one increment. A branch is one pull request and takes one bump — edit the \
number the branch already carries instead of adding another (CLAUDE.md §6)" ;;
    esac
fi

printf '\n'
if (( failed == 0 )); then
    printf '\033[32mpreflight: all checks passed\033[0m\n'
    exit 0
fi
printf '\033[31mpreflight: %d check(s) failed\033[0m\n' "$failed"
exit 1
