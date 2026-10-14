# Nanowell analysis — implementation plan and tracker

**This document is temporary.** It exists because this body of work spans
several pull requests and more than one session, and CLAUDE.md §6.1 requires
that a plan of this size live on disk rather than in conversation. It is
deleted by the pull request that closes its last row, and that pull request
says in its body that it is gone. Until then it is the plan *and* the record of
where the work stands.

It plans a new kind of analysis for All-Well: plates whose wells each hold a
molded array of roughly a thousand **nanowells**, imaged as a tiled grid of
fields, where the question is what happens *inside each nanowell* over time and
how that differs between plate wells. It also plans the two changes the
acquisition side needs first: the CZI converter recording where each tile was
taken and when, and the Concatenate tool merging separate acquisitions of one
plate into one timelapse.

---

## This document is live, and it leads the work

It is not a record of what was decided; it is the instruction for what happens
next, and it is wrong the moment it stops matching the tree.

1. **Read it before touching anything.** The first act of any session working
   on this is to open this file and find the first row that is not `done` or
   `declined` in the lowest-numbered pull request that is not yet merged. That
   row is the work.
2. **The row and the code move in the same commit.** Never a batch at the end
   of a session, never "once CI is green".
3. **What a row learns goes into the row.** A measurement, a refuted
   assumption, an answer from the user, a file that turned out to be somewhere
   else — it is written where the next reader will look, not left in a commit
   message.
4. **A finding is a row.** Anything a reviewer, a test failure, real data, or
   the tree itself turns up becomes a numbered row before it is acted on. Rows
   added this way go under the row that produced them and keep its letter
   (`N4a`, `N4b`).
5. **A row that turns out to be wrong is `declined` with the reason, never
   deleted.** A deleted row and a forgotten row look identical later.
6. **Report progress by reading this file, not by remembering.**
7. **The design sections are rewritten, not annotated.** When a measurement or
   an answer changes the design, the prose in §3 is rewritten to say what is
   now true, in the same commit. As each pull request merges, the part of §3 it
   implemented moves into `ARCHITECTURE.md` (which describes the tree as it is)
   and is replaced here by a one-line pointer to that section.

## How to read the rows

Every row has an id, a place, a state, and — once it is `done` — its evidence.

- **`todo`** — not started.
- **`wip`** — started, not finished. Never left across a session boundary
  without a note in the evidence column saying where it stopped.
