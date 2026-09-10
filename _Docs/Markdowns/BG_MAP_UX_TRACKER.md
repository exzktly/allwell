# Pair blanks editor — UX remediation

A UX review of the shipped editor, run against `main` at 7e63cc4 by driving the
documented workflow from a cold open rather than reading the code. The report
that prompted it: *"The pair blanks popup sucks. It doesn't work as expected
following the instructions."*

One row per finding. A row reaches `done` only when a test drives the real
control and has been watched failing without the fix.

Counts are read off the rows below, never typed.

| # | Blocks the workflow? | Finding | State |
|---|---|---|---|
| 1 | Totally | The manual's first step is impossible: the blank list is empty on open and the editor never reads the Background well field, so every drag is a silent no-op. `+ Add blank`, the only way to get a row, is not in the manual. | done |
| 2 | Totally | `+ Add blank` invents a reference from the end of the plate when the field names nothing free, so a sample well is held out and used to correct the other 95 — and the editor says the pairing will run. | done |
| 3 | Badly | The row's combo looks like the picker and is actually a rename; the real picker is a tooltip-only 18-px `●`. Picking from the combo replaces the user's blank with a sample well. | done |
| 4 | Badly | Dragging over another blank's block steals it silently, and there is no undo, no cancel, and no confirmation on `Clear pairing` or on a split that replaces a hand-painted map. | done |
| 5 | Moderately | The coverage line does double duty as a status line, and a split with too few blanks overwrites the uncovered count with a different message — losing the number exactly when it is largest. A refusal message also persists after the cause is fixed. | done |
| 6 | Moderately | `Split plate…` with fewer blanks than blocks always produces an unrunnable map, and pairs blanks to blocks by list order, so a row-H blank corrects row A. | done |
| 7 | Mildly | The blank list is 70 px tall — 2.5 of 8 rows — in a window that does not grow to fit it and stores no geometry. | done |
| 8 | Mildly | `_set_background_map` refreshes the form summary but not the open editor, so a restored map leaves the editor painting into a blank the row list does not name. | done |
| 9 | Mildly | A blank and the wells it corrects are drawn in one colour, so nothing on the plate says which well is the reference. | done |
| 10 | — | The tests do not drive the controls: seven mutations (dead pick button, dead `+ Add blank`, dead rename, emptied hint, deleted `setWellColors`/`setEnabledWells`, deleted `setWellRings`, black swatches) all leave 13 tests green. The fixture's `"H12"` is also `reversed(all_wells)[0]`, so seeded-from-field and invented-off-the-plate are indistinguishable in it. | done |

## Declined

None yet.
