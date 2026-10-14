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
how that differs between plate wells.

---

## This document is live, and it leads the work

It is not a record of what was decided; it is the instruction for what happens
next, and it is wrong the moment it stops matching the tree.

1. **Read it before touching anything.** The first act of any session working
   on nanowells is to open this file and find the first row that is not `done`
   or `declined` in the lowest-numbered pull request that is not yet merged.
   That row is the work.
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
7. **The design sections are rewritten, not annotated.** When a measurement
   changes the design, the prose in §3 is rewritten to say what is now true in
   the same commit. As each pull request merges, the part of §3 it implemented
   moves into `ARCHITECTURE.md` (which describes the tree as it is) and is
   replaced here by a one-line pointer to that section.

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
  circular.** The layout is the illustrated one: rows of circles offset by half
  a spacing (a hexagonal lattice) filling a circular area. Some arrays are the
  same layout "just smaller".
- Nanowells are on average **100 µm in diameter**, imaged at **10x**, about
  **1,100 nanowells per well**.
- **Each well is imaged as a 5×5 grid of overlapping fields.** The fields must
  be registered and assembled at the start of each well's analysis.
- **Air bubbles** sometimes make nanowells look black instead of light. Because
  the grid is regular, **every nanowell's position must always be deduced and
  never skipped.**
- **The edge of the array may be clipped**, and that must not make the
  analysis fail.
- **The phase channel is used to find ("segment") each nanowell.**
- Cells are **15–30 µm**. **Every timepoint has phase contrast; only some
  have fluorescence. Label-free analysis is critical.**
- The goal: an addressing scheme anchored on the central square that lets the
  same nanowell be compared across timepoints (cell count, brightness), per-
  nanowell metrics such as fold change over time, and comparison of those
  metrics between plate wells over time — integrated into the existing app as
  cleanly as possible.

### 1.2 Numbers derived from those facts (inferred, not measured)

Arithmetic on the reported numbers, so later measurements have something to be
checked against. Each line states what it assumes. None of these values is
hard-coded anywhere: the pitch, radius and orientation are **measured from each
dataset**; these are sanity bounds.

- **Pixel scale.** Assuming a 6.5 µm camera pixel and no extra magnification,
  10x gives 0.65 µm/px. A nanowell is then about **154 px** across and a cell
  **23–46 px** — close to Cellpose `cyto3`'s native 30 px scale. (Assumption:
  question Q1.)
- **Pitch.** A hexagonal lattice of pitch *p* has (√3/2)·*p*² of floor per
  site. 1,100 sites inside an array of radius 3.0 mm (a 96-well floor is about
  6.4 mm across) gives *p* ≈ **172 µm**, about 1.7× the nanowell diameter. The
  illustration draws the gaps narrower (pitch ≈ 1.3–1.5× diameter); at 130–150
  µm the same circle would hold 1,450–1,950 sites. The two agree only if the
  array is smaller than the well floor or the drawing is schematic. This is not
  something to settle by reasoning: the pitch is measured.
- **Rings.** With the square at the centre, a circular array of 1,100 sites
  reaches about **17–20 rings** from the centre (17 along the lattice axes, up
  to 20 between them).
- **Tile coverage.** A 2048-px tile at 0.65 µm/px is 1.33 mm. Five tiles with
  overlap fraction *o* span 1.33·(5 − 4*o*) mm: at 10 % overlap, 6.12 mm — less
  than a 6.4 mm well floor, so on that camera the outermost ring(s) of a
  full-well array would be partly unimaged. That is one concrete source of the
  "clipped edge" the user reports.
- **Overlap versus nanowell size.** For every nanowell to lie *wholly* inside
  at least one tile, the overlap must exceed the nanowell diameter plus a
  margin — about 120 µm, i.e. ≥ 9 % of a 1.33 mm tile. This is the single most
  consequential assumption in the design (§3.2.4; question Q2).
- **Sites per tile.** About 1.77 mm² / (0.866 × 0.172²) mm² ≈ **69 sites** per
  tile, so each tile carries enough nanowells to fit the lattice on its own.
- **Montage size.** At full resolution a 5×5 montage is about 9,400 px a side —
  ~89 Mpx, ~355 MB per channel in float32, per (well, timepoint), per worker.
  This is why the design never fuses tiles for analysis (§3.2.5).

### 1.3 Scope

