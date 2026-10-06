# Fast-lane performance

Run the standard lane with `bash scripts/test.sh`. Use
`bash scripts/test.sh --durations=40 --junitxml=/tmp/allwell-fast.xml` when
investigating runtime. JUnit testcase times include setup, call, and teardown;
use `--durations=0` to distinguish phases. Keep coverage off for timing the
normal lane, and do not overlap full-suite runs on the same machine.

## Reusing repository analysis

`tests/_repo_scan.py` caches parsed ASTs by complete source text and filename.
The deletion guard keys its writer closure and pairing results on the complete
tuple of source paths and parsed trees. The silent-exception guard caches each
module's handler sites by tree identity and relative filename. Every invocation
still discovers and reads sources. Edits, additions, and removals therefore
invalidate the applicable analysis, including same-size rewrites with preserved
timestamps. Cached trees and analysis results must remain immutable.

The source-string entry points used by synthetic violation tests still parse
their supplied text. Repository scans reuse the shared parser instead. The
deletion guard also computes each function's call set once per pairing scan;
the context guard skips functions without real-app fixtures before walking
their bodies; the patch-target guard discovers call-time imports in one
scope-aware traversal rather than walking every nested function repeatedly.

`tests/test_guard_scan_caching.py` checks reuse, invalidation, file membership,
innermost-function attribution, nested real-app calls, and the distinction
between module/class imports and imports inside functions. Existing synthetic
violation tests in `tests/test_guards_bite.py` remain required.

## Measurements and parallelism

On the same cloud machine, the pre-change full lane took 535.51 seconds and
the optimized serial lane took 472.60 seconds. These are single runs, not a
controlled estimate of the whole-suite speedup. The five modified guard files
accounted for 44.85 seconds before and 21.97 seconds after. The optimized lane
ran 10,653 passing tests, with nine skips and the same four pre-existing failures
(two scratch/transit ancestry assertions and two spin-box margin assertions).
The four added regression tests all passed. The targeted guard and synthetic
violation run had 576 passes and one skip.

A diagnostic `pytest-xdist` 3.8.0 run with two workers and `--dist=loadfile`
took 263.01 seconds. Both full reports contain the same executed test identities,
but their outcomes differed: the parallel run additionally failed the
minimal-shell cleanup check with a surviving `QFrame`, while one serial
scratch failure passed. A smaller parallel run including that cleanup test
had 39 passes and one skip. This makes the full parallel result unsuitable as
a replacement for the serial gate until the ordering/state issue is understood.
No parallel dependency or runner mode is enabled by this change. File-level
distribution alone does not guarantee isolation between files on a worker.

The parallel controller and workers used about 2.6 GiB in one memory sample;
that is not a peak measurement. Each worker duplicates imports and AST caches.
Future benchmarks should compare executed identities and failure identities,
not just counts or wall-clock time, before enabling parallelism in CI.

The existing Qt teardown issue can produce exit 139 after pytest writes its
complete report. Follow the CI report checks; a failed or incomplete report
must never be treated as a passing gate.
