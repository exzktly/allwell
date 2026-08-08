#!/usr/bin/env bash
# Prove that a guard test actually catches what it claims to catch.
#
# A guard written alongside the fix it protects is a hypothesis until it has
# been seen failing on code that violates the rule. CLAUDE.md §7.3 and §7.4 both
# say so, and both were followed by a session in which two of eight "proofs"
# measured nothing: one guard died on a collection-time ModuleNotFoundError, the
# other on a NameError raised while formatting its own failure message. In a
# terminal, each looks exactly like "it failed on the base branch".
#
# So this does not report whether the run failed. It reports *how*:
#
#   PROVEN                          AssertionError, N offenders listed
#   NOT PROVEN — the guard errored  any other exception (import, collection, …)
#   NOT PROVEN — passed on the base the rule already held; this is a hypothesis
#
# Usage:
#   scripts/prove_guard.sh tests/test_divider_naming.py
#   scripts/prove_guard.sh tests/test_qt_free_layers.py c16df04
#   scripts/prove_guard.sh tests/test_foo.py origin/main -- -k something
#
# The base ref defaults to origin/main. Exit status is 0 only for PROVEN.
set -uo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$PWD"

TEST_FILE="${1:-}"
if [[ -z "$TEST_FILE" || ! -f "$TEST_FILE" ]]; then
    echo "usage: scripts/prove_guard.sh <tests/test_*.py> [<base-ref>] [-- <pytest args>]" >&2
    exit 2
fi
shift

BASE_REF="origin/main"
if [[ $# -gt 0 && "$1" != "--" ]]; then
    BASE_REF="$1"; shift
fi
[[ "${1:-}" == "--" ]] && shift
PYTEST_EXTRA=("$@")

# Scratch, never the repo: CLAUDE.md §2 records 21 JUnit XMLs reaching main
# because a hand-run job wrote inside the working tree.
SCRATCH="${TMPDIR:-/tmp}/allwell-prove-guard.$$"
cleanup() { rm -rf "$SCRATCH"; git -C "$REPO_ROOT" worktree prune >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

printf '\033[1m── proving %s against %s\033[0m\n' "$TEST_FILE" "$BASE_REF"

if ! git rev-parse --verify --quiet "$BASE_REF^{commit}" >/dev/null; then
    echo "   base ref '$BASE_REF' does not resolve — fetch it first" >&2
    exit 2
fi

if ! git worktree add -q --detach "$SCRATCH" "$BASE_REF" 2>"$SCRATCH.err"; then
    echo "   could not create a worktree at $BASE_REF:" >&2
    cat "$SCRATCH.err" >&2; rm -f "$SCRATCH.err"
    exit 2
fi
rm -f "$SCRATCH.err"

# The guard, plus every tests/ support module it imports. Forgetting these is
# how a proof turns into a collection error: the base branch has the old code
# but not the new helper the guard was written against.
mkdir -p "$SCRATCH/tests"
cp "$TEST_FILE" "$SCRATCH/tests/"
SUPPORT=$(python - "$TEST_FILE" <<'PY'
import ast, sys
from pathlib import Path

tree = ast.parse(Path(sys.argv[1]).read_text())
names = set()
for node in ast.walk(tree):
    mods = []
    if isinstance(node, ast.ImportFrom) and node.module:
        mods.append(node.module)
    elif isinstance(node, ast.Import):
        mods += [a.name for a in node.names]
    for mod in mods:
        parts = mod.split(".")
        if parts[0] == "tests" and len(parts) > 1:
            names.add(parts[1])
print(" ".join(sorted(names)))
PY
)
for mod in $SUPPORT; do
    if [[ -f "tests/$mod.py" ]]; then
        cp "tests/$mod.py" "$SCRATCH/tests/"
        echo "   carried tests/$mod.py across (the guard imports it)"
    fi
done

LOG="$SCRATCH.log"
( cd "$SCRATCH" && python -m pytest "tests/$(basename "$TEST_FILE")" \
    -q --timeout=120 --timeout-method=signal "${PYTEST_EXTRA[@]}" ) >"$LOG" 2>&1
STATUS=$?

# What kind of failure was it? An assertion is the guard speaking; anything else
# is the guard falling over before it measured anything. The classifier lives in
# its own module so it can be tested — it decides whether a proof counts, and it
# has already been wrong once (see scripts/prove_guard_verdict.py).
VERDICT=$(python "$REPO_ROOT/scripts/prove_guard_verdict.py" "$LOG")

KIND=$(head -1 <<<"$VERDICT")
case "$KIND" in
    PASSED)
        printf '\033[31m   NOT PROVEN — the guard passed on %s\033[0m\n' "$BASE_REF"
        echo "   The rule already held before the fix, so this test is a"
        echo "   hypothesis rather than a guard. Point it at a ref that violates"
        echo "   the rule, or inject a violation and re-run."
        exit 1 ;;
    ERRORED*)
        printf '\033[31m   NOT PROVEN — the guard errored (%s)\033[0m\n' "${KIND#ERRORED }"
        echo "   It fell over before measuring anything, so a red run here is"
        echo "   evidence of nothing. Usually a missing support module or a"
        echo "   symbol the base branch does not have; carry a stub across."
        grep -E "^E\s+" "$LOG" | head -5 | sed 's/^/   /'
        exit 1 ;;
    UNCLEAR)
        printf '\033[31m   NOT PROVEN — could not tell what failed\033[0m\n'
        tail -20 "$LOG" | sed 's/^/   /'
        exit 1 ;;
    ASSERTED*)
        N="${KIND#ASSERTED }"
        if [[ "$N" == "0" ]]; then
            printf '\033[33m   PROVEN (assertion fired; offenders not countable)\033[0m\n'
            echo "   The guard asserted, but printed no 'path.py:line' offenders,"
            echo "   so this reports the assertion only — check the message says"
            echo "   what you expect:"
            grep -E "^E\s+" "$LOG" | head -5 | sed 's/^/   /'
            exit 0
        fi
        printf '\033[32m   PROVEN — AssertionError naming %s site(s)\033[0m\n' "$N"
        tail -n +2 <<<"$VERDICT" | sed 's/^/     /'
        [[ "$N" -gt 10 ]] && echo "     … and $((N - 10)) more"
        exit 0 ;;
    *)
        printf '\033[31m   NOT PROVEN — unrecognised pytest output (exit %s)\033[0m\n' "$STATUS"
        tail -20 "$LOG" | sed 's/^/   /'
        exit 1 ;;
esac