- **`done`** — implemented **and** pinned by a test that was watched failing
  without the change (CLAUDE.md §7.7 #2). For a question row (section Q),
  `done` means answered, and the answer is written in the row.
- **`blocked`** — cannot proceed until a named row closes. The blocker is named.
- **`declined`** — examined and deliberately not done, with the reason written
  in the row and at the site.

**Counts are derived from the rows, never typed.** The states reach `grep`
through a variable, so the command cannot match itself:

```bash
F=_Docs/Markdowns/NANOWELL_TRACKER.md
for s in todo wip done blocked declined; do
  printf '%-9s %s\n' "$s" "$(grep -c "| \`$s\` |" "$F")"
done
```

---

## 1. What is being built

### 1.1 The assay, as reported

These are the user's statements. They outrank anything inferred below.

- Each plate well (a 96-well plate in the illustration) holds a molded array of
  nanowells. **The central nanowell is square; every other nanowell is
  circular. The square is always physically at the centre of the array.** The
  layout is the illustrated one: rows of circles offset by half a spacing (a
  hexagonal lattice) filling a circular area.
- Nanowells are usually **100 µm in diameter**; an alternative plate format has
  **50 µm** nanowells. Imaged at **10x**, about **1,100 nanowells per well** in
  the 100 µm format.
- **Each well is imaged as a 5×5 grid of tiles with 10 % overlap**, as set in
  the acquisition software. The tiles must be registered and assembled at the
  start of each well's analysis.
- **The CZI file records each tile's position.** The converter may need
  changing to record it.
- **In phase contrast, nanowells appear light and the lattice between them
  appears black, at every timepoint.** Air bubbles make nanowells look black
  instead of light. Because the grid is regular, **every nanowell's position
  must always be deduced and never skipped.**
- **The edge of the array may be clipped**, and that must not make the analysis
  fail.
- **The phase channel is used to find ("segment") each nanowell.**
- Cells are **15–30 µm**. **Every timepoint has phase contrast; only some have
  fluorescence. Label-free analysis is critical.**
- **The plate may be removed from the microscope between timepoints**, and the
  software must support that. Separate acquisitions will probably be merged
  across time with the Datasets tools.
- The goal: an addressing scheme anchored on the central square that lets the
  same nanowell be compared across timepoints (cell count, brightness), per-
  nanowell metrics such as fold change over time, and comparison of those
  metrics between plate wells over time — integrated into the existing app as
  cleanly as possible.

### 1.2 Numbers derived from those facts (inferred, not measured)

Arithmetic on the reported numbers, so later measurements have something to be
checked against. Each line states what it assumes. None of these values is
hard-coded anywhere: pixel size and tile positions are **read from the CZI**,
and the pitch, radius and orientation are **measured from the images**; these
are sanity bounds.

- **Pixel scale.** For illustration, a 6.5 µm camera pixel with no extra
  magnification gives 0.65 µm/px at 10x. A 100 µm nanowell is then about
  **154 px** across, a 50 µm one about **77 px**, and a cell **23–46 px** —
  close to Cellpose `cyto3`'s native 30 px scale. The real value comes from the
  CZI's scaling metadata (row R2).
- **Pitch.** A hexagonal lattice of pitch *p* has (√3/2)·*p*² of floor per
  site. 1,100 sites inside an array of radius 3.0 mm (a 96-well floor is about
  6.4 mm across) gives *p* ≈ **172 µm** for the 100 µm format, about 1.7× the
  diameter. The illustration draws narrower gaps; it is schematic, and the
  pitch is measured. If the 50 µm format's pitch scales with its diameter
  (~86 µm), the same array area holds about **4,400 sites**.
- **Rings.** With the square at the centre, a circular array of 1,100 sites
  reaches about **17–20 rings**; 4,400 sites reach about **35–40**.
- **Overlap is a per-axis length, and it decides the design.** With 10 %
  overlap, the overlap along an axis is 0.1 × the tile's extent on that axis.
  For every nanowell to lie wholly inside at least one tile, that overlap must
  be at least the nanowell diameter plus a margin for position error — call it
  *d* + 20 µm. So the tile must be at least **1.2 mm** along *both* axes for the
  100 µm format, and **0.7 mm** for the 50 µm format. A 2048-px sensor at
  0.65 µm/px (1.33 mm) passes for 100 µm with 13 µm to spare; a sensor whose
  short side images under 1.2 mm at 10x fails on that axis. The CZI says which
  (row T2); the design handles both (§3.2.4).
- **Coverage.** Five tiles at 10 % overlap span 4.6 tile extents: 6.12 mm for a
  1.33 mm tile, a little under a 6.4 mm well floor, so the outermost ring of a
  full-well array can be partly unimaged — one concrete source of the clipped
  edge the user reports.
- **Sites per tile.** About 69 for a 1.33 mm tile in the 100 µm format, so each
  tile carries enough nanowells to fit the lattice on its own.
- **Montage size.** At full resolution a 5×5 montage of 2048-px tiles is about
  9,400 px a side — ~89 Mpx, ~355 MB per channel in float32, per (well,
  timepoint), per worker. This is why the design never fuses tiles for analysis
  except where a nanowell straddles a seam (§3.2.4–3.2.5).
- **Data volume per timepoint.** A 2048² 16-bit tile is 8.4 MB uncompressed;
  96 wells × 25 tiles is about **20 GB per channel per timepoint**. This is the
  number behind question Q9: Concatenate writes a fresh copy of the whole
  dataset each time it appends one.

### 1.3 Scope

**In:** recording tile positions, pixel size and acquisition time at CZI
conversion; merging separate single-timepoint acquisitions of one plate into one
timelapse; tile registration; nanowell detection in phase; the addressing
scheme; per-nanowell label-free and fluorescence measurements; per-cell rows
tagged with their nanowell; per-nanowell tables; Analyze form settings and a
detection preview; a Review page for per-nanowell and per-well comparison over
time; and the incremental / remote / completeness / Datasets-tool plumbing every
output already has.

**Out, unless a row is added:** cell tracking between timepoints (nanowells are
compared as units, not cells), stitched full-resolution exports, a fine-tuned
segmentation model, positions recorded by the OME and ND2 converters (row I5
opens it), and any new third-party dependency.

---

## 2. Ground rules — the repository contracts this work is most at risk of breaking

### 2.1 The pipeline stays self-sufficient, and the manifest is complete

`tests/test_architecture_contracts.py::test_pipeline_is_self_sufficient` forbids
`pipeline/` from importing `review` / `widgets` / Qt. `tests/test_remote_manifest.py`
requires an entry in `services/remote_manifest.py` for **every** `.py` in
`pipeline/` and `util/`. The pipeline imports nothing from `domain/` today, so
anything both the pipeline and Review need (the address format, the acquisition
record) goes in `util/`, not `domain/`.

The CZI reader has a constraint of its own: a header scan runs in spawned
worker processes and must not import numpy
(`tests/test_a_scan_worker_imports_no_pixels.py`). Reading positions, scaling
and times is header work, so it stays numpy-free.

### 2.2 No new third-party dependency

numpy, scipy, scikit-image, tifffile and Cellpose are already in the pipeline's
environment (`process_microscopy.py:67-69`, `pipeline/io.py:40`,
`requirements-remote.txt`). Everything here is built from those. No OpenCV, no
scikit-learn (`profiles/full.toml` scopes it to Multivariate), no new model
weights.

### 2.3 Four line budgets have almost no headroom — measured, not assumed

Measured against `tests/test_monolith_budgets.py` on the base of this branch:

| File | Lines | Budget | Headroom |
|---|---|---|---|
| `process_microscopy.py` | 3676 | 3676 | **0** |
| `analyze/tab.py` | 737 | 737 | **0** |
| `services/remote_runner.py` | 1182 | 1183 | 1 |
| `services/remote_fetch.py` | 2049 | 2052 | 3 |
| `analyze/form.py` | 715 | 729 | 14 |

The CZI modules and the Datasets tools carry no line budget.

CLAUDE.md §4: ratchets only move down; a budget failure is answered by
extracting a cohesive module. **Every hook into these files therefore carries
an extraction**, and those extractions are rows of their own (R6, P1, P9, A1)
rather than surprises at CI time. Re-measure at the start of each pull request;
other branches move these numbers.

### 2.4 Undefined is not zero, degraded is visible, and nothing is skipped

CLAUDE.md §7. This rule shapes most of §3. Three distinctions must survive from
the measurement to the plot:

- **A nanowell that holds no cells** (`n_cells = 0`, a measurement) versus **a
  nanowell that could not be measured** (`n_cells` empty — under a bubble, not
  imaged, or clipped beyond the usable fraction).
- **A nanowell whose position was fitted to image evidence** versus **one whose
  position was predicted from the lattice** because it was not visible.
- **A timepoint whose addresses are anchored** versus **one where no origin
  could be established**, which gets no addresses at all rather than guessed
  ones, and says why.

The user's "never skipped" is honoured by the **roster**: every nanowell in the
array has a row at every timepoint, whatever its state. Rows are never dropped;
metrics inside a row are undefined when they cannot be measured.

The same rule governs merging across time: a timepoint whose real acquisition
time is unknown is never given an invented one (§3.0.3).

### 2.5 Tests must be able to tell a right answer from a wrong one

CLAUDE.md §7.8. For every behavioural row: name the wrong implementation that
would still pass, build the fixture so it does not, and before calling the row
`done`, make the breaking edit in a `git worktree` of HEAD and watch a named
test fail with an `AssertionError`. The fixtures are synthetic until real data
arrives (Q7), so each section also has a real-data row that is `blocked` until
it does.

### 2.6 Every section ends with a scientific-Python review

Before each pull request opens, a reviewer is pointed at the diff's numerics:
estimator choice, edge cases, dtype behaviour, whether a statistic means what
its label claims, and whether the fixtures could distinguish right from wrong.
Its findings become rows.

---

## 3. Design

### 3.0 From the microscope to one dataset

Because the plate leaves the microscope between timepoints, each timepoint is a
separate acquisition and a separate CZI. Two things must happen before the
nanowell analysis can run, and neither exists today.

#### 3.0.1 The converter records where and when each tile was taken

Today the CZI converter writes `<dataset>/in/<WELL>.zip` and nothing else
(`pipeline/czi_explode.py:301`, `datasets/czi_import/apply.py:109`). The reader
already parses each tile's X/Y start from the subblock directory
(`pipeline/czi_read.SubBlock.dims`) and can read a plane's acquisition time
(`czi_read.parse_acquisition_time`), but neither is written out, and the pixel
size (the document's `Scaling`) is not read at all. Once the converter
finishes, where each tile was taken is gone.

The converter will write an **acquisition record**, `in/acquisition.json`,
through a stdlib-only `util/acquisition_record.py` that the converter, the
pipeline, the Datasets tools and Review all share:

- per dataset: the source files, pixel size in µm, tile size in px, the
  objective if the file names one, and the declared tile overlap if the file
  states it;
- per (well, field, timepoint): the tile's mosaic position in px (subblock
  directory) and its stage position in µm (metadata), each when present;
- per timepoint: the absolute acquisition start, ISO 8601.

It lives at the dataset level rather than inside each well's archive because
Concatenate copies non-image archive members under their own names, and two
datasets' same-named record members would collide and fail its verification.
At the dataset level each tool merges it explicitly instead.

Which of those fields the user's ZEN version actually populates is not known
until real files are read (R8, blocked on Q7). In particular, whether mosaic
positions are absolute stage-derived coordinates or are re-based per file
decides whether they can be compared *across* acquisitions, which §3.4.3's third
fallback needs.

#### 3.0.2 Concatenate exists, and cannot merge these acquisitions yet

`datasets/concatenate/` appends one dataset's timepoints after another's. For
this use case it refuses at three separate points, all confirmed in code:

1. **Both datasets must already be analysed.** `datasets/core/dataset_paths.resolve_dataset`
   (`:99-104`) requires `in/` and `out/`, and `read_info` (`:109-111`) requires
   `out/pipeline_info.json`. A freshly converted acquisition has neither.
2. **Dataset 1 must have at least two timepoints.** `concatenate_compute._interval_hours`
   (`:233-240`) refuses a single-timepoint dataset 1, and every acquisition here
   is one timepoint. The very first merge — t0 plus t1 — is refused.
3. **The time offset is guessed, not read.** `compute_offset` (`:262-272`)
   places dataset 2's first timepoint at dataset 1's last plus dataset 1's
   median interval. For acquisitions taken whenever the plate was put back,
   that invents the elapsed time.

It also writes a fresh destination every time and leaves both sources
untouched, so a timelapse built one timepoint at a time is copied in full at
every step (Q9).

#### 3.0.3 What Concatenate will do

- **Use recorded time when both datasets carry it.** Dataset 2 is offset by the
  difference between the two acquisitions' recorded starts; its labels are named
  by `pipeline.timepoints`' existing naming rules (which already refuse to round
  a reading further than its jitter allows) and rendered in dataset 1's format.
  An overlap is refused. The median-interval rule stays exactly as it is for
  datasets without records, including its refusal of a single-timepoint
  dataset 1 — there it is still the only information available.
- **Accept unanalysed datasets** — `in/` plus an acquisition record, no `out/`.
  Compatibility is then checked from the two records (filename schema, channels,
  pixel size, tile size, grid), and raw + raw gives a raw destination.
- **Accept analysed + unanalysed.** The destination keeps dataset 1's outputs,
  and dataset 2's timepoints are left for an incremental Analyze run, which
  processes exactly those (incremental runs already skip done units).
