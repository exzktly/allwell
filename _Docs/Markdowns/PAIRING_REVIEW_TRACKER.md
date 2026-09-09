# Empty-well pairing — review remediation

Adversarial review of the `claude/empty-well-pairing` branch (PR #909), run
against `3efc2fe`. One row per finding. A row reaches `done` only when a test
pins it and that test has been watched failing without the fix.

Counts are read off the rows below, never typed from memory.

| # | Severity | Finding | State | Where |
|---|---|---|---|---|
| 1 | High | A blank well is processed completely uncorrected on a paired run: `group_of(blank)` falls through to the pooled group, which does not exist when paired, so the blank's own frames pass through raw while its CSV looks like every other well's. | done | `pipeline/empty_well.py` `BlankGroups.__init__` / `group_of` |
| 2 | High | `--flatfield basic` plus a map installs grouped models and drops the flat-field for every unmapped well, with no refusal: `install_grouped_models` branches on `paired` while the coverage gate is gated on `in_force`. Also builds the identical plate-wide `basic` model once per blank. | done | `pipeline/empty_well.py` `prepare_models` / `install_grouped_models` |
| 3 | Medium-high | The editor reads "Every well is claimed — this pairing will run" at the moment every blank has an empty well list, i.e. when the map has silently reverted to pooled. One click of "+ Add blank" reaches it. | todo | `analyze/bg_map.py` `_bg_map_refresh_uncovered` |
| 4 | Medium | The refusal message names flags that do not exist — `--Flatfield empty_well`, `--Background empty_well` — because `what` acquired a second job as the flag name while carrying a Title-Case log label. | done | `pipeline/empty_well.py` `_require_empty_well_models` |
| 5 | Medium | Two seams into the app are unguarded: deleting the `refuse_uncovered_wells` call from `main()` leaves 80 tests green, and dropping the flag from `_emit_correction_flags` leaves 75 green. No test runs `main()` with a map at all, which is how rows 1 and 2 got through. | done | `tests/test_empty_well_pairing.py` |
| 6 | Medium | A `--background_well` blank the map omits becomes a sixth, disagreeing answer to "which wells are blanks": `BlankGroups` treats it as a sample needing coverage while `blank_wells_for_run` (remote staging, both incremental readers, the GUI validator) treats it as a blank. | done | `pipeline/empty_well.py` `BlankGroups.__init__` |
| 7 | Low-medium | A hand-edited sidecar map raises `ValueError` into an unguarded Qt slot in the Review image tab; the other two readers of the same parse are guarded. | todo | `well_viewer/features/image_browser/review_image/collect.py` |
| 8 | Low | Six statements written in this branch contradict code in the same commit: the refusal is not "before an image is read", the Review cache-key docstring, the README's `H1`/`H12` example and its reuse-scope claim, the Background-well tooltip, and a mis-indented dict entry. | todo | several |

## Declined

None yet.
