# Architecture-audit fixes — PIPELINE area tracker

Live remediation tracker (CLAUDE.md §6.1). One row per item; the row is updated in
the same commit as its fix, and this file is deleted by the final commit of the
branch. Counts are derived from the rows below, not typed.

| ID | Rule | Item | State | Evidence |
|----|------|------|-------|----------|
| P1 | §5 never swallow; §7 degraded output | `pipeline/run_report.py::_unseal` swallows a failed seal withdrawal, so a stale seal can vouch for partial output | open | |
| P2 | §5 / guard scan surface | `tests/test_no_silent_except.py` cannot see `pipeline/run_report.py`, `pipeline/fov_checkpoint.py`, nor narrow `except X: pass` | open | |
| P3 | §6 every PR bumps | `scripts/preflight.sh` passes a branch with no version bump | open | |
| P4 | §7 degraded output | `selection_controller._merge_imported` clears the groups and toasts success when every entry fails | open | |
| P5 | §7.5 #5 half-sweep | stale `bar_groups.json` references in `review/domain/sample_definitions.py`; test-only `review/domain/bar_groups.py` | open | |
| P6 | §6 running description; §15 UI thread | ARCHITECTURE changelog prose near the close-time teardown; close blocks with nothing on screen | open | |
| P7 | ARCH §3 rule 4 | `core/local_storage_view.py` is app-level UI in `core/` | open | |
| P8 | §15 ASCII digits | `review/domain/sample_variables._unit_suffix` uses `str.isdigit()` | open | |
| P9 | §15 UI thread | `review/data/cell_pool.sweep_stale_spills` runs an unbounded rmtree on the UI thread | open | |