- **Merge the acquisition record**, keeping each timepoint's own positions —
  after the plate was put back they differ.
- **Optionally, merge several datasets in one run**, ordered by recorded start,
  which is one copy instead of one per added timepoint (M7).

#### 3.0.4 The workflows this gives

- **All timepoints already acquired:** convert each CZI → Concatenate them in
  order (or once, with M7) → run Analyze once.
- **An experiment still running:** day 0, convert and analyse. Each later day,
  convert the new CZI → Concatenate (analysed dataset + new raw acquisition) →
  run Analyze incrementally; only the new timepoint is processed.

### 3.1 The pipeline's unit of work becomes (well, timepoint)

Today the pipeline's unit of work inside a well is the **FOV-group**: one
nuclear image plus its fluorescence channels, processed independently
(`process_microscopy._process_groups_in_worker`). Incremental runs, resume
checkpoints and failure traces are all keyed on `(fov, timepoint)`.

A nanowell analysis cannot work field by field: the address of a nanowell in
field 7 depends on where the square is, which may be in field 13. So in
nanowell mode the unit becomes **one well at one timepoint — all 25 tiles
together**:

```
for each timepoint of the well:
    tiles complete?  ──no──▶ status "incomplete_grid"; nothing processed; rerun picks it up
        │yes
    place tiles from the acquisition record, then register (3.2)
        ─▶ detect lattice + nanowells in phase (3.3)
        ─▶ anchor addresses on the square (3.4) ─▶ classify every site (3.5)
        ─▶ measure contents, label-free first (3.6) ─▶ write rows (3.7)
after every well:
    re-anchor any (well, timepoint) left unaddressed, from the plate's other wells (3.4.3)
```

The surrounding well lifecycle is reused unchanged — extracting the well's
archive, writing the per-well CSV, zipping outputs, cleaning up
(`process_well_zip_task` / `process_well_folder_task`). Only the inner
`_process_groups_in_worker` call is swapped for the nanowell task body in a new
module, `pipeline/nanowell_run.py`, because `process_microscopy.py` has no
headroom (§2.3).

### 3.2 Tile layout and registration

#### 3.2.1 Which tile is where

For CZI data, from the acquisition record: each tile's recorded position, in
µm via the recorded pixel size. The overlap is *measured* from those positions
along each axis and compared with the declared 10 %; a large disagreement is
reported, because one of the two is wrong about the acquisition.

For data with no record (loose TIFFs, until the OME and ND2 converters record
positions too), the grid position comes from a **scan pattern** setting —
raster or serpentine, row- or column-major, starting corner — and a nominal
overlap.

A timepoint with fewer than rows×cols tiles is **not** processed into a partial
montage: it is reported as `incomplete_grid`, naming the missing tiles, and an
incremental rerun processes it once the scan is complete.

#### 3.2.2 Three independent sources of tile position

1. **Recorded.** The acquisition record's position (or the scan pattern's,
   without one). Accurate to the stage's repeatability.
2. **Lattice phase.** Every tile sees the same physical lattice. Fitting the
   lattice in a tile gives the sub-pixel position of its sites, which fixes the
   tile's offset **modulo one lattice vector**. The integer number of lattice
   vectors comes from the recorded position, which works whenever that is
   accurate to under half a pitch (~85 µm for the 100 µm format, ~43 µm for the
   50 µm one) — far above a stage's error within one acquisition. This uses
   every nanowell in the tile, not just the overlap strip, and is unaffected by
   bubbles in the overlap.
3. **Overlap correlation.** Phase correlation of the overlap strip between
   neighbours, using cells, debris and the well wall, which break the lattice's
   periodicity.

#### 3.2.3 The periodicity trap

The nanowell array is periodic, so an unconstrained correlation of two
overlapping tiles has a peak at the true shift **and at every lattice vector
from it**, and can lock onto the wrong one with high confidence. Overlap
correlation is therefore searched only within ±pitch/2 of the recorded offset.
The test fixture for this (T4) is built so that the unconstrained search *does*
lock one pitch off — otherwise the test cannot tell the constrained search from
the unconstrained one.

All pairwise constraints are combined in a weighted least-squares solve. Each
tile records which source placed it (`lattice`, `overlap`, or `recorded`) and
its residual. Corner tiles that hold little or no array (a 5×5 grid over a
circular well) fall back to the recorded position and say so; they never fail
the solve.

#### 3.2.4 Ownership: every nanowell is measured in one place

Where the overlap along both axes is at least the nanowell diameter plus the
margin (§1.2), every nanowell lies wholly inside at least one tile. Each is then
**owned** by one tile — the one that contains its disk with margin,
nearest-centre on a tie — and is measured only there. That one rule does three
things:

- no pixel fusion is needed for analysis;
- a cell in an overlap is counted once, because only cells inside sites the
  tile owns are kept;
- cells stay in tile coordinates with tile-local label ids, so the existing
  cell identity `(well, fov, timepoint, nucleus_id)`
  (`domain/image_identity.py`), the per-tile `_labels` images and every Image
  Browser surface keep working unchanged.

With 10 % overlap this holds for the 50 µm format on any ordinary 10x camera,
and for the 100 µm format only when the tile images at least ~1.2 mm on both
axes (§1.2). So the **fused-site path is planned, not hypothetical** (T9): a
site that no single tile wholly contains is measured from a crop fused from the
two to four tiles covering it; its cells are attributed to the tile holding most
of its disk; and its row says `fused`. Which sites need it is computed from the
measured overlap per axis, and the Analyze preview states how many.

#### 3.2.5 The montage is built for looking, not for measuring

The user's "assemble the well first" is honoured by a **downsampled montage**
of the phase channel per (well, timepoint) — long side at most 4,096 px,
blended in overlaps, written into the well's output archive as a new image
role. It is what the detection preview, the Array QC view and the Image Browser
show. Analysis reads the full-resolution owner tile (or the fused crop, for a
seam site) instead, for the memory reason in §1.2. A full-resolution montage
export stays out of scope (§1.3).

### 3.3 Finding nanowells in the phase channel

The phase channel is the existing `--phase_token`. Nanowells are **light disks
on a black lattice** at every timepoint; a bubble turns a nanowell black, so an
occluded nanowell is a black disk on the black lattice.

1. **Lattice first, nanowells second.** The 2-D Fourier transform of the
   downsampled montage has peaks at the reciprocal lattice, which give the
   lattice's pitch and angle from the whole well at once. This survives a bubble
   covering a hundred nanowells, because those hundred contribute nothing and
   the other thousand still do.
2. **Candidates** are found by a matched filter — a light disk on a dark ground,
   at the radius of the selected **plate format** (100 µm by default, 50 µm, or
   a custom diameter) converted to pixels with the recorded pixel size — and the
   lattice is refined by least squares on the candidates that sit on lattice
   sites.
3. **Per-site refinement** at full resolution fits each nanowell's centre and
   radius from its radial edge profile. A site where the fit succeeds is
   `geometry = fitted`; one where it does not (a bubble, debris, a clipped edge)
   takes the lattice position and the well's median radius and is
   `geometry = predicted`. **No site is ever dropped for lack of evidence.**
4. **Per-site focus** comes free: the nanowell rim is a strong, identical edge
   in every site, and `pipeline.field_quality.edge_focus(image, mask)` already
   measures transition width at an outline. A nanowell's focus is therefore
   measured independently of whether it holds cells.

### 3.4 The addressing scheme

#### 3.4.1 Coordinates

Each nanowell gets two integers `(q, r)` in the lattice's own grid, with the
square at `(0, 0)`:

```
 r=+1      (-1,+1)   ( 0,+1)
 r= 0  (-1, 0)  [ 0, 0]  (+1, 0)        [ ] = the square nanowell
 r=-1      ( 0,-1)   (+1,-1)
```

- `q` counts along the lattice direction nearest image east; `r` along the
  direction 60° counter-clockwise from it (as seen on screen). Position =
  origin + q·a₁ + r·a₂.
