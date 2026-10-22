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
| done | 0 |
| open | 14 |

| # | Item | State | Where | Evidence |
|---|---|---|---|---|
| A1 | Anchors file: Qt-free read/write/validate, one anchor per (well, timepoint) | open | `util/nanowell_anchors.py`, remote manifest | |
| A2 | `locate_timepoint` takes the anchor; `resolve_origin` offers `manual` first; a click far from a site is refused with the distance | open | `pipeline/nanowell_run.py`, `pipeline/nanowell_lattice.py` | |
| A3 | Grid record states the anchor that was used (`origin_method: manual`, `manual_anchor_um`) | open | `pipeline/nanowell_run.py` | |
| A4 | A manual anchor is independent evidence for the plate re-anchoring | open | `pipeline/nanowell_task.py::_INDEPENDENT` | |
| A5 | Reuse is keyed on the anchor: an incremental run and a checkpoint do not keep a timepoint computed under another anchor | open | `pipeline/nanowell_task.py::_prior_state`, `nanowell_run._checkpoint_key` | |
| A6 | `process_well_zip` reads the anchors beside the record and hands each timepoint its own | open | `pipeline/nanowell_task.py` | |
| B1 | Remote: the anchors file is uploaded with the input and reaches every batch; the upload cache is keyed on it | open | `services/acquisition_transport.py`, `services/remote_upload.py` | |
| B2 | Subset / Merge Plates / Concatenate carry the anchors (wells remapped, timepoints shifted), or say they did not | open | `datasets/*/…_compute.py` | |
| C1 | Qt-free render of a region of a well's registered montage at a chosen resolution, from the raw tiles | open | `review/features/nanowell/` | |
| C2 | Zoomable, pannable full-resolution montage view with tile outlines and nanowell addresses | open | `review/features/nanowell/` | |
| C3 | Anchor mode in that view: click sets the well's anchor at this timepoint, marker shown, clear/replace, written to the anchors file | open | `review/features/nanowell/` | |
| C4 | Array QC opens the view; the page says which timepoints are unaddressed and why, and how to re-run with the anchors | open | `review/features/nanowell/` | |
| D1 | ARCHITECTURE.md and README describe the anchor and the view | open | `_Docs/Markdowns/` | |
| D2 | One test drives the real Array QC entry and the real view constructor | open | `tests/` | |
