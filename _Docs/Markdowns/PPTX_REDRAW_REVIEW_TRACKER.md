# PowerPoint redraw + scroll fix — adversarial review remediation

A live tracker. Rows move to `done` **in the same commit as the fix**, and only
with a test watched failing without it. Counts are derived from the rows below,
never typed. **Delete this file in the pull request that closes its last row.**

## State

| State | Rows |
| --- | --- |
| `todo` | 8 |
| `doing` | 1 |
| `done` | 5 |

## Rows

`loss` marks a defect that destroys the user's work; `silent` marks one whose
output is indistinguishable from correct output. Those come first.

| # | Sev | State | Defect | Where |
| --- | --- | --- | --- | --- |
| B1 | loss | done | The export replaces the user's replicate sets, gating and stats groups with the last regenerated plot's and cannot put them back. `view_state.snapshot` captures none of the three; `restore_saved_plot` replaces all three. The clobbered gating later reaches `pipeline_info.json` on the next gate edit. | `saved_plots/controller.regenerate_missing_vectors` |
| B2 | silent | done | The captured figure is drawn with neither the record's gating nor the user's. `restore_gating_block` kicks an async `QThread`; the capture happens one line later against the old flags, and each iteration cancels the previous worker. The slide claims it "reflects the current data". | `saved_plots/controller._regenerate_one` |
| B3 | silent | todo | The caps produce decks that cannot be opened. At 249,000 shapes (under the cap) LibreOffice was OOM-killed at 13.9 GB; 100,000 takes 2m39s to open. The constant was set from *build* time, which is not the number that decides it. ARCHITECTURE now contradicts a correct paragraph four lines below. | `fig_clipboard.MAX_VECTOR_PATHS`, `_deck.MAX_SHAPES_PER_SLIDE` |
| B4 | silent | todo | A rasterized point cloud is reported as "all editable": `_svg_shapes` parses the embedded image fine, so nothing is recorded and no note is written. Only a log line says otherwise, which §7 explicitly rules out. | `fig_clipboard.render_figure_svg`, `_deck._place_vector` |
| B5 | silent | todo | A record saved from one panel of a multi-panel figure regenerates as the **whole** figure, shown under the panel's title beside the panel's parameters. `captureVectorSvg` renders the whole card; `_on_save_axis_clicked` saved one axis. | `plot_card.captureVectorSvg`, `SavedPlot` |
| B6 | high | todo | A regenerated slide that also lost a construct writes two notes at identical coordinates; both become illegible. | `_deck._note` |
| B7 | test | done | The regeneration feature has no test: replacing `restore_saved_plot(ctx, rec.id)` with `pass` leaves all 44 tests green. The one test that covers it monkeypatches that call away, so it proves the plumbing and nothing about the record. | `tests/test_saved_plots_pptx_export.py` |
| B8 | silent | done | `view_state.snapshot` raising leaves `before = None`, which silently skips the whole restore — the safety mechanism the docs describe. Never executed by any test, because it raises against the stub ctx there. A repair that cannot be undone should not start. | `saved_plots/controller.regenerate_missing_vectors` |
| B9 | high | done | Multivariate, Spatial and smFISH are `lazy_only`, so an unopened tab has no card and its records are skipped — told to "re-save", which is what the fix exists to eliminate. Code and docs blame the Image Table alone for a branch with at least four causes. | `saved_plots/controller.regenerate_missing_vectors` |
| B10 | test | todo | `test_a_tab_with_no_figure_is_left_as_a_picture` returns at the empty-`cards_by_tab` guard and never reaches the `card is None` branch it names; deleting that branch keeps it green. | `tests/test_saved_plots_pptx_export.py` |
| B11 | test | todo | The assertion added to `test_the_gallery_can_be_opened_at_one_particular_card` evaluates `0 == 0` — the parent is never shown and the PNGs are not decodable, so the scroll area has no range. Its comment claims it would have caught the shipped bug; it would have passed against it. | `tests/test_saved_plots_tab.py` |
| B12 | low | todo | `render_figure_svg`'s docstring names `copy_figure_to_clipboard` as the caller passing `max_paths=None`. It is not — that function calls `_render_figure` directly, and the parameter now has no production caller and no test. | `fig_clipboard.render_figure_svg` |
| B13 | low | todo | The fast lane got 13.6× slower on this file (3.0s → 40.8s) because a test's input size is keyed to the raised constant: 300,500 points rendered and parsed on every run. | `tests/test_saved_plots_pptx_export.py` |
| B14 | low | doing | Five minor defects (persist-per-record: **done**): `attach_vector` rewrites the whole `persistence.json` per record; the loop blocks the UI thread with no progress; `RASTERIZED_LAYER_DPI` inflates every SVG render, not only rasterized ones; `scroll_to_saved_plot` now returns True for a destroyed card; a failed `attach_vector` discards an SVG already in hand. | several |