- **Ring** `k = (|q| + |r| + |q + r|) / 2` — the number of steps from the
  centre. Ring *k* holds 6*k* sites.
- **Label** `k.i`, where *i* runs 0 … 6*k* − 1 counter-clockwise from east.
  `(q, r)` is the join key everywhere; the label is for people. The pair is
  stored, the label is derived, and `util/nanowell_address.py` holds both
  directions as exact inverses.
- The scheme is the same for both plate formats; the 50 µm format simply has
  more rings.
- A square lattice, if one is ever used, keeps the same integer coordinates;
  only the ring rule changes (`max(|i|, |j|)`, 8*k* per ring).

#### 3.4.2 Why orientation is unambiguous

The square looks the same every 90° and the hexagonal lattice every 60°; the
only rotation that preserves both is 180°. A plate sits in its holder by its
chamfered A1 corner and cannot be 180° out, so with a placement tilt under 30°
the direction nearest east is unambiguous — including after the plate has been
taken out and put back. A camera that mirrors the image mirrors every image the
same way and is a single setting.

#### 3.4.3 Where the origin comes from, at every timepoint

Because the plate is removed between timepoints, **every timepoint is anchored
on its own**; nothing is carried over from a previous timepoint on the
assumption that the plate stayed put. The origin is resolved by a fixed chain,
and the method used is recorded on every (well, timepoint):

1. **Fiducial** — the central-region site whose shape scores most square-like
   (square-versus-circle template at the lattice's angle), beating the
   runner-up by a set margin.
2. **Array centre** — the square is always at the centre of the array, so a
   circle fitted to the outermost sites (a partial arc is enough) gives the
   centre, rounded to the nearest site.
3. **The rest of the plate** — a plate is rigid, so putting it back moves every
   well by the same rotation and translation. Fitted to the wells anchored at
   both timepoints, that transform predicts where a hidden square must be; the
   lattice phase supplies the sub-pitch part exactly, so the prediction only
   has to be right to within half a pitch. This runs as an end-of-run pass after
   every well, and needs stage positions comparable across acquisitions (R8
   says whether the CZI provides them).
4. **Otherwise the (well, timepoint) is `unaddressed`**, with the reason, and
   no address is written for it.

When the fiducial and the array centre are both available they must agree; a
disagreement is recorded (`origin_conflict`) and shown in the Array QC view
rather than resolved silently.

#### 3.4.4 The roster

The set of sites that exist in a well is defined by the array model — every
lattice site whose centre lies inside the array boundary — not by which sites
happened to be detected. It is therefore the same at every timepoint of the
well, whatever the bubbles do and however the plate was put back, and a
timepoint's rows are always the full roster. Within one well, pitch (±0.5 %),
angle (±0.5° after removing the plate's measured rotation) and roster must agree
across timepoints; a violation is a named status on the timepoint, not an
exception.

### 3.5 What state a site can be in, and which numbers it may carry

| State (per site, per timepoint) | Set when | Position | Contents metrics |
|---|---|---|---|
| measured | imaged, visible, geometry fitted or predicted | yes | yes |
| empty | measured, no cells found | yes | `n_cells = 0`, `confluence = 0` |
| occluded | `visible_fraction` below the setting (bubble, debris) | yes | empty (undefined) |
| clipped | `imaged_fraction` below the setting (array edge, well wall, coverage) | yes | empty (undefined) |
| partially visible | between the two thresholds and 1 | yes | yes, with the fraction recorded |
| fused | no single tile wholly contains it; measured from a fused crop | yes | yes, flagged |
| unaddressed timepoint | no origin (§3.4.3) | tile coordinates only | yes, but no address, so not joinable over time |

`visible_fraction` and `imaged_fraction` are stored on every row, so a
downstream filter can be stricter than the pipeline's default.

### 3.6 What is inside each nanowell — label-free first

Phase contrast is always present and is the primary readout; fluorescence adds
to it when present.

1. **Empty-nanowell template.** Every nanowell in a well is the same molded
   shape, so the rim's phase halo and the illumination inside a nanowell are
   nearly identical from site to site. A per-(well, timepoint) template is the
   median of the aligned crops of the least-textured quarter of visible sites;
   when too few remain (a confluent late timepoint, or the 50 µm format at high
   loading), the well's earliest template is used, and which one was used is
   recorded. The earliest template is aligned by the lattice, so it remains
   valid after the plate is put back.
2. **Rim-suppressed phase image.** Each site's crop is divided by the template;
   everything outside the sites is set flat. The segmenter then sees cells on a
   flat floor rather than cells beside a bright ring — the main source of false
   cells along a nanowell's edge. The image is optionally written as an output
   role so it can be inspected.
3. **Confluence** — the fraction of a site's interior covered by cells — from
   local texture on the rim-suppressed image, thresholded against the texture
   of the empty sites. This needs no instance segmentation and is the most
   robust label-free number when cells clump. In the 50 µm format a few cells
   fill a nanowell, so confluence saturates early and the count carries more of
   the information.
4. **Cell count** from Cellpose's label-free path (`pipeline.cellpose._segment_cellpose`,
   `cyto3`, diameter from the cell-size setting) on the rim-suppressed owner
   tile: one call per tile, as today, keeping only cells whose centroid lies in
   an owned site's interior. Fused sites are segmented on their fused crop.
5. **Area-based estimate** — foreground area divided by the median area of
   isolated cells — stored beside the count, with a `count_disagreement` flag
   when the two diverge, because a clump segmented as one cell undercounts
   silently otherwise.
6. **Fluorescence, when present**: the existing per-cell intensities, plus a
   per-site integrated and mean intensity over the interior minus the empty-site
   background — defined even where segmentation fails. A timepoint without a
   fluorescence channel has those columns empty, not zero.

### 3.7 Outputs

| Output | Contents |
|---|---|
| `in/acquisition.json` (new, written by the converter) | §3.0.1 |
| per-cell CSV (`<prefix>_<well>.csv`, existing) | unchanged columns, `fov` = owner tile, plus `nw_q`, `nw_r`, `nw_ring`, `nw_label`, `well_x_um`, `well_y_um`. The new columns exist only in nanowell mode — absent, not blank, otherwise |
| `nanowells/<well>.csv` (new) | one row per (timepoint, roster site): address, well-frame position (µm), owner tile and tile-local centre and radius (px), `geometry`, `fused`, `imaged_fraction`, `visible_fraction`, state, `focus`, `n_cells`, `confluence`, `area_est_cells`, `count_disagreement`, per-channel site intensities |
| `nanowells/<well>_grid.json` (new) | per timepoint: tile positions with source and residual, measured overlap per axis, lattice pitch and angle, origin method and agreement, array radius, roster size, status and reasons |
| `<well>_out.zip` (existing) | per-tile `_labels` as today; new roles for the downsampled montage and (optionally) the rim-suppressed tiles, declared in `domain/image_identity.ROLE_CATALOG` |
| `pipeline_info.json` (existing) | an `acquisition` block (pixel size, tile size) so Review can draw in µm, and a `nanowell` block: plate format, settings, conventions (east axis, counter-clockwise, mirror), and a scheme version so Review can refuse a scheme it does not understand |

The tables live in a `nanowells/` subdirectory because Review loads every
top-level `*.csv` as a well's cell table (`review/core/load_controller.py:402`).

### 3.8 Incremental runs, resume, completeness, remote, Datasets tools

- **Incremental.** The done unit is a (well, timepoint) present in the well's
  nanowell table with a terminal status. Every nanowell setting joins the reuse
  key, with a row in `tests/test_reuse_is_keyed_completely.py` per setting —
  otherwise turning nanowell mode on over an existing dataset finds every
  timepoint "done" and writes no table. This is also what makes the daily
  Concatenate-then-Analyze workflow (§3.0.4) process only the new timepoint.
- **Resume.** Checkpoints are per (well, timepoint) (`pipeline/fov_checkpoint.py`).
- **Completeness.** `pipeline/completeness.py` is the one definition of "is this
  well complete", shared with the Datasets Validate tool; nanowell mode extends
  it there, so Validate and the run agree.
- **Remote.** New modules join the manifest; the acquisition record is uploaded
  with the input; the upload planner keeps a timepoint's tiles together (a
  partial upload is an `incomplete_grid`); the fetch brings `nanowells/` back.
  Both remote files have ≤ 3 lines of headroom.
