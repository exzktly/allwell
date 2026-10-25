# Starting-cells filter — remediation tracker

Live state for the Nanowells page's *Starting cells* filter (CLAUDE.md §6.1). One row per item of the plan agreed
with the UX review; a row is `done` only with the test that pins it. This file is deleted by the PR that closes its
last row (PR #1058, which this work stacks onto).

The request: "filter nanowells for purposes of inclusion … only compute statistics/display [nanowells] that start
with a minimum number of cells, and then use that for the nanowell montage, trajectories, distributions display".

| # | Item | State | Evidence |
|---|------|-------|----------|
| S1 | Qt-free filter: bounds, presets, membership by `n0`, kept/outside/unknown/late counts, per-well kept, the note | done | `tests/test_nanowell_page_starting_cells.py::test_the_filter_keeps_…`, `…first_measurable…`, `…bounds_are_whole_numbers…` (mutation of `inside`'s `notna` is equivalent: NaN fails every bound) |
| S2 | State: `start_min` / `start_max` persisted, refused when not a whole number ≥ 0 or min > max | done | `tests/test_nanowell_page_starting_cells.py::test_the_bounds_persist_and_a_saved_pair_that_is_not_a_filter_is_refused` |
| S3 | Band control: preset combo (Any, ≥1, 1, 1–2, ≥2, Custom…) after Baseline on every view, Custom shows min/max spins, accent while a bound is set | done | `tests/test_nanowell_page_starting_cells.py::test_choosing_a_preset…` (accent property; mutation caught), `…custom_bounds…` |
| S4 | Distribution, Trajectories, Position, Extremes draw only the nanowells in the filter, through `draw_view`, each figure carrying the note | done | `tests/test_nanowell_page_starting_cells.py::test_choosing_a_preset…`, `…trajectories_and_position_carry_the_note`, `…extremes_ranks_only…` (both mutations caught). Position's filter-emptied-well wording has no test of its own |
| S5 | Filter before Fixed cohort; responding fraction over the filtered roster | done | order is structural (`draw_view` filters before `distribution_model` applies the cohort); the responding fraction is over the kept nanowells, and the filter note says which those are — the responding sentence itself is unchanged |
| S6 | A filter that keeps nothing says so on the figure, naming the filter | done | `tests/test_nanowell_page_starting_cells.py::test_a_filter_that_keeps_nothing_says_so` |
| S7 | Array view: nanowells outside drawn dimmed, still clickable, counted in the note | done | `tests/test_nanowell_page_starting_cells.py::test_the_array_view_dims_…` (mutation caught) |
| S8 | Site detail: a selected nanowell outside the filter is marked as such | done | `tests/test_nanowell_page_starting_cells.py::test_a_selected_nanowell_outside_the_filter_says_so_in_its_detail` (mutation caught) |
| S9 | Occupancy key marks the classes the filter removes | done | `tests/test_nanowell_page_starting_cells.py::test_the_occupancy_key_greys_…` (marker and label mutations caught) |
| S10 | *Save nanowell data…* keeps every row and flags it (`in_starting_cells_filter`, bounds, baseline) | done | `tests/test_nanowell_page_starting_cells.py::test_the_long_csv_keeps_every_row_and_flags_it` (mutation caught) |
| S11 | Batch Export: same control seeded from the page, figures and summary CSV filtered, CSV carries the bounds | done | `tests/test_batch_export_nanowell_mode.py::test_the_starting_cells_filter_is_seeded_from_the_page_…` (mutation caught) |
| S12 | Docs: ARCHITECTURE §6.2j/§6.2k, user manual Controls list, Fixed cohort tooltip cross-reference | done | ARCHITECTURE §6.2j *The starting-cells filter* + §6.2k band; manual Controls list, Save, Batch Export; Fixed cohort tooltip |

Done: 12 of 12 (count the rows above, never edit this line by hand without recounting).
