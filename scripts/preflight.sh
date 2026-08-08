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
#   scripts/preflight.sh          # lint + fast lane + working-tree check
#   scripts/preflight.sh --quick  # skip the fast lane (lint + tree only, ~2s)
#
# Exit status: 0 only when every check passes.
set -uo pipefail

cd "$(dirname "$0")/.."

QUICK=0
[[ "${1:-}" == "--quick" ]] && QUICK=1

failed=0
step() { printf '\n\033[1m── %s\033[0m\n' "$1"; }
fail() { printf '\033[31m   FAILED: %s\033[0m\n' "$1" >&2; failed=$(( failed + 1 )); }

step "ruff"
if ruff check .; then
    echo "   clean"
else
    fail "lint errors above"
fi

if (( QUICK == 0 )); then
    step "fast lane (scripts/test.sh)"
    # Gate on the JUnit report, not the exit code — the same thing ci.yml does.
    # PySide6 can raise a teardown SIGSEGV *after* a green session has finished
    # and written its report, so a 139 is not on its own a failure; a missing or
    # non-empty-failure report is.
    report=$(mktemp -t preflight-junit-XXXXXX.xml)
    scripts/test.sh --junitxml="$report" > /tmp/preflight-pytest.log 2>&1 || true
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
        MISSING)  tail -25 /tmp/preflight-pytest.log
                  fail "fast lane wrote no report — it crashed or hung before \
finishing (log: /tmp/preflight-pytest.log)" ;;
        *)        grep -E "^(FAILED|ERROR)" /tmp/preflight-pytest.log | head -20
                  fail "fast lane ${verdict#RED } — full log at /tmp/preflight-pytest.log" ;;
    esac
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
# CLAUDE.md §6: one bump per pull request, not one per commit. The failure this
# catches is not forgetting the rule — it is holding a mental model of two
# pull requests while the branch carries one, which is what happens when a
# second body of planned work stacks onto a branch whose PR is still open.
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
    verdict=$(python - "$base_version" "$head_version" <<'PY_VER'
import sys

def parse(v):
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return None

base, head = parse(sys.argv[1]), parse(sys.argv[2])
if base is None or head is None or len(base) != 3 or len(head) != 3:
    print("UNREADABLE"); raise SystemExit
if head == base:
    print("SAME"); raise SystemExit
ok = (
    head == (base[0], base[1], base[2] + 1)
    or head == (base[0], base[1] + 1, 0)
    or head == (base[0] + 1, 0, 0)
)
print("ONE" if ok else "MANY")
PY_VER
)
    case "$verdict" in
        SAME)  echo "   unchanged from $base_ref ($base_version)" ;;
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