- **Datasets tools.** Subset, Merge Plates and Concatenate carry the acquisition
  record (filtered, well-remapped, timepoint-shifted respectively) and, once the
  pipeline writes them, the `nanowells/` tables; Validate checks both against
  the archives. A tool that cannot carry them refuses with a reason rather than
  dropping them.

### 3.9 Analyze form

A **Nanowells** section (its own module, because `analyze/tab.py` has no
headroom): enable, plate format (100 µm / 50 µm / custom diameter), cell
diameter, occlusion and clipping thresholds — and, only for data with no
acquisition record, grid rows × columns, scan pattern, overlap and pixel size.
Its centrepiece is **Test detection**: run registration and detection on one
(well, timepoint) in a worker and show the montage with every site labelled,
occluded, clipped and fused sites marked, the origin method, the measured
overlap per axis, and a few rim-suppressed crops with their segmentation.
Nobody should start a plate-wide run without seeing the addresses land.
Preflight reports missing tiles, a missing acquisition record, an overlap
smaller than a nanowell (with the number of seam sites it affects), and a
missing phase channel.

### 3.10 Review

#### 3.10.1 Per-nanowell metrics

Nanowells are joined across timepoints on `(well, q, r)`. Undefined is `NaN`,
never zero, and every undefined case is listed:

| Metric | Definition | Undefined when |
|---|---|---|
| count fold change | n(t) / n(baseline) | n(baseline) = 0 — reported as its own "seeded later" class — or either timepoint unmeasurable |
| log₂ count fold change | log₂((n + 1) / (n₀ + 1)); the +1 is printed in the axis label | either timepoint unmeasurable |
| confluence fold change | c(t) / c(baseline) | c(baseline) = 0, or unmeasurable |
| brightness fold change | mean per-cell intensity, t over baseline | no cells at either, or no fluorescence at either timepoint |
| site-signal fold change | site-integrated intensity, t over baseline | unmeasurable, or no fluorescence, at either |
| growth rate | slope of ln n against recorded time | fewer than three measurable timepoints with n > 0 |
| occupancy class | 0 / 1 / 2 / 3+ cells at baseline | baseline unmeasurable |

Time is the recorded elapsed time from §3.0.3, so a growth rate over unevenly
spaced acquisitions is a rate per real hour.

**Baseline**: the earliest timepoint by default; a site unmeasurable there has
no fold change. An explicit, labelled alternative uses each site's first
measurable timepoint, because a bubble that clears after t₀ is common.

#### 3.10.2 Comparing plate wells

For each (well, timepoint), the distribution of a metric over the well's sites
is summarised — median, interquartile range, fraction responding, fraction
occupied — with **the number of sites used, and the number excluded by reason,
printed on the figure**. Replicate groups and variables come from the existing
Sample Definitions. Normalising to control wells follows `fold_change.py`'s
rule that numerator and denominator use the same statistic.

**Wells are the replicate unit, not nanowells.** A thousand nanowells in one
well share its medium, seeding and handling, so treating them as independent
replicates in a well-versus-well test would report confidence nobody has. Site
distributions are drawn; statistics are on wells.

#### 3.10.3 The Nanowells page

A new **Analysis** rail entry beside Spatial, `review/features/nanowell/`, laid
out like Spatial (state / collect / compute / controller / renderer / tab_view).
It is not a Plotting sub-tab: Plotting is built around one aggregate per well,
and joining it means the eight-roster checklist in ARCHITECTURE's *Adding a
Plotting leaf* plus `fold_change`'s tuple model, for a page whose unit is a
nanowell's trajectory.

- **Array map** — every roster site of one well, coloured by a metric at a
  timepoint, with a time slider; occluded sites hatched, clipped outlined.
- **Site detail** — click a site: its trajectory, and its crop at every
  timepoint from that timepoint's owner tile.
- **Trajectories** — one line per site with the well median over them,
  coloured by occupancy class.
- **Well comparison** — per-well or per-group distributions at a timepoint;
  median and spread over time.
- **Position effects** — metric against ring, as an edge-effect check.
- **Array QC** — the montage with lattice, tile seams, each tile's placement
  source, fused sites, the origin method per timepoint, and every status the
  pipeline recorded.
- **Export** — a long-format CSV of every site, timepoint and metric.

It joins the shared machinery every page joins: the Properties panel, Saved
Plots, its own persistence block reset on dataset switch, build profiles, help
topics, and the one-line-removable feature test.

---

## 4. The pull-request stack

Eleven pull requests, each based on the one below it, so each diff contains
only its own section. Each leaves `main` working if merged alone: the
acquisition PRs (2, 3) are useful on their own for any separately acquired
timelapse; the geometry and image PRs (1, 4–6) add modules whose only callers
are their tests; nothing nanowell-specific is user-visible until PR 7.

| PR | Branch | Base | Section | Delivers | Version |
|---|---|---|---|---|---|
| 1 | `claude/nanowell-1-geometry` | `main` | G | this tracker; addressing; lattice fitting on point sets; origin chain; plate-rigid transform; roster; ownership | patch |
| 2 | `claude/nanowell-2-acquisition` | PR 1 | R | the acquisition record; the CZI converter writes it; Subset, Merge Plates, Validate and remote carry it | patch |
| 3 | `claude/nanowell-3-merge-time` | PR 2 | M | Concatenate: recorded-time offsets, single-timepoint and unanalysed datasets, record merging | patch |
| 4 | `claude/nanowell-4-tiles` | PR 3 | T | recorded placement; three-source registration; periodicity guard; fused-site crops; montage preview | patch |
| 5 | `claude/nanowell-5-sites` | PR 4 | N | phase-channel detection for both formats; fiducial; per-site refinement; occlusion; clipping; focus | patch |
| 6 | `claude/nanowell-6-contents` | PR 5 | C | template; rim suppression; confluence; label-free count; fluorescence | patch |
| 7 | `claude/nanowell-7-run` | PR 6 | P | the (well, timepoint) task; end-of-run re-anchoring; CLI; outputs; incremental; resume; completeness; remote; Datasets tools carry `nanowells/` | patch |
| 8 | `claude/nanowell-8-analyze` | PR 7 | A | Analyze section; Test detection preview; preflight | patch |
| 9 | `claude/nanowell-9-review-data` | PR 8 | D | loader; per-site and per-well metrics; export; persistence | patch |
| 10 | `claude/nanowell-10-review-page` | PR 9 | V | the Nanowells page and its views | minor (new page) |
| 11 | `claude/nanowell-11-integration` | PR 10 | I | optional: Image Browser, Spatial, Plotting, Batch Export hooks; OME / ND2 records | patch |

PRs 2–3 do not depend on PR 1's code, and PRs 8 and 9 depend only on PR 7; all
are stacked linearly anyway so there is one retargeting chain to manage. If PR 1
stalls in review, PRs 2–3 can be rebased onto `main` and opened as their own
stack before anything is pushed above them.

### 4.1 Rules for the stack

These come from CLAUDE.md §6, where each was learned the hard way.

1. **Each PR body states its retargeting condition when it is opened**:
   *"This targets `claude/nanowell-N-…` rather than `main`; once that merges,
   retarget this to `main` before merging it."* GitHub retargets automatically
   only when the base branch is deleted.
2. **Check the base before merging, not after.** After a merge, verify the work
   reached `main` with `git cat-file -e origin/main:<a file the PR added>`; the
   merge notification says "merged" either way.
3. **Open each PR when its section's rows are done**, not before — pushing to a
   branch with no PR is free, and every push to an open PR bills a CI run.
   While PR N is in review, work continues on PR N+1's branch.
4. **Review fixes to PR N reach the branches above it by merge**, never rebase,
   once anything above has been pushed: merge PR N's branch into N+1, then N+1
   into N+2, one push per branch per round.
