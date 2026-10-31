# Nanowell focus filter — remediation tracker

Live state for one pull request: make the nanowell rim-focus rule an app-wide inclusion layer, so every Review
tab drops the cells of an out-of-focus nanowell-timepoint, with one user-facing switch. Decided with a
scientific, a UX and an architecture review: the verdict is its own column (`NW_Focus_Pass`), never folded into
the per-cell `Focus_Pass`; the switch lives in the QC parameters and is shown on the Cell Gating panel and,
mirrored, on the Nanowells page; per-cell transmitted-light focus is deferred. Deleted by the PR that closes the
last row.

Counts are derived from the rows below.

| # | Item | State | Where |
|---|---|---|---|
| 1 | Rule moves to a Qt-free domain module that `review/data` may import | done — `tests/test_nanowell_rim_focus.py::test_the_per_cell_layer_judges_exactly_the_nanowell_timepoints_the_page_does` (fails if unaddressed rows join the reference) | `review/domain/nanowell_rim_focus.py` |
| 2 | The switch and fraction live in `QCParams`, on by default for nanowell runs, persisted, and default-filled for sections saved before them | done — `test_the_rule_is_the_qc_layer_s_on_by_default…`, `test_a_qc_section_saved_before_the_switch_existed_opens_with_it_on` (fails without the default-fill) | `review/domain/qc_params.py`, `review/persistence/qc_params.py` |
| 3 | `NW_Focus_Pass` projector: per-cell rows inherit their nanowell-timepoint's verdict; no table or no rim value passes and is said | done — `tests/test_nanowell_focus_filter.py` first two tests, through `get_rows` on a real dataset | `review/data/nanowell_focus_gate.py` |
| 4 | Registered unconditionally, declared columns, joins `VERDICT_LAYERS` and every roster of layers | done — removing the registration fails four tests in `test_nanowell_focus_filter.py`; overlay palette tests pass with the new ink | `registry.py`, `data_loading.py`, `well_summary.py`, `review_image/collect.py`, `gating_compute.py` |
| 5 | The Nanowells page's cell-gating tally excludes this layer, so its note does not blame Cell Gating | done — `test_the_page_and_the_cells_leave_out_the_same_nanowell_timepoints` (fails without `except_layer`) | `review/features/nanowell/controller.py` |
| 6 | The Nanowells page and Batch Export read the rule from `QCParams`; the page control writes through the QC path; `NanowellState` loses the rule | done — `test_nanowell_page.py::test_the_rim_focus_control_on_the_band_changes_the_qc_layer_s_rule…`, `test_batch_export_nanowell_mode.py::…qc_layer_s_rim_focus_rule` | `review/features/nanowell/` |
| 7 | Cell Gating panel: nanowell row on nanowell datasets with a plate-wide status line; per-cell row hidden there, and its "re-run" sentence no longer shown on nanowell datasets | done — `test_the_cell_gating_panel_shows_the_nanowell_row_and_hides_the_per_cell_one` (fails with the per-cell row always shown) | `review/features/qc/` |
| 8 | Docs and version | done — ARCHITECTURE *The nanowell focus layer*, README Cell Gating and Nanowells entries, 9.20.12 | `ARCHITECTURE.md`, `README_WellViewer.md`, `version.py` |
