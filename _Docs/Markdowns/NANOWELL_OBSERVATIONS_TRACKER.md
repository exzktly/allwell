# Nanowell observations — remediation tracker

A live tracker (CLAUDE.md §6.1) for making nanowell and standard plates one experience in Review. It is
deleted by the pull request that closes its last row.

**Origin.** A nanowell run loaded in the real app (2 wells × 2 timepoints, real pipeline, a threshold standing in
for Cellpose) showed the Plotting leaves defaulting to the phase channel and drawing a "fraction above threshold"
of 1.0 from an unset cutoff, the Image Table never offering the montage the run analysed and always failing its
NUC+SEG row, Segmentation drawing nothing at all, and the per-nanowell metrics reachable only inside the Nanowells
page — so no plot could correlate two nanowell metrics. A UX reviewer and a Python architect debated a plan to
consensus; the user then chose that removing the Nanowells feature from a build removes nanowell plotting too
(amendment B). The rows below are that plan.

**Rules every row keeps.** Wells are the replicates, never nanowells. The phase channel is never the default and
never fluorescence. Nanowell metrics reach other features only through the Nanowells descriptor's registry
services (`ctx.features.query`), never an import. Every figure says what its observations are; the word "unit"
appears in no user-facing text about the toggle. Degraded output says so in the artifact.

States: `todo`, `doing`, `done` (with the test that fails without the fix), `declined` (with the reason).

| # | Item | State | Where / evidence |
|---|------|-------|------------------|
| 1 | One fluorescence predicate: the phase channel is listed last and never the default plot channel | done | `review/domain/channel_roles.py`; `tests/test_the_phase_channel_is_never_the_default.py` (fails `['phase','gfp'] == ['gfp','phase']` with the old first-token default) |
| 2 | One dataset-layout resolver: opening `out/` finds the `in/` beside it | done | `review/data/dataset_layout.py`; `tests/test_opening_out_finds_the_in_beside_it.py`; the real-app load is asserted in the #20 file |
| 3 | An unset ON threshold draws "ON threshold not set" instead of Fraction = 1.0 bars | doing | Bar and Line tabs done: `channel_state_controller.on_cut_not_set` + `review/core/threshold_note.py`; `tests/test_an_unset_on_cut_is_not_drawn_as_data.py` (each tab's hook mutated out fails with `[] == ['ON threshold…']`). **Scope, measured:** only a cut that is unstored *and* 0.0 (the segmentation channel's default) qualifies; a readout channel's unstored 50.0 is a real cut and keeps drawing — the architect proposed noting that case too, which would hide the Fraction panels of every standard plate whose thresholds were never edited, so it is left out. **Still owed:** Batch Export's Bar and Line figures (`batch_export/bar_panel.py`, `base_panel.py`) draw the same panels unhooked |
| 4 | Segmentation without a label map draws the image, says why, disables Delete / Crop / Edit | todo | |
| 5 | Segmentation draws a nanowell run's cell points and rims through a Nanowells service | todo | |
| 6 | Montage reader in `review/data`; Image Table "Source: Montage \| Raw tile"; NUC+SEG only with label maps; scale bar from the acquisition pixel size | todo | |
| 7 | Observation contract (`review/domain/observation.py`) and `review/data/observations.get_observations` through the `observations.nanowell` service | todo | |
| 8 | `FeatureDescriptor.observation_units` and the band's "Observations: Cells \| Nanowells" toggle, derived from the registry | todo | |
| 9 | Scatter reads observations: nanowell points, any metric per axis, "Points: each observation \| per well", X ≠ Y by default | todo | |
| 10 | Multivariate reads observations | todo | |
| 11 | A cell's own nanowell's metrics as cell properties ("Nanowell ▸") | todo | |
| 12 | Distribution under Nanowells (subsumes Nanowells ▸ Distribution) | todo | |
| 13 | Bar / Response under Nanowells: well medians, error bars over wells, fold-change titles that name their form | todo | |
| 14 | Line under Nanowells: well medians over time | todo | |
| 15 | Heat Map under Nanowells: well medians; empty state links to Nanowells ▸ Array | todo | |
| 16 | Statistics under Nanowells (subsumes Nanowells ▸ Statistics) | todo | |
| 17 | Batch Export's Nanowells mode reaches the feature through services, not `_lazy` imports | todo | |
| 18 | Every figure's footer begins "Observations: cells" / "Observations: nanowells"; without the feature, "pooled from N occupied nanowells per well · nanowell views are not in this build" | todo | |
| 19 | A status-bar message belongs to the tab that wrote it | todo | |
| 20 | `test_feature_removal` case: a nanowell dataset with the Nanowells package blocked | todo | |
| 21 | ARCHITECTURE.md and README_WellViewer.md describe all of the above | todo | |
