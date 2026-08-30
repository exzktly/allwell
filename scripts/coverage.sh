#!/usr/bin/env bash
# Combine both lanes' coverage data and report on it.
#
# Coverage here answers one question: which application modules does NO test
# reach. It is deliberately not a percentage gate — a threshold over ~500 source
# files is a number to game, and this repo's risk is not thin coverage but absent
# coverage in specific places.
#
# Both lanes must be measured before this is worth reading. The fast lane alone
# reports every full-app-only module as dead, and acting on that list would
# delete live code:
#
#   ALLWELL_COVERAGE=1 scripts/test.sh
#   ALLWELL_COVERAGE=1 scripts/test_slow.sh      # minutes to hours; see §10.4
#   scripts/coverage.sh                          # combine + summarise
#   scripts/coverage.sh --zero                   # the modules no test reaches
#   scripts/coverage.sh --diff                   # lines THIS branch adds that no test runs
#   scripts/coverage.sh --html                   # browsable report in htmlcov/
#
# Run order does not matter: parallel mode writes one data file per process and
# `coverage combine` merges whatever is there.
set -euo pipefail

cd "$(dirname "$0")/.."

MODE="summary"
case "${1:-}" in
    --zero) MODE="zero" ;;
    --html) MODE="html" ;;
    --diff) MODE="diff" ;;
    "") ;;
    *) echo "usage: $0 [--zero|--diff|--html]" >&2; exit 2 ;;
esac

shopt -s nullglob
parts=( .coverage.* )
shopt -u nullglob
if (( ${#parts[@]} == 0 )) && [[ ! -f .coverage ]]; then
    echo "No coverage data. Run a lane with ALLWELL_COVERAGE=1 first:" >&2
    echo "  ALLWELL_COVERAGE=1 scripts/test.sh" >&2
    exit 1
fi

# `combine` consumes the per-process files into .coverage. It refuses to run
# twice over nothing, so only call it when there is something to consume.
if (( ${#parts[@]} > 0 )); then
    python -m coverage combine
fi

case "$MODE" in
    html)
        python -m coverage html
        echo "→ htmlcov/index.html"
        ;;
    zero)
        # The inventory: application modules with no executed lines at all. This
        # is the finding, not the percentage — each entry is either a test that
        # should exist, dead code to delete, or something genuinely unreachable
        # that deserves a comment saying so.
        python - <<'PY'
import json, subprocess, sys
raw = subprocess.run([sys.executable, "-m", "coverage", "json", "-o", "-", "-q"],
                     capture_output=True, text=True, check=True).stdout
data = json.loads(raw)
zero = sorted(name for name, f in data["files"].items()
              if f["summary"]["covered_lines"] == 0
              and f["summary"]["num_statements"] > 0)
if not zero:
    print("Every measured module has at least one line executed.")
else:
    print(f"{len(zero)} module(s) with no executed lines:\n")
    for name in zero:
        n = data["files"][name]["summary"]["num_statements"]
        print(f"  {n:5d} stmt  {name}")
    print("\nTriage each into: a test that should exist, dead code, or "
          "genuinely unreachable (and say which, in a comment).")
PY
        ;;
    diff)
        # The question a reviewer actually has: which lines does this branch add
        # that no test executes. Advisory — it prints and does not fail, because
        # the fast lane alone cannot see a line covered only by a full-app test
        # and a check that cries wolf gets muted.
        python -m coverage xml -q -o coverage.xml
        python -m diff_cover.diff_cover_tool coverage.xml \
            --compare-branch=origin/main --fail-under=0
        ;;
    summary)
        python -m coverage report --sort=cover | tail -40
        ;;
esac