**In:** tile registration, nanowell detection in phase, the addressing scheme,
per-nanowell label-free and fluorescence measurements, per-cell rows tagged with
their nanowell, per-nanowell tables, Analyze form settings and a detection
preview, a Review page for per-nanowell and per-well comparison over time, and
the incremental / remote / completeness plumbing every pipeline output already
has.

**Out, unless a row is added:** cell tracking between timepoints (nanowells are
compared as units, not cells), stitched full-resolution exports, a fine-tuned
segmentation model, and any new third-party dependency.

---

## 2. Ground rules — the repository contracts this work is most at risk of breaking

### 2.1 The pipeline stays self-sufficient, and the manifest is complete

`tests/test_architecture_contracts.py::test_pipeline_is_self_sufficient` forbids
`pipeline/` from importing `review` / `widgets` / Qt. `tests/test_remote_manifest.py`
requires an entry in `services/remote_manifest.py` for **every** `.py` in
`pipeline/` and `util/`. The pipeline imports nothing from `domain/` today, so
anything both the pipeline and Review need (the address format) goes in `util/`,
not `domain/`.

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

CLAUDE.md §4: ratchets only move down; a budget failure is answered by
extracting a cohesive module. **Every hook into these files therefore carries
an extraction**, and those extractions are rows of their own (P1, P8, A1)
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

### 2.5 Tests must be able to tell a right answer from a wrong one

CLAUDE.md §7.8. For every behavioural row: name the wrong implementation that
would still pass, build the fixture so it does not, and before calling the row
`done`, make the breaking edit in a `git worktree` of HEAD and watch a named
test fail with an `AssertionError`. The fixtures for this work are synthetic
until real data arrives (Q7), so each section also has a real-data row that is
`blocked` until it does.

### 2.6 Every section ends with a scientific-Python review

Before each pull request opens, a reviewer is pointed at the diff's numerics:
estimator choice, edge cases, dtype behaviour, whether a statistic means what
its label claims, and whether the fixtures could distinguish right from wrong.
Its findings become rows.

---

## 3. Design

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
    register tiles (3.2) ─▶ detect lattice + nanowells in phase (3.3)
        ─▶ anchor addresses on the square (3.4) ─▶ classify every site (3.5)
        ─▶ measure contents, label-free first (3.6) ─▶ write rows (3.7)