5. **Versions.** Each PR bumps exactly one increment over its base. On a stacked
   branch `scripts/preflight.sh` compares against `origin/main` and reports more
   than one; that is a property of the check, and the stacked number is not
   "corrected" (CLAUDE.md §6). If `main` takes the next number first, the PR
   being merged re-bumps against `main` at merge time and the PRs above follow.
6. **Never `checkout -B` a branch whose PR is open.**
7. **Before each push, fetch `main` and read `git log origin/main -- <files
   this section touches>`.** Another session may be in the same files; a clean
   merge is not evidence the two changes agree.
8. **Docs move with the code.** Each PR's close-out row updates the
   `ARCHITECTURE.md` section it made true, and `README_WellViewer.md` when a
   user can see the change, then trims §3 here to a pointer.

---

## 5. Rows

### Section Q — Questions for the user

Answers are written into the row. Rows that depend on an answer say so.

| # | Question | Default until answered | State | Answer / evidence |
|---|---|---|---|---|
| Q1 | Pixel size at 10x and tile size in pixels | — | `done` | Read from the CZI's scaling metadata and subblock sizes (R2); no user input needed. Real values recorded in R8 |
| Q2 | Tile overlap | — | `done` | 10 %, set in the acquisition software. Whether that clears a 100 µm nanowell depends on the tile's extent per axis (§1.2); measured per dataset (T2) |
| Q3 | How the 25 tiles are numbered and where each sits | — | `done` | The CZI records each tile's position; the converter will record it (R2–R3). The scan-pattern setting remains only for data with no record |
| Q4 | Is the square always at the centre of the array? | — | `done` | Yes, physically. Origin fallback 2 (§3.4.3) relies on it |
| Q5 | Is the plate taken off the microscope between timepoints? | — | `done` | It can be, and must be supported. Every timepoint is anchored on its own; acquisitions are merged across time with Concatenate (§3.0) |
| Q6 | What does "sometimes smaller" mean? | — | `done` | Nanowells are usually 100 µm; an alternative plate format has 50 µm nanowells. Plate format becomes a setting (§3.3, §3.9) |
| Q7 | Sample data: CZIs of 2–3 wells at 2+ timepoints with the plate removed between them, including a bubble, a clipped edge, and if possible a hidden square; one plate of each format if available | blocks every real-data row | `todo` | |
| Q8 | How do nanowells look in phase? | — | `done` | Light nanowells on a black lattice, at every timepoint (§3.3) |
| Q9 | How many timepoints, channels and wells does a typical experiment have, and is a full copy of the dataset per added timepoint acceptable (~20 GB per channel per timepoint for 96 wells, §1.2)? | keep Concatenate's fresh-copy rule | `todo` | |
| Q10 | Is each timepoint saved as one CZI holding every well, or as a Split Scenes folder of per-well CZIs? Is the same ZEN experiment (same tile plan) reused at every timepoint? | either is read; same plan assumed | `todo` | |

### Section G — Geometry and addressing (PR 1)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| G1 | Address type `(q, r)`, ring, label `k.i` and its parser as exact inverses; within-ring ordering counter-clockwise from east; square-lattice variant | `util/nanowell_address.py` | `todo` | |
| G2 | Lattice fit from a point set: basis vectors from nearest-neighbour displacement clustering, robust to 30 % missing points and outliers; returns pitch, angle, RMS residual, inlier mask | `pipeline/nanowell_lattice.py` | `todo` | |
| G3 | Index points to integer `(q, r)` with sub-pixel residual; points more than a quarter pitch from any site are outliers, not forced onto one | `pipeline/nanowell_lattice.py` | `todo` | |
| G4 | Basis convention (a₁ nearest east, a₂ +60° on screen) under rotations −25°…+25° and a mirror flag | `pipeline/nanowell_lattice.py` | `todo` | |
| G5 | Origin chain (fiducial → array centre → plate-rigid prediction → `unaddressed`), recording method and agreement; never guesses | `pipeline/nanowell_lattice.py` | `todo` | |
| G6 | Array-boundary circle fit from a partial arc; roster = sites inside; roster identical for shifted observation windows of one array | `pipeline/nanowell_lattice.py` | `todo` | |
| G7 | Ownership per axis: owner tile per site, and the set of sites no tile wholly contains (the fused set), from tile rectangles and the nanowell diameter | `pipeline/nanowell_lattice.py` | `todo` | |
| G8 | Plate-rigid transform between two timepoints from the anchored wells' origins in stage coordinates (rotation + translation, robust to outlier wells); prediction for a well whose square is hidden | `pipeline/nanowell_lattice.py` | `todo` | |
| G9 | Synthetic point-set generator: hex array in both formats, central square, circular boundary, bubble holes, rotation, translation, a plate moved between timepoints | `tests/_nanowell_synth.py` | `todo` | |
| G10 | The discriminating fixture: two observations of one array, the second shifted 1.5 pitches and rotated 3°, window centre not on the square — every site keeps its address. Mutation: origin = "site nearest window centre" must fail it | `tests/test_nanowell_addressing.py` | `todo` | |
| G11 | Plate-rigid fixture: a plate moved by more than a pitch between timepoints with one well's square hidden; G8 recovers its address. Mutation: carry the previous timepoint's origin unchanged — must fail | `tests/test_nanowell_addressing.py` | `todo` | |
| G12 | Manifest entries for the new `util/` and `pipeline/` modules; confirm no guard rejects a module whose only importer is tests | `services/remote_manifest.py` | `todo` | |
| G13 | Close-out: guard family, ruff, touched tests, scientific review, ARCHITECTURE section for the addressing, version, PR opened with stack note | — | `todo` | |

### Section R — The acquisition record (PR 2)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| R1 | Record format, reader and writer (stdlib only, atomic write through `util/json_write`, a format version); validation against the dataset's archives (a record naming wells or fields that are not there, or missing ones that are, is reported) | `util/acquisition_record.py` | `todo` | |
| R2 | CZI reader exposes pixel size (`Scaling`), each tile's mosaic X/Y from the subblock directory, stage positions from the metadata, and each timepoint's absolute start — numpy-free (`tests/test_a_scan_worker_imports_no_pixels.py`) | `pipeline/czi_read.py` | `todo` | |
| R3 | The CZI exploder writes the record with the wells it converted, in the same publish; a resumed or partial conversion records exactly the wells it wrote | `pipeline/czi_explode.py`, `datasets/czi_import/apply.py` | `todo` | |
| R4 | Split Scenes sets (`pipeline/czi_set.py`): the record is assembled across the set's files | `pipeline/czi_set.py` | `todo` | |
| R5 | Subset filters the record to the kept wells and timepoints; Merge Plates remaps its wells; Validate recognises it and checks it against the archives | `datasets/subset/`, `datasets/merge_plates/`, `datasets/validate/` | `todo` | |
| R6 | Remote: the record is uploaded with the input and read by the remote pipeline; extraction to pay for any line in `services/remote_runner.py` | `services/` | `todo` | |
| R7 | The CZI import page states what was recorded (pixel size, positions present, start time) so a file that lacks them is visible before analysis | `datasets/czi_import/view.py` | `todo` | |
| R8 | Real data: which fields the user's ZEN version populates, and whether mosaic or stage positions are comparable across separate acquisitions (decides whether G8's fallback can run) — recorded here | — | `blocked` | Q7 |
| R9 | Close-out: README CZI import section, ARCHITECTURE, version | — | `todo` | |

