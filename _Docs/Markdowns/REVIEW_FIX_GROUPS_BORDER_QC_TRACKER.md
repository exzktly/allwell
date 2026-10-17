# Review fixes — groups import, border gate, QC bounds (tracker)

Live state for the review findings this branch owns, from the adversarial
review of PRs #995, #996, #999, #1003 and #1004. One row per finding. A row
goes to `done` only with the test that pins it, seen failing on the unfixed
code. This file is deleted by the commit that closes its last row.

| ID | Defect | State | Test |
|---|---|---|---|
| M1 | "Add to current groups" strips overlapping wells out of existing groups, contradicting "cannot lose anything" | open | |
| M2 | Replace drops each group's `hidden` flag, its id and the file's `current_id` | open | |
| L6 | Import runs one `rebuild_all` per imported group | open | |
| L7 | Stale comment/docstring on Home's Import (`home/controller.py`) | open | |
| L8 | `add_value` accepts numerically equal number values ("1", "1.0", "1uM" / "1 µM") | open | |
| WT | `tests/test_groups_import.py` lacks overlap fixture, hidden/id/current_id and redraw-count assertions | open | |
| M3 | Border count counts unchecked (NaN) rows and omits wells without the column; reuse keys ignore the column | open | |
| M4 | Plate-wide border count reads every well in full; border layer missing from `test_narrow_rows` LAYERS | open | |
| L11 | Border panel / README advise "re-run" where only Force re-run records the column | open | |
| L12 | Merging a nanowell and an ordinary plate turns border exclusion off for both | open | |
| M5 | Typed QC bound not applied when the user clicks a control that takes no focus | open | |
| M6 | Typed QC bound applied after the close flush, so never saved | open | |
| L15 | `_walk_capped` follows symlinked directories (rglob on 3.11 does not) | open | |
