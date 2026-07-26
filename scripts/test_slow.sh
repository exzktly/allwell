#!/usr/bin/env bash
# Run the `slow` lane — the full-app Qt tests the fast lane skips.
#
# Why this needs a script rather than a pytest invocation: every test using the
# `viewer_app` fixture (and its `loaded_app` / `image_table_app` /
# `loaded_app_shape` / `loaded_app_multifov` dependents) builds a real
# WellViewerApp whose Qt<->Python reference web is never reclaimed in-process.
# RSS climbs ~40 MB per test, monotonically, so `pytest -m slow` across all 82
# files hangs or gets OOM-killed. See ARCHITECTURE.md §10.4.
#
# So the lane is run as many short pytest processes, a couple of files each, and
# the per-batch JUnit reports are aggregated at the end. Memory is reclaimed by
# process exit, which is the only thing that reclaims it.
#
#   scripts/test_slow.sh                    # every slow file, 2 per process
#   scripts/test_slow.sh --batch 1          # one file per process (safest)
#   scripts/test_slow.sh --shard 3/6        # shard 3 of 6, for a CI matrix
#   scripts/test_slow.sh -k threshold       # extra args go to pytest
#
# Exit status: 0 only when every batch produced a report with no failures and
# no errors. A batch that crashes before writing its report fails the run — that
# is the OOM / C-level-hang signal, and it must not read as success.
set -uo pipefail

cd "$(dirname "$0")/.."

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
export MPLBACKEND="${MPLBACKEND:-Agg}"

BATCH=2
SHARD_INDEX=1
SHARD_TOTAL=1
# Wall-clock ceiling per batch. pytest's own --timeout uses a signal, which a
# C-level hang inside Qt will not honour; this is the backstop that does.
#
# It has to sit well above what a legitimately slow batch costs, because a batch
# killed here writes no report and so reads as a failure. Every test in this lane
# builds a real WellViewerApp, which dominates its runtime: the heaviest observed
# pair (test_multivariate_tab + test_playground, 29 tests) takes ~12 minutes with
# the machine to itself and passes. Per-test hangs are already covered by
# --timeout=300 below, so this only has to catch the signal-proof C-level case —
# which means erring high costs nothing and erring low fails green batches.
BATCH_TIMEOUT="${SLOW_BATCH_TIMEOUT:-1800}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --batch) BATCH="$2"; shift 2 ;;
        --shard)
            SHARD_INDEX="${2%%/*}"
            SHARD_TOTAL="${2##*/}"
            shift 2 ;;
        *) break ;;
    esac
done

REPORT_DIR="${SLOW_REPORT_DIR:-.pytest-slow-reports}"
rm -rf "$REPORT_DIR"
mkdir -p "$REPORT_DIR"

# Discover the lane from pytest's own marker rather than by grepping for fixture
# names: conftest.py applies `slow` by fixture closure, so the marker is the only
# thing that knows the real membership.
echo "Collecting the slow lane..."
mapfile -t ALL_FILES < <(
    python -m pytest -m slow --collect-only -q 2>/dev/null \
        | grep -oP '^\s*<Module \K[^>]+' \
        | sort -u
)

if [[ ${#ALL_FILES[@]} -eq 0 ]]; then
    echo "ERROR: collected no slow test files — collection itself failed." >&2
    exit 1
fi

# Deal out files round-robin so each shard gets a mix of cheap and expensive
# files rather than one shard inheriting a contiguous block of the slowest.
FILES=()
for i in "${!ALL_FILES[@]}"; do
    if (( i % SHARD_TOTAL == SHARD_INDEX - 1 )); then
        FILES+=("tests/${ALL_FILES[$i]}")
    fi
done

echo "Slow lane: ${#ALL_FILES[@]} files total, ${#FILES[@]} in shard ${SHARD_INDEX}/${SHARD_TOTAL}, ${BATCH} per process."
echo

failed_batches=0
batch_no=0
for (( i = 0; i < ${#FILES[@]}; i += BATCH )); do
    batch_no=$(( batch_no + 1 ))
    chunk=( "${FILES[@]:i:BATCH}" )
    report="${REPORT_DIR}/batch-${SHARD_INDEX}-${batch_no}.xml"

    echo "── batch ${batch_no}: ${chunk[*]}"
    timeout --kill-after=30 "$BATCH_TIMEOUT" \
        python -m pytest -m slow -q \
        --timeout=300 --timeout-method=signal \
        --junitxml="$report" \
        "${chunk[@]}" "$@"
    status=$?

    # A PySide6 teardown SIGSEGV (139) fires after the session has finished and
    # the report is written, so it is not a failure on its own — the report is
    # what decides. A missing report is always a failure.
    if [[ ! -f "$report" ]]; then
        echo "   FAILED: no report written (exit ${status}) — crashed or timed out." >&2
        failed_batches=$(( failed_batches + 1 ))
    fi
done

echo
python - "$REPORT_DIR" "$failed_batches" <<'PY'
import sys, xml.etree.ElementTree as ET
from pathlib import Path

report_dir, crashed = Path(sys.argv[1]), int(sys.argv[2])
tests = failures = errors = skipped = 0
for report in sorted(report_dir.glob("*.xml")):
    root = ET.parse(report).getroot()
    suites = root.findall("testsuite") or ([root] if root.tag == "testsuite" else [])
    for s in suites:
        tests += int(s.get("tests", 0))
        failures += int(s.get("failures", 0))
        errors += int(s.get("errors", 0))
        skipped += int(s.get("skipped", 0))

print(f"slow lane: tests={tests} failures={failures} errors={errors} "
      f"skipped={skipped} crashed_batches={crashed}")
if tests == 0:
    print("No tests ran — treating as failure.")
sys.exit(1 if (crashed or failures or errors or tests == 0) else 0)
PY