### Section M — Merging acquisitions across time (PR 3)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| M1 | Recorded-time offset when both datasets carry a record: dataset 2 offset by the difference of recorded starts, named by `pipeline.timepoints`' rules, rendered in dataset 1's format; overlap refused; the median-interval rule unchanged for datasets without records | `datasets/concatenate/concatenate_compute.py` | `todo` | |
| M2 | Single-timepoint dataset 1 accepted when M1 applies; still refused otherwise | `datasets/concatenate/concatenate_compute.py` | `todo` | |
| M3 | Unanalysed datasets (`in/` + record, no `out/`): compatibility from the records; raw + raw gives a raw destination; verification checks images rather than rows | `datasets/concatenate/`, `datasets/core/dataset_paths.py` | `todo` | |
| M4 | Analysed + unanalysed: destination keeps dataset 1's outputs; a test runs an incremental Analyze on the result and checks that exactly dataset 2's timepoints are processed | `datasets/concatenate/` | `todo` | |
| M5 | Record merge: dataset 2's timepoint keys shifted; each timepoint keeps its own positions and absolute start | `datasets/concatenate/apply.py` | `todo` | |
| M6 | The discriminating fixture: dataset 1 at 0 h and 24 h, dataset 2 recorded 30 h after dataset 1's start — the old rule says 48 h, the new one 30 h. Mutation: revert to the interval rule — must fail | `tests/test_concatenate_compute.py` | `todo` | |
| M7 | Several datasets in one run, ordered by recorded start (one copy instead of one per added timepoint) | `datasets/concatenate/` | `todo` | |
| M8 | The copy cost at the user's scale, and whether an append that avoids a full copy is needed — a layout question, because a zip archive cannot be extended atomically without being rewritten | — | `blocked` | Q9 |
| M9 | Close-out: README Concatenate section rewritten for the new rules (including the refusal it no longer makes), ARCHITECTURE §6b, version | — | `todo` | |

### Section T — Tiles and registration (PR 4)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| T1 | Placement from the acquisition record; scan-pattern placement (16 patterns) for data without one; a timepoint short of rows×cols tiles is `incomplete_grid` naming the missing tiles | `pipeline/tile_grid.py` | `todo` | |
| T2 | Overlap measured per axis from positions; compared with the declared overlap; ownership condition evaluated per axis; fused-site count reported | `pipeline/tile_grid.py` | `todo` | |
| T3 | Lattice-phase registration: offset modulo the lattice from each tile's fit; integer part from the recorded position; reports when the recorded error approaches half a pitch | `pipeline/tile_register.py` | `todo` | |
| T4 | Overlap correlation constrained to ±pitch/2 of the recorded offset. Fixture built so the unconstrained search locks one pitch off — the test must fail if the constraint is removed | `pipeline/tile_register.py` | `todo` | |
| T5 | Weighted least-squares solve; per-tile source and residual; corner tiles with no array fall back to the recorded position without failing | `pipeline/tile_register.py` | `todo` | |
| T6 | Scan-pattern auto-detection | — | `declined` | The CZI records positions (Q3), so CZI data never needs it; data without a record uses the explicit setting, and a guessed layout that scores well is still a guess |
| T7 | Downsampled phase montage (long side ≤ 4,096 px), blended in overlaps, 8-bit with the stretch recorded | `pipeline/tile_register.py` | `todo` | |
| T8 | Synthetic tile renderer: G9's array rendered into 5×5 tiles at 10 % overlap with stage jitter, camera rotation, vignetting, noise, cells, debris and a bubble; one tile geometry where 100 µm sites all have owners and one where they do not | `tests/_nanowell_synth.py` | `todo` | |
| T9 | Fused-site crops: the covering tiles blended into one crop per seam site; cells attributed to the tile with most of the disk; row flagged `fused` | `pipeline/tile_register.py` | `todo` | |
| T10 | Real-data check of registration residuals and measured overlap on Q7 data | — | `blocked` | Q7 |
| T11 | Close-out (as G13) | — | `todo` | |

