# Properties sidebar relevance — remediation tracker

A live tracking document (CLAUDE.md §6.1), deleted by the pull request that
closes its last row. It holds the work of making the Review Properties sidebar
offer only the controls that change the plot it is bound to.

The findings come from two read-only audits made against `85d133a`: a control ×
figure applicability matrix (each cell read from the renderer that would honour
the control, a few measured with a style-pass probe on synthetic artists), and
an implementation survey of `review/features/export/properties_view.py`,
`review/core/plot_style.py` and the feature registry. Every row is a hypothesis
about the tree as it stood then; a row moves to `done` only with the test that
pins it.

States: `open`, `in progress`, `done`, `declined` (with the reason written
here and at the site).

## How the decision is made

A control is hidden on a figure only when changing it cannot change that
figure: the renderer never reads the PlotState field it writes, or the style
pass has no artist of the kind it styles. A control the style pass *does* act
on stays visible even where the result is unwanted (a reference line restyled
as data, a grid forced over a matrix), because hiding it would remove the only
way to undo what the default does. Those cases are the H rows below, and they
are fixed in the style pass, not in the panel.

## M — the mechanism

| # | Item | State | Evidence |
|---|---|---|---|
| M1 | One declared vocabulary of style facets (`review/domain/style_facets.py`), each naming the panel rows it gates | done | `review/domain/style_facets.py`; `test_every_section_names_a_facet_and_every_facet_gates_a_control` |
| M2 | Every figure the panel opens on declares, per facet, whether it has it — total, with a written reason for every absence — on its owning `FeatureDescriptor` | done | `<feature>/_style_facets.py` ×9; `test_every_figure_the_panel_opens_on_is_declared_once`, `test_each_declaration_is_total_and_gives_its_reasons` |
| M3 | The panel reads the declaration; the six ad-hoc predicates and two rosters (`_STYLE_FACETS_A_FIGURE_LACKS`, `_COLOUR_SCALE_FIGURES`, `_supports_well_order`, `_draws_beeswarm`, and the gating uses of `_is_*_fig`) go | done | removed; `test_a_section_the_plot_cannot_use_is_not_even_built` fails with `_supports` forced true |
| M4 | A section with no live row is not shown | done | `_apply_facets`; the same test caught an empty Statistics section built on five figures, now not built |
| M5 | A figure that draws several views stamps the view it shows, and an open panel re-gates on the stamp (built-then-hidden for facets absent in some views) | done | `plot_style.mark_style_view`; `test_the_beeswarm_follows_the_nanowells_view`, `test_the_nanowells_redraw_stamps_its_view` |
| M6 | A hidden control never writes its pref: `_persist` skips keys whose facet is not live | done | `_hidden_keys`; `test_a_hidden_control_never_writes_its_setting` fails with the skip removed (synthetic declaration: no real figure withholds a keyed facet today) |
| M7 | The footer says how many sections do not apply here and why (tooltip), so a missing control has an answer | done | `_refresh_not_offered_note`; `test_the_footer_names_what_is_not_offered_and_why`, `test_the_footer_count_follows_the_view` |
| M8 | Guards: every panel figure is declared exactly once and totally; every section names a facet in the vocabulary; each PlotState facet's declaration agrees with which feature packages read that field | done | `test_a_plotstate_facet_is_declared_exactly_where_its_field_is_read` fails when Spatial is made to lack `time_axis` |

## G — controls shown where they do nothing (from the matrix)

| # | Control | Figures where it has no effect | State | Evidence |
|---|---|---|---|---|
| G1 | Statistics → Show (`bar_show_mode`) | every figure but Bar — and setting it elsewhere silently changes the Bar tab | done | `test_show_mode_is_offered_on_the_bar_plot_alone` |
| G2 | Statistics → Error bars / Across | Scatter (cells), Distribution, Spatial, Nanowells, Multivariate — none reads `use_sem` / `bar_err_kind` | done | declarations + reader oracle (`error_bars`); `test_each_plot_is_shown_the_sections_it_can_use` |
| G3 | Axes → Group label size | every figure but Bar | done | `test_group_label_size_is_offered_on_the_bar_plot_alone` |
| G4 | Overlays → Threshold line | Scatter (both), Spatial, Nanowells, Multivariate, Response | done | reader oracle (`threshold_line`); per-figure section lists |
| G5 | Overlays → Value labels | Distribution, Spatial, Nanowells, Multivariate, Response | done | reader oracle (`value_labels`); per-figure section lists |
| G6 | Time → Interval / Offset | Scatter (both), Nanowells, Multivariate — none reads the time rescale | done | reader oracle (`time_axis`); per-figure section lists |
| G7 | Lines & Markers → Line style / width | Scatter (both): points are markers-only lines | done | `test_the_scatters_keep_their_marker_rows_and_lose_the_line_rows`; Multivariate loses both line and marker rows (no `ax.lines`, untagged collections — read, not measured) |
| G8 | Beeswarm → Point spacing | Nanowells in every view but Distribution | done | `test_the_beeswarm_follows_the_nanowells_view` |
| G9 | Statistics → Error bars "None" / "95% CI" on Line, Response, Scatter (aggregate): they read `use_sem` only, so the control names an interval the plot does not draw | open | |
| G10 | Axes → Axis # list is filled once and never refilled after a view or split change; it offers Nanowells image panels the style pass skips; it is shown on single-axes figures | open | |
| G11 | Legend on views that draw none (Multivariate outside dimred, Spatial map, Bar outside plate colour mode, Nanowells figure-level keys) | open | |
| G12 | Limits & Scale → X log on Bar (categorical axis, skipped silently) | open | |
| G13 | Layout → Tight while Constrained is on; both in phone layout | open | |
| G14 | Shared settings (Statistics, Overlays, Time, Colours, Bars, Beeswarm write global PlotState) are not marked as shared; the manual says every setting is per-plot | in progress | the manual now names the shared sections; the panel carries no badge yet |

## H — the style pass overwrites styling that carries meaning (follow-up)

Not panel visibility: these happen on every redraw with the default
preferences, whether or not the panel is open.

| # | Finding | State | Evidence |
|---|---|---|---|
| H1 | Bar "not computed" n/a cap (`marker="_"`, `barplot/_annotations.py`) is restyled into a filled data marker | open | probe on a synthetic artist |
| H2 | Untagged reference / fit / density lines (CDF threshold, Bar 0.5 reference, Distribution threshold and KDE/CDF step, Nanowells no-change line and brackets, Response fit and axis break, Spatial references) take the data line and marker style | open | probe on synthetic artists; per tab inferred |
| H3 | Response off-scale hollow markers are re-filled | open | probe |
| H4 | X tick angle overwrites deliberate renderer angles (Bar 45°, Heat Map 30°/left, violin 30°, Multivariate matrices) | open | |
| H5 | Grid is forced on over Multivariate matrices that the renderer turned off | open | |
| H6 | Log scale applied to position axes not flagged categorical (Multivariate matrices, violin positions, Nanowells swarm) | open | |
