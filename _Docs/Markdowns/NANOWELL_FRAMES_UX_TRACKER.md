# Nanowell frames UX review tracker

An independent UX review of the Image Table's nanowell frames (PR #1188, branch
`claude/image-table-nanowell-frames`) looked at real renders of a synthetic four-well plate:
`MONTAGE`, `MONTAGE GFP`, and three `NW:` metric maps. These are its findings, one row each.

**Status:** derived from the rows below. Count the `done` and `declined` rows rather than
trusting a number typed here.

States: `open`, `in progress`, `done` (a test pins it), `declined` (with the reason written at the site and here).

| ID | Finding | Where | State | Evidence / reason |
|---|---|---|---|---|
| H1 | Metric maps have no colour key: no value range, no units, nothing saying the window is shared; fold change has no visible 1.0 | live grid and export; new `image_compose` key strip | done | `tests/test_image_table_nanowell_frames_ux.py::test_the_key_names_each_metric_in_full_with_the_window_its_cells_are_drawn_in`; live grid and slide in `tests/test_image_table_nanowell_frames_app.py`. Mutation (drop the centre tick) failed the key test |
| H1b | Fold-change metrics need a diverging colormap centred at 1, log-scaled | `_default_colormap_for`, `_compute_auto_luts` | done | `tests/test_image_table_nanowell_frames_ux.py::test_a_fold_change_map_is_diverging_log_scaled_and_its_window_centred_on_no_change`, `::test_a_fold_change_already_on_a_log_scale_is_centred_on_zero`; mutation (no centring) failed both |
| H2 | The phase `MONTAGE` defaults to green and `PHASE` to magenta, so transmitted light reads as fluorescence | `builder_view._default_colormap_for` | done | `tests/test_image_table_nanowell_frames_ux.py::test_the_phase_montage_and_the_phase_channel_default_to_grey_and_a_fluorophore_keeps_its_colour`; mutation failed it |
| H3 | Tile seams in the GFP montage | `table_frame_compute.channel_frame` | declined | Not a stitcher defect. The review's synthetic plate wrote tiles F1 and F2 swapped against the grid record (the generator unpacked `(name, ty, tx)` from `(name, tx, ty)`); `channel_frame` reproduced the tiles exactly (max difference 0 against the 2×2-mean of the four tiles), and with the generator fixed the row step at the tile boundary (51) matches its neighbours (46) |
| M1 | "No value" grey reads as a high value and is unexplained | `table_frame_compute.metric_map`, key strip | done | `tests/test_image_table_nanowell_frames.py::test_a_nanowell_with_no_value_is_an_empty_ring_never_background_a_disc_or_zero`, `::test_the_ring_is_the_rim_of_the_nanowell_and_a_value_fills_its_disc`; the key's ring swatch in H1. Mutation (filled disc) failed three tests |
| M2 | The status line is a per-well wall of text, cut off with "and 9 more" | `image_compose.nanowell_frame_clause` | done | `tests/test_image_table_nanowell_frames.py::test_what_the_frames_leave_out_is_one_sentence_per_metric_summed_over_the_wells`; mutation (no summing) failed it |
| M3 | Labels too long; upper-casing breaks "log₂" and "n₀"; the builder tray's NW chips are a wall | `ChannelSpec.axis_token`, facet labels, `_build_tray` | done | `tests/test_image_table_nanowell_frames_ux.py::test_a_metric_is_named_short_and_as_spelt_and_its_token_keeps_its_case` (it caught a trailing-comma bug in `short_label`), `::test_the_starting_table_leaves_out_the_frames_that_read_every_tile`; tray folding in `tests/test_image_table_nanowell_frames_app.py` |
| M4 | The builder's chip swatch shows green for viridis metric rows; the LUT editor's colormap combo does not offer viridis | `builder_view._cmap_swatch`, LUT editor | done | `tests/test_image_table_nanowell_frames_ux.py::test_a_ramp_s_swatch_is_its_gradient_and_the_lut_editor_offers_it` |
| M5 | The scale bar is a tiny unlabelled tick | `draw_cell_chrome` | done | `tests/test_image_table_nanowell_frames_ux.py::test_the_scale_bar_carries_its_length_and_can_be_told_not_to`; mutation (no text) failed it |
| L1 | Metric discs are stair-stepped after a crop | `_nanowell_frame_rgb` | done | `tests/test_image_table_nanowell_frames_ux.py::test_a_cropped_metric_map_is_redrawn_finer_and_its_scale_bar_follows`; mutation (never re-paint) failed it. Montages declined by design: their pixels are the data |
| L2 | A pooled Auto window leaves dim wells near-black, unsaid; offer a per-cell window, visibly marked | key strip, `ChannelSpec`, LUT editor | done | `tests/test_image_table_nanowell_frames_ux.py::test_per_cell_leaves_a_channel_out_of_the_pooled_window_and_the_key_says_colours_are_not_comparable`; mutation failed it |
| L3 | The corner title shows "FOV:F0", meaningless for whole-well frames | `facet_view.plan_facets` | done | `tests/test_image_table_nanowell_frames_ux.py::test_a_grid_of_whole_wells_names_no_field`; mutation failed it |