### Section N — Nanowells in the phase channel (PR 5)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| N1 | Lattice pitch and angle from the montage's 2-D Fourier peaks; survives a bubble covering 10 % of the array | `pipeline/nanowell_detect.py` | `todo` | |
| N2 | Matched filter for a light disk on a dark lattice at the plate format's radius (100 µm, 50 µm, custom) via the recorded pixel size; least-squares lattice refinement on candidates at sites (G2/G3) | `pipeline/nanowell_detect.py` | `todo` | |
| N3 | Fiducial: square-versus-circle score per central site at the lattice angle; margin over runner-up; score recorded | `pipeline/nanowell_detect.py` | `todo` | |
| N4 | Per-site centre and radius refinement; `fitted` versus `predicted`; no site dropped | `pipeline/nanowell_detect.py` | `todo` | |
| N5 | Occlusion: bubble mask from the montage plus per-site contrast against the lattice (an occluded site is a black disk on black); `visible_fraction` | `pipeline/nanowell_detect.py` | `todo` | |
| N6 | `imaged_fraction` and clipping at the coverage edge, the array edge and the well wall; a clipped edge never raises | `pipeline/nanowell_detect.py` | `todo` | |
| N7 | Per-site rim focus through `pipeline.field_quality.edge_focus` | `pipeline/nanowell_detect.py` | `todo` | |
| N8 | Cross-timepoint consistency (pitch, angle after the plate's rotation, roster) as a named status | `pipeline/nanowell_detect.py` | `todo` | |
| N9 | Both formats in the synthetic tests: every N row passes at 100 µm and at 50 µm, and a 50 µm array analysed with the 100 µm setting is reported as a format mismatch rather than detected wrongly | `tests/` | `todo` | |
| N10 | Real data: addresses checked by hand on 3 wells × 2 timepoints, including a bubble and a clipped edge; counts recorded here | — | `blocked` | Q7 |
| N11 | Close-out (as G13) | — | `todo` | |

### Section C — Contents, label-free first (PR 6)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| C1 | Empty-nanowell template per (well, timepoint) from the least-textured visible quarter; fallback to the earliest template, aligned by the lattice; source and n recorded | `pipeline/nanowell_measure.py` | `todo` | |
| C2 | Rim-suppressed phase tile (crop ÷ template inside sites, flat outside) | `pipeline/nanowell_measure.py` | `todo` | |
| C3 | Confluence from local texture against the empty-site texture distribution | `pipeline/nanowell_measure.py` | `todo` | |
| C4 | Label-free count: Cellpose on the rim-suppressed owner tile, one call per tile; cells kept only in owned site interiors; fused sites segmented on their crop | `pipeline/nanowell_measure.py` | `todo` | |
| C5 | Area-based estimate and `count_disagreement` | `pipeline/nanowell_measure.py` | `todo` | |
| C6 | Per-cell records through the existing quantification, plus address and well-frame columns | `pipeline/nanowell_measure.py` | `todo` | |
| C7 | Fluorescence when present: per-cell (existing) and per-site integrated and mean minus empty-site background; empty, not zero, at a timepoint without the channel | `pipeline/nanowell_measure.py` | `todo` | |
| C8 | Per-site record for every state in §3.5. Test: an empty site (0) and an occluded site (empty) can never be confused | `pipeline/nanowell_measure.py` | `todo` | |
| C9 | Mutation: remove the ownership filter — a test counting cells in a synthetic overlap must fail on the double count | `tests/test_nanowell_contents.py` | `todo` | |
| C10 | Real data: Cellpose `cyto3` on rim-suppressed versus raw phase, counts against hand counts on 50 sites per format | — | `blocked` | Q7 |
| C11 | Close-out (as G13) | — | `todo` | |

### Section P — The pipeline run (PR 7)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| P1 | Extraction from `process_microscopy.py` sized to pay for the dispatch hook and the `pipeline_info` blocks (headroom 0) | `process_microscopy.py` → a `pipeline/` module | `todo` | |
| P2 | The (well, timepoint) task body: group tiles by timepoint, grid check, registration, detection, contents, records; per-(well, timepoint) failure trace in `errors/` | `pipeline/nanowell_run.py` | `todo` | |
| P3 | End-of-run re-anchoring pass: G8's plate-rigid transform for every unaddressed (well, timepoint); rewrites only those wells' rows; reports how many it anchored and how many remain unaddressed | `pipeline/nanowell_run.py` | `todo` | |
| P4 | CLI flags and their validation messages (plate format, cell diameter, thresholds, and the no-record fallbacks) | `pipeline/cli_args.py` | `todo` | |
| P5 | Writers: `nanowells/<well>.csv`, `_grid.json`, per-cell columns, new roles in `ROLE_CATALOG`, the `acquisition` and `nanowell` blocks of `pipeline_info.json` | `pipeline/nanowell_run.py`, `domain/image_identity.py` | `todo` | |
| P6 | Incremental: (well, timepoint) done unit; every setting in the reuse key with a roster row each | `pipeline/incremental_keys.py`, `tests/test_reuse_is_keyed_completely.py` | `todo` | |
| P7 | Resume checkpoints per (well, timepoint) | `pipeline/fov_checkpoint.py` | `todo` | |
| P8 | Completeness for nanowell mode, shared with Validate | `pipeline/completeness.py` | `todo` | |
| P9 | Remote: manifest, tiles kept together on upload, `nanowells/` fetched; extractions to pay for `remote_runner.py` and `remote_fetch.py` | `services/` | `todo` | |
| P10 | Datasets tools carry `nanowells/`: Subset filters, Merge Plates remaps wells, Concatenate shifts timepoints; Validate checks the tables against the archives | `datasets/` | `todo` | |
| P11 | End-to-end CLI test on synthetic tiles with a deterministic stand-in segmenter (fast lane); real Cellpose in the slow segmentation lane | `tests/` | `todo` | |
| P12 | End-to-end over a merge: two single-timepoint raw datasets (plate moved between them) → Concatenate → Analyze → every site joined across the two timepoints | `tests/` | `todo` | |
| P13 | Real-data run: time and peak memory per (well, timepoint) recorded here | — | `blocked` | Q7 |
| P14 | Close-out: ARCHITECTURE §6.2 section, README CLI flags, version | — | `todo` | |

### Section A — Analyze form (PR 8)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| A1 | Nanowells section as its own mixin, Qt-free fields and validation beside it; extraction to pay for the `analyze/tab.py` hook (headroom 0) | `analyze/nanowell_section.py`, `analyze/domain/nanowell_fields.py` | `todo` | |
| A2 | Run-spec wiring, tested through `analyze.run_spec._collect_run_spec` down to the argv | `analyze/run_spec.py` | `todo` | |
| A3 | Preflight: missing tiles, missing acquisition record, overlap smaller than a nanowell (with the seam-site count), no phase channel | `analyze/preflight.py` | `todo` | |
| A4 | Test detection preview in a worker: montage, labelled sites, occluded / clipped / fused marks, origin method, measured overlap, sample crops with segmentation | `analyze/nanowell_preview.py` | `todo` | |
| A5 | A test that presses the real controls (enable → argv; plate format → argv; preview → worker started) — CLAUDE.md §7.6 | `tests/` | `todo` | |
| A6 | Close-out: README Analyze section, rendering reviewed, version | — | `todo` | |

### Section D — Review data and calculations (PR 9)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| D1 | Loader for `nanowells/`; test that the top-level `*.csv` glob never picks the tables up | `review/features/nanowell/collect.py` | `todo` | |
| D2 | Per-site time series joined on `(well, q, r)`; refuses an unknown scheme version | `review/features/nanowell/compute.py` | `todo` | |
| D3 | The §3.10.1 metrics, each with its undefined cases tested, against recorded time | `review/features/nanowell/compute.py` | `todo` | |
| D4 | Baselines: earliest (default) and first measurable (labelled) | `review/features/nanowell/compute.py` | `todo` | |
| D5 | Per-(well, timepoint) summaries with sites used and excluded by reason | `review/features/nanowell/compute.py` | `todo` | |
| D6 | Group summaries via Sample Definitions; vs-control uses the same statistic top and bottom | `review/features/nanowell/compute.py` | `todo` | |
| D7 | Wells as the replicate unit; no public path pools sites as replicates | `review/features/nanowell/compute.py` | `todo` | |
| D8 | Long-format CSV export | `review/features/nanowell/controller.py` | `todo` | |
| D9 | Persistence block, reset on dataset switch | `review/persistence/nanowell.py` | `todo` | |
| D10 | Close-out (as G13) | — | `todo` | |

### Section V — The Nanowells page (PR 10)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| V1 | Package registered; build profile; help topic; feature-removal and profile tests | `review/features/nanowell/`, `features/registry.py`, `ui/help_topics.py` | `todo` | |
| V2 | Array map | `review/features/nanowell/renderer.py` | `todo` | |
| V3 | Site detail: trajectory and per-timepoint crops from owner tiles | `review/features/nanowell/` | `todo` | |
| V4 | Trajectories | `review/features/nanowell/renderer.py` | `todo` | |
| V5 | Well comparison | `review/features/nanowell/renderer.py` | `todo` | |
| V6 | Position effects | `review/features/nanowell/renderer.py` | `todo` | |
| V7 | Array QC | `review/features/nanowell/renderer.py` | `todo` | |
| V8 | Properties panel, Saved Plots, Copy / Save; redraw ends with `apply_export_style_to_current` | `review/features/nanowell/controller.py` | `todo` | |
| V9 | Real-constructor test in the slow lane; `test_no_tab_builder_raised_while_draining` stays green with the page | `tests/` | `todo` | |
| V10 | Close-out: ARCHITECTURE §5 section, README section, minor version | — | `todo` | |

### Section I — Integration with existing surfaces (PR 11, optional)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| I1 | Image Browser: montage with lattice overlay; site crop gallery | `review/features/image_browser/` | `todo` | |
| I2 | Spatial reads well-frame coordinates in µm on nanowell datasets | `review/features/spatial/` | `todo` | |
| I3 | Nanowell as a replicate unit in Bar / Line plots — scoped separately first, because it touches `fold_change`'s tuples | `review/domain/` | `todo` | |
| I4 | Batch Export nanowell mode | `review/features/batch_export/` | `todo` | |
| I5 | The OME and ND2 converters write the same acquisition record. The OME reader already holds stage coordinates (`pipeline/ome_explode.py` `stage_positions`); the ND2 reader names stage positions, and whether it reads their coordinates is unchecked | `pipeline/ome_explode.py`, `pipeline/nd2_explode.py` | `todo` | |
| I6 | Close-out; delete this tracker if every row is `done` or `declined`, and say so in the PR body | — | `todo` | |

---

## 6. What would make this plan wrong

Each of these, if it turns out true, changes a section rather than a row.

- **The CZI's positions are not comparable across acquisitions (R8).** Origin
  fallback 3 then has nothing to work from, and a timepoint whose square is
  hidden depends on fallback 2 alone — still sound, because the square is
  always at the array's centre, but with one fewer cross-check.
- **The CZI does not record what §3.0.1 expects** (no scaling, no stage
  positions, no start time on some ZEN versions). The record then carries what
  exists and says what is missing, Concatenate falls back to its interval rule
  for those datasets, and the Analyze no-record settings become the main path
  for them.
- **The copy cost of one Concatenate per timepoint is unacceptable (Q9).** M7
  helps only when all timepoints are already acquired. A running experiment
  then needs an input layout that can grow without rewriting — a decision about
  every reader of `in/`, not a Concatenate change.
- **Most 100 µm datasets have overlap smaller than a nanowell.** The fused-site
  path stops being the seam exception and becomes a large share of sites; its
  cost and its effect on per-tile label images must then be measured, not
  assumed.
- **The lattice is not uniform across a well** (molding distortion, optical
  field curvature). Lattice-phase registration then biases tiles; overlap
  correlation becomes primary, and the per-tile residual is the signal.
- **Cells fill nanowells so that the empty-site template is contaminated** at
  late timepoints, most likely in the 50 µm format. The earliest-template
  fallback is aligned by the lattice and survives the plate being moved, but
  not a change in illumination between acquisitions; C10 measures it.
- **Cellpose `cyto3` is poor on these cells in phase (C10).** The answer is not
  a new model (§2.2) without a row and a measurement; confluence remains the
  robust readout either way.
- **Wells are not always 5×5.** The grid comes from the record, so this costs
  nothing unless grids vary *within* a plate.
