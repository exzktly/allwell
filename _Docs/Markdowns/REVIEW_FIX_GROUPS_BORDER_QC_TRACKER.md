# Review fixes — groups import, border gate, QC bounds (tracker)

Live state for the review findings this branch owns, from the adversarial
review of PRs #995, #996, #999, #1003 and #1004. One row per finding. A row
goes to `done` only with the test that pins it, seen failing on the unfixed
code. This file is deleted by the commit that closes its last row.

| ID | Defect | State | Test |
|---|---|---|---|
| M1 | "Add to current groups" strips overlapping wells out of existing groups, contradicting "cannot lose anything" | done — second prompt (Skip / Move / Cancel) through the injectable `ask_import_overlap`; red on main | `test_groups_import.py`: `test_add_over_grouped_wells_asks_naming_the_wells_and_their_groups`, `test_skip_leaves_every_existing_group_whole`, `test_move_hands_the_wells_to_the_imported_groups`, `test_cancelling_the_overlap_question_changes_nothing` |
| M2 | Replace drops each group's `hidden` flag, its id and the file's `current_id` | done — the loop builds through `make_selection(hidden=, sel_id=)` and restores `current_id`; red on main (fresh random ids, flag dropped) | `test_replace_keeps_each_group_s_hidden_flag_and_id_and_the_current_group`, `test_add_keeps_the_file_ids_unless_they_collide` |
| L6 | Import runs one `rebuild_all` per imported group | done — one `rebuild_all` per import; red on main (one per group) | `test_an_import_redraws_once_not_once_per_group` |
| L7 | Stale comment/docstring on Home's Import (`home/controller.py`) | done — comment and docstring say Import brings in groups only | comment only |
| L8 | `add_value` accepts numerically equal number values ("1", "1.0", "1uM" / "1 µM") | open | |
| WT | `tests/test_groups_import.py` lacks overlap fixture, hidden/id/current_id and redraw-count assertions | done — `_seed_overlapping`, the `HIDDEN` payload with a middle `current_id`, redraw count | the tests above |
| M3 | Border count counts unchecked (NaN) rows and omits wells without the column; reuse keys ignore the column | open | |
| M4 | Plate-wide border count reads every well in full; border layer missing from `test_narrow_rows` LAYERS | open | |
| L11 | Border panel / README advise "re-run" where only Force re-run records the column | open | |
| L12 | Merging a nanowell and an ordinary plate turns border exclusion off for both | open | |
| M5 | Typed QC bound not applied when the user clicks a control that takes no focus | open | |
| M6 | Typed QC bound applied after the close flush, so never saved | open | |
| L15 | `_walk_capped` follows symlinked directories (rglob on 3.11 does not) | open | |
