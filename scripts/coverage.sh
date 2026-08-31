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
# Run order does not matter, and that is a property of the two scripts rather
# than of coverage: each lane writes under its own COVERAGE_FILE prefix
# (`.coverage.fast`, `.coverage.slow-<shard>-<batch>`), so neither can be
# skipped by the combine nor erase the other's files. It did matter, both ways,
# while the fast lane used the default name — see the note in scripts/test.sh.
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

# A bare `.coverage` sitting beside per-process files is data this run is about
# to destroy: `coverage combine` writes its result to that name and does not read
# it first. It can only come from a lane run before the lanes took their own
# COVERAGE_FILE prefixes, or from a hand-rolled `pytest --cov`. Say so rather
# than folding it in silently — the whole point of this script is that a union
# missing a lane names live code dead, and that is the shape it would take.
if (( ${#parts[@]} > 0 )) && [[ -f .coverage ]]; then
    echo "WARNING: a bare .coverage is present alongside ${#parts[@]} per-process file(s)." >&2
    echo "         combine writes to .coverage without reading it, so whatever that" >&2
    echo "         file holds is NOT in this report. Delete it and re-run the lane" >&2
    echo "         it came from, or accept a report that is missing it." >&2
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
        # `--sort=cover` is ascending, so the *least*-covered modules come
        # first. `tail` here printed the forty best-covered ones — thirty-nine
        # rows near 100% and the total — under a heading promising a summary,
        # which is the inverse of the question this measurement exists to ask.
        # `head` keeps the total by appending it separately.
        python -m coverage report --sort=cover | head -42
        echo "  … (least-covered first; run 'python -m coverage report' for all)"
        python -m coverage report --sort=cover | tail -2
        ;;
esac
