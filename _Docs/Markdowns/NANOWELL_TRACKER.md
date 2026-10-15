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
- **Each well is imaged as a grid of overlapping tiles** — 5×5 with 10 %
  overlap in the current experiments, set in the acquisition software. The
  tiles must be registered and assembled at the start of each well's analysis.
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
  software must support that. Separate acquisitions are merged across time with
  the Datasets tools, **on the raw images, before analysis**, and the user wants
  **a field to enter the time gap explicitly** when concatenating.
- Both CZI layouts occur: **one CZI holding every well**, and **one CZI per
  scene** (ZEN's Split Scenes export). The existing reader handles both
  (`pipeline/czi_set.py`).
- **The tile plan changes between experiments.** Nothing about the grid — its
  size, its overlap, its positions — may be assumed; it is read from each
  dataset.
- The goal: an addressing scheme anchored on the central square that lets the
  same nanowell be compared across timepoints (cell count, brightness), per-
  nanowell metrics such as fold change over time, and comparison of those
  metrics between plate wells over time — integrated into the existing app as
  cleanly as possible.

### 1.2 Numbers derived from those facts (inferred, not measured)

Arithmetic on the reported numbers, so later measurements have something to be
checked against. Each line states what it assumes, and the tile figures use the
current experiments' 5×5 grid at 10 % overlap — the plan changes between
experiments. None of these values is hard-coded anywhere: pixel size, grid and
tile positions are **read from the CZI**, and the pitch, radius and orientation
are **measured from the images**; these are sanity bounds.

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
- **Overlap is a per-axis length, and it decides the design.** With the
  current 10 % overlap, the overlap along an axis is 0.1 × the tile's extent on that axis.
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

The same rule governs merging across time: a merged timepoint's position in time
comes from the gap the user entered or from recorded acquisition times, and the
merge records which (§3.0.3). It is never silently derived from a cadence that
does not exist.

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
separate acquisition and a separate CZI — or a separate Split Scenes folder of
CZIs. Reading either shape already works: `pipeline/czi_set.py` treats one file
holding every scene and a folder of one-scene files as the same plate (it
refuses only ZEN's numbered *file parts*, a third shape, by name). Two things
must happen before the nanowell analysis can run, and neither exists today.

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

- per dataset: the source files;
- per timepoint — each timepoint is one acquisition, and acquisitions merged by
  Concatenate may differ — the pixel size in µm, tile size in px, the
  objective if the file names one, the declared tile overlap if the file states
  it, and **the timepoint's time**: the absolute start of that acquisition,
  ISO 8601, taken as the earliest recorded tile of the *whole* acquisition —
  every tile in the CZI or Split Scenes set, whichever wells a run converts —
  so that converting a plate in parts names its timepoints exactly as
  converting it at once;
- per (well, field, timepoint): the tile's mosaic position in px (subblock
  directory) and its stage position in µm (metadata), each when present.

**A timepoint has one time, shared by every well and tile in it**, even though
scanning a plate can take an hour. That is the user's decision: the timepoint
is labelled, merged, and measured against as one moment, and no per-well or
per-tile acquisition time is recorded or used anywhere downstream. When the
tiles' clocks mix zoned and unzoned readings, the start is left empty with the
reason rather than guessed, and the number of tiles that carried no reading is
recorded beside it.

**The filename's timepoint token is generated from that same time** (R3f). The
token is the time the app actually uses — Review reads `timepoint_hours` from
it and Concatenate's interval rule reads it — so a token derived from any other
clock would give one timepoint two times. Tokens are the elapsed hours from the
first timepoint's start, named by `pipeline.timepoints`' existing rules. This
changes naming for every CZI import, not only nanowell plates: tokens were
previously the elapsed times of one sampled field. Two cases keep their current
behaviour: an acquisition with no comparable start (no readings, or mixed
zoned and unzoned clocks) keeps today's positional tokens (`t0`, `t1`) and says
so; and a single-timepoint acquisition is `0h` either way, with Concatenate
shifting it by the entered gap. A dataset never mixes the two naming rules: a
resumed conversion that finds archives named under the other rule re-converts
those wells rather than adding to them.

It lives at the dataset level rather than inside each well's archive because
Concatenate copies non-image archive members under their own names, and two
datasets' same-named record members would collide and fail its verification.
At the dataset level each tool merges it explicitly instead.

Which of those fields the user's ZEN version actually populates is not known
until real files are read (R8, blocked on Q7). In particular, whether mosaic
positions are absolute stage-derived coordinates or are re-based per file
decides whether they can be compared *across* acquisitions, which §3.4.3's plate-rigid
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
3. **The time gap is guessed, and the user cannot say otherwise.**
   `compute_offset` (`:262-272`) places dataset 2's first timepoint at dataset
   1's last plus dataset 1's median interval, and the page
   (`datasets/concatenate/view.py`) has no field to override it. For
   acquisitions taken whenever the plate was put back, that invents the elapsed
   time.

It also writes a fresh destination every time and leaves both sources
untouched, so a timelapse built one timepoint at a time is copied in full at
every step (Q9).

#### 3.0.3 What Concatenate will do

**A time-gap field.** The page gains one field beside the dataset row: *dataset
2's first timepoint comes __ after dataset 1's last*. It reads any duration
`util.timepoint.parse_hours` already reads (`24`, `24h`, `1d`, `1d2h30m`), must
be positive, and is the user's answer to the question the tool currently
guesses at. Where it gets its starting value, in order:

1. **Recorded acquisition times**, when both datasets carry an acquisition
   record: the field is pre-filled with the difference between dataset 1's last
   recorded start and dataset 2's first, rounded by `pipeline.timepoints`'
   existing naming rules (which already refuse to round a reading further than
   its jitter allows).
2. **Dataset 1's median interval**, when dataset 1 has two or more timepoints:
   the field is pre-filled with today's rule, so a user who does nothing gets
   exactly today's behaviour.
3. **Nothing** — a single-timepoint dataset 1 with no record. The field is then
   required, and the page says why, instead of refusing the merge outright as
   it does today.

The user can overwrite the pre-filled value with any gap; a nominal schedule
(`24h`, `48h`) rather than the stopwatch reading is a legitimate choice. When an
entered gap disagrees with the recorded times by more than the naming
tolerance, the page shows both numbers and proceeds with the entered one — it is
a warning, not a refusal. The status preview lists dataset 2's resulting labels
before anything is copied, and the merge records the gap and where it came from
(`entered`, `recorded`, `interval`) in the destination's provenance line
(`datasets/core/provenance.record_derivation`, which already carries
`offset_hours`) and in the merged acquisition record. The existing check that
dataset 2's timepoints land after dataset 1's with no overlap is unchanged.

**Raw datasets, before analysis — the primary path.** Concatenate accepts a
dataset that has `in/` and no `out/` — a converted plate nobody has analysed —
and raw + raw gives a raw destination that Analyze then runs on once.
Compatibility is checked from the two acquisition records when both exist
(filename schema, channels, pixel size), and from the archives' own member
names otherwise (`util/schema_infer.py`). **Differing tile plans are accepted**:
the tile plan changes between experiments and may change between acquisitions,
and each timepoint keeps its own positions. A differing pixel size is shown in
the preview as a warning — the nanowell analysis works in µm, but intensities
measured at two magnifications may not compare.

**Which combinations merge.** The gap field applies to every one of them.

| Dataset 1 + dataset 2 | Today | Planned | Row |
|---|---|---|---|
| analysed + analysed | supported | unchanged, plus the gap field; leaving its pre-filled value reproduces today's result exactly | M1, M3 |
| raw + raw | refused (needs `out/` and `pipeline_info.json`) | **the primary path**: a raw destination, analysed once | M7 |
| analysed + raw | refused | supported: dataset 1's outputs kept; the next incremental Analyze processes only dataset 2's timepoints | M12 |
| raw + analysed | refused | still refused, with a message naming the order to use instead | M12a |

For nanowell plates, merge raw and then analyse. Two fallbacks only work when
one run sees every timepoint: re-anchoring a well whose square is hidden, from
the rest of the plate (§3.4.3), and the earliest empty-nanowell template
(§3.6). Concatenating two analysed nanowell datasets still joins sites
correctly over time, but those fallbacks did not have the other timepoints.
Until the `nanowells/` tables are carried (P10), Concatenate refuses analysed
nanowell datasets rather than dropping them.

**Also:**

- **Analysed + unanalysed.** The destination keeps dataset 1's outputs, and
  dataset 2's timepoints are left for an incremental Analyze run, which
  processes exactly those (incremental runs already skip done units). Secondary
  to the raw path; it is what a running experiment needs if re-copying the
  accumulated raw dataset each day is too slow (Q9).
- **Merge the acquisition record**, keeping each timepoint's own positions —
  after the plate was put back they differ.
- **Optionally, merge several datasets in one run**, each with its own gap
  field, which is one copy instead of one per added timepoint (M13, deferred; see its row).

#### 3.0.4 The workflows this gives

- **All timepoints already acquired:** convert each CZI → Concatenate the raw
  datasets in order, entering each gap (M13, deferred, would allow it once) → run Analyze once.
- **An experiment still running:** convert each new CZI and Concatenate it onto
  the accumulated raw dataset, then re-run Analyze incrementally; only the new
  timepoint is processed. If the copy cost is too high at scale (Q9), the
  analysed + unanalysed path avoids re-analysing anything, but not re-copying.

### 3.1 The pipeline's unit of work becomes (well, timepoint)

Today the pipeline's unit of work inside a well is the **FOV-group**: one
nuclear image plus its fluorescence channels, processed independently
(`process_microscopy._process_groups_in_worker`). Incremental runs, resume
checkpoints and failure traces are all keyed on `(fov, timepoint)`.

A nanowell analysis cannot work field by field: the address of a nanowell in
field 7 depends on where the square is, which may be in field 13. So in
nanowell mode the unit becomes **one well at one timepoint — every tile of its
grid together** (25 in the current experiments):

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
µm via the recorded pixel size. Because the tile plan changes between
experiments (and possibly between acquisitions of one), the grid's size and
overlap are read per (well, timepoint) and never carried from one to another.
The overlap is *measured* from the positions along each axis and compared with
the declared overlap when the file states one; a large disagreement is reported,
because one of the two is wrong about the acquisition.

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

With the current 10 % overlap this holds for the 50 µm format on any ordinary 10x camera,
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

What PR 1 built — the coordinates and labels, the lattice fit, the orientation
limit, origin resolution, the roster primitive, ownership and the plate-rigid
transform — is described in `ARCHITECTURE.md` §6.2e, which is now the
reference for it. What remains below is the design later sections still have
to build on top of those primitives.

#### 3.4.1 Coordinates and labels

Built; see `ARCHITECTURE.md` §6.2e.

#### 3.4.2 Orientation

Built, with a limit: a fit is accepted only within ±25° (hexagonal) or ±40°
(square) of image east and refused beyond (G4b). The lattice alone cannot tell a
rotation from one a sixth (hex) or a quarter (square) of a turn away, so a true
rotation of 35°–85° (hex) or 50°–130° (square) would fold back inside the limit
on the wrong axis. A plate keyed in its holder cannot get there; a misaligned
camera could. The square nanowell is what can catch it: its edges repeat every
90° and the hexagonal lattice every 60°, so together they repeat only every
180°, which a keyed plate never reaches (N3a).

#### 3.4.3 Where the origin comes from, at every timepoint

`resolve_origin` (built) chooses between the evidence it is given. What later
sections must supply, and in what order:

- Because the plate is removed between timepoints, **every timepoint is
  anchored on its own**; nothing is carried over from a previous timepoint on
  the assumption that the plate stayed put.
- The fiducial comes from the square-versus-circle score (N3); the array centre
  from `fit_array_boundary` over the detected sites (N2); the plate-rigid
  prediction from the wells anchored at both timepoints, in stage coordinates.
- The plate-rigid fallback runs as an **end-of-run pass** after every well has
  been processed, because it needs the other wells (P3). It needs stage
  positions comparable across acquisitions, which R8 establishes from real
  CZIs; without them the fallback is unavailable and a well whose square is
  hidden and whose boundary is not visible stays `unaddressed`, with the
  reason.

#### 3.4.4 The roster

`roster()` (built) enumerates the model disk for a given origin and radius,
and keeps hidden sites. Two things are left to the sections that call it:

- **One array-model radius per well**, shared by every timepoint. G6a measured
  that each clipped observation's own radius estimate changed twelve marginal
  sites, so the per-timepoint estimates must be combined into one value per
  well before any roster is built (N8a).
- **Cross-timepoint consistency.** Within one well, pitch (±0.5 %), angle (±0.5°
  after removing the plate's measured rotation) and roster must agree across
  timepoints; a violation is a named status on the timepoint, not an exception
  (N8).

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

Time is each timepoint's one recorded time (§3.0.1), the same for every well, so a growth rate over unevenly
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
- **Site detail** — click a nanowell on the array map (or on the Array QC
  montage) and its statistics over time appear beside the map: cell count,
  confluence, brightness and site signal against recorded time, with the
  state at each timepoint (empty, measured, occluded, clipped, fused) marked
  on the curve so a gap reads as "not seen", never as zero, and its crop at
  every timepoint from that timepoint's owner tile. The selection is the
  site's `(well, q, r)` address, so it follows the same nanowell across
  timepoints even when the plate moved between acquisitions; a site that
  exists at some timepoints only (unaddressed, or off the image) shows just
  those. Clicking a second site adds it to the plot for comparison; clicking
  empty space clears the selection. The selection is not a plate selection
  (`_selected_wells` is untouched).
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
| 1 (#960) | `claude/nanowell-1-geometry` | `main` | G | this tracker; addressing; lattice fitting on point sets; origin chain; plate-rigid transform; roster; ownership | patch |
| 2 (#962) | `claude/nanowell-2-acquisition` | PR 1 | R | the acquisition record; the CZI converter writes it; Subset, Merge Plates, Validate and remote carry it | patch |
| 3 | `claude/nanowell-3-merge-time` | PR 2 | M | Concatenate: a time-gap field (pre-filled from recorded times or the interval), raw datasets before analysis, single-timepoint datasets, differing tile plans, record merging | patch |
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
3. **Open each PR when its section's rows are done and the work has been
   reviewed (§4.2)**, not before — pushing to a branch with no PR is free, and
   every push to an open PR bills a CI run. While PR N is in review, work
   continues on PR N+1's branch.
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

### 4.2 Working a section, and handing it back

Any worker — a later session, or another assistant — works a section the same
way:

1. Read `CLAUDE.md`, then this file's opening rules and the §3 parts its rows
   cite.
2. Work on the section's branch from §4; commit and push as you go, each
   commit with a prose body saying what changed and why (CLAUDE.md §0). Never
   create another branch, rebase, amend or force-push a published branch.
3. Touch nothing outside the section's rows. A finding outside them becomes a
   row, not a change.
4. Before handing back, run the checks `CLAUDE.md` §2 lists: the guard family
   as a set, the section's test files, `ruff check .`, and the full fast lane
   after the last commit.
5. **Do not open the pull request.** A section's work is reviewed first; the PR
   opens after review, per §4.1.
6. Hand back a report with:
   - the commits, SHA and subject, confirmed pushed (or `git format-patch
     origin/main..HEAD` output if pushing was not possible);
   - each row's final state with its evidence;
   - each check run, its counts, and the commit it ran against;
   - every point where this file was silent or ambiguous, and what was chosen
     (also written into the row);
   - anything done differently, anything doubted, and any guard that pushed
     back;
   - the row counts from the command at the top of this file.

   A check that was not run is reported as not run.

---

## 5. Rows

### Section Q — Questions for the user

Answers are written into the row. Rows that depend on an answer say so.

| # | Question | Default until answered | State | Answer / evidence |
|---|---|---|---|---|
| Q1 | Pixel size at 10x and tile size in pixels | — | `done` | Read from the CZI's scaling metadata and subblock sizes (R2); no user input needed. Real values recorded in R8 |
| Q2 | Tile overlap | — | `done` | 10 % in the current experiments, set in the acquisition software (the plan changes between experiments, Q10). Whether that clears a 100 µm nanowell depends on the tile's extent per axis (§1.2); measured per dataset (T2) |
| Q3 | How the 25 tiles are numbered and where each sits | — | `done` | The CZI records each tile's position; the converter will record it (R2–R3). The scan-pattern setting remains only for data with no record |
| Q4 | Is the square always at the centre of the array? | — | `done` | Yes, physically. The array-centre fallback (§3.4.3) relies on it |
| Q5 | Is the plate taken off the microscope between timepoints? | — | `done` | It can be, and must be supported. Every timepoint is anchored on its own; acquisitions are merged across time with Concatenate (§3.0) |
| Q6 | What does "sometimes smaller" mean? | — | `done` | Nanowells are usually 100 µm; an alternative plate format has 50 µm nanowells. Plate format becomes a setting (§3.3, §3.9) |
| Q7 | Sample data: CZIs of 2–3 wells at 2+ timepoints with the plate removed between them, including a bubble, a clipped edge, and if possible a hidden square; one plate of each format if available | blocks every real-data row | `todo` | |
| Q8 | How do nanowells look in phase? | — | `done` | Light nanowells on a black lattice, at every timepoint (§3.3) |
| Q9 | How many timepoints, channels and wells does a typical experiment have, and is a full copy of the dataset per added timepoint acceptable (~20 GB per channel per timepoint for 96 wells, §1.2)? | keep Concatenate's fresh-copy rule | `todo` | |
| Q10 | Is each timepoint saved as one CZI holding every well, or as a Split Scenes folder of per-well CZIs? Is the tile plan reused? | — | `done` | Both layouts occur, and `pipeline/czi_set.py` already reads both. The tile plan changes between experiments, so grid size, overlap and positions are read per (well, timepoint) and never assumed (§3.2.1) |
| Q11 | Is the central square cut with its edges parallel to a lattice row (so its orientation equals the lattice axis modulo 90 degrees), or at some other fixed angle? | 0 degrees (parallel); `check_orientation(..., square_to_axis_deg=0.0)` | `todo` | N3a's check compares the square's edge direction with the lattice axis the fit chose. It is only as right as this assumption, which the renderer shares (it draws the square aligned), so the tests cannot tell. A mask whose square were cut 30 degrees off a row would read as a folded rotation on every well |

### Section G — Geometry and addressing (PR 1)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| G1 | Address type `NanowellAddress(q, r)` (frozen), `ring`, `label` `k.i` and `parse_label` as exact inverses, per §3.4.1, for hex and square lattices; stdlib only. Round trip tested exhaustively to ring 45 (the 50 µm format reaches ~40) | `util/nanowell_address.py` | `done` | `tests/test_nanowell_addressing.py`: 16 tests pass, including exhaustive hex and square round trips through ring 45 and the exact first two hex rings. Reflecting parsed r in the mutation worktree makes both inverse cases fail with AssertionError. Hex is the default (G1a); square requires explicit kind |
| G1a | Specify how one `NanowellAddress(q, r)` represents hex and square addresses | `util/nanowell_address.py` | `done` | User answered "Assume hex". Constructor and parser default to hex; an explicit keyword-only kind is retained for the square support required by G1. The label alone cannot identify kind |
| G1b | Pin the square-lattice order. Review measured that reversing the square walk to clockwise leaves all 16 address tests passing; only the hex order is pinned. Add a test with explicit square labels: ring 1 `(1,0)=1.0, (1,1)=1.1, (0,1)=1.2, (−1,1)=1.3, (−1,0)=1.4, (−1,−1)=1.5, (0,−1)=1.6, (1,−1)=1.7`; ring 2 `(2,0)=2.0, (2,1)=2.1, (2,2)=2.2, (1,2)=2.3, (0,2)=2.4, (−1,2)=2.5, (−2,2)=2.6, (−2,1)=2.7, (−2,0)=2.8, (−2,−1)=2.9, (−2,−2)=2.10, (−1,−2)=2.11, (0,−2)=2.12, (1,−2)=2.13, (2,−2)=2.14, (2,−1)=2.15` (what the current walk produces, which matches §3.4.1). Mutation: the clockwise walk must fail it | `tests/test_nanowell_addressing.py` | `done` | The explicit square ring-1 and ring-2 label test passes; reversing the square walk while preserving round-trip inverses fails it with AssertionError. The addressing file now has 19 passing tests, including G10/G11 recovery fixtures. |
| G2 | `fit_lattice(points, *, expected_pitch=None, kind="hex"\|"square"\|"auto")` → frozen result: `ok`, `reason`, `a1`, `a2`, `pitch`, `angle_deg`, `kind`, **`phase_origin`** (the fitted position of the lattice site nearest the inliers' centroid, in the input units), `rms_residual`, `inlier_mask`, and the basis's departure from ideal (a₁–a₂ angle, length ratio). Seeded from nearest-neighbour displacement vectors (`scipy.spatial.cKDTree`, antipodes folded, clustered), refined as a general 2×2 basis plus origin by least squares on cleanly indexed points. Succeeds with 30 % of points missing plus 10 % random outliers; on failure `ok=False` with a reason, never a fabricated lattice. `auto` decides hex or square from nearest-neighbour angles and says which | `pipeline/nanowell_lattice.py` | `done` | Frozen LatticeFit includes phase_origin, inlier mask, measured basis angle and length ratio. Nearest-neighbour folded-angle clustering seeds a general affine least-squares fit. Both pitches pass thirty-percent dropout plus ten-percent contamination; an independent 50-seed sweep passes. Forcing an ideal basis instead of affine refinement fails the departure test with AssertionError. G2b pins the outlier-centroid phase seed. |
| G2a | Specify the phase-origin output, origin-candidate units, and caller-supplied fiducial thresholds used by G2/G5/G6 | `pipeline/nanowell_lattice.py` | `done` | Answered in review, adopting the worker's proposal: G2's result carries `phase_origin`; every position is `(x, y)` in the caller's units (§3.4.1); the fiducial margin is a required argument of `resolve_origin` with no default until real data (Q7) sets one. Written into §3.4.1, G2 and G5. The gap was the plan's: the result list omitted the origin the fit already refines, and the units sentence was lost when the specification moved into this file |
| G2b | Seed phase from the periodic coordinate consensus, not an arbitrary central observation | `pipeline/nanowell_lattice.py` | `done` | Circular fractional-coordinate consensus now seeds phase before affine refinement. All 50 sweep seeds recover every genuine site. Removing the phase-consensus step makes test_outlier_nearest_centroid_cannot_choose_the_lattice_phase fail with AssertionError. The regression also locates phase_origin independently as the site nearest the retained centroid. |
| G3 | `index_points(points, fit, origin)` → integer `(q, r)`, residual, outlier flag (residual over a quarter pitch). Outliers are not forced onto a site; two points on one site keep the nearer and mark the other | `pipeline/nanowell_lattice.py` | `done` | index_points uses Cartesian nearest-site distances, refuses residuals above a quarter pitch and retains only the nearer duplicate; rejected points have no q/r address. Forcing all detections to have addresses fails the duplicate/residual regression with AssertionError. |
| G4 | Basis convention per §3.4.1 under rotations −25°…+25° in 5° steps, with a `mirrored` flag (a mirroring camera) under which the counter-clockwise rule holds in the unmirrored frame; accepted within ±25° (hex) / ±40° (square), per the answered G4b | `pipeline/nanowell_lattice.py` | `done` | Hex and square nearest-east/counter-clockwise bases and actual recovered integer addresses pass rotations −25° through +25° in 5° steps. Both mirror axes pass in explicitly unmirrored frames. Ignoring unmirroring makes two parametrised cases fail with AssertionError. Exact endpoint ambiguity is separately recorded in G4b. |
| G4a | Avoid guessing which coordinate a mirroring camera reflects | `pipeline/nanowell_lattice.py` | `done` | mirrored=True requires mirror_axis="x" or "y"; omission returns a failure reason rather than guessing a camera axis. Both explicit axes recover the independently known basis and addresses. Ignoring the unmirroring step produces two AssertionError failures. |
| G4b | Resolve the contradictory exact orientation endpoints in G4 and §3.4.2 | `pipeline/nanowell_lattice.py`, `tests/test_nanowell_lattice.py` | `done` | Answered by the user in review: accept within ±25° (hex) and ±40° (square) of east, refuse beyond. Review had measured that the exact-tie guard never fires on noisy data: with 2 µm noise a true 29.99° fitted as −30.03° and 31° as −29.02°, both reported as successes with every address turned. `_ORIENTATION_LIMIT_DEG` now refuses beyond the limit with the measured angle in the reason. `test_orientations_near_the_ambiguity_are_refused_not_relabelled` (11 cases at 25.5°, 29.99°, 31°, 40.5°, 44.99°, 46° and their negatives) fails all 11 against the previous code with AssertionErrors naming the accepted angle (29.99° → −30.00°, 31° → −28.99°, −31° → 29.00°, 46° → −43.99°); `test_orientations_inside_the_limit_keep_their_true_axis` holds ±24.9° / ±39.9°. Rotations further out fold back inside the limit; N3a covers them |
| G5 | `resolve_origin(fit, *, fiducial=None, array_centre=None, plate_rigid=None, fiducial_margin)`: `fiducial` is the candidate site's `(x, y)` with its square-likeness score and the runner-up's score; `array_centre` is G6's `(x, y)`; `plate_rigid` is G8's predicted `(x, y)` with its one-sigma uncertainty; `fiducial_margin` is required, with no default. Frozen result: `status` (`anchored` / `unaddressed`), `origin_xy` (the chosen lattice site's position, snapped through `fit`), `method` (`fiducial` / `array_centre` / `plate_rigid` / none), `agreement` (`agree` / `conflict` / `single_source`), `reason`. Order and conflict rule per §3.4.3; the plate-rigid prediction is accepted only when 3 × its uncertainty is under half a pitch; never guesses. Addresses then come from `index_points(points, fit, origin_xy)` | `pipeline/nanowell_lattice.py` | `done` | resolve_origin requires fiducial_margin, snaps all accepted XY evidence through the lattice phase, applies fiducial/centre/rigid priority and reports conflicts. The plate gate uses strictly three sigma below half pitch. Mutations accepting an ambiguous fiducial, using one sigma, or reversing priority each fail the evidence-priority regression with AssertionError. No accepted source returns unaddressed with no origin. |
| G6 | `fit_array_boundary(site_xy)`: circle through the outermost site centres, robust to seeing as little as 120° of arc; returns centre, radius, arc coverage, status. The array-centre estimate is its centre snapped to the nearest site. `roster(fit, origin, radius)`: every `(q, r)` whose centre lies within radius + 0.25·pitch; identical for shifted observation windows of one array | `pipeline/nanowell_lattice.py` | `done` | ArrayBoundary reports a robust convex-hull circle, centre, radius, measured coverage and failure reason. Continuous 120/180/360-degree arcs, an actual hexagonal 120-degree cap, and bubble-hidden central sites pass. Using a window centre fails G10; omitting a hidden model site fails the roster regression, both with AssertionError. Roster stability requires one shared caller-supplied array-model radius (G6a); angular discretisation is explicit (G6b). |
| G6a | Clarify roster stability when each clipped observation estimates a slightly different radius | `pipeline/nanowell_lattice.py` | `done` | The first recovery run found twelve marginal roster sites changed when each clipped observation supplied its independently measured radius. The documented roster contract uses one shared physical array-model radius across observations; G10 independently recovers both origins and compares the resulting complete rosters using that same radius. No claim is made that independent noisy radius estimates always select identical edge sites; later integration must check model agreement. No silent radius quantisation was added. |
| G6b | Recover an actual lattice boundary seen through a 120-degree cap | `pipeline/nanowell_lattice.py` | `done` | The actual 120-degree lattice cap now fits with measured coverage 117.9246 degrees, centre error 36.35 µm at 172 µm pitch, and snaps to the exact physical centre. Acceptance accounts for one spacing of endpoint angular resolution while reporting the measured angle unchanged. Removing that allowance makes the cap regression fail with AssertionError. |
| G7 | `assign_owners(site_xy, nanowell_diameter, tiles, margin)` over axis-aligned tile rectangles with ids: owner = a tile holding the whole disk plus margin, nearest tile centre on a tie; sites no tile wholly holds are returned as the fused set, never dropped. Tested: overlap ≥ diameter + 2·margin on both axes gives an empty fused set; a smaller overlap gives exactly the seam sites the test computes independently | `pipeline/nanowell_lattice.py` | `done` | The original seven ownership cases still pass, covering both diameters, independently computed seam sets, nearest-centre ties and required margins. Exact distance ties use lexical tile ids. G7a now partitions unowned sites into covered seam disks and physically unimaged disks; the original outside-coverage expectation was corrected to the new explicit contract. |
| G7a | Split the unowned set: **fused** = no single tile holds the disk plus margin, but the union of the tiles covers the whole disk (a seam; a blended crop can measure it); **unimaged** = part of the disk lies outside every tile (it feeds §3.5's `clipped` state, never a fused crop). The result reports both, and no site is in both or in neither. Test: a site past the outer edge of a 2×2 grid is `unimaged`, never `fused`; a seam site is `fused`. Raised by the worker in G7's evidence; the plan's G7 had conflated the two | `pipeline/nanowell_lattice.py` | `done` | OwnershipResult reports disjoint fused_indices and unimaged_indices. Exact vertical-slab union coverage tests the physical disk; caller margin controls single-tile ownership. Tests pin an outer-edge disk as unimaged, a seam disk as fused, an interior coverage hole, and bounding-box corner gaps outside the disk. Fusing every unowned site makes the outside-edge regression fail with AssertionError. The ownership file has nine passing tests. |
| G8 | `fit_plate_rigid(origins_a, origins_b)` (well → origin in stage coordinates at two timepoints): rotation + translation, no scale (2-D Kabsch), robust to outlier wells; needs ≥ 3 wells, else `ok=False` with a reason; per-well residuals and RMS. `predict_origin(transform, origin_a)` → position plus an uncertainty derived from the residuals | `pipeline/nanowell_lattice.py` | `done` | Proper Kabsch rotation/translation with no scale, per-well residuals, at least three common consensus wells and prediction leverage remain pinned. G8a/G8b correct the radial consensus and uncertainty calibration. The original identity-rotation mutation fails the motion recovery test; restoring the old cutoff fails the new simulation with AssertionError. Input-position measurement and calibration uncertainty remain excluded and are documented for scientific review. |
| G8a | Make the plate-rigid uncertainty honest. Measured in review (12 wells on a 9 mm grid, 5 µm per-axis noise on both timepoints, no outliers, 500 fits each): predicted 2.60 µm against an actual RMS error of 3.43 µm at the wells' centroid, and 15.62 µm against 19.83 µm at a distant point — about 25 % low. Cause: the cutoff, applied to residuals from the best two-well hypothesis, drops at least one genuine well in 51 % of fits (11.04 of 12 kept on average), and the variance is then taken from the trimmed residuals (40.8 against a true 50.0). Fix: refit on the consensus and re-apply the cutoff to the refit's residuals, using a cutoff suited to 2-D radial residuals. Test by simulation, not by restating the formula: with no outliers, nearly every fit keeps every well, and the predicted uncertainty is within 10 % of the empirical RMS error at the centroid and far away; a planted outlier is still rejected. A 96-well fit takes 0.46 s today, which is acceptable | `pipeline/nanowell_lattice.py`, `tests/test_nanowell_plate_rigid.py` | `done` | Consensus is refitted and reselected using a conservative six-sigma Rayleigh radial cutoff. The independent seeded 500-fit, twelve-well simulation with 5 µm per-axis noise in both acquisitions retains every genuine well in 499 fits. Predicted versus empirical radial RMS is 2.8989 versus 2.9548 µm at the centroid, and 20.8577 versus 21.5147 µm far away, within ten percent. A planted outlier is still rejected. Restoring the old fitter makes the simulation fail with AssertionError. |
| G8b | Avoid trimming genuine wells because a small-sample radial median underestimates scale | `pipeline/nanowell_lattice.py` | `done` | The 4.5-sigma exploratory cutoff retained all twelve wells in only 484/500 fits and failed the unchanged ≥495 assertion. A conservative six-sigma Rayleigh cutoff retains all wells in 499/500 fits without weakening that assertion and passes empirical uncertainty calibration. Residuals from the refit permit genuine wells to rejoin consensus; planted gross outliers remain rejected. |
| G9 | Seeded synthetic point-set generator: hex array of radius R in both formats (100 µm / ~172 µm pitch and 50 µm / ~86 µm), square flagged at `(0, 0)`, circular boundary, bubble holes (removed discs of sites), dropouts, outliers, rotation, translation, mirror, window clipping, and a plate of wells moved rigidly between two timepoints | `tests/_nanowell_synth.py` | `done` | `tests/_nanowell_synth.py` and `tests/test_nanowell_synth.py`: 7 tests pass for both formats, complete circular rosters, central-square flags, seeded holes/dropouts/outliers, clipping, both explicit mirror axes, and rigid plate motion with a hidden square. Replacing the roster by observed sites or reversing screen rotation produces five named AssertionErrors in the mutation worktree. Point positions are micrometres; rotation is screen-counter-clockwise, then explicit x/y reflection. Fixtures do not simulate phase optics; Q7 real-data evidence remains outstanding |
| G10 | The discriminating fixture: two observations of one array, the second shifted 1.5 pitches and rotated 3°, window centre not on the square — every site keeps its address. Mutation: origin = "site nearest window centre" must fail with an `AssertionError` that says why | `tests/test_nanowell_addressing.py` | `done` | Two genuinely fitted and boundary-anchored observations shifted by 1.5 pitches and rotated 3 degrees recover every observed ground-truth address, with hidden central sites and off-square window centres. Both return the same complete roster for one shared array-model radius. Mutating the boundary centre to the observed window bounding-box centre fails the physical-origin assertion with an explanatory AssertionError. |
| G11 | Plate-rigid fixture: a plate moved by more than a pitch between timepoints with one well's square hidden; G8 recovers its addresses. Mutation: carry the previous timepoint's origin unchanged — must fail | `tests/test_nanowell_addressing.py` | `done` | Twelve synthetic wells move by more than one pitch and rotate 3 degrees. Eleven independently fiducial-anchored wells fit the plate transform; the remaining well has its square hidden and only an interior window. The actual lattice fit, plate prediction and resolver recover all its addresses. Replacing predict_origin position with the previous origin fails the new-origin assertion with an explanatory AssertionError. |
| G12 | Both new modules in `_REQUIRED_REMOTE_FILES` (`services/remote_manifest.py`); confirm `tests/test_a_module_lives_at_its_widest_use.py` and `tests/test_first_party_imports_resolve.py` accept a module whose only importers are tests — if one does not, report it rather than work around it | `services/remote_manifest.py` | `done` | Both modules added to _REQUIRED_REMOTE_FILES. Initial targeted run: remote-manifest 6, widest-use 4, first-party-import 2 tests all pass with test-only callers. Omitting entries in the mutation worktree makes test_manifest_includes_all_pipeline_and_util_files fail with AssertionError naming both modules; no guard exemption or workaround |
| G12a | Update the legacy exact-manifest contract test for the two new modules | `tests/test_remote_runner.py::test_required_files_list_is_what_we_promised` | `done` | Full lane on 5fee4745 first failed with AssertionError because this independent tuple omitted both modules. Added exactly those entries in manifest order, preserving exact equality and the headless requirements contract; the remote-runner/manifest follow-up has 151 passing tests |
| G13 | Close-out per §4.2: checks; an ARCHITECTURE §6 section describing the addressing and `pipeline/nanowell_lattice.py` as they exist; one patch bump in `version.py` (after `git diff origin/main -- version.py`); §3.4 here trimmed to a pointer where ARCHITECTURE now holds it; report handed back. The PR opens after review | — | `done` | Scientific self-review of the Section G diff on a8bbe41e (carried in PR 1's description; the standalone review file was removed because CLAUDE.md §5 keeps write-ups out of `_Docs/Markdowns/`): no additional blocker within the specified model assumptions; finite-difference covariance agrees with reported rigid uncertainty within one part per million. This is inline self-review, not independent external or real-data validation. The full guard family, section tests, manifest/import contracts and cloud-runtime regressions pass: 327 tests on a8bbe41e; Ruff and whitespace checks pass. ARCHITECTURE §6.2e describes the implementation; §3.4 preserves future design with pointers for built parts; the existing version change remains one patch increment over origin/main. Full fast lane on c86314df (the close-out commit; the following commit changes documentation only): 10,977 passed, 10 skipped, 0 failed, exit 139 being the expected PySide teardown. Q7/R8 real-data work and N3a/N8a integration remain outside Section G. No PR is opened under §4.2; opening still awaits user approval |
| G13a | Diagnose scratch-path ancestry failures before attributing them to nanowell code | Cloud test runtime; `tests/test_remote_transit_volume.py`, `tests/test_run_completeness.py` | `done` | Both failures reproduce on unchanged base c7a0d158 with ALLWELL_SCRATCH_DIR unset: read-only home falls back to /tmp, which also contains pytest fixtures. The same two tests pass on that base with a fresh --basetemp outside /tmp under /workspace/.test-tmp. Existing PipelineRunner calls can clear the process scratch override. Final validation uses an owned, non-hidden workspace basetemp under /workspace/test-tmp; no outside-Section-G application/test code is changed or assertions relaxed |
| G13b | Use a non-hidden pytest basetemp so image grouping can scan test images | Cloud test runtime; `tests/test_image_grouping_characterization.py` | `done` | Full lane on fcb31f65 passed the scratch tests but failed two image-grouping tests because the chosen parent /workspace/.test-tmp is hidden. The unchanged base c7a0d158 reproduces both failures under a hidden parent and passes both under /workspace/test-tmp. The scanner deliberately rejects paths with hidden components. Corrected the runtime path, not image-discovery code or assertions |
| G14 | Adversarial review of #960 (independent reviewer): three wells must be enough with noise | `pipeline/nanowell_lattice.py` | `done` | Reproduced: 39 of 200 noisy 3-well fits survived. The winning pair fits exactly, so the median residual is zero and the cutoff rejected the third well. The first cutoff now takes its scale from the wells the pair did not define. `test_three_wells_are_enough_even_with_measurement_noise`: 200 of 200 succeed and recover the rotation to under 0.1 degrees; on the base commit the first failure is trial 1 and most trials fail |
| G15 | Review: a fiducial or array centre far from every site was snapped and reported `anchored` | `pipeline/nanowell_lattice.py` | `done` | Reproduced: `array_centre=(1e6,1e6)` returned `anchored`. Evidence more than a quarter pitch from every site is refused with the distance in the reason; the next source is then tried. Three tests; the first two fail on the base commit (`anchored` returned) |
| G16 | Review: the plate-rigid gate tested the transform's error, not the target origin's | `pipeline/nanowell_lattice.py` | `done` | `OriginPrediction.uncertainty_with_site` = variance x (1 + leverage) is the error of the target's true origin and is what `resolve_origin` tests against a pitch. The G8a calibration (2.82 vs 2.86 um) was true of the narrower quantity, which is kept as `uncertainty`. New simulation scatters the target too: with-site RMS ratio 1.0 +- 0.15, transform-only ratio above 1.8 (2.48 when the term is removed: the test fails) |
| G17 | Review: `roster` ignores its `origin` argument | `pipeline/nanowell_lattice.py` | `declined` | By design, now stated in the docstring and pinned: addresses are relative to the anchored origin and the square is at the array's physical centre, so the disk is centred on address (0,0). The reviewer's case, an origin away from the array centre, cannot arise unless the square is not at the centre, which is the assumption of the whole addressing scheme (§1.1) |
| G18 | Review: `ok=True` for a hex rotation of 35 or 55 degrees, addresses wrong by a sixth of a turn | `pipeline/nanowell_lattice.py` | `declined` | Known and documented from the start: the lattice alone cannot tell theta from theta +- 60 degrees, the ARCHITECTURE text says so, and N3a (the square nanowell's own orientation) is the check that catches it. Not fixable inside this module without that evidence. Recorded so the next reader sees it was seen again |
| G19 | Review minor: variance after the outlier cut is slightly biased low (499 of 500 retained); a mirrored stage refuses without naming reflection; a doubled point list refuses instead of deduplicating; the final lattice cutoff reused the previous iteration's | `pipeline/nanowell_lattice.py` | `done` | The last is fixed (the cutoff is recomputed from the final basis; all lattice tests unchanged). The other three are safe refusals or a negligible bias, recorded and not changed |

### Section R — The acquisition record (PR 2)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| R1 | Record format, reader and writer (stdlib only, atomic write through `util/json_write`, a format version); validation against the dataset's archives (a record naming wells or fields that are not there, or missing ones that are, is reported) | `util/acquisition_record.py` | `done` | util/acquisition_record.py defines format 1 at in/acquisition.json, nullable per-axis scale/size/overlap metadata, exact tile identities, optional source-file attribution and ISO starts without inventing timezones. Missing records return None; malformed, unsupported and duplicate-key records raise an explicit error. Atomic writes use util/json_write. Archive validation uses ZIP headers, the caller's schema and the shared input-member predicate; missing/extra wells and tiles, unreadable archives and bad names are reported without reading pixels. Twenty record tests pass, and the complete guard/manifest/import set has 94 passing tests. Mutations skipping missing-tile checks, using a non-atomic writer, or replacing missing metadata with zero each produce a named AssertionError. Converter and dataset integration remain R2-R7; the converter producer now exists under R3 |
| R1a | Carry the new acquisition-record utility in the complete remote manifest and its exact contract test | `services/remote_manifest.py`, `tests/test_remote_runner.py` | `done` | The acquisition-record utility is included in the complete manifest and the legacy exact tuple without loosening either contract. Manifest six-test coverage and the exact tuple pass; omitting the module produces AssertionError in test_manifest_includes_all_pipeline_and_util_files. Record-file transport remains R6 |
| R1b | Move pixel size, tile size, objective and declared overlap from the dataset level to each timepoint, with the timepoint's start beside them (§3.0.1). Review found the record could not describe a dataset Concatenate builds from acquisitions with different tile plans or magnifications, which §3.0.3 accepts. Format version 1 is unreleased, so it changes in place; validation, the archive check and the 20 record tests follow | `util/acquisition_record.py`, `tests/test_acquisition_record.py` | `done` | Unreleased format 1 now keeps independent per-timepoint pixel size, tile size, objective, declared overlap and shared start, with start_reason and tiles_without_time beside it. Dataset calibration and per-tile clocks are refused. Twenty-two record tests cover differing magnifications/tile plans, nullable axes, archive coverage and atomic preservation; skipping per-timepoint scale validation produces AssertionError in test_each_timepoint_keeps_its_own_calibration_and_clock. |
| R2 | CZI reader exposes pixel size (`Scaling`), each tile's mosaic X/Y from the subblock directory, stage positions from the metadata, and each timepoint's absolute start — numpy-free (`tests/test_a_scan_worker_imports_no_pixels.py`) | `pipeline/czi_read.py` | `done` | AcquisitionClock reduces transient channel/Z readings to the earliest comparable tile instant, retains offset presence, counts unreadable times once per spatial tile and refuses mixed zoned/unzoned clocks with a reason. Disjoint published-well clocks merge without storing per-well or per-tile times downstream. position_metadata uses the existing indexed plane roster; the initial header scan keeps its sampled seek budget. Real-byte and reduction tests pass; choosing the latest stamp, dropping missing counts, or retaining a start across mixed clocks each produces a named AssertionError. R3 wires this reduction where conversion visits every tile. |
| R2a | Preserve the representative position's absolute timestamps separately from elapsed hours, including missing-first and mixed-timezone refusals | `pipeline/timepoints.py`, `pipeline/czi_read.py` | `done` | TimeScan.instants_by_index retains ISO instants without extra seeks, independently of elapsed-hour calculation. Missing-first and mixed-timezone cases preserve the measured stamps while continuing to refuse undecidable elapsed intervals. A mutation discarding instants produces AssertionError in test_mixing_zoned_and_unzoned_instants_is_refused. These readings are explicitly not asserted to be earliest acquisition starts across the plate. |
| R2b | Resolve what per-timepoint absolute start denotes | — | `done` | Answered by the user: a timepoint carries one time, shared by every well and tile in it, even when the scan takes an hour. Defined as the acquisition's start, its earliest recorded tile instant across the whole acquisition, whichever wells a run converts (amended by R3f; first written as "across the wells written", which would let a conversion done in parts name one timepoint two ways), computed where the converter visits every tile (R3), since the header scan samples one position. No per-well or per-tile time is recorded or used downstream. Mixed zoned and unzoned readings leave the start empty with the reason; tiles without a reading are counted beside it. Written into §3.0.1 |
| R2c | Establish native stage-coordinate units before emitting micrometres | `pipeline/czi_read.py` | `done` | ZEISS libCZI confirms METADATA/Tags/StageXPosition and StageYPosition. OME Bio-Formats ZeissCZIReader.parseMetadata explicitly interprets these tags in micrometres; this supplies the native convention without relying on the fixture. ZEISS ScalingInfo explicitly defines Scaling/Items/Distance/Value in metres and DefaultUnitFormat as display only. A stage-unit mutation multiplying values by one million produces AssertionError in test_exact_plane_metadata_reads_no_pixel_bytes_or_pixel_modules. Real ZEN population and cross-acquisition comparability remain R8/Q7. |
| R2d | Invalidate persistent scans from the old reader/shape and extend the complete round-trip fixture for acquisition metadata | `pipeline/scan_cache.py`, `tests/test_the_scan_is_remembered_on_disk.py` | `done` | Full fast lane on 470f84b2 found three genuine failures: the all-fields fixture omitted pixel_size_um for CziLayout and _PlateMetadata, and the parser-version/digest guard correctly rejected the changed reader. The generic serializer already supports the added fields. Parser and format versions now both advance so old scans cannot default available measurements to null; the signature guard is retained. Non-default scale and instants survive real cold/warm disk-cache scans without opening a source file, and all-fields coverage includes TimeScan. Independently reverting either version produces AssertionError in test_pre_acquisition_metadata_cache_is_reparsed for the corresponding key. The focused cache/metadata/timing set passes 84 tests. |
| R2e | Include the new scale and absolute-timing producers in the persistent reader signature guard | `tests/test_the_scan_is_remembered_on_disk.py` | `done` | The signature now includes the new scaling producer and finite-number parser, the timepoint scan and representative-position selector, timestamp parsers/normalizer and TimeScan. A scaling-only mutation changing the conversion factor without changing PARSER_VERSION produces AssertionError in test_a_changed_reader_invalidates_every_cached_scan. This extends the existing version/digest guard rather than loosening it; no further cache-version increment is needed because runtime outputs remain identical to R2d. |
| R2f | Pin that a tile instant without a timezone stays without one through `read_tile_metadata`. Review measured that stamping such an instant as UTC left all 71 record and CZI metadata tests passing, although the record's rule is that a missing timezone is never invented. Mutation: the UTC stamp must fail the new test | `tests/test_czi_acquisition_metadata.py` | `done` | test_unzoned_tile_instant_stays_unzoned reads real ZISRAW bytes and compares the exact unzoned ISO value. A mutation appending a fabricated UTC offset produces a named AssertionError. |
| R3 | The CZI exploder writes the record with the wells it converted, in the same publish; a resumed or partial conversion records exactly the wells it wrote | `pipeline/czi_explode.py`, `datasets/czi_import/apply.py` | `done` | Publisher and whole-acquisition naming are implemented. Twenty real-byte tests cover exact coverage, cancellation, guarded metadata failure/recovery, split files, source changes, five-hour labels, rounded jitter, old-rule resume re-conversion and unknown-clock fallback. Focused checks pass; Final full-lane verification is reported under §4.2 after the close-out commit. Whole-acquisition subset-clock and five-hour mutations each produce named AssertionErrors; single-frame 0h and rounded labels follow the user’s explicit answers. |
| R3a | Support explicit record-file paths for the converter's arbitrary input folder, and guard reads during multi-file publication | `util/acquisition_record.py` | `done` | Explicit file APIs and pending marker are implemented; the absent-JSON guard test passed and disabling the guard produced a named AssertionError. The final affected-test and complete-guard set passes 633 tests; the full lane follows the close-out commit under §4.2. |
| R3b | Extract record assembly from actual published ZIP headers and native plane metadata, including resume/cancellation and complete remote-manifest coverage | `pipeline/czi_acquisition.py`, `services/remote_manifest.py`, `tests/test_remote_runner.py` | `done` | RecordBuilder and guarded publication are implemented and included in the remote manifest. Coverage and manifest mutations produced named AssertionErrors; cancellation/resume tests pass. The final affected-test and complete-guard set passes 633 tests; the full lane follows the close-out commit under §4.2. |
| R3c | Reject source changes during conversion rather than publishing old pixels with newly read acquisition metadata | `pipeline/czi_acquisition.py` | `done` | Immutable source size/mtime checks are implemented before and after metadata assembly. Disabling them produced a named AssertionError in the source-change test. The final affected-test and complete-guard set passes 633 tests; the full lane follows the close-out commit under §4.2. |
| R3d | Establish the native objective and declared-overlap metadata paths before populating those optional fields | `pipeline/czi_read.py` | `declined` | Declined for PR2 by the user: the record keeps objective and declared overlap null; the overlap that matters is measured from recorded positions (T2), and the objective is informational. Reopen if Q7’s real CZIs show the fields. |
| R3e | Derive each timepoint's tile dimensions from its published native planes rather than the file-wide maximum | `pipeline/czi_read.py`, `pipeline/czi_acquisition.py` | `done` | Exact native subblock dimensions now reduce over published planes per timepoint. Restoring file-wide maxima produced a named AssertionError in test_timepoint_dimensions_do_not_inherit_another_acquisitions_maximum. The final affected-test and complete-guard set passes 633 tests; the full lane follows the close-out commit under §4.2. |
| R3f | Filename timepoint tokens versus the shared acquisition starts. Raised by the worker: a synthetic CZI produced tokens `0h`/`4h` (elapsed times of the sampled field) while the shared starts were 09:00/14:00, five hours apart | `pipeline/czi_explode.py`, `pipeline/timepoints.py` | `done` | Answered by the user in review: regenerate the tokens from the shared starts, so a timepoint's label is its one time (§3.0.1). The start is taken over the whole acquisition, not the wells written (R2b amended), so a plate converted in parts is named identically to one converted at once; a partial conversion therefore reads the metadata, not the pixels, of every tile in the source, opening every file of a Split Scenes set. No comparable start keeps positional `t0`/`t1` tokens and says so. A resumed conversion never mixes rules: wells already named under the other rule are re-converted. R3 implements it; its tests must cover: the 09:00/14:00 fixture named `0h`/`5h` with the record agreeing; converting A1–A6 and then the rest producing exactly the tokens of one full conversion; a resume over old-rule archives re-converting them; and the positional fallback |
| R3g | Preserve native timepoint identity through provisional and regenerated ZIP names, including interrupted renames and resume | `pipeline/czi_acquisition.py`, `pipeline/czi_explode.py`, `util/acquisition_record.py` | `done` | Native ordinals are stored in member comments and timepoint provenance. A mutation removing comments produced a named AssertionError; archives named under the sampled-clock rule are re-converted. The final affected-test and complete-guard set passes 633 tests; the full lane follows the close-out commit under §4.2. |
| R3h | Resolve whether shared elapsed-hour labels retain the existing cadence rounding | `pipeline/czi_acquisition.py` | `done` | User answered: use the rounded label for 09:00:00 and 13:00:01 (4h). Whole-acquisition starts feed elapsed_hour_tokens; precise absolute starts remain recorded. Bypassing the existing formatter produced a named AssertionError in the rounding test. The final affected-test and complete-guard set passes 633 tests; the full lane follows the close-out commit under §4.2. |
| R3i | Keep converter complexity within the existing ratchet after publication integration | `pipeline/czi_explode.py`, `pipeline/czi_acquisition.py` | `done` | Setup, source discovery and resume selection were extracted. The original guard failures measured CC 43 and then 41; the unchanged complexity and line-budget guards now pass in the 383-test set. The final affected-test and complete-guard set passes 633 tests; the full lane follows the close-out commit under §4.2. |
| R3j | Close the old archive before publishing renamed members on Windows | `pipeline/czi_acquisition.py` | `done` | Old ZIP readers close before replacement. Reversing reader/writer nesting produced a named AssertionError at the simulated Windows replacement boundary. The final affected-test and complete-guard set passes 633 tests; the full lane follows the close-out commit under §4.2. |
| R3k | Whole-source clock reduction must not expand the reported stride output | `pipeline/czi_acquisition.py` | `done` | The original stride test failed with all six source tokens reported. The corrected result lists only published tokens; all 22 stride tests and the 43-test push check pass. The final affected-test and complete-guard set passes 633 tests; the full lane follows the close-out commit under §4.2. |
| R3l | Resolve timestamp-free single-timepoint naming | `pipeline/czi_acquisition.py` | `done` | Answered by the user: use 0h for a single frame. The converter now labels that frame 0h while preserving started_at=null and the missing-reading count/reason; no absolute clock is invented. test_single_frame_without_stamp_is_zero_hours_but_start_is_unknown failed with a named AssertionError before the change. Multi-frame unknown starts remain positional. The initial all-CZI regression run exposed nine single-frame filename expectations; those were updated to the user’s new 0h rule without changing their pixel, projection, mapping, completeness or cancellation assertions. The final affected-test and complete-guard set passes 633 tests. |
| R3m | Do not silently relabel retained old-rule archives excluded from a resumed well selection | `pipeline/czi_acquisition.py` | `done` | A selected old-rule well is re-converted, but initial publication could rename an unselected retained archive. Refuse that incompatible selection before writes and name the well that must be included for re-conversion.  Removing the retained-name check produces a named AssertionError in test_unselected_old_rule_archive_is_refused_instead_of_silently_relabelled; the source archive remains byte-identical on refusal. |
| R3n | Preserve provenance for every file contributing the whole-acquisition clock | `pipeline/czi_acquisition.py` | `done` | A Split Scenes subset can record the start from an unconverted file, while source_files/source_file_stats describe only published image sources. Retain separate clock_source_file_stats for every scanned source so the shared start has complete source identity without advertising unwritten tiles.  The Split Scenes subset test first failed with a named AssertionError for the missing second clock source; it now passes while tile provenance still names only published images. |
| R3o | Update the remaining multi-scene single-frame filename expectation to the user’s 0h rule | `tests/test_several_scenes_make_one_well.py` | `done` | Only the expected single-frame token was changed to the user’s 0h rule; pixel and field-mapping assertions remain. All 17 multi-scene tests pass in the final 85-test affected/guard run. The earlier full-lane failure was a named AssertionError on the former t0 expectation. |
| R4 | Split Scenes sets (`pipeline/czi_set.py`): the record is assembled across the set's files | `pipeline/czi_set.py` | `done` | Assembly attributes published tiles to each Split Scenes file, including several files for one well. Attribution mutation produced a named AssertionError; split-source tests pass. The final affected-test and complete-guard set passes 633 tests; the full lane follows the close-out commit under §4.2. |
| R5 | Subset filters the record to the kept wells and timepoints; Merge Plates remaps its wells; Validate recognises it and checks it against the archives | `datasets/subset/`, `datasets/merge_plates/`, `datasets/validate/` | `done` | Subset filters exact published identities; Merge Plates remaps wells and refuses conflicting shared metadata before writes; Validate reports presence, corruption and coverage mismatches. Subset, conflict-refusal and Validate mutations each produced named AssertionErrors. The final affected-test and complete-guard set passes 633 tests; the full lane follows the close-out commit under §4.2. |
| R5a | Resolve conflicting timepoint metadata when Merge Plates combines acquisition records | `datasets/merge_plates/` | `done` | Answered by the user: as needed, refuse with a specified reason. Merge Plates keeps existing timepoint labels and combines wells; successive acquisitions use Concatenate (PR3), whose directory order and GUI interval define the later timeline. The implementation refuses differing shared timepoint metadata and a record present in only one input, naming the reason before destination writes. |
| R6 | Remote: the record is uploaded with the input and read by the remote pipeline; extraction to pay for any line in `services/remote_runner.py` | `services/` | `done` | Staged/scoped and batched uploads carry filtered JSON. Record content changes upload/re-attach identities; the pipeline reads and validates metadata before pixels. Staged-input description, batch blank selection and sidecar preservation were extracted within unchanged budgets; remote_runner.py is unchanged. Upload, cache and pipeline-read mutations produced named AssertionErrors. The final affected-test and complete-guard set passes 633 tests; the full lane follows the close-out commit under §4.2. |
| R6a | Make invalid acquisition metadata a real CLI refusal rather than a swallowed sidecar warning | `process_microscopy.py`, `tests/test_acquisition_remote_transport.py` | `done` | The actual CLI test first failed with three named AssertionErrors for coverage mismatch, corrupt JSON and pending publication. Validation now runs before the best-effort sidecar block; all three refuse before pixel setup. The final affected and complete-guard set passes 85 tests, exit 0, in 55.94 seconds. |
| R7 | The CZI import page states what was recorded (pixel size, positions present, start time) so a file that lacks them is visible before analysis | `datasets/czi_import/view.py` | `done` | The real controller/page shows unknown source scale and provisional naming, then recorded per-timepoint scale, mosaic/stage coverage and known/unknown shared starts after conversion and its final refresh. Hiding the recorded readout produced a named AssertionError. The final affected-test and complete-guard set passes 633 tests; the full lane follows the close-out commit under §4.2. |
| R8 | Real data: which fields the user's ZEN version populates, and whether mosaic or stage positions are comparable across separate acquisitions (decides whether G8's fallback can run) — recorded here | — | `blocked` | Q7. The user explicitly states this does not hold PR2. |
| R9 | Close-out: README CZI import section, ARCHITECTURE, version | — | `done` | Docs: README's CZI import section and ARCHITECTURE describe the record, whole-acquisition starts, rounded naming, single-frame 0h, guarded recovery, Dataset-tool propagation and scoped remote transport. Version 9.11.12, one increment over PR 1's 9.11.11 (R9a). R3d declined; R8 stays blocked on Q7 and does not hold the PR. Full fast lane on 2ef45281: 11,063 passed, 10 skipped, 0 failed, exit 139 being the expected PySide teardown. Two failures from the earlier lanes were fixed first: on 3ad40405 a stale single-frame `t0` expectation in test_several_scenes_make_one_well.py, and on f487db24 the facade guard, because the new CLI test reached `_suspend` and `plan_parallelism` through process_microscopy. That test now patches `pipeline.suspend` and lists `plan_parallelism` in BINDING_SEAMS with its reason. `scripts/preflight.sh --quick` reports 9.11.10 → 9.11.12 against origin/main as two increments; expected on this stacked branch per CLAUDE.md §6 and not corrected. Reviewed by the user before the PR opened, which it does against claude/nanowell-1-geometry, to be retargeted to main once #960 merges |
| R9a | Resolve the stacked patch version against the root single-increment rule | `version.py`, `scripts/preflight.sh` | `done` | Answered by the user: measured against its real base the bump is one; do not correct a stacked version to satisfy preflight. PR2’s base is PR1’s branch. main then took 9.11.10 through PR #961, PR1 merged main and re-bumped to 9.11.11, and this branch merged PR1 and moved to 9.11.12: still one increment over its real base. Preflight’s two-increment report against origin/main is expected on this stacked branch; the guard remains unchanged. |
| R9 | Adversarial review of #962: a cancel or failure was replaced by the error from publishing the partial record, and the marker stayed | `pipeline/czi_acquisition.py` | `done` | Reproduced: a conversion of experiment A, then experiment B over the same folder cancelled before it replaced A's archive, raised `AcquisitionRecordError: A01.zip: 'A_phase_A01_f0_0h.tif' does not belong to this CZI conversion` instead of `CziExplodeCancelled`. `acquisition_publication` now publishes the partial record without letting a publication failure replace the error in flight (the failure is logged, the marker stays, which is the state that says the conversion must be resumed). `test_a_cancel_stays_a_cancel_when_the_partial_record_cannot_be_published` fails on the base commit with the reviewer's error |
| R10 | Review: a single-frame CZI with an unknown clock was given a degradation reason for its name | `pipeline/czi_acquisition.py` | `done` | The name is `0h` by rule, so nothing about it is degraded; `record['timepoint_naming']['reason']` and the import page said 'Some shared acquisition starts are unknown' anyway, contradicting the repo's rule that a degradation message describes a real degradation (and `elapsed_hour_tokens`, which says nothing here). The reason is now None for one frame; the unknown start stays visible as the timepoint's null `started_at` and the page's 'Shared starts: 0/1'. Three tests that asserted the false reason were rewritten around the report (CLAUDE.md §6, a failing test may be asserting the bug) |
| R11 | Review: a resumed conversion read the previous record with plain `json.loads` | `pipeline/czi_acquisition.py`, `util/acquisition_record.py` | `done` | Confirmed by reading and by test: duplicate JSON keys and malformed JSON were accepted or raised the wrong type on the resume path. New `parse_record_text` is the strict parse without the publication-marker check; the builder uses it. `test_a_resume_reads_the_previous_record_as_strictly_as_any_other_reader` fails on the base commit (`DID NOT RAISE`) |
| R12 | Review, not reproduced: `merge_plate_records` refuses any difference in a shared timepoint's metadata, including `tile_size_px` and `pixel_size_um` that are computed only from published archives, so two partial conversions of one acquisition could differ (null vs known) | `util/acquisition_record.py` | `todo` | Unconfirmed: the reviewer read it and did not run it. It fails loudly, not silently. Reproduce with two partial conversions before changing anything |
| R13 | Review: `source_fingerprint` (remote scratch) and `acquisition_cache_key` hash the record with different JSON separators | `services/remote_scratch.py` | `declined` | Each is used for its own purpose and compared only with itself; no value of one is ever compared with the other. Not changed |

### Section M — Merging acquisitions across time (PR 3)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| M1 | Gap rule: dataset 2's first timepoint = dataset 1's last + the gap; the gap is a parameter of the plan (`compute_offset` takes it), positive, read through `util.timepoint.parse_hours`; overlap still refused | `datasets/concatenate/concatenate_compute.py` | `done` | `compute_offset(..., gap_hours=)` and `build_merge_plan(..., gap_hours=)`: dataset 2's first timepoint lands the gap after dataset 1's last; the gap is positive and finite, read through `parse_hours` by `parse_gap_hours`; overlap cannot occur because the gap is positive. test_an_entered_gap_places_dataset_2_where_the_user_said: a 6h gap gives 30h where the interval rule gives 48h; ignoring the entered gap fails it and the records test with AssertionErrors |
| M2 | Gap field on the page beside the dataset row, carried through the controller and worker into the plan; required (with the reason shown) when no default exists | `datasets/concatenate/view.py`, `controller.py`, `worker.py` | `done` | A field beside the dataset row (`ConcatGapField`), worker takes `gap_text` and parses it in the thread so a bad queued value fails with its reason, controller sends it, queue spec carries it. test_a_single_timepoint_dataset_needs_a_typed_gap_and_the_run_uses_it types into the real field, runs the real worker, and reads the merged sidecar; a worker that ignores the text, a controller that never sends it, and a queue spec that drops it each fail named tests |
| M3 | Pre-fill order: recorded acquisition times (both records present) → dataset 1's median interval (two or more timepoints) → empty and required. Leaving the pre-filled interval untouched reproduces today's result exactly | `datasets/concatenate/concatenate_compute.py` | `done` | `default_gap`: the recorded gap when both records give the two instants on comparable clocks (rounded to the minute), else dataset 1's median interval, else none (required). test_leaving_the_prefilled_interval_reproduces_the_old_result equals `compute_offset`'s old answer; records 30h apart give 30h where the interval gives 24h; a record on a different clock (timezone against none) is not compared and a dataset 2 recorded before dataset 1 is refused as a default. Dropping the recorded default or comparing mixed clocks fails named tests |
| M4 | An entered gap that disagrees with the recorded times beyond the naming tolerance shows both numbers as a warning and proceeds with the entered one | `datasets/concatenate/view.py` | `done` | `choose_gap` warns when the entered gap differs from the recorded one by more than max(2 %, 1 minute), naming both numbers, and proceeds with the entered one; the page shows it under the field in the warning style while Concatenate stays enabled. test_an_entered_gap_beats_the_recorded_one_and_warns_when_they_differ and test_records_prefill_the_field_and_a_different_entry_warns (36h warns, 30.4h does not) |
| M5 | Status preview lists dataset 2's resulting timepoint labels before anything is copied, and updates as the gap is typed | `datasets/concatenate/view.py` | `done` | `gap_preview` returns dataset 2's resulting labels from the same offset and format the merge uses; the page shows them and redraws on every edit and every dataset pick. test_the_prefilled_interval_is_shown_and_typing_over_it_changes_the_merge reads 48h, then 30h after typing 6h, then the merged sidecar agrees |
| M6 | Single-timepoint dataset 1 accepted whenever a gap exists (entered or recorded); the old refusal survives only as the "field is required" message | `datasets/concatenate/concatenate_compute.py` | `done` | A single-timepoint dataset 1 merges once a gap exists (entered or recorded): `ds1_interval_hours` is None on the plan. Without one the refusal is now the "enter the time gap" message, shown under the field and disabling Run. test_a_single_timepoint_dataset_1_merges_once_a_gap_is_given |
| M7 | Raw datasets before analysis (`in/`, no `out/`) — the primary path: raw + raw gives a raw destination; compatibility from the acquisition records, else from member names via `util/schema_infer.py`; verification checks images rather than measurement rows | `datasets/concatenate/`, `datasets/core/dataset_paths.py` | `done` | Two raw datasets (`in/` with an empty or missing `out/`) merge into a raw destination: no `out/`, no sidecar, no review button; the schema is inferred from the archives (`datasets/concatenate/raw_compute.py`), and refused unless `util.schema_infer` is confident and finds a timepoint slot (`well:timepoint` names are refused, not guessed). Two raw datasets must agree on separator and fields. Verification runs on the archives: independently of the copy, every dataset-2 member's expected shifted name must be in the destination archive with the same stored CRC, and a collision or a missing member fails. A raw dataset beside an analysed one is refused naming which (analysed first waits on M12; raw first is declined, M12a). tests/test_concatenate_raw.py: the shifted names and each image's own bytes, wells held by one dataset, single-timepoint dataset 1 with a gap, the page end to end. Mutations that skip the offset, skip verification, ignore the CRC, accept raw beside analysed, ignore a schema mismatch, or accept an unconfident schema each fail a named test (the offset one through the verification's own collision message) |
| M8 | Differing tile plans accepted (each timepoint keeps its positions); a differing pixel size is a preview warning, not a refusal | `datasets/concatenate/concatenate_compute.py` | `done` | Nothing in the compatibility check reads tile plans, so acquisitions with different tile sizes merge; `scale_warning` reads each timepoint's recorded pixel size (dataset 1's last, dataset 2's first) and, when they differ by more than 1 %, says so in the page's preview, the merge log and the plan, without refusing. An unrecorded size is not a mismatch. test_acquisitions_with_different_tile_plans_merge_without_complaint, test_a_different_pixel_size_warns_in_the_preview_and_the_plan_but_still_merges, test_an_unrecorded_pixel_size_is_not_a_mismatch; never raising the warning, treating unknown as different, and refusing differing tile sizes each fail a named test |
| M9 | Record merge and provenance: dataset 2's timepoint keys shifted; each timepoint keeps its own positions and absolute start; the gap and its source (`entered` / `recorded` / `interval`) written to the merged record and to `record_derivation` (**raw merges: not to `record_derivation`, which needs a sidecar they do not have; see M18**) | `datasets/concatenate/apply.py`, `datasets/core/provenance.py` | `done` | `util.acquisition_record.concatenate_records` and the executor's `_merge_acquisition_records`: dataset 1's timepoints keep their tokens, dataset 2's are renamed by the same shift its images got, and each timepoint keeps its own calibration, absolute start and tile positions; source-file names are prefixed `dataset1/` / `dataset2/` (with the stats keys) and a `concatenations` list records the gap, its source and the offset. Written to `in/acquisition.json` for analysed and raw merges, then validated against the merged archives, so a record that would stop the next Analyze run stops the merge. One dataset without a record: none is written and the log says so; neither: nothing. The analysed merge's `derived_from` line carries the gap and source too (a raw destination has no `out/` to hold it, so its record does). tests/test_concatenate_raw.py: own clocks, plans and positions kept (a plate put back differently), the record agreeing with the merged images, the missing-record note, the analysed path with a recorded gap. Mutations that skip the relabel, overwrite dataset 2's calibration, drop the gap, skip the record merge, or stay silent about a missing record each fail a named test |
| M10 | The discriminating fixture: dataset 1 at 0 h and 24 h, gap entered as 6 h — dataset 2 must start at 30 h, where the interval rule gives 48 h; a second case with recorded starts 30 h apart and no entry gives 30 h. Mutation: ignore the entered gap — must fail | `tests/test_concatenate_compute.py` | `done` | tests/test_concatenate_gap.py: 18 tests, each built so the interval rule gives a different answer. Three mutations (entered gap ignored, recorded default never used, mixed clocks compared) fail named tests |
| M11 | A test that types into the real gap field and presses Concatenate, and asserts the plan the worker receives (CLAUDE.md §7.6) | `tests/` | `done` | tests/test_concatenate_gap_page.py drives the real controller and page: typing into the field, the Run button's state, the real worker's merged sidecar, and the queued spec. Five mutations (worker ignores text, controller never sends it, Run not blocked by a gap error, queue drops it, default not pre-filled) fail named tests |
| M12 | Analysed + unanalysed (secondary): destination keeps dataset 1's outputs; a test runs an incremental Analyze on the result and checks that exactly dataset 2's timepoints are processed | `datasets/concatenate/` | `done` | An analysed dataset 1 and a raw dataset 2 merge (`KIND_ANALYSED_RAW`): dataset 1's measurements, QC images and sidecar are kept as they are (the sidecar is not unioned with the channels inferred from dataset 2's filenames), dataset 2's images are added shifted, and the log and summary say to run Analyze. No well is flagged for missing rows because of dataset 2. A raw dataset 1 beside an analysed dataset 2 is refused naming the order to use (M12a). tests/test_concatenate_raw.py asks the pipeline's own incremental rule what is left: `fov_keys_from_member_names` on the merged archive minus `read_done_fov_keys` on the merged CSV is exactly `{(F001, 48h)}`. A real incremental Analyze run is not performed (it needs a segmenter); the rule it applies is. Mutations that refuse a raw dataset 2, union the sidecar, flag every well, skip dataset 2's image check, or accept raw first each fail a named test |
| M12a | Raw dataset 1 + analysed dataset 2 | `datasets/concatenate/concatenate_compute.py` | `declined` | It would leave earlier timepoints unanalysed beneath analysed later ones, and the other three combinations cover every real case better. Refused with a message naming the order to use instead. Reopen if the user asks for it |
| M13 | Several datasets in one run, each with its own gap field (one copy instead of one per added timepoint) | `datasets/concatenate/` | `deferred` | Not built in this PR. Measured against the code: the plan (`MergePlan` has `ds1`/`ds2`), the executor (`merge_input`, `_MemberRename`, `merge_measurements`), both verifiers and the record merge are two-way throughout, so a one-copy N-way merge is a redesign of those layers, not a field on the page. Its value is the copy cost of one Concatenate per added timepoint, which is Q9 (experiment size), still unanswered; M14 is blocked on the same question. Until then the workflow is one Concatenate per added timepoint |
| M14 | The copy cost at the user's scale, and whether an append that avoids a full copy is needed — a layout question, because a zip archive cannot be extended atomically without being rewritten | — | `blocked` | Q9 |
| M15 | Close-out: README Concatenate section rewritten for the gap field and raw datasets (including the refusals it no longer makes), ARCHITECTURE §6b, version | — | `done` | README Concatenate section rewritten (Gap field, raw datasets, analysed + raw, changed refusals); ARCHITECTURE gains the raw_compute row and a gap/raw/record section; version 9.11.13. Full fast lane: 11,111 passed, 1 failed (test_no_inline_pixel_sizes, the 140 px gap-field width written inline), fixed by naming `_GAP_FIELD_W`; the guard file passes after the fix |
| M16 | Adversarial review of #963: typed gaps were misread by the timepoint-label grammar's trailing-digit fallback | `datasets/concatenate/concatenate_compute.py` | `done` | Confirmed: `1,5` gave 5 h, `6h30` 30 h, `12:30` 30 h, `1d6` 6 h, `2d 6` 6 h, `abc7` 7 h, `1_0` 10 h, and the README's `1d 6h` failed outright. `parse_gap_hours` has its own strict grammar (bare hours, or number+unit parts in descending order, each unit once, a comma decimal, `1,500` refused as ambiguous, over ten years refused as a unit typo). 29 of the new parametrised cases fail on the base commit |
| M17 | Review: `format_gap` used `:.4g`, so the pre-fill lost minutes and large gaps formatted as `1e+04h` which does not parse | `datasets/concatenate/concatenate_compute.py` | `done` | Confirmed. `format_gap` now writes whole minutes (`100h 1m`, `45m`) and parses back; the interval default is rounded to the minute so the pre-fill is exactly what the merge uses (24.0067 h shown as `24h` and used as 24.0). Compound-format labels were also off by up to 30 s for the same reason and are now exact at minute resolution |
| M18 | Review: a raw merge writes no `derived_from`, contradicting the claim that the gap and source are stored there | `datasets/concatenate/apply.py` | `done` | Confirmed and answered by narrowing the claim, not by adding a file: a raw dataset has no sidecar, the merged acquisition record carries the gap when either dataset had one, and when neither did the log now says the gap is recorded nowhere else (the test that asserted this was silent was rewritten around the report). M9's evidence text corrected here |
| M19 | Review: an image of dataset 2 with no timepoint field lands unshifted and verification passes | `datasets/concatenate/apply.py` | `done` | Confirmed, and the reviewer's 'no warning' was half right: a generic count was already logged, but the headline said success. `_verify_raw` returns the unshiftable *images* (not `notes.txt`); the log names up to five and the summary counts them. Not refused, as in the analysed path |
| M20 | Review: two datasets spelling a well differently (`A01.zip`, `A1.zip`) merge into one archive holding both spellings | `datasets/concatenate/concatenate_compute.py` | `done` | Confirmed; refused at planning from the container names, with both named. `test_two_datasets_spelling_a_well_differently_are_refused` fails on the base commit (the merge completed) |
| M21 | Review, not reproduced: the gap preview runs on the GUI thread for every keystroke and re-infers a raw schema from the archives each time; computed twice per dataset change | `datasets/concatenate/view.py` | `todo` | Unconfirmed: read from the code, not timed. Measure on a large raw plate before adding a debounce or cache |

### Section T — Tiles and registration (PR 4)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| T1 | Placement from the acquisition record; scan-pattern placement (16 patterns) for data without one; a timepoint short of rows×cols tiles is `incomplete_grid` naming the missing tiles | `pipeline/tile_grid.py` | `done` | `tile_grid.place_from_record` / `place_from_pattern` / `tiles_from_record` / `scan_cells`. Grid shape and calibration are read per (well, timepoint): a 5x5 grid at 10 % and a 4x6 grid of non-square tiles at 15 % both place, and the record's second timepoint at another pixel size is read with its own calibration. A tile short of the grid is `incomplete_grid` naming `r2c3`; a whole absent middle line is seen from the positions, a whole absent outer line only with `expected_shape` and is then named "1 column(s) at an unknown outer end" because the record does not say which end (the first version mislabelled it). Decisions where the tracker was silent: a stage-only position is used only when the stage axes' direction is stated (a guessed sign mirrors the grid); mosaic and stage placed tiles are never mixed (different frames); tiles that do not touch are `bad_grid`, not an OK grid with a note. Mutations each failed a named test with an AssertionError: stage used without direction, missing never reported, serpentine ignored, rank used instead of step index, gap allowed, outer lines unnamed |
| T2 | Overlap measured per axis from positions; compared with the declared overlap; ownership condition evaluated per axis; fused-site count reported | `pipeline/tile_grid.py` | `done` | Overlap per axis = extent minus median line spacing, compared with the declared overlap (2-point tolerance). `ownership_report` classifies with `assign_owners` and states whether the overlap guarantees ownership. **Two findings, both written into the architecture text:** the guarantee needs `diameter + 2 x margin`, not `diameter + margin` as the tracker said (the margin is on both sides); and `assign_owners`' fused class holds two kinds of site, seam-straddling (`blend_indices`, need a crop blended from several tiles) and wholly inside one tile but within the margin of the outer edge (`edge_indices`), so counting every fused site as blending work overstates it even when the overlap is ample. The guarantee is sufficient, not necessary: at 10 % overlap no site straddled until the renderer put a column on the seam (`array_shift_um`). Ownership and its guarantee follow registered positions when given. Mutations caught: overlap = step, tolerance widened, edge counted as blend, required = d + margin, holds from recorded |
| T3 | Lattice-phase registration: offset modulo the lattice from each tile's fit; integer part from the recorded position; reports when the recorded error approaches half a pitch | `pipeline/tile_register.py` | `done` | `lattice_phase_constraints`: o_j - o_i = p_i - p_j modulo a lattice vector, the vector chosen by the recorded offset among the neighbouring integer pairs (rounding in lattice coordinates is not always the nearest lattice point). On rendered 3x3 and 2x4 plans with 20 um stage jitter, recorded relative error 34 um falls to under 1 um (0.29 observed, limited by the 0.3 um point noise). Warns past 40 % of a pitch. **A recorded error past half a pitch is wrong by exactly one lattice vector and nothing in the data says so**: pinned by a test, not hidden; the assumption is a stage repeatable to under half a pitch within one acquisition, and it is the error *between two tiles* that counts. Two fits whose bases differ by over 5 % of a pitch give no constraint. Mutations caught: recorded vector ignored, no ambiguity warning, neighbours = all pairs |
| T4 | Overlap correlation constrained to ±pitch/2 of the recorded offset. Fixture built so the unconstrained search locks one pitch off — the test must fail if the constraint is removed | `pipeline/tile_register.py` | `done` | `correlate_overlap`: the Pearson correlation of the pixels two tiles share at every integer offset within `search_radius_px` (just under half a pitch). The fixture (1x2 tiles, no cells, noise, vignetting) makes the unconstrained search lock exactly one pitch off with a *higher* correlation (0.67) than the right answer (0.65), asserted as a precondition; the production path finds the true offset to under 1 um. **Design change found while building:** the first version matched a fixed template cut from inside the overlap, which cannot work on the real plan, where a 10 % overlap (133 um) is narrower than one pitch (172 um); it now scores the shared region at each offset so the region may shrink. A 31 px overlap (0.78 pitch) registers. Mutations caught: window widened to 1.3 pitch, fixed-size window, overlap sigma constant, no sub-pixel (and its sign), both gates removed, flat-image threshold made absolute |
| T5 | Weighted least-squares solve; per-tile source and residual; corner tiles with no array fall back to the recorded position without failing | `pipeline/tile_register.py` | `done` | `solve_placements`: weighted least squares over pairwise constraints plus a 50 um prior, shared matrix for both axes. Weighted mean verified analytically (10 at sigma 1 and 20 at sigma 2 give 12). Each tile reports source, residual and correction. Corner tiles over a circular array (5x5, radius 1000 um) keep their recorded position, say so, and do not fail the solve. After review: the correction is measured after removing the median shift of the measured tiles (one mis-recorded tile no longer shows as a correction on four others); a pair measured by both lattice and overlap that disagree warns; the lattice uncertainty gains a stated 0.25 um systematic allowance (a choice, awaiting T10). Not a quality measure: the residual of a tile with a single constraint is always about 0. Mutations caught: weights ignored, unmeasured tile called lattice, common shift removed, disagreement warning removed |
| T6 | Scan-pattern auto-detection | — | `declined` | The CZI records positions (Q3), so CZI data never needs it; data without a record uses the explicit setting, and a guessed layout that scores well is still a guess |
| T7 | Downsampled phase montage (long side ≤ 4,096 px), blended in overlaps, 8-bit with the stretch recorded | `pipeline/tile_register.py` | `done` | `build_montage`: tiles block-averaged by one integer factor so the long side is at most `max_side_px` (4,096 default), placed at the nearest montage pixel, blended by distance from each tile's edge, 8-bit with the percentile stretch returned and a coverage mask (uncovered pixels marked, not painted). Tested with constant tiles of 0.2 and 0.8 (monotonic across the seam, ends at the stretch's own ends), the cap and the factor, rounding to nearest, and uncovered pixels. Mutations caught: no feather, stretch not recorded, floor instead of round. Photometric matching between tiles is not done and is stated |
| T8 | Synthetic tile renderer: G9's array rendered into overlapping tiles with stage jitter, camera rotation, vignetting, noise, cells, debris and a bubble — at 5×5 / 10 % and at a different plan (4×6 / 15 %), so nothing passes by assuming the current one; one tile geometry where 100 µm sites all have owners and one where they do not | `tests/_nanowell_synth.py` | `done` | `tests/_nanowell_synth.render_tiles` and `TilePlan`: one array drawn in the common frame into overlapping tiles with stage jitter, camera rotation, vignetting, per-tile noise, cells, debris, a bubble, a square centre nanowell, and `array_shift_um`. Used at 3x3/30 %, 2x4 non-square/15 %, 5x5/30 %, 1x2/50 % and 1x2 at 31 px overlap. 100 um sites all have owners at 30 % overlap (258 um >= 120) and some straddle at 10 % (86 um) with a column on the seam (`test_rendered_plans_cover_both_ownership_regimes`). Looked at, not only tested: tiles rendered and viewed |
| T9 | Fused-site crops: the covering tiles blended into one crop per seam site; cells attributed to the tile with most of the disk; row flagged `fused` | `pipeline/tile_register.py` | `done` | `fused_crop`: every covering tile sampled bilinearly at the crop's pixel centres and blended by distance from each tile's edge; cells go to the tile holding most of the disk (tie: lexically first id); reports the share of the disk no tile covers. Against truth: the blended crop of four tiles matches the same field imaged whole (rms < 0.02), a crop aligned to the pixel grid equals the image slice exactly (pins the pixel-centre convention, which comparing two crops from the same function cannot), the blend weights are exact at the first and last samples. Mutations caught: half-pixel shift, ramp weights removed, attribution picking the largest id |
| T10 | Real-data check of registration residuals and measured overlap on Q7 data | — | `blocked` | Q7 |
| T11 | Close-out (as G13) | — | `done` | Close-out. Full fast lane on the final code commit: 11,231 passed, 10 skipped, 0 failed, 604.78 s, uncontended. An earlier lane on the merged tree failed two guards (cyclomatic complexity 47 in `place_from_record`, two unannotated optional returns), both fixed by decomposing the function and annotating, and four `test_well_worker_pool.py` tests that failed in that lane while other test processes were running passed alone and in this uncontended lane. Guards run as a set inside the lane. An independent numerics review of the diff found 11 confirmed defects, all fixed or recorded (the T-row evidence names them); each fixed defect has a test that fails on the code it was found in. The reviewer's mutation list (10 mutations that left tests green) was closed with new tests, each re-mutated. README unchanged: nothing here is user-visible. Version 9.11.14, one increment over #963 |

### Section N — Nanowells in the phase channel (PR 5)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| N1 | Lattice pitch and angle from the montage's 2-D Fourier peaks; survives a bubble covering 10 % of the array | `pipeline/nanowell_detect.py` | `done` | `estimate_lattice_fft`: Hann-windowed `rfft2`, strongest peaks in the pitch ring, each refined by a local DFT a tenth of a bin wide, first-order peaks read as hex (partners at 60 degrees) or square (90). On rendered 900 px montages with a bubble over 10 % of the array: pitch within 0.13 % (171.77 against 172) and angle 7.003 against 7 at 100 um; the same at 50 um. Needs no expected pitch and ignores a wrong one (30 % off). Mutations caught: pitch/angle sign, no sub-bin refinement. One mutation is equivalent: taking the shortest of the four combinations of the two lattice vectors versus the first one, because for hex and square peaks all give the same length |
| N2 | Matched filter for a light disk on a dark lattice at the plate format's radius (100 µm, 50 µm, custom) via the recorded pixel size; least-squares lattice refinement on candidates at sites (G2/G3) | `pipeline/nanowell_detect.py` | `done` | `detect_sites`: matched filter (light disk in a dark annulus, template sums to zero so offsets and ramps change nothing), local maxima a half pitch apart above half the strong response, `fit_lattice` on them, `fit_array_boundary` on the inliers. Precision: every candidate within a tenth of a pitch of a drawn site; recall over 97 % of visible sites and under 5 % of those under the bubble; no half-pixel bias (median offset under 0.15 px; the first version was half a pixel off, found by tightening the test). Refused when a nanowell is under 2.5 px across. Mutations caught: no annulus (via the zero-sum property), candidate threshold, polarity, half-pixel convention |
| N3 | Fiducial: square-versus-circle score per central site at the lattice angle; margin over runner-up; score recorded | `pipeline/nanowell_detect.py` | `done` | `square_score` (four-fold gradient coherence; circle near 0, square 0.74) and `score_fiducial` returning a `FiducialCandidate`: best 0.74, runner-up 0.065, at the array centre to under 0.2 pitch at both formats. With the square under a bubble the margin is under 0.15 and `resolve_origin` anchors on the array centre with identical addresses. Mutations caught: two-fold coherence, candidate position from the runner-up |
| N3a | Cross-check the lattice axis against the square's orientation. The square repeats every 90° and the hex lattice every 60°, so together they fix the axis up to 180°, which a keyed plate never reaches. A square whose edges disagree with the axis the fit chose means the rotation folded back across the ±25° / ±40° limit (§3.4.2): refuse that (well, timepoint) with the reason rather than address it on the wrong axis. Fixture: a hex array rotated 40°, which the lattice alone fits as −20° | `pipeline/nanowell_detect.py` | `done` | `check_orientation`. The fixture the tracker names: a hex array rotated 40 degrees is fitted at -20, the square reads 40.5, the discrepancy is -29.5 degrees and the well is refused with the reason; 7, -20 and 24 degrees are accepted. In the end-to-end test the folded fit really does hand out wrong addresses to over half the sites, and this check is what says so. The square-to-axis angle is a parameter with an assumed default of 0 (Q11). Mutations caught: wrap period, orientation sign, tolerance, offset ignored, never refusing |
| N4 | Per-site centre and radius refinement; `fitted` versus `predicted`; no site dropped | `pipeline/nanowell_detect.py` | `done` | `refine_sites`: radial edge profiles, circle fit with outlier rejection, accepted only with 60 % of rays finding an edge, scatter under 6 % of the radius (20 % for the square), radius within 30 % of nominal, centre within a radius of the lattice position; otherwise `predicted` at the lattice position with the well's median fitted radius. Every input site has an output row. Fitted sites land within 0.8 px (median under 0.25 px) with the median radius within 1 % of drawn (49.86 against 50). The first version read the edge drop over 0.7 px and fitted nothing; the 10 % scatter limit let a 55 um radius through and became 6 %. Mutations caught: fallback radius, scatter limit, half-pixel output, relaxed ignored, ray fraction, edge-drop width |
| N5 | Occlusion: bubble mask from the montage plus per-site contrast against the lattice (an occluded site is a black disk on black); `visible_fraction` | `pipeline/nanowell_sites.py` | `done` | `bubble_mask` (lattice-frequency amplitude under 30 % of typical, inside the array only: overlap with the true bubble over 0.7, nothing flagged outside the array or in a bubble-free scene) and `site_visibility` (share of the disk bright against the lattice and outside the mask; contrast relative to the typical site). Sites wholly under the bubble: visible under 0.1 and contrast within 0.2 of zero; clear sites above 0.95 and median contrast 1.0; rim sites strictly between. A site off the image has `None`, not zero. Mutations caught: threshold, floor not excluded, midpoint, mask ignored, contrast sign |
| N6 | `imaged_fraction` and clipping at the coverage edge, the array edge and the well wall; a clipped edge never raises | `pipeline/nanowell_sites.py` | `done` | `site_coverage`: shares of a disk inside the tile union, the array boundary and the well wall, by a fixed 61-point disk sampling. Centred on a tile edge 0.5, on a corner 0.25, outside 0.0, inside the overlap of two tiles once, in either tile alone 1.0; array and well shares `None` without a boundary; no tiles gives 0.0; never raises. Mutations caught: centre-only sampling, last tile only, inverted array share |
| N7 | Per-site rim focus through `pipeline.field_quality.edge_focus` | `pipeline/nanowell_sites.py` | `done` | `rim_focus` via `pipeline.field_quality.edge_focus` on the fitted disk: falls monotonically across blur of 1.0, 2.5 and 5.0 px; `None` for a predicted site and for one off the image. Mutation caught: a predicted site given a focus |
| N8 | Cross-timepoint consistency (pitch, angle after the plate's rotation, roster) as a named status | `pipeline/nanowell_sites.py` | `done` | `check_consistency`: pitch within 0.5 %, angle within 0.5 degrees after removing the plate's measured rotation and folding into the 60-degree period, roster equal; the first violated check names the status, all are in the detail. A 10-degree plate rotation is not an angle change; 29.8 and -29.8 are 0.4 apart. Mutations caught: fold period, rotation not removed, roster ignored, both tolerances |
| N8a | One array-model radius per well, shared by every timepoint's roster: combine the per-timepoint `fit_array_boundary` radii (the median of those that fitted) and record each timepoint's deviation from it; a timepoint whose own radius disagrees by more than half a pitch gets a named status. Raised by G6a, which measured twelve marginal sites changing when each observation used its own estimate | `pipeline/nanowell_sites.py` | `done` | `shared_array_radius`: the median of the timepoints whose boundary fitted (not the mean: a 3600 outlier among 3000s would give 3120), each deviation recorded, `radius_disagrees` beyond half a pitch, `no_boundary` for an unresolved one, none dropped; no fitted boundary gives `None` and `unresolved`. Mutations caught: mean, flag threshold |
| N9 | Both formats in the synthetic tests: every N row passes at 100 µm and at 50 µm, and a 50 µm array analysed with the 100 µm setting is reported as a format mismatch rather than detected wrongly | `pipeline/nanowell_sites.py` | `done` | `check_format`: a setting whose nanowell is nearly as wide as the measured pitch is refused (a 50 um array, pitch 85.9 from the Fourier estimate, against the 100 um setting), decidable before any matched filter is chosen; and fitted nanowells over 25 % off the setting are a mismatch (37.6 passes, 37.4 fails). Every N row is exercised at 100 um and 50 um. Mutations caught: pitch test off, radius tolerance |
| N10 | Real data: addresses checked by hand on 3 wells × 2 timepoints, including a bubble and a clipped edge; counts recorded here | — | `blocked` | Q7 |
| N11 | Close-out (as G13) | — | `done` | Close-out. Full fast lane on the last code commit (48f76ae8): 11,311 passed, 10 skipped, 0 failed, 742.62 s (12 min 22 s locally, about 100 s more than before this section; CI adds about two minutes of setup against a 15-minute cap, so the margin is thin and the next section should watch it). Guards ran inside the lane. An independent numerics review of this diff found real defects (rows N12 to N22), all reproduced and fixed, one first fix was found by the new tests to regress and was replaced, eight mutations it named as uncaught were closed or recorded as indistinguishable (N23), and one reported bias was not reproduced (N24). README unchanged: nothing user-visible. Version 9.11.15, one increment over #965. N10 stays blocked on Q7 and the square-to-axis assumption is Q11 |
| N12 | Adversarial review of this section: `refine_sites` fitted nothing from a blur of 2.5 px (312 of 313 at 1, 272 at 2, 0 from 2.5), and a bright halo gave 0 of 313, so the rim focus was undefined on exactly the defocused images it should flag | `pipeline/nanowell_detect.py` | `done` | Reproduced. The edge had to show 40 % of the image's range over a 3 px drop; it now has to show the disk's plateau over the lattice's, and the scatter limit allows a quarter of the rim's width beyond a sharp edge's. 95 % fit at blurs of 2.5 and 3.5 px, 60 % at 5 px (43 % of the radius: barely a disk), and 90 % through a glow. The sites test had hidden this by passing `contrast=0.05` explicitly. Mutations caught: the old drop, no softness allowance, the scatter limit |
| N13 | Review: one global midpoint flagged 102 of 313 sites occluded when illumination fell to 36 % at the rim, and 51 of 60 with a dark cell on half the disk | `pipeline/nanowell_sites.py` | `done` | Reproduced. The reference is now the median of the twelve nearest fitted sites (illumination falling to a third flags none). **A dark cell covering most of a nanowell still reads as occluded**, as a bubble does: a first attempt to separate them with a contrast condition was found by mutation to change nothing, because for a uniform disk the two conditions coincide, and was removed. Telling a cell from a bubble (structure against flat) is for Section C |
| N14 | Review: with the square under a bubble, a bubble's rim crossing a circle scored as a square and one case anchored the well 1.7 pitches off (score 0.36, margin 0.3); with the square visible a circle scored 0.82 against its 0.54 | `pipeline/nanowell_detect.py` | `done` | Reproduced. A straight edge has its gradients in one direction, so the four-fold sum scores it like a square. Squareness is now the four-fold sum minus the two-fold one (a square's two edges cancel in it; a straight edge's do not), with an absolute floor of 0.4. A painted straight edge through a circle scores 0.0 squareness and 0.58 straightness; a clean square 0.73 and 0.01, at rotations 0, 7, 20 and 40 degrees. `score_fiducial` returns no candidate when nothing is square enough. Mutations caught: four-fold only, no floor |
| N15 | Review: `detect_sites` failed on a 199-site array in a 1500 px image (3,147 candidates, 'lattice could not be fitted') because the threshold was a percentile of all local maxima, mostly noise outside the array | `pipeline/nanowell_detect.py` | `done` | Reproduced. The typical site's response now comes from a two-class split of the maxima on a log scale (used only when the classes differ by more than 2x). A 196-site array in a 1500 px image detects. Mutation caught: the percentile |
| N16 | Review: a harmonic stronger than the fundamental sets the pitch (172 read as 65 or 99.6 with a strong high-pass halo) | `pipeline/nanowell_detect.py` | `done` | The reviewer's filter recipe did not reproduce on the committed code (every filter I tried read 172 +- 0.5), so this is tested with a synthetic spectrum: second shell at amplitude 1.0 over a first at 0.3 read the pitch as 1/sqrt 3 of the real one. The strongest peak is replaced only by a lower family that carries real power, has both partners, and has the strongest peak on its own reciprocal lattice. **A first version took the lowest family outright and regressed: a spurious low family put the pitch 3.6 % off on a small array**; the integer-combination test and the two-partner requirement were added, and a weak unrelated lattice at 4 % of the power is tested not to win. Mutations caught: never switching, switching to any lower family |
| N17 | Review: `check_format` returned ok with no evidence, never reported a 100 um array read with the 50 um setting, and raised `TypeError` for an unreadable pitch | `pipeline/nanowell_sites.py` | `done` | Passing the pitch test alone proves little (a setting too small passes it), so with no fitted radii the answer is `unverified` (`ok` is `None`), as is an unreadable pitch. **The reverse mismatch is not reported as `format_mismatch`**: a 100 um array with the 50 um setting fits no site and is `unverified`; the ratio of diameter to pitch is not assumed because the real 50 um format's pitch is not known (tracker section 1.2). Three tests that asserted ok without evidence were rewritten around the report |
| N18 | Review: the 2.5 px radius guard did not match what `refine_sites` needs (8 % of 1,285 sites fitted at 5.8 px) | `pipeline/nanowell_detect.py` | `done` | Measured here: 24 % at 7.1 px, 57 % at 8.3 px, all but one of 313 at 11.6 px. `refine_sites` now raises below a 10 px radius instead of returning every site predicted. At 0.65 um/px a 50 um nanowell is 38 px in radius, so this bites only on a downsampled montage. Detection keeps its 2.5 px guard (it works at 24.6 px pitch). Mutation caught: the guard removed |
| N19 | Review: `bubble_mask` failed when most of the array was hidden (84 % hidden flagged 69 %, all hidden flagged 10 %) and took 54 s on 4096 x 4096 | `pipeline/nanowell_sites.py` | `done` | The typical amplitude can be read at the detected sites (the array's own 80th percentile is bubble when most of it is hidden), and the demodulation runs on an image block-averaged to about 8 px per pitch. 0.84 hidden: over 75 % of the bubble flagged and under 10 % of the clear array; the percentile fallback is shown to do worse. Timing was not re-measured at 4096 px |
| N20 | Review: `_disk_samples` over-weighted the outer ring (39 % of the points for 23 % of the area, a chord's outside share overstated up to 2.5x) | `pipeline/nanowell_sites.py` | `done` | Replaced by a Vogel spiral where every point stands for the same area; tested against the analytic chord areas to 0.02 at four offsets. Mutation caught: a linear radius |
| N21 | Review: a 15 % y-stretch was accepted (`ok=True`, pitch 183 against 172) | `pipeline/nanowell_detect.py` | `done` | Refused by `detect_sites` when the fitted basis' length ratio is off by over 5 % or its angle by over 5 degrees from ideal, with the reason. Mutation caught |
| N22 | Review: documentation claims the code did not support (an offset *or ramp* leaves candidates unchanged; a wrong expected pitch is ignored; mask IoU) | `_Docs/Markdowns/ARCHITECTURE.md` | `done` | Rewritten: additive offsets and ramps only (a multiplicative ramp scales the response); an expected pitch narrows the search to a factor of two either side and one outside it is excluded, not ignored; the bubble-mask recall is stated |
| N23 | Review: eight mutations left the tests green. Of them, five were closed with tests, and three are not distinguishable | `tests/` | `done` | Closed: the 40-px assumption (a fine and a coarse pixel scale were added, and immediately found a real defect in the first harmonic rule), the square lattice (a synthetic square spectrum), the rim-only coverage sampling (analytic chords), and the iteration count and parabolic refinement (per-site error bounds). **Not distinguishable, recorded rather than hidden:** the centre-within-a-radius acceptance in `refine_sites` (no tested start from 0.3 to 0.5 pitch off fits at all, so the other criteria already refuse it; kept as belt and braces), the Hann window (even a floor 4x brighter than the lattice moves the pitch by 0.13 % either way), and the third hexagonal frequency of the bubble mask (the percentile normalisation absorbs it) |
| N24 | Review, not reproduced: a bright background biases the pitch to 171.24 (-0.44 %), near the 0.5 % consistency tolerance | `pipeline/nanowell_detect.py` | `todo` | I lifted the flat floor outside the array by up to 3.0 (4x the lattice contrast) and the pitch moved by at most 0.14 % (171.77 to 172.24 without the window). The reviewer's scene is not reproduced, so nothing was changed; a bias of 0.44 % would matter against `PITCH_TOLERANCE` and should be re-measured on real data (Q7) |

### Section C — Contents, label-free first (PR 6)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| C1 | Empty-nanowell template per (well, timepoint) from the least-textured visible quarter; fallback to the earliest template, aligned by the lattice; source and n recorded | `pipeline/nanowell_measure.py` | `done` | `build_template`: the least-textured quarter by departure from each crop's own radial mean, refined by a second pass against the first pass's median; fallback to the earliest (`earliest`) or `own_sparse`, with `used` and `n_sites` recorded. With 12 of 16 sites heavily loaded the template is within 0.15 of the floor and the plain median is more than 0.3 off. The first version ranked against the median of all crops and picked the loaded sites. Mutations caught: plain median, no second pass, no fallback, half-pixel offset |
| C2 | Rim-suppressed phase tile (crop ÷ template inside sites, flat outside) | `pipeline/nanowell_measure.py` | `done` | `rim_suppress`: empty sites within 0.2 of 1 including the rim, a cell above 1.5, outside every site exactly 1.0; the raw image at the same level reads the rings as cells. Mutations caught: no division, outside not flat |
| C3 | Confluence from local texture against the empty-site texture distribution | `pipeline/nanowell_measure.py` | `done` | `texture_threshold` / `site_confluence`: monotone with painted coverage, bounded by coverage below and by the coverage of a disk 3 px larger above (the optical halo makes it read high; documented), 0.0 for empty sites, `None` without empties or interior. Mutations caught: zero as `None`, threshold without the spread |
| C4 | Label-free count: Cellpose on the rim-suppressed owner tile, one call per tile; cells kept only in owned site interiors; fused sites segmented on their crop | `pipeline/nanowell_measure.py` | `done` | `segment_tile` / `count_cells`: exact counts for separated cells on a 16-site scene with a threshold-and-label stand-in for Cellpose, zero reported as zero, rim and between-site labels dropped. `cellpose_segmenter` imports Cellpose lazily and is not exercised here (not installed; C10). Mutation caught: interior radius |
| C5 | Area-based estimate and `count_disagreement` | `pipeline/nanowell_measure.py` | `done` | `area_estimate`: nine fused cells count as one and the area estimate reads above 2 with `count_disagreement` true; isolated cells and empty sites do not flag; a gap under one cell does not flag; no isolated cell gives `None`. Mutations caught: flag never set, no one-cell minimum, undefined as zero |
| C6 | Per-cell records through the existing quantification, plus address and well-frame columns | `pipeline/nanowell_measure.py` | `done` | `cell_table`: site, address (`None` kept as `None`), well-frame position from the tile origin, area, mean intensity. The 'existing quantification' columns are not wired in (that is the pipeline integration, later); this is the record builder. Mutations caught: origin dropped, `None` address as 0 |
| C7 | Fluorescence when present: per-cell (existing) and per-site integrated and mean minus empty-site background; empty, not zero, at a timepoint without the channel | `pipeline/nanowell_measure.py` | `done` | `site_fluorescence`: background is the empty sites' median interior mean; integrated over a 6 px disk of +50 within 5 %; no image gives all `None`, no empty site gives `None`. Mutations caught: `None` as zero, background not subtracted |
| C8 | Per-site record for every state in §3.5. Test: an empty site (0) and an occluded site (empty) can never be confused | `pipeline/nanowell_measure.py` | `done` | `site_state`: occluded and clipped carry `n_cells=None` and `confluence=None` whatever was passed; empty is `0` and `0.0`; a seen site with no count is `None`; partial visibility and unaddressed keep their metrics. Mutations caught: occluded and clipped keeping the count; the no-count case turned into a crash |
| C9 | Mutation: remove the ownership filter — a test counting cells in a synthetic overlap must fail on the double count | `tests/test_nanowell_contents.py` | `done` | `test_a_cell_in_an_overlap_is_counted_by_the_owning_tile_only`: a cell seen by two tiles is counted once with the filter and twice without. Mutation caught: ownership filter removed, one failure on the double count |
| C10 | Real data: Cellpose `cyto3` on rim-suppressed versus raw phase, counts against hand counts on 50 sites per format | — | `blocked` | Q7 |
| C12 | Review, open: a well where over three quarters of the sites are loaded gives a darkened template (40 sites, 36 loaded: error 39.9 against 0.6 at 30 loaded) that is still reported `source="own"` | `pipeline/nanowell_measure.py` | `todo` | Not fixed: from the crops alone it cannot be told from a well of unusually bright cells. Surface it (compare the template with the earliest one when there is one) when real data shows how often it happens (Q7) |
| C13 | Review (all fixed): `rim_suppress` divided by a zero or negative template and left part of a site at 1.0 when the template was smaller than the reach or held NaN; `count_cells` dropped an owned site with no geometry; `site_state` filled an unmeasured confluence with 0.0 and passed NaN fractions as fully seen; fluorescence and confluence returned numbers for NaN pixels and for a quadrant of a site at the image edge; isolated-cell area counted edge-cut cells | `pipeline/nanowell_measure.py` | `done` | Each now raises or returns `None`, with a test per case and a mutation per fix caught; the three that raise show as `Failed: DID NOT RAISE` under mutation, which is the assertion. Open and recorded: a clump touching nothing still passes as an isolated cell; `cell_table` gives address `None` for a site missing from `addresses` |
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
| P12 | End-to-end over a merge: two single-timepoint raw datasets (plate moved between them, different tile plans) → Concatenate with an entered gap → Analyze → every site joined across the two timepoints at the entered time | `tests/` | `todo` | |
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
| D2a | `site_series(table, well, q, r)` returns that nanowell's every timepoint in recorded-time order with its state and each metric, `NaN` where undefined; a timepoint where the site was occluded, clipped or unaddressed is present with its state, not omitted | `review/features/nanowell/compute.py` | `todo` | Feeds V3; unknown address returns an empty series, not an error |
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
| V3 | Site detail: the clicked nanowell's statistics over time (count, confluence, brightness, site signal against recorded time, state marked per timepoint, undefined drawn as a gap) and its crop at every timepoint from the owner tile | `review/features/nanowell/` | `todo` | Feature requested by the user. Data comes from D2's per-site series, never recomputed in the view |
| V3a | Click-to-select, tested through the real widget (CLAUDE.md §7.6): a synthetic mouse press on the array map's canvas at a site's pixel position selects that `(well, q, r)` and the detail plot receives that series; a press between sites clears; a second press adds; `_selected_wells` is unchanged. Name the wrong implementation: a hit-test that picks the nearest site regardless of distance, or one that is off by half a site in the axes' y-down transform | `tests/` | `todo` | Slow lane (needs the built page); the hit-test itself is a pure function in `renderer.py` and is tested in the fast lane |
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
  exists and says what is missing, Concatenate's gap field has no recorded
  value to pre-fill (the user's entry is then the only source, which the field
  already supports), and the Analyze no-record settings become the main path
  for those datasets.
- **The copy cost of one Concatenate per timepoint is unacceptable (Q9).** M13
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
- **The tile plan varies between wells of one acquisition.** Plans changing
  between experiments, and between acquisitions, is handled — the grid is read
  per (well, timepoint). Within one acquisition, wells with different grids are
  still handled, but the Analyze preview and preflight summarise one plan per
  dataset and would need to show several.
