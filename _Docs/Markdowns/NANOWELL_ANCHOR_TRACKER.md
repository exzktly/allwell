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
| done | 6 |
| open | 8 |

| # | Item | State | Where | Evidence |
|---|---|---|---|---|
| A1 | Anchors file: Qt-free read/write/validate, one anchor per (well, timepoint) | done | `util/nanowell_anchors.py`, remote manifest | `tests/test_nanowell_manual_anchor.py` (set/replace/clear, five unusable files each refused by name) |
| A2 | `locate_timepoint` takes the anchor; `resolve_origin` offers `manual` first; a click far from a site is refused with the distance | done | `pipeline/nanowell_run.py`, `pipeline/nanowell_lattice.py` | `test_an_anchor_near_a_site_…`, `test_an_anchor_between_sites_…` (both fail with the manual branch removed); `test_nanowell_manual_anchor_run.py::test_the_anchor_is_read_in_the_well_frame_…` (fails without the frame conversion) |
| A3 | Grid record states the anchor that was used (`origin_method: manual`, `manual_anchor_um`) | done | `pipeline/nanowell_run.py` | `test_one_anchor_at_one_timepoint_addresses_the_well_at_both` reads `origin_method: manual` and `manual_anchor_um` from the grid record |
| A4 | A manual anchor is independent evidence for the plate re-anchoring | done | `pipeline/nanowell_task.py::_INDEPENDENT` | `test_a_manual_anchor_is_evidence_the_plate_re_anchoring_can_carry` (fails without `manual` in `_INDEPENDENT`); end to end, 24h is carried by `plate_rigid` with every address right |
| A5 | Reuse is keyed on the anchor: an incremental run and a checkpoint do not keep a timepoint computed under another anchor | done | `pipeline/nanowell_task.py::_prior_state`, `nanowell_run._checkpoint_key` | `test_an_incremental_run_recomputes_…`, `test_a_checkpoint_is_keyed_on_the_anchor_…` (each fails with its line removed) |
| A6 | `process_well_zip` reads the anchors beside the record and hands each timepoint its own | done | `pipeline/nanowell_task.py` | `test_one_anchor_at_one_timepoint_…` fails with `('unaddressed', None)` when `process_well_zip` drops the anchors; Test detection reads them too (`pipeline/nanowell_preview.py`) |
| B1 | Remote: the anchors file is uploaded with the input and reaches every batch; the upload cache is keyed on it | open | `services/acquisition_transport.py`, `services/remote_upload.py` | |
| B2 | Subset / Merge Plates / Concatenate carry the anchors (wells remapped, timepoints shifted), or say they did not | open | `datasets/*/…_compute.py` | |
| C1 | Qt-free render of a region of a well's registered montage at a chosen resolution, from the raw tiles | open | `review/features/nanowell/` | |
| C2 | Zoomable, pannable full-resolution montage view with tile outlines and nanowell addresses | open | `review/features/nanowell/` | |
| C3 | Anchor mode in that view: click sets the well's anchor at this timepoint, marker shown, clear/replace, written to the anchors file | open | `review/features/nanowell/` | |
| C4 | Array QC opens the view; the page says which timepoints are unaddressed and why, and how to re-run with the anchors | open | `review/features/nanowell/` | |
| D1 | ARCHITECTURE.md and README describe the anchor and the view | open | `_Docs/Markdowns/` | |
| D2 | One test drives the real Array QC entry and the real view constructor | open | `tests/` | |
