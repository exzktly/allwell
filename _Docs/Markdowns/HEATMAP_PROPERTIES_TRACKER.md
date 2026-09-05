# Heat Map → Properties: rows that do nothing

The Properties dock is built once per figure and shows nearly every section to
every plot. On the Heat Map most of those sections style artists the figure does
not contain, or write state its renderer never reads, so the control moves and
the grid does not.

Every row below was **measured**, not read: the Heat Map figure was drawn, the
pref was changed from its default, `apply_export_style_prefs` was re-applied, and
the figure was rendered to PNG and hashed. `no effect` means the two renders were
byte-identical. That method is what caught row 7 — `Show grid` turns nothing off
on *any* plot in the app, which reading the call would not have shown.

Counts are read off the rows, never typed.

| # | Section → row | Measured | Decision | State |
|---|---|---|---|---|
| 1 | Legend → Show legend / Font size / Location | no effect (a heat map has no legend; the colour bar is not one) | Hide the section on the Heat Map | done — `tests/test_properties_hides_what_it_cannot_do.py`, red on `origin/main` |
| 2 | Lines & Markers → Line style / Line width / Marker style / Marker size / Marker edge width | no effect (`ax.lines` is empty and no collection is tagged `_aw_marker_collection`) | Hide the section | done — `tests/test_properties_hides_what_it_cannot_do.py`, red on `origin/main` |
| 3 | Overlays → Show threshold line / Show value labels / Value format | no effect (the renderer reads none of the three; the cell number has its own format under **Cell values**) | Hide the section | done — `tests/test_properties_hides_what_it_cannot_do.py`, red on `origin/main` |
| 4 | Statistics → Show (Mean / Mean+spread / All points) | writes `bar_show_mode`, read only by the bar renderer | Hide the row, keep Error bars + Across (both live — the cell `±`) | done — `tests/test_properties_hides_what_it_cannot_do.py`, red on `origin/main` |
| 5 | Axes → Group label size | no effect (bar group labels only) | Hide the row | done — `tests/test_properties_hides_what_it_cannot_do.py`, red on `origin/main` |
| 6 | Limits & Scale → Log scale X/Y + Log base X/Y | no effect (the axes are marked `_categorical_xaxis` / `_categorical_yaxis`, so the log pass skips them) | Hide both rows, keep X/Y limits + Auto scale (live) | done — `tests/test_properties_hides_what_it_cannot_do.py`, red on `origin/main` |
| 7 | Grid → Show grid | no effect **on every plot in the app** — `ax.grid(False, alpha=…, linestyle=…)` re-enables the grid, and matplotlib says so in a `UserWarning` nothing was reading | Fix `apply_export_style_prefs` | done — `tests/test_grid_show_turns_the_grid_off.py`, 3 of its 4 red on `origin/main` |
| 8 | Time → Interval per timepoint / Offset | no effect (the Heat Map title prints the raw filename hour) | Wire it: the title goes through `time_rescale.figure_timepoint_label`, the helper written for figures whose title is the only place the timepoint appears | done — `tests/test_menu_timepoint_titles_are_corrected.py`, 7 of its 10 red on `origin/main` |
| 9 | Axes → Axis # offers `2` | selecting it changes nothing (axis 2 is the colour bar, which every per-axis pass skips) | Leave colour-bar axes out of the picker | done — `tests/test_properties_hides_what_it_cannot_do.py`, red on `origin/main` |
| 10 | `_is_response_fig` is defined twice, the second shadowing the first | n/a — read, not measured | Remove the duplicate | done — `tests/test_properties_hides_what_it_cannot_do.py`, red on `origin/main` |
| 11 | The Distribution tab names its timepoint the same way and was equally uncorrected | read, then confirmed against `_title_for` | Same fix, same helper — correcting one of two identical siblings would have replaced one disagreement with another (§7.5 #5) | done — `tests/test_menu_timepoint_titles_are_corrected.py`, red on `origin/main` |
| 12 | Response Curves names its timepoints raw in both its live and Batch Export titles (`features/response/controller.py:918`, `features/batch_export/response_panel.py:581`) | read, not measured | **declined** — the same defect class, but a different shape: those titles name a *list* of timepoints as series rather than one picked from a menu, so `figure_timepoint_label` is the wrong helper and doing it properly needs a new one. Out of what was asked for; recorded here and in the pull request rather than dropped | declined |

Rows 1-6 and 9 are the Heat Map's alone. Rows 7, 8, 10 and 11 are shared code: 7
and 10 are defects on every plot, and 8 with 11 make a control that already
claims to govern every figure's timepoint label actually govern the two tabs
that pick a timepoint from a menu instead of drawing an axis.

Row 8 reversed a decision the README stated as deliberate ("their titles keep
naming the filename's own value, matching the menu the choice came from"). It
was put to the user rather than decided here, because the previous behaviour was
written down as a choice rather than left as an oversight — and because the same
paragraph already contradicted itself, exempting the batch-export scatter, which
also has no time axis, from the same rule.

Delete this file in the pull request that closes its last row.
