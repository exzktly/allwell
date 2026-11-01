# Lane selection tracker

A remediation tracker (CLAUDE.md §6.1): run only the tests a change can reach, locally and in CI, with every lane
run in full on each push to `main`. Deleted by the pull request that closes its last row.

Measured before any code (61 merges to `main`, average worker-seconds per pull request, from
`scripts/lane_needed.py`'s rule applied per test file and per-file JUnit timings on a 4-CPU container):

| Lane | Today | Own gate | Own gate + file selection |
|---|---|---|---|
| nanowell end-to-end | 4,008 s (runs on 85%) | 2,467 s (52%) | 1,451 s |
| nanowell features | 1,247 s | 1,247 s | 560 s |
| remote | 263 s | 263 s | 120 s |

States: `open`, `done` (with the evidence), `declined` (with the reason).

## Phase 1 — gated lanes (one pull request)

| # | Item | State | Evidence |
|---|------|-------|----------|
| P1 | One gate per lane: `nanowell-e2e`, `nanowell-features`, `remote` (the `nanowell` gate starts both nanowell lanes today) | done | `GATES` has `nanowell-e2e`, `nanowell-features` and `remote`; CI and preflight ask each by name (`test_ci_runs_every_gated_lane_behind_its_gate_and_never_the_ui_tests`, `test_preflight_runs_every_gated_lane_behind_its_gate`) |
| P2 | `lane_needed.py` reports every reached roster file, not only the first, with "all" for what cannot be narrowed (harness, pytest config, an autouse fixture or hook, a pattern change) | done | `select` / `reach_all`; `test_every_reached_lane_test_is_reported_and_no_other` fails when propagation stops at the first hit; `test_an_autouse_fixture_or_hook_runs_the_whole_lane`, `test_a_changed_lane_trigger_runs_the_whole_lane` |
| P3 | `scripts/test.sh --nanowell / --nanowell-features / --remote` runs only the reached files; nothing reached skips the lane; `ALLWELL_FULL_LANE=1` runs it whole | done | `test_the_remote_lane_runs_only_the_test_a_change_reaches` (fails when test.sh drops the selection), `test_a_change_no_lane_test_reaches_runs_nothing`, `test_the_whole_lane_runs_when_asked_or_when_the_harness_changes`. A `set -e` defect (the status line lost when the script exits 1) was found by these tests and fixed |
| P4 | CI: each gated step asks its own gate and selects files on a pull request; a push to `main` and a `full-ci` label run every lane whole | done | gate steps set `ALLWELL_FULL_LANE` on a push or a `full-ci` label and `ALLWELL_SCOPE_BASE` on a pull request; the workflow parses as YAML |
| P5 | `scripts/preflight.sh` follows the same selection | done | `gated_lane` in preflight computes the selection once and hands the files to `scripts/test.sh`; driven with a stub `run_lane` in a scratch repository through all three outcomes (whole, skipped, one file) |
| P6 | Tests: per-file reach on synthetic trees, the real command selecting in a scratch repository, the escape hatches; mutations bite | done | 4 mutations, each turning a named test red (first-hit-only propagation, test.sh ignoring the selection, any `_lanes.py` change running the whole lane, autouse changes not running the whole lane) |
| P7 | Docs: CLAUDE.md §1/§2, ARCHITECTURE lanes section, `tests/_lanes.py` docstring, `ci.yml` comments | done | CLAUDE.md §1 and §2, ARCHITECTURE's lanes section, the `tests/_lanes.py` docstring and the `ci.yml` comments describe the per-lane gates and the selection |
| P8 | Measure after: replay selected-file cost per lane against the table above | done | 61 merges replayed through `select()` with each lane's own roster: e2e 4,008 → 1,572 s per merge (runs on 34, 8 whole), features 1,247 → 613 s (53, 8 whole), remote 263 → 144 s (40, 8 whole); 5,518 → 2,328 s in all. Historical `_lanes.py` files name the old gates, so the replay compares each lane's roster directly for them |

## Phase 2 — fast lane (second pull request)

| # | Item | State | Evidence |
|---|------|-------|----------|
| F1 | Measure first: per-file reach of fast-lane files over the 61 merges, and per-file timings | done | fast lane 1,306 s worker time (6 min 11 s wall, 4 workers, 12,436 tests). Replayed over 61 merges with the always-run set below: 896 s average (−31%), 8 merges whole; floor 587 s |
| F2 | Identify tests that must always run (whole-tree guards, anything reading files by computed path) and prove the set complete | open | |
| F3 | Fast-lane selection in `scripts/test.sh` and CI, with the docs lane folded in | open | |
| F4 | Tests, docs, measurement after | open | |
| G1 | Profile the always-run files (587 s, 45% of the fast lane): where the time goes, by phase and by shared cost | done | serial profile of the 170 always-run files: 538 s, 485 s of it in test bodies; `ast.walk` (a Python generator) re-walking the shared trees is the largest single cost in the guards profiled. The 60,744 `rmdir` calls seen once are pytest clearing earlier sessions' temp dirs, local only |
| G2 | Cut the shared costs G1 finds, without weakening any guard | open | |