```

The surrounding well lifecycle is reused unchanged — extracting the well's
archive, writing the per-well CSV, zipping outputs, cleaning up
(`process_well_zip_task` / `process_well_folder_task`). Only the inner
`_process_groups_in_worker` call is swapped for the nanowell task body in a new
module, `pipeline/nanowell_run.py`, because `process_microscopy.py` has no
headroom (§2.3).

### 3.2 Tile layout and registration

#### 3.2.1 Which tile is where

Each tile arrives as an FOV token (`util/fov.py` orders tokens such as `1`,
`f07`, `fov13`). Its grid position comes from a **scan pattern**: raster or
serpentine, row- or column-major, starting at one of four corners — sixteen
patterns. The pattern is a setting (default to be set from Q3). An optional
auto-detection scores every pattern by how consistent the pairwise overlaps
are, and is accepted only when the best pattern beats the second by a margin;
otherwise the run asks for the setting rather than picking one. The imported
formats' stage positions are not persisted into the dataset today
(`datasets/ome_import/stage_layout.py` uses them only to infer the layout at
conversion time), so positions come from the pattern, not the stage.

A timepoint with fewer than rows×cols tiles is **not** processed into a partial
montage: it is reported as `incomplete_grid`, naming the missing tiles, and an
incremental rerun processes it once the scan is complete.

#### 3.2.2 Three independent sources of tile position

1. **Nominal.** Grid position × tile size × (1 − overlap), rotated by the
   camera's angle to the stage. Accurate to the stage's repeatability.
2. **Lattice phase.** Every tile sees the same physical lattice. Fitting the
   lattice in a tile gives the sub-pixel position of its sites, which fixes the
   tile's offset **modulo one lattice vector**. The integer number of lattice
   vectors comes from the nominal position, which works whenever the nominal
   error is under half a pitch (~85 µm) — far above a stage's error. This uses
   every nanowell in the tile, not just the overlap strip, and is unaffected by
   bubbles in the overlap.
3. **Overlap correlation.** Phase correlation of the overlap strip between
   neighbours, using cells, debris and the well wall, which break the lattice's
   periodicity.

#### 3.2.3 The periodicity trap

The nanowell array is periodic, so an unconstrained correlation of two
overlapping tiles has a peak at the true shift **and at every lattice vector
from it**, and can lock onto the wrong one with high confidence. Overlap
correlation is therefore searched only within ±pitch/2 of the nominal offset.
The test fixture for this (T4) is built so that the unconstrained search *does*
lock one pitch off — otherwise the test cannot tell the constrained search from
the unconstrained one.

All pairwise constraints are combined in a weighted least-squares solve. Each
tile records which source placed it (`lattice`, `overlap`, or `nominal`) and its
residual. Corner tiles that hold little or no array (a 5×5 grid over a circular
well) fall back to nominal and say so; they never fail the solve.

#### 3.2.4 Ownership: every nanowell is measured in exactly one tile

If the overlap exceeds the nanowell diameter plus a margin (§1.2), every
nanowell lies wholly inside at least one tile. Each nanowell is then **owned**
by one tile — the one that contains its disk with margin, nearest-centre on a
tie — and is measured only there. This one rule does three things:

- no pixel fusion is needed for analysis;
- a cell in an overlap is counted once, because only cells inside sites the
  tile owns are kept;
- cells stay in tile coordinates with tile-local label ids, so the existing
  cell identity `(well, fov, timepoint, nucleus_id)`
  (`domain/image_identity.py`), the per-tile `_labels` images and every Image
  Browser surface keep working unchanged.

If Q2 shows the overlap is smaller than a nanowell, sites wholly inside no tile
are measured from a crop fused from the two to four tiles that cover them, and
are marked `fused` in their row. That fallback is a row (G7a, opened only if
needed), not the main path.

#### 3.2.5 The montage is built for looking, not for measuring

The user's "assemble the well first" is honoured by a **downsampled montage**
of the phase channel per (well, timepoint) — long side at most 4,096 px,
blended in overlaps, written into the well's output archive as a new image role.
It is what the detection preview, the Array QC view and the Image Browser show.
Analysis reads the full-resolution owner tile instead, for the memory reason in
§1.2. A full-resolution montage export stays out of scope (§1.3).

### 3.3 Finding nanowells in the phase channel

The phase channel is the existing `--phase_token`; in phase, nanowells read as
light disks on a darker floor (bubbles turn them black, per §1.1).

1. **Lattice first, nanowells second.** The 2-D Fourier transform of the
   downsampled montage has peaks at the reciprocal lattice, which give the
   lattice's pitch and angle from the whole well at once. This survives a bubble
   covering a hundred nanowells, because those hundred contribute nothing and
   the other thousand still do.
2. **Candidates** are found by a matched filter (a disk of the expected radius,
   from the setting or the measured pitch) on the montage, and the lattice is
   refined by least squares on the candidates that sit on lattice sites.
3. **Per-site refinement** at full resolution in the owner tile fits each
   nanowell's centre and radius from its radial edge profile. A site where the
   fit succeeds is `geometry = fitted`; one where it does not (a bubble, debris,
   a clipped edge) takes the lattice position and the well's median radius and
   is `geometry = predicted`. **No site is ever dropped for lack of evidence.**
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
- A square lattice, if one is ever used, keeps the same integer coordinates;
  only the ring rule changes (`max(|i|, |j|)`, 8*k* per ring).

#### 3.4.2 Why orientation is unambiguous

The square looks the same every 90° and the hexagonal lattice every 60°; the
only rotation that preserves both is 180°. A plate sits in its holder by its
chamfered A1 corner and cannot be 180° out, so with a placement tilt under 30°
the direction nearest east is unambiguous. A camera that mirrors the image
mirrors every image in the dataset the same way and is a single setting.

#### 3.4.3 Where the origin comes from when the square cannot be seen

The square can be under a bubble. The origin is resolved by a fixed chain, and
the method used is recorded on every timepoint:

1. **Fiducial** — the central-region site whose shape scores most square-like
   (square-versus-circle template at the lattice's angle), beating the
   runner-up by a set margin.
2. **Array boundary** — the array is circular and centred on the square (Q4
   confirms), so a circle fitted to the outermost sites — a partial arc is
   enough — gives the centre, which is rounded to the nearest site.
3. **Same well, other timepoints** — the lattice phase is known exactly, so
   only the integer part of the offset is missing; another timepoint's anchored
   origin supplies it if the plate moved less than half a pitch between them
   (Q5 says whether that holds).
4. **Otherwise the timepoint is `unaddressed`**, with the reason, and no
   address is written for it.

When the fiducial and the boundary are both available they must agree; a
disagreement is recorded on the timepoint (`origin_conflict`) and shown in the
Array QC view rather than resolved silently.

#### 3.4.4 The roster

The set of sites that exist in a well is defined by the array model — every
lattice site whose centre lies inside the array boundary — not by which sites
happened to be detected. It is therefore the same at every timepoint of the
well, whatever the bubbles do, and a timepoint's rows are always the full
roster. Within one well, pitch (±0.5 %), angle (±0.5°) and roster must agree
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
   when too few remain (a confluent late timepoint), the well's earliest
   template is used, and which one was used is recorded.
2. **Rim-suppressed phase image.** Each owned site's crop is divided by the
   template; everything outside the sites is set flat. The segmenter then sees
   cells on a flat floor rather than cells beside a bright ring — the main
   source of false cells along a nanowell's edge. The image is optionally
   written as an output role so it can be inspected.
3. **Confluence** — the fraction of a site's interior covered by cells — from
   local texture on the rim-suppressed image, thresholded against the texture
   of the empty sites. This needs no instance segmentation and is the most
   robust label-free number when cells clump.
4. **Cell count** from Cellpose's label-free path (`pipeline.cellpose._segment_cellpose`,
   `cyto3`, diameter from the cell-size setting) on the rim-suppressed owner
   tile: one call per tile, as today, keeping only cells whose centroid lies in
   an owned site's interior.
5. **Area-based estimate** — foreground area divided by the median area of
   isolated cells — stored beside the count, with a `count_disagreement` flag
   when the two diverge, because a clump segmented as one cell undercounts
   silently otherwise.
6. **Fluorescence, when present**: the existing per-cell intensities, plus a
   per-site integrated and mean intensity over the interior minus the empty-site
   background — defined even where segmentation fails.

### 3.7 Outputs

| Output | Contents |
|---|---|
| per-cell CSV (`<prefix>_<well>.csv`, existing) | unchanged columns, `fov` = owner tile, plus `nw_q`, `nw_r`, `nw_ring`, `nw_label`, `well_x_um`, `well_y_um`. The new columns exist only in nanowell mode — absent, not blank, otherwise |
| `nanowells/<well>.csv` (new) | one row per (timepoint, roster site): address, well-frame position (µm), owner tile and tile-local centre and radius (px), `geometry`, `imaged_fraction`, `visible_fraction`, state, `focus`, `n_cells`, `confluence`, `area_est_cells`, `count_disagreement`, per-channel site intensities |
| `nanowells/<well>_grid.json` (new) | per timepoint: scan pattern, tile offsets with source and residual, lattice pitch and angle, origin method and agreement, array radius, roster size, status and reasons |
| `<well>_out.zip` (existing) | per-tile `_labels` as today; new roles for the downsampled montage and (optionally) the rim-suppressed tiles, declared in `domain/image_identity.ROLE_CATALOG` |
| `pipeline_info.json` (existing) | a `nanowell` block: settings, conventions (east axis, counter-clockwise, mirror), and a scheme version so Review can refuse a scheme it does not understand |

The tables live in a `nanowells/` subdirectory because Review loads every
top-level `*.csv` as a well's cell table (`review/core/load_controller.py:402`).

### 3.8 Incremental runs, resume, completeness, remote

- **Incremental.** The done unit is a (well, timepoint) present in the well's
  nanowell table with a terminal status. Every nanowell setting joins the reuse
  key, with a row in `tests/test_reuse_is_keyed_completely.py` per setting —
  otherwise turning nanowell mode on over an existing dataset finds every
  timepoint "done" and writes no table.
- **Resume.** Checkpoints are per (well, timepoint) (`pipeline/fov_checkpoint.py`).
- **Completeness.** `pipeline/completeness.py` is the one definition of "is this
  well complete", shared with the Datasets Validate tool; nanowell mode extends
  it there, so Validate and the run agree.
- **Remote.** New modules join the manifest; the upload planner keeps a
  timepoint's tiles together (a partial upload is an `incomplete_grid`); the
  fetch brings `nanowells/` back. Both remote files have ≤ 3 lines of headroom.

### 3.9 Analyze form

A **Nanowells** section (its own module, because `analyze/tab.py` has no
headroom): enable, grid rows × columns, scan pattern, overlap, pixel size,
nanowell diameter, cell diameter, occlusion and clipping thresholds. Its
centrepiece is **Test detection**: run registration and detection on one
(well, timepoint) in a worker and show the montage with every site labelled,
occluded and clipped sites marked, the origin method, and a few rim-suppressed
crops with their segmentation. Nobody should start a plate-wide run without
seeing the addresses land. Preflight reports missing tiles and an overlap
smaller than a nanowell.

### 3.10 Review

#### 3.10.1 Per-nanowell metrics

Nanowells are joined across timepoints on `(well, q, r)`. Undefined is `NaN`,
never zero, and every undefined case is listed:

| Metric | Definition | Undefined when |
|---|---|---|
| count fold change | n(t) / n(baseline) | n(baseline) = 0 — reported as its own "seeded later" class — or either timepoint unmeasurable |
| log₂ count fold change | log₂((n + 1) / (n₀ + 1)); the +1 is printed in the axis label | either timepoint unmeasurable |
| confluence fold change | c(t) / c(baseline) | c(baseline) = 0, or unmeasurable |
| brightness fold change | mean per-cell intensity, t over baseline | no cells at either, or no fluorescence channel |
| site-signal fold change | site-integrated intensity, t over baseline | unmeasurable at either |
| growth rate | slope of ln n over time | fewer than three measurable timepoints with n > 0 |
| occupancy class | 0 / 1 / 2 / 3+ cells at baseline | baseline unmeasurable |

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
  source, the origin method per timepoint, and every status the pipeline
  recorded.
- **Export** — a long-format CSV of every site, timepoint and metric.

It joins the shared machinery every page joins: the Properties panel, Saved
Plots, its own persistence block reset on dataset switch, build profiles, help
topics, and the one-line-removable feature test.

---

## 4. The pull-request stack

Nine pull requests, each based on the one below it, so each diff contains only
its own section. Each leaves `main` working if merged alone: the early ones add
modules whose only callers are their tests, and nothing user-visible appears
until P.

| PR | Branch | Base | Section | Delivers | Version |
|---|---|---|---|---|---|
| 1 | `claude/nanowell-1-geometry` | `main` | G | this tracker; addressing; lattice fitting on point sets; origin chain; roster; ownership | patch |
| 2 | `claude/nanowell-2-tiles` | PR 1 | T | scan patterns; three-source registration; periodicity guard; montage preview; synthetic tile renderer | patch |
| 3 | `claude/nanowell-3-sites` | PR 2 | N | phase-channel detection; fiducial; per-site refinement; occlusion; clipping; focus | patch |
| 4 | `claude/nanowell-4-contents` | PR 3 | C | template; rim suppression; confluence; label-free count; fluorescence | patch |
| 5 | `claude/nanowell-5-run` | PR 4 | P | the (well, timepoint) task; CLI; outputs; incremental; resume; completeness; remote | patch |
| 6 | `claude/nanowell-6-analyze` | PR 5 | A | Analyze section; Test detection preview; preflight | patch |
| 7 | `claude/nanowell-7-review-data` | PR 6 | D | loader; per-site and per-well metrics; export; persistence | patch |
| 8 | `claude/nanowell-8-review-page` | PR 7 | V | the Nanowells page and its views | minor (new page) |
| 9 | `claude/nanowell-9-integration` | PR 8 | I | optional: Image Browser, Spatial, Plotting, Batch Export hooks | patch |

PR 6 and PR 7 depend only on PR 5 and could be siblings; they are stacked
linearly so there is one retargeting chain to manage.

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
| Q1 | Pixel size at 10x (µm/px) and tile size in pixels | 0.65 µm/px, 2048×2048 | `todo` | |
| Q2 | Tile overlap: fraction or µm. Is it at least ~120 µm (one nanowell plus margin)? | assume yes; §3.2.4 fallback otherwise | `todo` | |
| Q3 | How the 25 tiles are numbered: raster or serpentine, row- or column-major, starting corner; acquisition software and file format | must be set; auto-detect as a check | `todo` | |
| Q4 | Is the square always at the centre of the circular array? | assume yes (origin fallback 2 depends on it) | `todo` | |
| Q5 | Is the plate taken off the microscope between timepoints, and how far can it move? | assume moved, by more than half a pitch (fallback 3 off) | `todo` | |
| Q6 | "Sometimes just smaller": smaller nanowells, a smaller array, or a smaller well format (384)? | diameter and pitch measured per dataset, with a prior from settings | `todo` | |
| Q7 | Sample data: 2–3 wells × 2+ timepoints, including a bubble, a clipped edge, and if possible a hidden square | blocks every real-data row | `todo` | |
| Q8 | Are nanowells light on a dark floor in phase at every timepoint (cells inside do not invert them)? | yes, per the report | `todo` | |

### Section G — Geometry and addressing (PR 1)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| G1 | Address type `(q, r)`, ring, label `k.i` and its parser as exact inverses; within-ring ordering counter-clockwise from east; square-lattice variant | `util/nanowell_address.py` | `todo` | |
| G2 | Lattice fit from a point set: basis vectors from nearest-neighbour displacement clustering, robust to 30 % missing points and outliers; returns pitch, angle, RMS residual, inlier mask | `pipeline/nanowell_lattice.py` | `todo` | |
| G3 | Index points to integer `(q, r)` with sub-pixel residual; points more than a quarter pitch from any site are outliers, not forced onto one | `pipeline/nanowell_lattice.py` | `todo` | |
| G4 | Basis convention (a₁ nearest east, a₂ +60° on screen) under rotations −25°…+25° and a mirror flag | `pipeline/nanowell_lattice.py` | `todo` | |
| G5 | Origin chain (fiducial → boundary → other timepoint → `unaddressed`), recording method and agreement; never guesses | `pipeline/nanowell_lattice.py` | `todo` | |
| G6 | Array-boundary circle fit from a partial arc; roster = sites inside; roster identical for shifted observation windows of one array | `pipeline/nanowell_lattice.py` | `todo` | |
| G7 | Ownership: owner tile per site; with overlap ≥ diameter, no site is unowned | `pipeline/nanowell_lattice.py` | `todo` | |
| G8 | Synthetic point-set generator: hex array, central square, circular boundary, bubble holes, rotation, translation | `tests/_nanowell_synth.py` | `todo` | |
| G9 | The discriminating fixture: two observations of one array, the second shifted 1.5 pitches and rotated 3°, window centre not on the square — every site keeps its address. Mutation: origin = "site nearest window centre" must fail it | `tests/test_nanowell_addressing.py` | `todo` | |
| G10 | Manifest entries for the new `util/` and `pipeline/` modules; confirm no guard rejects a module whose only importer is tests | `services/remote_manifest.py` | `todo` | |
| G11 | Close-out: guard family, ruff, touched tests, scientific review, ARCHITECTURE section for the addressing, version, PR opened with stack note | — | `todo` | |

### Section T — Tiles and registration (PR 2)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| T1 | Scan patterns (16) and FOV token → (row, col); a timepoint short of rows×cols tiles is `incomplete_grid` naming the missing tiles | `pipeline/tile_grid.py` | `todo` | |
| T2 | Nominal offsets from tile size, overlap and camera angle, in px and µm | `pipeline/tile_grid.py` | `todo` | |
| T3 | Lattice-phase registration: offset modulo the lattice from each tile's fit; integer part from nominal; reports when nominal error approaches half a pitch | `pipeline/tile_register.py` | `todo` | |
| T4 | Overlap correlation constrained to ±pitch/2 of nominal. Fixture built so the unconstrained search locks one pitch off — the test must fail if the constraint is removed | `pipeline/tile_register.py` | `todo` | |
| T5 | Weighted least-squares solve; per-tile source and residual; corner tiles with no array fall back to nominal without failing | `pipeline/tile_register.py` | `todo` | |
| T6 | Scan-pattern auto-detection with an acceptance margin; explicit setting always wins | `pipeline/tile_grid.py` | `todo` | |
| T7 | Downsampled phase montage (long side ≤ 4,096 px), blended in overlaps, 8-bit with the stretch recorded | `pipeline/tile_register.py` | `todo` | |
| T8 | Synthetic tile renderer: the G8 array rendered into 5×5 overlapping tiles with stage jitter, camera rotation, vignetting, noise, cells, debris and a bubble | `tests/_nanowell_synth.py` | `todo` | |
| T9 | Real-data check of registration residuals on Q7 data | — | `blocked` | Q7 |
| T10 | Close-out (as G11) | — | `todo` | |

### Section N — Nanowells in the phase channel (PR 3)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| N1 | Lattice pitch and angle from the montage's 2-D Fourier peaks; survives a bubble covering 10 % of the array | `pipeline/nanowell_detect.py` | `todo` | |
| N2 | Matched-filter candidates at the expected radius; least-squares lattice refinement on candidates at sites (G2/G3) | `pipeline/nanowell_detect.py` | `todo` | |
| N3 | Fiducial: square-versus-circle score per central site at the lattice angle; margin over runner-up; score recorded | `pipeline/nanowell_detect.py` | `todo` | |
| N4 | Per-site centre and radius refinement in the owner tile; `fitted` versus `predicted`; no site dropped | `pipeline/nanowell_detect.py` | `todo` | |
| N5 | Occlusion: bubble mask from the montage plus per-site interior contrast against the well's typical site; `visible_fraction` | `pipeline/nanowell_detect.py` | `todo` | |
| N6 | `imaged_fraction` and clipping at the coverage edge, the array edge and the well wall; a clipped edge never raises | `pipeline/nanowell_detect.py` | `todo` | |
| N7 | Per-site rim focus through `pipeline.field_quality.edge_focus` | `pipeline/nanowell_detect.py` | `todo` | |
| N8 | Cross-timepoint consistency (pitch, angle, roster) as a named status | `pipeline/nanowell_detect.py` | `todo` | |
| N9 | Real data: addresses checked by hand on 3 wells × 2 timepoints, including a bubble and a clipped edge; counts recorded here | — | `blocked` | Q7 |
| N10 | Close-out (as G11) | — | `todo` | |

### Section C — Contents, label-free first (PR 4)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| C1 | Empty-nanowell template per (well, timepoint) from the least-textured visible quarter; fallback to the earliest template; source and n recorded | `pipeline/nanowell_measure.py` | `todo` | |
| C2 | Rim-suppressed phase tile (crop ÷ template inside sites, flat outside) | `pipeline/nanowell_measure.py` | `todo` | |
| C3 | Confluence from local texture against the empty-site texture distribution | `pipeline/nanowell_measure.py` | `todo` | |
| C4 | Label-free count: Cellpose on the rim-suppressed owner tile, one call per tile; cells kept only in owned site interiors | `pipeline/nanowell_measure.py` | `todo` | |
| C5 | Area-based estimate and `count_disagreement` | `pipeline/nanowell_measure.py` | `todo` | |
| C6 | Per-cell records through the existing quantification, plus address and well-frame columns | `pipeline/nanowell_measure.py` | `todo` | |
| C7 | Fluorescence when present: per-cell (existing) and per-site integrated and mean minus empty-site background | `pipeline/nanowell_measure.py` | `todo` | |
| C8 | Per-site record for every state in §3.5. Test: an empty site (0) and an occluded site (empty) can never be confused | `pipeline/nanowell_measure.py` | `todo` | |
| C9 | Mutation: remove the ownership filter — a test counting cells in a synthetic overlap must fail on the double count | `tests/test_nanowell_contents.py` | `todo` | |
| C10 | Real data: Cellpose `cyto3` on rim-suppressed versus raw phase, counts against hand counts on 50 sites | — | `blocked` | Q7 |
| C11 | Close-out (as G11) | — | `todo` | |

### Section P — The pipeline run (PR 5)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| P1 | Extraction from `process_microscopy.py` sized to pay for the dispatch hook (headroom 0) | `process_microscopy.py` → a `pipeline/` module | `todo` | |
| P2 | The (well, timepoint) task body: group tiles by timepoint, grid check, registration, detection, contents, records; per-(well, timepoint) failure trace in `errors/` | `pipeline/nanowell_run.py` | `todo` | |
| P3 | CLI flags and their validation messages | `pipeline/cli_args.py` | `todo` | |
| P4 | Writers: `nanowells/<well>.csv`, `_grid.json`, per-cell columns, new roles in `ROLE_CATALOG`, the `pipeline_info` block | `pipeline/nanowell_run.py`, `domain/image_identity.py`, `process_microscopy.write_pipeline_info` | `todo` | |
| P5 | Incremental: (well, timepoint) done unit; every setting in the reuse key with a roster row each | `pipeline/incremental_keys.py`, `tests/test_reuse_is_keyed_completely.py` | `todo` | |
| P6 | Resume checkpoints per (well, timepoint) | `pipeline/fov_checkpoint.py` | `todo` | |
| P7 | Completeness for nanowell mode, shared with Validate | `pipeline/completeness.py` | `todo` | |
| P8 | Remote: manifest, tiles kept together on upload, `nanowells/` fetched; extractions to pay for `remote_runner.py` and `remote_fetch.py` | `services/` | `todo` | |
| P9 | End-to-end CLI test on synthetic tiles with a deterministic stand-in segmenter (fast lane); real Cellpose in the slow segmentation lane | `tests/` | `todo` | |
| P10 | Real-data run: time and peak memory per (well, timepoint) recorded here | — | `blocked` | Q7 |
| P11 | Close-out: ARCHITECTURE §6.2 section, README CLI flags, version | — | `todo` | |

### Section A — Analyze form (PR 6)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| A1 | Nanowells section as its own mixin, Qt-free fields and validation beside it; extraction to pay for the `analyze/tab.py` hook (headroom 0) | `analyze/nanowell_section.py`, `analyze/domain/nanowell_fields.py` | `todo` | |
| A2 | Run-spec wiring, tested through `analyze.run_spec._collect_run_spec` down to the argv | `analyze/run_spec.py` | `todo` | |
| A3 | Preflight: missing tiles, overlap smaller than a nanowell, no phase channel | `analyze/preflight.py` | `todo` | |
| A4 | Test detection preview in a worker: montage, labelled sites, occluded and clipped marks, origin method, sample crops with segmentation | `analyze/nanowell_preview.py` | `todo` | |
| A5 | A test that presses the real controls (enable → argv; preview → worker started) — CLAUDE.md §7.6 | `tests/` | `todo` | |
| A6 | Close-out: README Analyze section, rendering reviewed, version | — | `todo` | |

### Section D — Review data and calculations (PR 7)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| D1 | Loader for `nanowells/`; test that the top-level `*.csv` glob never picks the tables up | `review/features/nanowell/collect.py` | `todo` | |
| D2 | Per-site time series joined on `(well, q, r)`; refuses an unknown scheme version | `review/features/nanowell/compute.py` | `todo` | |
| D3 | The §3.10.1 metrics, each with its undefined cases tested | `review/features/nanowell/compute.py` | `todo` | |
| D4 | Baselines: earliest (default) and first measurable (labelled) | `review/features/nanowell/compute.py` | `todo` | |
| D5 | Per-(well, timepoint) summaries with sites used and excluded by reason | `review/features/nanowell/compute.py` | `todo` | |
| D6 | Group summaries via Sample Definitions; vs-control uses the same statistic top and bottom | `review/features/nanowell/compute.py` | `todo` | |
| D7 | Wells as the replicate unit; no public path pools sites as replicates | `review/features/nanowell/compute.py` | `todo` | |
| D8 | Long-format CSV export | `review/features/nanowell/controller.py` | `todo` | |
| D9 | Persistence block, reset on dataset switch | `review/persistence/nanowell.py` | `todo` | |
| D10 | Close-out (as G11) | — | `todo` | |

### Section V — The Nanowells page (PR 8)

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

### Section I — Integration with existing surfaces (PR 9, optional)

| # | Item | Place | State | Evidence |
|---|---|---|---|---|
| I1 | Image Browser: montage with lattice overlay; site crop gallery | `review/features/image_browser/` | `todo` | |
| I2 | Spatial reads well-frame coordinates on nanowell datasets | `review/features/spatial/` | `todo` | |
| I3 | Nanowell as a replicate unit in Bar / Line plots — scoped separately first, because it touches `fold_change`'s tuples | `review/domain/` | `todo` | |
| I4 | Batch Export nanowell mode | `review/features/batch_export/` | `todo` | |
| I5 | Close-out; delete this tracker if every row is `done` or `declined`, and say so in the PR body | — | `todo` | |

---

## 6. What would make this plan wrong

Each of these, if it turns out true, changes a section rather than a row.

- **The overlap is smaller than a nanowell (Q2).** Ownership fails for the
  sites in the seams, and the fused-crop fallback becomes a main path, with
  per-tile label images no longer covering every cell. PR 4 grows.
- **The lattice is not uniform across a well** (molding distortion, optical
  field curvature). Lattice-phase registration then biases tiles; overlap
  correlation becomes primary, and the per-tile residual is the signal.
- **The square is not at the array's centre (Q4).** Origin fallback 2 is gone,
  and a timepoint with a hidden square depends on fallback 3 (Q5).
- **Cells fill nanowells so that the empty-site template is contaminated** at
  late timepoints. The earliest-template fallback covers it only if the plate
  does not move between timepoints by more than the template tolerates.
- **Cellpose `cyto3` is poor on these cells in phase (C10).** The answer is not
  a new model (§2.2) without a row and a measurement; confluence remains the
  robust readout either way.
- **Wells are not always 5×5.** The grid size is a setting from the start, so
  this costs nothing unless grids vary *within* a plate.
