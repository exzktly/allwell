# Review remediation — the combined-diff gate

A live tracker, not a write-up. It exists because this body of work is larger
than one session can hold honestly: four expert reviewers were run over the
combined branch diff (125 files, ~17,000 insertions) and returned more findings
than anyone can report from memory without the failure CLAUDE.md §6.1 describes
— progress reported from the parts of the work that were memorable.

**The rules that make it worth having** (§6.1): a row moves to `done` only with
evidence, the counts below are read off the rows rather than typed, a row that
turns out not to be a defect is `declined` with the reason written at the site
as well as here, and this file is deleted by the pull request that closes its
last row.

Reviewers: **R1** contracts and §7; **R2** behaviour-as-claimed; **R3**
over-architecture and workstream B's gate; **R4** UX against F's written design.
Where two reviewers found the same thing the row records both ids.

## Status

| state | meaning |
|---|---|
| ☐ | open |
| ☑ | fixed, with the test that pins it watched failing first |
| ⊘ | declined, reason at the site and in the row |

## Rows

| # | id | Sev | Finding | State | Note |
|---|---|---|---|---|---|
| 1 | R1-1 / R4-F3 | High | The Group-by shortfall reaches no figure but the Response tab, though every tab pools through the same seam | ☑ | Drawn from `plot_style.apply_export_style_to_current` — the call every renderer already ends with (39 sites, with a convention that keeps it that way) — so it cannot miss a tab and a tab added later inherits it. Editing eight renderers instead would have been the half-swept shape §7.5 #5 is about. Figure-level text, tagged, and *removed* before each redraw so filling the gap clears it rather than leaving a sentence that outlived its condition. `tests/test_the_grouping_notice_reaches_the_figure.py`: six helper tests plus one that drives a real Bar redraw on a loaded app and reads the sentence off the figure. Four mutations watched failing. |
| 2 | R1-2 | High | `group_by` / `colour_by` / `split_by` are not persisted, so a saved plot restores drawing different numbers | ☑ | Added to the cross-tab block of `view_state.snapshot`/`restore`. `tests/test_view_state_band_variables_restore.py`; four mutations watched failing, including a restore that copies one key into all three. |
| 3 | R4-F1 | High | `repeat each N` counts wells, not rows/columns — F's own acceptance walkthrough does not produce the plate it describes | ☐ | |
| 4 | R4-F2 | High | Gap-closing in `fill` silently misaligns a block design when one well is absent | ☐ | |
| 5 | R4-F4 | High | `plot_replicate_sets` falls back silently when a grouping matches nothing, and the shortfall sentence then contradicts the figure | ☑ | `group_by_shortfall` now branches on whether the grouping actually applied. When `variable_replicate_sets` returns nothing, `plot_replicate_sets` keeps the user's own sets rather than emptying every plot — so those wells *are* drawn, and the old sentence's "are not plotted" contradicted the figure it was printed on. That case now reads "Group by Dose matched no wells — showing your own groups instead." Mutation watched failing. |
| 6 | R3-B1 | High | `scripts/coverage.sh` discards the fast lane in the flow its own header documents | ☑ | Reproduced first: the documented fast → slow → combine sequence printed `Combined 1 file` and a fast-lane-covered module read 0%. `scripts/test.sh` now writes under `.coverage.fast`, so `combine` — which globs `<data_file>.*` and never matches the bare name — cannot skip it. Both orders now print `Combined 2 files` with both modules covered. |
| 7 | R3-B2 | High | Running the slow lane first and the fast lane second deletes every slow-lane data file | ☑ | Same root cause, worse direction: pytest-cov's session-start `erase()` globs `<data_file>.*`, so a fast lane on the default name unlinked every `.coverage.slow-*`. Reproduced (the slow file was simply gone), and fixed by the same prefix. `coverage.sh` also warns when a bare `.coverage` sits beside per-process files, since combine writes to that name without reading it. |
| 8 | R3-B3 | High | `coverage.yml` prints the fast-lane-only zero list monthly under "Modules no test reaches", with a delete-it instruction attached | ☑ | `coverage.yml` no longer prints `--zero` at all. It runs one lane, and one lane's list is the eleven-modules-six-of-them-live measurement — published unattended on `main` monthly with `coverage.sh`'s own triage-or-delete instruction attached. It now prints the summary and a notice naming the two-step inventory instead. |
| 9 | R4-F5 | Med-High | `Mixed` and `no value` are 23 RGB apart — the file that forbids this is the file that does it | ☐ | |
| 10 | R4-F6 / R1-5 | Med-High | `Decorate ▾` is offered on six tabs and honoured by one, and by only one of the Bar tab's display modes | ☐ | |
| 11 | R1-3 | Med | Grey bar mode discards the Colour-by inks but still draws the Colour-by legend | ☐ | |
| 12 | R1-4 / R4-F9 | Med | Batch Export gets the decoration but neither the legend nor the split notice | ☐ | |
| 13 | R1-6 | Med | Response's "no error bar" caption names a cause that cannot fire, and misses the one that does | ☐ | |
| 14 | R1-7 / R4-F7 | Med | Two colour vocabularies for one variable — the plate and the plot disagree | ☐ | |
| 15 | R1-10 / R4-F8 | Med | The overlay glyph collides and the key it prints contains duplicates | ☐ | |
| 16 | R4-F10 | Med | The unit is on the plate and the x labels but not the Colour-by legend | ☐ | |
| 17 | R4-F11 | Med | Variable ids are minted from a fixed placeholder, so a band setting follows the *slot* across a dataset swap | ☐ | |
| 18 | R4-F12 | Med | With no variable defined the Fill row and the plate are shown, enabled and inert | ☐ | |
| 19 | R4-F13 | Med | Apply fill and Remove are destructive with no confirmation and no report | ☐ | |
| 20 | R3-B4 | Med | `coverage.yml`'s upload matches zero files and is green | ☑ | `include-hidden-files: true` added, and `--html` is actually run so `htmlcov/` exists. The step had matched zero files and stayed green on `if-no-files-found: warn` — the trap `slow-lane.yml` already documents at its own upload. |
| 21 | R3-B5 | Med | The inventory's refusal branch exits before printing the four lines that say what to do | ☑ | `set +e` before the `shards=$(…)` assignment. GitHub runs a step as `bash -e {0}`, which `set -uo pipefail` does not clear, so `ls` exiting 2 through `pipefail` killed the step at exit 2 — before the four lines saying what to do, with only `(none)` on screen. Simulated both ways: old exits 2 silently, new prints the guidance and exits 1. |
| 22 | R3-B6 | Med | The inventory refuses on "no data", not on the data that makes the answer valid (commit, completeness, retention) | ☑ | The refusal was keyed on `shards > 0`, which passes for a run at a different commit, a run that died with partial artifacts, and expired artifacts. A new step reads the slow run through the API and fails unless its `head_sha` equals this checkout, its conclusion is `success`, and all six shard artifacts are present and unexpired. |
| 23 | R3-B7 | Med | `coverage.yml`'s comment claims a data-file check the file does not contain | ☑ | The comment promised a data-file check the file did not contain; it now contains one. `|| true` swallows a lane that died mid-session exactly as it swallows the teardown crash, and the two are indistinguishable afterwards without this. |
| 24 | R3-B8 | Med | The coverage summary prints the 40 *best*-covered modules — the inverse of the question | ☑ | `--sort=cover` is ascending, so `tail -40` printed the forty *best*-covered modules under a heading promising a summary. `head -42` plus the total, labelled least-covered-first. |
| 25 | R3-B12 | Med | Nothing in `tests/` pins any of workstream B | ☑ | `tests/test_the_lanes_combine.py` — six tests. Two hold the scripts to the property (each lane under its own non-default prefix, neither prefix a prefix of the other, since `erase()` sweeps by prefix); two drive real `coverage` runs in both orders and assert the union; one pins the library behaviour the fix depends on. Three mutations watched failing. Its own first assertion was wrong in the way §7.8 warns about — `endswith("0%")` also matches `100%` — and now parses the number. |
| 26 | R3-A1 | Med | `fit_space` is dead and states a rule `fit_response` does not follow | ☐ | |
| 27 | R3-A2 | Med | `core/csv_export.py` has one caller, its stated rationale is contradicted in the same package, and its filter differs from the eight-site original | ☐ | |
| 28 | R3-A3 | Med | `domain/variable_series.py` has one importer and four functions with no user | ☐ | |
| 29 | R3-A4 | Med | `NOT_NUMERIC` is dead, and `omission_caption` pools the two reasons its own docstring forbids pooling | ☐ | |
| 30 | R1-9 | Low | Response's CSV writes a literal `nan` where every other export blanks it | ☐ | |
| 31 | R1-8 | Low | `ResponsePoint.label` never reaches the tick it was written for | ☐ | |
| 32 | R1-11 | Low | `RecentDataset.path`'s docstring contradicts what the code stores | ☐ | |
| 33 | R1-12 | Low | Group-by reports the wells it subtracts and never the ones it adds | ☐ | |
| 34 | R3-A5 | Low | A roster of two beside a third un-rostered copy of the same loop | ☐ | |
| 35 | R3-A6 | Low | `orphaned_wells` has no application caller | ☐ | |
| 36 | R3-B9 | Low | Inventory trigger hygiene: no branch filter, no concurrency group, re-trigger needs a changed value | ☑ | `branches-ignore: [main]` so merging this does not start a 40-minute inventory against a stale id, and a `concurrency` group so pushes to that path do not stack overlapping fast lanes. |
| 37 | R3-B10 | Low | The dispatch input is interpolated before it is validated | ☑ | The dispatch input arrives through `env:` and is read as `$SLOW_RUN_ID`, so the `case` validation runs on a value rather than after an expansion it cannot protect. |
| 38 | R3-B11 | Low | Stale `.coverage.slow-*` files join the next union | ☑ | `rm -f .coverage.slow-${SHARD_INDEX}-*` beside the report clear, so a `--batch 1` run followed by a `--batch 2` run cannot leave the tail of the first in the union. |
| 39 | R4-F14 | Low | `Also show` is disabled where its comment says hidden, with no rule stated | ☐ | |
| 40 | R4-F15 | Low | The Variables plate drags in `paint` mode where F specified rubber-band | ☐ | |
| 41 | R4-F16 | Low | `Mixed` is spelled two ways, in the file claiming they are identical | ☐ | |
| 42 | R4-F17 | Low | A comment says two functions were removed; one is 59 lines below, the other never existed but is claimed in row 33 | ☐ | |
| 43 | R4-F18 | Low | A failed autosave of the variables block is invisible | ☐ | |
| 44 | R4-F19 | Low | Row 34 files a behaviour change as a naming change | ☐ | |
| 45 | R4-F20 | Low | `swallowed_scope` mints a third name in the confusable pair §7.6 #4 is about | ☐ | |
| 46 | R4-F21 | Low | The value palette is not measured for colour-vision deficiency, and Colour-by is a colour-only encoding | ☐ | |
| 47 | R2-1 | High | The three band controls are connected only in `centre_view`, and cutting all three connections leaves both lanes green | ☐ | |
| 48 | R2-6 | Med | `test_a_settings_backend_that_will_not_write…` cannot fail, and the suite writes to the developer's real `~/.config` | ☐ | |
| 49 | R2-5 | Med | `control_gaps` and `undefined_spread` are written by code no test reaches — both captions survive their counter being deleted | ☐ | |
| 50 | R2-4 | Med | `_activate_response`'s redraw can be deleted and 50 response tests stay green | ☐ | |
| 51 | R2-8d | Low | `non_numeric`'s docstring says the caller draws them off-scale; no caller reads it | ☐ | |
| 52 | R2-8e | Low | Seven new guards have no `test_guards_bite.py` case, including one written for the wrong shape | ☐ | |
