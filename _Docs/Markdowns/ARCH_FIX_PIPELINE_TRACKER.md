# Architecture-audit fixes — PIPELINE area tracker

Live remediation tracker (CLAUDE.md §6.1). One row per item; the row is updated in
the same commit as its fix, and this file is deleted by the final commit of the
branch. Counts are derived from the rows below, not typed.

| ID | Rule | Item | State | Evidence |
|----|------|------|-------|----------|
| P1 | §5 never swallow; §7 degraded output | `pipeline/run_report.py::_unseal` swallows a failed seal withdrawal, so a stale seal can vouch for partial output | done | run_report.withdraw_seal logs and raises SealNotWithdrawnError; all three well paths withdraw before their first write. tests/test_a_part_way_well_is_not_sealed.py::test_a_seal_that_cannot_be_withdrawn_stops_the_rewrite drives main; red on unfixed code (CSV held ['F001'] under the stale seal) and red with the zip-path withdraw removed; ::test_every_well_path_withdraws_the_seal_before_its_first_write covers folder + nanowell |
| P2 | §5 / guard scan surface | `tests/test_no_silent_except.py` cannot see `pipeline/run_report.py`, `pipeline/fov_checkpoint.py`, nor narrow `except X: pass` | done | PIPELINE_DIRS derives pipeline/; new test_no_silent_narrow_except_in_pipeline + liveness; prove_guard on origin/main: PROVEN, 16 sites (15 converted to aw_safe.swallowed + _unseal fixed by P1); 2 allowlisted with reasons; test_guards_bite narrow_silent cases |
| P3 | §6 every PR bumps | `scripts/preflight.sh` passes a branch with no version bump | done | scripts/version_delta.py decides the verdict; preflight fails on SAME as on MANY; stacked-branch caveat kept in the script, the module docstring and ARCHITECTURE. tests/test_version_delta.py runs the real preflight --quick in a throwaway repo: red with the SAME branch reverted to echo ('preflight: all checks passed'). CLAUDE.md line ~566 still describes only the >1 half (not edited by an agent; flagged) |
| P4 | §7 degraded output | `selection_controller._merge_imported` clears the groups and toasts success when every entry fails | open | |
| P5 | §7.5 #5 half-sweep | stale `bar_groups.json` references in `review/domain/sample_definitions.py`; test-only `review/domain/bar_groups.py` | open | |
| P6 | §6 running description; §15 UI thread | ARCHITECTURE changelog prose near the close-time teardown; close blocks with nothing on screen | open | |
| P7 | ARCH §3 rule 4 | `core/local_storage_view.py` is app-level UI in `core/` | open | |
| P8 | §15 ASCII digits | `review/domain/sample_variables._unit_suffix` uses `str.isdigit()` | open | |
| P9 | §15 UI thread | `review/data/cell_pool.sweep_stale_spills` runs an unbounded rmtree on the UI thread | open | |
