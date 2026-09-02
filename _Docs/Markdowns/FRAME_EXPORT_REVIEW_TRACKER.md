# Frame-export review — remediation tracker

An adversarial review of the per-field PowerPoint export (branch
`claude/image-table-ppt-exports-h6xa7t`, commits `156388d`…`6e82df3`) returned
fourteen findings. This file is the live state of that work, on disk rather
than in a reply, because a summary written from memory reports the items that
were memorable rather than the items that were done (CLAUDE.md §6.1).

**A row reaches `done` only with evidence** — the test that pins it, watched
failing without the fix. "Implemented" is not `done`.

**This file is deleted by the commit that closes its last row.** A tracker left
behind at zero outstanding is a stale document claiming work is owed.

## Rows

| # | Finding | Severity | State | Evidence |
| --- | --- | --- | --- | --- |
| 1 | `_bar_mark` / `_label_mark` gate on `bar_fits` (the length half) rather than `can_carry_bar` plus the array guards `draw_cell_chrome` returns early on, so a 200×3 crop gets a bar in the deck that the picture refused and the status line says was refused | High | `done` | `test_a_field_too_small_for_chrome_gets_no_marks_in_the_deck` + its paired `…_large_enough_still_gets_its_marks`; removing the `chrome_fits` gate takes one down |
| 2 | `_try_frames` places a grid whose every field failed to encode, counts it editable, and never falls back to the good snapshot — the guard exists on the direct-export route only | High | `done` | `test_a_grid_whose_fields_all_failed_to_encode_falls_back_to_the_snapshot`; replacing the `has_pictures` check with `False` takes it down |
| 3 | A colour read back off disk reaches `_hex`, which raises by design, so one malformed plan aborts the whole gallery deck | High | `done` | `test_every_malformed_plan_is_a_framed_grid_error[colour]` + `test_a_malformed_plan_costs_that_plot_its_frames_and_nothing_else`; dropping `_hex6` takes one down |
| 4 | `from_bytes` lets `AttributeError` escape its documented `FramedGridError`, and a non-numeric `width_in` silently becomes `0.0`, scaling every rectangle off the slide | High | `done` | the same parametrised test over `frames`-as-dict, a string mark, and a non-numeric / zero / negative size; narrowing the `except` tuple or restoring the `or 0.0` coercion takes two down each |
| 5 | Three mutations at the deck boundary (no scale-bar shape at all; the bar at half length; the label plate dropped) leave all 17 tests green — the only bar assertion is satisfied by a text run's own `solidFill`. Plus an `or True` tautology, and no test at all for `image_table_export_pptx` | High | `done` | the deck test now reads the bar's EMU off the written slide and the plate off the label's `spPr`; deleting the bar shape, halving its length and dropping the plate each take one down. `or True` removed; `test_the_export_menu_action_writes_a_deck_of_groups` and its no-field sibling drive the menu action |
| 6 | `FacetLabel.band` is carried and never read by the frame plan, so a group strip loses the filled band the figure draws — the exact drift the descriptor was added to prevent | Medium | `done` | `test_a_group_strip_keeps_the_band_the_figure_draws`; setting `plate=False` on the facet marks takes it down |
| 7 | With per-axes titles on, the plan carves a title strip out of the slot while matplotlib centres the axes in the whole slot and draws the title above it, so every picture sits 0.1 in low; and the group's extent excludes its own title shape | Medium | `done` | `test_the_title_sits_above_the_picture_and_inside_the_group`, measured against the axes matplotlib drew (Agg canvas, `images[0].get_window_extent`) rather than against the shared slot; re-carving the strip, and `rect=image` in place of the union, each take it down |
| 8 | `_frames` hardcodes `"1A1D24"`, a copy of `theme.CPub.text` | Medium | `done` | the literal is now `_token(theme.CPub.text)`; `test_hardcoded_tokens` covers the rule |
| 9 | A frame with no picture and no marks is dropped entirely, while both "missing field" notes say such fields "are placed as an empty frame carrying only their labels" | Medium | `done` | `test_a_field_that_could_not_be_read_is_placed_and_says_so`; both deck notes reworded to what the slide shows |
| 10 | A long identity label at a right-hand corner is offset left with no clamp, so it lands outside the field; the burn-in clamps | Low | `done` | `test_a_long_identity_label_is_clamped_into_its_field`, whose fixture asserts the unclamped run would be wider than the field before testing the clamp; removing the clamp takes it down |
| 11 | ARCHITECTURE says the frame picture is pooled to 900 px; the gallery route passes the quality preset (280/480/800) and 900 is the direct-export number only | Low | `done` | ARCHITECTURE now states the `min(cell_max_px, FRAME_MAX_PX)` rule and the preset's 280/480/800 |
| 12 | README lists the export menu in an order the menu does not use | Low | `done` | README's menu order matches `tab_view.py` |
| 13 | The per-field PNGs are a materially larger on-disk footprint per saved grid than the single snapshot, and nothing says so | Low | `done` | stated in ARCHITECTURE and in README beside the quality preset that controls it |
| 14 | `_frames._ADVANCE_FRACTION` duplicates `_drawingml._ADVANCE_FRACTION` with a comment claiming they are the same and nothing enforcing it | Low | `declined` | the comment claimed a tie nothing holds. `_frames` may not import `_drawingml` (Saved Plots is removable) and the two estimates need not agree — only the plate's width depends on this one. The comment now says that instead of asserting a shared constant |

## Cleared by the review, recorded so it is not re-litigated

* `draw_cell_chrome` is byte-identical to `origin/main` — 1,440 sampled cases in
  the review, and 46,080 in a separate probe run here, 0 differences in both.
* `drawing_group_xml` and `text_box_xml` under its old keyword arguments are
  byte-identical, shape-id ordering included.
* `filled_rect_xml` / `picture_xml` / `group_xml` are valid DrawingML — python-pptx
  walks a written deck and resolves every part.
* With titles off, the plan's rectangles reproduce the box matplotlib actually
  drew to under 1e-4 in, for square, wide and tall fields.
* The `rgb` / `png_rgb` split behaves: marks are measured against the source
  array and the bar's length is independent of the pooled picture's resolution.
