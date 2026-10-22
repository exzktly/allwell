# Nanowell manual anchor and full-resolution montage — remediation tracker

A live tracker (CLAUDE.md §6.1): one row per item, updated in the commit that closes it, deleted by the PR that
closes its last row.

**The request.** Nanowell runs often fail to find the fiducial (the central square), which leaves a well's
timepoints unaddressed. The user needs a way to give the anchor by hand, and a full-resolution, zoomable view of a
well's registered montage to click it on.

**The decisions the user made.**

- The anchor is set in Review → Nanowells → Array QC, after a run, and those wells are then re-run.
- One click per well, at one timepoint. The end-of-run plate re-anchoring carries it to the well's other
  timepoints. A timepoint it cannot carry stays unaddressed and asks for a click of its own.
- The failure being fixed is "unaddressed".
- The montage is a zoomable in-app view at native resolution, drawn from the raw tiles.

**The design.**

- Anchors live in `in/nanowell_anchors.json`, beside `acquisition.json`. They are an annotation of the input
  images, so they survive re-runs and travel to a remote host the way the record does.
- Each anchor gives the well, the timepoint and the position in the well frame (µm; the frame the grid record's
  `montage_origin_um` and `tiles` are stated in).
- The pipeline offers a well's anchor to `resolve_origin` as the first source, `manual`. It is snapped to the
  nearest lattice site, and refused with a stated reason when it lies more than a quarter pitch from every site.
- A manual anchor is independent evidence, so it joins `_INDEPENDENT` and the plate-rigid re-anchoring can
  carry it across timepoints.

| State | Count |
|---|---|
| done | 14 |
| open | 0 |

| # | Item | State | Where | Evidence |
|---|---|---|---|---|
| A1 | Anchors file: Qt-free read/write/validate, one anchor per (well, timepoint) | done | `util/nanowell_anchors.py`, remote manifest | `tests/test_nanowell_manual_anchor.py` (set/replace/clear, five unusable files each refused by name) |
| A2 | `locate_timepoint` takes the anchor; `resolve_origin` offers `manual` first; a click far from a site is refused with the distance | done | `pipeline/nanowell_run.py`, `pipeline/nanowell_lattice.py` | `test_an_anchor_near_a_site_…`, `test_an_anchor_between_sites_…` (both fail with the manual branch removed); `test_nanowell_manual_anchor_run.py::test_the_anchor_is_read_in_the_well_frame_…` (fails without the frame conversion) |
| A3 | Grid record states the anchor that was used (`origin_method: manual`, `manual_anchor_um`) | done | `pipeline/nanowell_run.py` | `test_one_anchor_at_one_timepoint_addresses_the_well_at_both` reads `origin_method: manual` and `manual_anchor_um` from the grid record |
| A4 | A manual anchor is independent evidence for the plate re-anchoring | done | `pipeline/nanowell_task.py::_INDEPENDENT` | `test_a_manual_anchor_is_evidence_the_plate_re_anchoring_can_carry` (fails without `manual` in `_INDEPENDENT`); end to end, 24h is carried by `plate_rigid` with every address right |
| A5 | Reuse is keyed on the anchor: an incremental run and a checkpoint do not keep a timepoint computed under another anchor | done | `pipeline/nanowell_task.py::_prior_state`, `nanowell_run._checkpoint_key` | `test_an_incremental_run_recomputes_…`, `test_a_checkpoint_is_keyed_on_the_anchor_…` (each fails with its line removed) |
| A6 | `process_well_zip` reads the anchors beside the record and hands each timepoint its own | done | `pipeline/nanowell_task.py` | `test_one_anchor_at_one_timepoint_…` fails with `('unaddressed', None)` when `process_well_zip` drops the anchors; Test detection reads them too (`pipeline/nanowell_preview.py`) |
| B1 | Remote: the anchors file is uploaded with the input and reaches every batch; the upload cache is keyed on it | done | `services/acquisition_transport.py`, `services/remote_scratch.py::source_fingerprint` | `test_acquisition_remote_transport.py::test_the_nanowell_anchors_reach_…`: the bytes reach the full upload and a batch, the cache key and the re-attach fingerprint move with an anchor; each of the three fails with its line removed |
| B2 | Subset / Merge Plates / Concatenate carry the anchors (wells remapped, timepoints shifted), or say they did not | done | `datasets/*/…_compute.py` | `tests/test_nanowell_anchors_follow_the_dataset_tools.py`: each tool through its real entry point; each of the four call sites (and the in-place preserve) fails its test when removed |
| C1 | Qt-free render of a region of a well's registered montage at a chosen resolution, from the raw tiles | done | `review/features/nanowell/` | `tests/test_nanowell_full_montage.py`: pixel values from four constant tiles, the overlap drawn by the later tile, a one-pixel mark only full resolution shows, eviction; reversing the draw order, always taking the coarsest level and disabling eviction each fail |
| C2 | Zoomable, pannable full-resolution montage view with tile outlines and nanowell addresses | done | `review/features/nanowell/` | same file, through the real page (`build_nanowell_tab`): the button opens the window on Array QC's well and timepoint |
| C3 | Anchor mode in that view: click sets the well's anchor at this timepoint, marker shown, clear/replace, written to the anchors file | done | `review/features/nanowell/` | `test_a_click_with_set_anchor_on_writes_the_anchor_where_it_landed_and_clear_removes_it` (fails with the click unconnected) |
| C4 | Array QC opens the view; the page says which timepoints are unaddressed and why, and how to re-run with the anchors | done | `review/features/nanowell/` | `test_the_button_opens_…_says_why_it_is_unaddressed`, `test_re_run_opens_analyze_limited_to_the_anchored_wells_each_forced` (Incremental off, so a remote nanowell run is not refused) |
| D1 | ARCHITECTURE.md and README describe the anchor and the view | done | `_Docs/Markdowns/` | ARCHITECTURE.md (origin resolution, end of run, *Anchors set by hand*, *Full montage*, `core/analyze_handoff.py` row) and README (*Setting the anchor by hand*, Array QC) |
| D2 | One test drives the real Array QC entry and the real view constructor | done | `tests/` | the three window tests above press the real button, canvas and re-run on the real tab builder |
