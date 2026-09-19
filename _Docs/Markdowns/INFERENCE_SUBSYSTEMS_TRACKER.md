# Inference subsystems — implementation tracker

**This document is temporary.** It exists because this body of work is carried
across more than one session, and CLAUDE.md §6.1 requires that a plan of this
size live on disk rather than in conversation. It is deleted by the pull request
that closes its last row, and that pull request says in its body that it is gone.

It plans the addition of *inference* — decisions made from the data rather than
typed by the user — to parts of All-Well that currently lack it.

## Revision note

The first draft of this plan proposed three subsystems of roughly equal size. An
adversarial review, followed by direct measurement, changed all three. What
follows records the measurements, because a plan that quietly drops a section
reads identically to one that forgot it.

- **Section C was already built.** `util/schema_infer.py` is 347 lines, is
  already in the remote manifest (`services/remote_manifest.py:66`), is already
  wired into the Analyze form (`analyze/domain/input.py:34,170`), is covered by
  four test files, and is documented in both live documents
  (`ARCHITECTURE.md:5403-5450`, `README_WellViewer.md:296-331`). Fifteen rows
  planned to build it again. The section is now a short delta against what
  exists. **The author did not grep before planning**; CLAUDE.md's opening rule
  is to read the ARCHITECTURE section covering what you are about to touch.
- **Section B's criterion is degenerate**, measured two ways, and one of those
  ways returns the constant the section exists to question. Its suggestion is
  removed and the section is reduced to drawing the curve.
- **Section A's metric table contained two metrics that cannot work at the site
  they were specified for**, and an anomaly model that would preferentially
  discard the wells where a treatment worked. The metrics move, and the model is
  split in two.

## This document is live, and it leads the work

It is not a record of what was decided; it is the instruction for what happens
next, and it is wrong the moment it stops matching the tree. Six rules keep it
that way, and they are what make it worth reading at all.

1. **Read it before touching anything.** The first act of any session working on
   these subsystems is to open this file and find the first row that is not
   `done` or `declined`. That row is the work. A session that starts from a
   recollection of where things stood is the failure mode §6.1 exists to
   prevent.
2. **The row and the code move in the same commit.** Never a batch at the end of
   a session, never "once CI is green". A tracker updated in arrears is a
   summary, and summaries are what failed before.
3. **What a row learns goes into the row.** A measurement, a refuted assumption,
   a file that turned out to be somewhere else — it is written down where the
   next reader will look, not left in a commit message. Rows carry their
   evidence column for exactly this.
4. **A finding is a row.** Anything a reviewer, a test failure, or the tree
   itself turns up becomes a numbered row before it is acted on, so that acting
   on it can be recorded. Rows added this way are appended under the row that
   produced them and keep its letter.
5. **A row that turns out to be wrong is `declined` with the reason, never
   deleted.** A deleted row and a forgotten row look identical six months later.
6. **Report progress by reading this file, not by remembering.** If the honest
   answer is "I believe so", the honest answer is "I have not checked".

The prose sections above each row table are part of the same contract: when a
measurement changes what a section is doing, the prose is rewritten to describe
what is now true, in the same commit. This file states what is true *now* — the
history is in `git log`.

## How to read the rows

Every row has a state, a place, and — once it is `done` — the test that pins it.

- **`todo`** — not started.
- **`wip`** — started, not finished. Never left across a session boundary
  without a note saying where it stopped.
- **`done`** — implemented **and** pinned by a test watched failing without the
  fix (CLAUDE.md §7.7 #2). "Implemented" alone is not `done`.
- **`blocked`** — cannot proceed in this environment or until a named row
  elsewhere closes. The blocker is named in the row.
- **`declined`** — examined and deliberately not done, with the reason written
  both here and at the site. Never silently dropped.

**Counts are derived from the rows, never typed.** The states are spelled with
backticks in the rows and as plain prose in this legend, so these patterns count
work rows and cannot match the legend:

```bash
F=_Docs/Markdowns/INFERENCE_SUBSYSTEMS_TRACKER.md
for s in todo wip done blocked declined; do
  printf '%-9s %s\n' "$s" "$(grep -c "| \`$s\` |" "$F")"
done
```

Two things that pattern has to avoid, both of which this file got wrong before
getting right. The first draft's patterns omitted the backticks and so matched
only the state legend, reporting `1` and `1` against fifty-one outstanding rows.
Spelling the states out literally in the command then made the command **match
itself**, reporting one more `done` than the rows hold. Hence the loop: the
states reach `grep` through a variable, the legend above is prose rather than a
table, and the count comes from the rows and nothing else.

A row moves to `done` in the *same commit* as the change it describes.

---

## 0. Ground rules

These are the repository's existing contracts, restated only where a section is
at risk of breaking one.

### 0.1 No new third-party dependency, and scikit-learn is off limits

`profiles/full.toml:24-33` declares `sklearn`, `umap` and `pynndescent` as deps
only the Multivariate feature pulls in, and the PyInstaller build excludes them
when Multivariate is profiled out. A QC surface importing sklearn would break the
thin build the day it shipped. No `IsolationForest`, no `EllipticEnvelope`, no
`MinCovDet`. numpy, scipy and stdlib only.

### 0.2 The pipeline stays self-sufficient, and the manifest is not optional

`tests/test_architecture_contracts.py::test_pipeline_is_self_sufficient` forbids
`pipeline/` from importing `review` / `widgets` / Qt at any nesting.
`tests/test_remote_manifest.py:66-80` requires an entry in
`services/remote_manifest.py` for **every** `.py` in `pipeline/` and `util/` —
reachable or not, because the guard checks completeness rather than reachability.

### 0.3 Report; do not decide. Offer; do not apply.

`analyze/preflight.py:33,43` documents both halves. Note the stronger form of the
second: preflight requires every *claim* to come back **verbatim from the
function that owns it**. A sentence preflight composes itself is a sentence that
can contradict the run it describes.

### 0.4 Undefined is not zero

CLAUDE.md §7 #3. A metric that cannot be computed stores `None` and says why. The
first draft violated this in its own metric table — see A.1.

### 0.5 Degraded output must be visible in the artifact

CLAUDE.md §7. This rule does most of the work in Section A, because the ways a
field-quality block can be *partial* are numerous and none of them is visible
without a deliberate marker.

### 0.6 Two line budgets are at zero headroom, and this is load-bearing

Measured against `tests/test_monolith_budgets.py`:

| File | Lines | Budget | Headroom |
|---|---|---|---|
| `process_microscopy.py` | 4048 | 4048 (`:93`) | **0** |
| `services/remote_runner.py` | 7326 | 7326 (`:115`) | **0** |
| `analyze/form.py` | 961 | 994 (`:209`) | 33 |

CLAUDE.md §4: ratchets only move down; a budget failure is answered by extracting
a cohesive module, never by raising the number. **Section A therefore contains
two forced extractions of the two largest modules in the repository**, and they
have rows of their own rather than hiding inside a feature row. This is the
single largest cost in the plan and the first draft did not mention it.

### 0.7 Every UI addition is reviewed against a rendering — but the harness does not reach two of the three surfaces

`tests/visual/render_views.py` builds the real `AllWellApp` off-screen and writes
a PNG per view to `_Docs/visual_audit/screenshots/`. Two limits, both verified:

- It switches `rev._central_pane_stack` between index 0 (Review) and 1 (Analyze)
  only (`render_views.py:222-225`). **The Datasets pane is never rendered.**
- Its own docstring (`:12-15`) says image-bearing views — Image Table,
  Segmentation, smFISH, **Playground** — render their empty state, because the
  CSV fixture produces no per-well image archives.

So any row that says "render the new view" for the Datasets pane or the
Playground carries a harness extension inside it, and that extension is its own
row. A UX review conducted against a description instead of a rendering does not
count.

### 0.8 Every section ends with a scientific-Python review

Dispatched against the section's diff before its pull request opens, pointed at
the numerics: estimator choice, edge cases, dtype behaviour, whether the
statistic means what the label claims, and whether the fixtures could
distinguish a right answer from a wrong one.

### 0.9 A test that cannot fail is not evidence — and neither is a fixture that cannot discriminate

CLAUDE.md §7.8. Both halves bit during this plan's own review:

- The review reported the focus metric moves only 11% under total defocus. Its
  synthetic nuclei were built at σ=2 — already blurred — so there was little
  high-frequency content left for defocus to remove. Re-measured with σ=1.2
  nuclei, defocus moves it **14×**. The reviewer's fixture could not
  distinguish a working metric from a broken one, which is §7.8 rule 2 exactly,
  and it produced a confident wrong number in the direction of killing the
  section.
- The same review's other measurements reproduced exactly on independent
  fixtures. A wrong number in one row is not grounds for discarding the rest.

For every behavioural row: name the single edit that would break the feature,
make it in a `git worktree` of HEAD, and watch a named test fail with a real
`AssertionError`.

### 0.10 Interpolating from someone else's measurement is not measuring

Two of this plan's sections were re-scoped by numbers produced in about ninety
seconds of scripting. Where a row says *measure*, it means run it.

---

## Section A — Field quality

### A.0 What is missing, and what is already there

Verified by reading the tree:

- **No focus, blur, sharpness or saturation metric exists** in `pipeline/` or
  `review/domain/`. (The first draft cited a search result to support this that
  was itself wrong — the search returns eight hits across four files, including
  `pipeline/ome_explode.py:224` and `pipeline/threshold_optimize.py:135,456`.
  The conclusion survives; the evidence offered for it did not.)
- `review/domain/qc_params.py` gates per **cell**. Nothing can see a bad field.
- `pipeline/run_report.py` exists because a run that lost FOV-groups wrote output
  indistinguishable from a clean well. That fixed **coverage**, not quality.
- `pipeline/stardist.py:293-297` computes field statistics per FOV and puts them
  in a log line, then discards them. Note they are computed on `nir_norm` —
  percentile-normalised and clipped to `[0,1]` (`stardist.py:56`) — so its
  `max − min` is ≈1.0 for every non-degenerate frame and says nothing about the
  raw frame. **It is not reusable as a dynamic-range metric**, contrary to the
  first draft.
- `review/data/cache_projectors.py:47::project_verdict_column` is shared by QC
  (`review/features/qc/controller.py:56`) and crop
  (`review/features/crop/controller.py:76`), and projectors are registered in
  `review/features/registry.py:1263-1296`. A third consumer fits.
  `review/data/data_loading.py:354-386` (`df_included_mask`) ANDs optional
  `*_Pass` columns and treats an absent column as all-pass.

### A.1 The metrics, and where they can actually be measured

The first draft specified `sat_frac` and `dyn_range` "on the raw frame" inside
`process_image_group`. **There is no raw frame there.**
`pipeline/io.py:72-77::load_2d_gray` returns `float32` unconditionally, so the
camera dtype is gone one call earlier and "fraction of pixels at the dtype
maximum" is identically `0.0` for every field on every plate forever — a number
stored where nothing was computed, which is §0.4 broken by the metric table that
declares it. `process_microscopy.py:1283-1287` compounds this: flat-field
correction rewrites the arrays **in place**, so by `process_image_group` the
"raw" nuclear frame has already been divided by an illumination field, which
changes local variance spatially — the exact quantity a focus metric reads.

So the metrics split by **where the information still exists**:

| Metric | Measured where | Definition | Status |
|---|---|---|---|
| `sat_frac` | inside `pipeline/io.load_2d_gray`, before the `float32` cast | fraction of pixels at the source dtype's maximum | the only place it is computable at all |
| `dyn_range` | same | `p99.9 − p0.1` in source units | same |
| `focus_*` | `process_microscopy.py:1690-1691`, on the arrays **before** `_correct_image_group` rewrites them | see A.2 | needs the pre-correction arrays held, which is a real change to that function |
| `bg_level` | post-correction | median outside the label mask | fine where it was |
| `nuc_count`, `nuc_area_median` | label image | as before | fine where they were, but see A.3 |

Measuring two metrics inside `load_2d_gray` means that function grows a return
value or a side channel, and it is called from everywhere. That is a row.

### A.2 The focus metric is not what the first draft claimed, and the correction matters

Measured on synthetic FOVs (σ=1.2 Gaussian nuclei, Poisson shot noise, a 100-count
pedestal, 512²), reporting `var(laplace(I)) / mean(I)**2` as a ratio to the
sharp 300-cell case:

**Exposure, identical biology, gain varied 16×** — ideal is 1.00 throughout:

| gain | ×0.25 | ×0.5 | ×1 | ×2 | ×4 |
|---|---|---|---|---|---|
| metric | 0.170 | 0.379 | 1.000 | 2.492 | 5.129 |

**30× spread over a 16× exposure change.** The first draft's justification —
"dividing by mean squared makes it invariant to exposure, so it compares across
a plate whose wells differ in brightness" — is **measurably false**. It holds
only for a noiseless pure multiplicative gain. Shot-noise variance scales as `a`,
not `a²`, and `laplace` amplifies it; and an additive sCMOS pedestal leaves the
numerator at `a²V` while the denominator becomes `(aμ+b)²`.

**Defocus and biology, same exposure:**

| | sharp | σ=1 | σ=2 | σ=4 | σ=8 |
|---|---|---|---|---|---|
| 300 cells | 1.000 | 0.281 | 0.090 | 0.070 | 0.069 |

| cells (all perfectly sharp) | 300 | 150 | 50 | 20 |
|---|---|---|---|---|
| metric | 1.000 | 0.675 | 0.328 | 0.190 |

Defocus moves it **14×**; killing 15/16 of the cells moves it **5×**. So the
focus signal is real and is the larger of the two — but the two **overlap**: a
defocused dense well and a sharp sparse well can land on the same number. The
metric as specified confounds focus with cell density and with exposure.

**Consequence:** the metric cannot ship with the sentence A.4 promised to emit
("B7 field 3: focus 6.2 MADs below the plate median"). That sentence would be
produced, at high confidence, for wells where the compound was cytotoxic. Either
the normaliser is fixed and the density confound controlled, or the metric is
reported as a number without a causal label. **Both require measurement on real
plates, which cannot happen in this environment.**

### A.3 The anomaly model, split in two

The first draft scored all six metrics as robust z against the plate median.
Robust-z-versus-plate encodes "anomalous relative to the plate ⇒ artifact",
which is valid only for a quantity that *should* be constant across the plate by
design. That is true of acquisition properties and false of biology:

- **`nuc_count`** — a cytotoxic compound drops it 10–20×. That is `|z| ≫ 5`. With
  the exclusion toggle on, the feature **deletes the wells with the largest
  treatment effect**, biasing every dose-response toward null. This is not
  "different means and different error bars"; it is a *directional* bias that
  systematically removes the signal, and the first draft's risk section did not
  name it.
- **`nuc_area_median`** — cell-cycle arrest enlarges nuclei.
- **`bg_level`** — treatment-induced autofluorescence raises background.

So:

**Acquisition metrics** (`sat_frac`, `dyn_range`, `focus_*`) are scored against
the plate, with the per-metric justification for "deviation implies artifact"
written beside each one.

**Biological metrics** (`nuc_count`, `nuc_area_median`) are **never** scored
plate-wide. They are scored **within a well, across its fields**, where the
fields are technical replicates of the same biology and disagreement between
them genuinely is a technical fault. Their plate-wide values are *reported* and
never *flagged*.

`bg_level` is ambiguous and starts in the biological group until measurement on
real plates says otherwise.

**Timepoints** are a grouping axis, not a pooling axis: `available_timepoints` is
first-class in this app, and pooling t=0 with t=72h makes each mutually
anomalous in a time course. Scoring is within-timepoint.

**Statistical defects carried over from the first draft, all needing rows:**

- **Zero-inflated metrics break MAD on exactly the healthy plates.** `sat_frac`
  is 0 for most fields on a good plate, so `MAD == 0`, so the refusal fires and
  the metric contributes `None` — precisely when one saturated field is the thing
  worth finding. The first draft treated this refusal as rigour; it is a metric
  that switches itself off when it would be useful. Zero-inflated metrics need an
  absolute threshold, not a relative one.
- **Near-zero MAD** is worse and was unhandled: 60% of fields at 0.0 and 40% at
  0.001 gives `|z|` in the thousands for every nonzero field.
- **`max |z|` across metrics is not comparable between fields** when the number
  of scored metrics varies, and the first draft explicitly allowed it to vary.
- **The `|z| ≥ 5` default is self-refuting**: its justification is that the
  metrics are not normal, answered by moving a normal-calibrated cutoff.

### A.4 The new silent-substitution paths, each of which needs a row

The repo's cardinal rule is that degraded output must never be indistinguishable
from good output. This subsystem creates four new ways to break it:

1. **Resumed FOVs get no metrics.** `process_microscopy.py:2500-2512`
   (`_process_or_resume`) returns cached checkpoint records and never calls
   `process_image_group`. A block covering only the re-processed FOVs, scored as
   though it covered the plate, makes **every field's z wrong** with nothing in
   the artifact saying so. Related: changing `process_image_group`'s return
   contract requires a `_FOV_CHECKPOINT_SCHEMA` bump
   (`pipeline/fov_checkpoint.py:32`) or every existing checkpoint is misread.
2. **Incremental runs** (`pipeline/well_skip.py::incremental_for_well`) do the
   same at well granularity. Note `run_report` solved its version of this by
   reading coverage back **from the finished CSV**
   (`pipeline/run_report.py:200-207`) rather than from what the run believed it
   did. There is no equivalent place to read field metrics back from, so the
   partial-block marker has to be explicit.
3. **`merge_plates` silently keeps plate 1's block.**
   `datasets/merge_plates/merge_plates_compute.py:425` is `dict(info1)`, and the
   warning mechanism iterates a **hand-written roster** `_UNMERGEABLE_BLOCKS`
   (`:245-250`) with **no guard test** — `grep -rn "_UNMERGEABLE_BLOCKS" tests/`
   returns nothing. That is CLAUDE.md §3's "roster written when there were fewer
   tabs". Worse, merge remaps well labels (`well_map`, `:449`), so plate 1's
   block would be keyed on pre-remap labels and mis-attribute even the half it
   keeps. `run_report` is *already* missing from that roster — a pre-existing
   instance of the same defect.
4. **`Field_Pass` joins on `fov`, which this repo already documents as
   untrustworthy.** `review/domain/fov_integrity.py:1-30`: a well whose filenames
   do not match the schema arrives with *every* row labelled field 1, and
   nothing downstream can tell that from a well that genuinely has one field. A
   per-field verdict joined on `(well, fov, timepoint)` either applies one
   field's verdict to a whole well or silently matches nothing.
   `dataset.fov_integrity` (`review/data/data_loading.py:499`) already holds the
   answer and must be consulted.

**CSV_ONLY remote runs are fine** — `_CSV_ONLY_GLOBS` includes
`pipeline_info.json` (`services/remote_runner.py:257`) and the block is merged
remotely before fetch — with one caveat: the `<well>_out.zip` is discarded
(`:4331`), so a flagged field has no image to inspect. One line in the readout.

### A.5 The plate map's colour vocabulary is closed

The first draft said the plate-map form was "to be decided by the UX review — a
badge, a corner tint and a hatch are all plausible". `plate_panel.py:69-90`
already enumerates eight states under a written invariant: *"no state carries a
colour another state also carries — a plate on which two states looked alike
would be a plate that cannot be read against its legend."* `:76-82` further
records that `accent2` is **reserved and forbidden for anomaly use**, and that
`threshold` was *proven* indistinguishable from `warn` by the render guard. The
painter is shared, so §7.3 #5's blast-radius rule applies. This is a constraint
to read before designing, not a question to put to a reviewer.

### A.6 Rows

| # | Row | State | Place | Evidence |
|---|---|---|---|---|
| A1 | **Discriminative-power gate.** On real plates: does the focus metric separate a defocused field from an in-focus one by more than biology separates two legitimate wells? The section does not proceed if it does not. (The first draft's A1 measured metric *cost*, which a `laplace` on a 2048² array passes trivially — a gate that cannot fail, §0.9 broken in row 1.) | `blocked` | needs real plates | the measured separation, written into this row |
| A2 | Fix the normaliser: a form of the focus statistic invariant to gain **and** pedestal, measured against the A.2 table | `blocked` | on A1 | the re-measured exposure table, ideally 1.00 throughout |
| A3 | Per-metric written justification for "deviation from the plate implies artifact", one line each, and the acquisition/biological split it produces | `todo` | this file, then the module | four of six are expected not to survive it |
| A4 | Extract a cohesive module from `process_microscopy.py` to create headroom (§0.6) | `todo` | `pipeline/` + manifest | `tests/test_monolith_budgets.py` green with headroom |
| A5 | Extract a cohesive module from `services/remote_runner.py` to create headroom (§0.6) | `todo` | `services/` | same |
| A6 | `sat_frac` + `dyn_range` inside `load_2d_gray`, where the source dtype still exists | `todo` | `pipeline/io.py` | a `uint16` fixture read **through `load_2d_gray`**, not constructed beside it |
| A7 | Hold the pre-correction arrays for the focus metric, given `_correct_image_group` rewrites in place | `todo` | `process_microscopy.py:1283` | the metric differs pre- and post-correction on a fixture with a non-uniform field |
| A8 | `pipeline/field_quality.py` — metrics + per-well JSON drop/collect, modelled on `run_report.py` | `todo` | new module | — |
| A9 | Manifest entry for `pipeline/field_quality.py` | `todo` | `services/remote_manifest.py` | `tests/test_remote_manifest.py` |
| A10 | `process_image_group` return-contract change **with** the `_FOV_CHECKPOINT_SCHEMA` bump | `todo` | `pipeline/fov_checkpoint.py:32` | an old checkpoint is refused, not misread |
| A11 | **Resume produces a partial block, and the block says so** | `todo` | `process_microscopy.py:2500` | mutation: drop half the fields; the artifact must declare partial and the plate median must refuse |
| A12 | **Incremental runs produce a partial block, and the block says so** | `todo` | `pipeline/well_skip.py` | as above at well granularity |
| A13 | `merge_plates`: add `field_quality` (and `run_report`) to `_UNMERGEABLE_BLOCKS`, **and add the guard test that roster has never had** | `todo` | `merge_plates_compute.py:245` | `scripts/prove_guard.sh` against the base ref; site count compared |
| A14 | Merge across a remote fetch | `todo` | `services/remote_runner.py` | test mirroring `merge_fetched_run_report`'s |
| A15 | `review/domain/field_quality_scores.py` — split scoring (§A.3), within-timepoint, zero-inflated metrics on an absolute threshold, near-zero-MAD handled, comparable aggregation | `todo` | new module | one test per refusal, each asserting `None` and never `0.0` |
| A16 | **`Field_Pass` consults `dataset.fov_integrity` and refuses on a degenerate-FOV well** | `todo` | `review/` | mutation: make the join match nothing; a named test must fail |
| A17 | Register the projector in `registry.py` (the one module allowed to import a feature) | `todo` | `review/features/registry.py:1263` | `tests/test_feature_removal.py` one-line removability |
| A18 | Cell Gating panel, exclusion toggle **off by default** | `todo` | `review/features/qc/tab_view.py` | driven through the real control (§7.6) |
| A19 | Plate-map indication within the closed colour vocabulary (§A.5) | `todo` | `plate_panel.py` | render guard proving it is distinguishable from all eight existing states |
| A20 | End-of-run + preflight line, composed by a pipeline-side summariser and **quoted** by preflight (§0.3) | `todo` | `pipeline/`, `analyze/preflight.py` | assertion on the emitted line (§7.4 #1) |
| A21 | Extend `render_views.py` to cover the new panel and plate map with flagged fields present | `todo` | `tests/visual/` | PNGs exist and are non-empty |
| A22 | **UX review against the rendered PNGs** | `todo` | subagent | each finding lands as a numbered row below A22 |
| A23 | Mutation: delete the `Field_Pass` AND at the chokepoint; a named test must fail | `todo` | worktree of HEAD | the failing test named here |
| A24 | Mutation: `MAD == 0` returns `0.0` instead of `None`; a named test must fail | `todo` | worktree of HEAD | the failing test named here |
| A25 | ARCHITECTURE.md + README_WellViewer.md | `todo` | `_Docs/Markdowns/` | `tests/test_doc_version_sync.py` |
| A26 | Guard family + affected files + full fast lane; version bump; PR | `todo` | — | what was run, reported honestly |
| A27 | **Scientific-Python review of the section diff** | `todo` | subagent | each finding lands as a numbered row below A27 |

---

## Section B — The threshold curve (the suggestion is cut)

### B.0 What the review and the measurement did to this section

The first draft proposed sweeping StarDist's probability threshold and
suggesting the midpoint of the widest count plateau. Implemented on a realistic
monotone decay (400 nuclei, sigmoidal fall-off, 48 steps over τ ∈ [0.05, 0.99],
tolerance 0.05), the criterion returns:

| derivative taken on | widest run | suggestion | counts over that run |
|---|---|---|---|
| `log`, nan-guarded | τ 0.050–0.350 | **0.200** | 400 → 352 |
| `log1p` | τ 0.050–0.350 | **0.200** | 400 → 352 |
| raw counts | τ 0.790–0.990 | **0.890** | **0 → 0** |

Two degenerate answers, and **which one you get is decided by an implementation
detail the first draft never specified** — how `log(0)` is guarded.

1. The zero tail wins: the suggestion is *detect nothing*. The draft's stated
   refusals covered "a frame with no nuclei" and "a count that never moves", and
   neither catches the zero **tail** of a perfectly normal frame.
2. The low-τ shoulder wins: the suggestion is **0.200** — `DEFAULT_PROB_THRESH_TEXT`
   (`analyze/seg_params.py:42`) to three decimal places. The feature built to
   give the user evidence against that constant returns that constant, for
   reasons having nothing to do with their data. That is the worst available
   outcome and it is the *likely* one.

The structural reason: for a **monotone** curve there is no interior plateau
maximum, so "widest region of small derivative" always lands at an extreme. The
informative feature — the knee — is where the derivative is *largest*. The
criterion points away from the only interesting part of the curve. And
`|Δ log count|` is scale-relative, so 500→495→490 reads as flat while 5→4→3 does
not, biasing further toward over-detection.

**The suggestion is cut.** The section is now: draw the curve, mark the current
value, suggest nothing. A user who can see whether their threshold sits on a
shoulder or on a cliff has better evidence than a number they must trust, and
this is the one form of the feature that cannot be wrong.

Two further findings that keep this section last:

- **The affordability argument rested on a branch that has never run.**
  `pipeline/stardist.py:325-342` is gated on `ALLWELL_SEG_DEBUG`, wrapped in a
  bare `try/except Exception` whose own comment says it "falls back silently if
  the split API differs", and it **never touches instance resolution** — the
  half the sweep depends on. CLAUDE.md §7.7 by name: a branch that exists is not
  a branch the app takes. It also says nothing about tiling (`:352-354`), which
  changes the shape of what `predict` returns.
- **`pipeline/threshold_optimize.py` is unacknowledged prior art.** 486 lines,
  already carrying `tie_count` / `tie_span` / `tie_contiguous` (`:76-103`), and
  its docstring (`:145-160`) already concludes that *"which point of a plateau
  should be reported is a domain question about the assay, not one this module
  can decide."* The first draft proposed deciding it by fiat, in a third
  implementation of the same statistics, in the same repository.

### B.1 Rows

| # | Row | State | Place | Evidence |
|---|---|---|---|---|
| B1 | Establish whether instances can be resolved from a cached `(prob, dist)` at *M* thresholds, **with tiling**, on the pinned StarDist. **Cannot run here** — `import stardist` and `import tensorflow` both fail in this environment (CLAUDE.md §10.6). | `blocked` | needs a TF/StarDist install | the measured API behaviour |
| B2 | Measure NMS cost at the low-threshold end, where candidate count explodes. The "one inference plus *M* cheap passes" claim is unverified and the cheap end is not the end that matters. | `blocked` | on B1 | the measured cost curve |
| B3 | Test whether count-vs-threshold is actually monotone. NMS can let two suppressed polygons survive when a dominant one is removed; the first draft asserted monotonicity as fact. | `blocked` | on B1 | measured, either way |
| B4 | Curve rendering: count vs threshold, current value marked, **no suggestion** | `todo` | `playground/renderer.py` | the drawn artifact is asserted, not the computed array (§7.4 #1) |
| B5 | Sweep execution on the existing Playground worker, bounded and cancellable | `todo` | `playground/worker.py` | the UI thread is never blocked *and* the sweep returns real counts |
| B6 | Crop fallback if B1 fails, with the crop named in the readout whenever it is used | `todo` | `playground/` | the readout states the crop |
| B7 | Refusals: no nuclei, a flat curve, a sweep that died — each named, none returning a number | `todo` | `playground/` | one test each |
| B8 | Extend `render_views.py` with an image-bearing fixture so the Playground renders non-empty (§0.7) | `blocked` | on B1 (needs StarDist) | PNG shows a real curve |
| B9 | **UX review against the rendered PNGs** | `blocked` | on B8 | each finding lands as a numbered row below B9 |
| B10 | One test builds the Playground through its **real constructor** with the curve control present (§7.8 rule 3) | `todo` | `tests/` | the builder is exercised, not a stub |
| B11 | ARCHITECTURE.md §5 (Playground) + README_WellViewer.md | `todo` | `_Docs/Markdowns/` | `tests/test_doc_version_sync.py` |
| B12 | Guard family + affected files + the Playground slow-lane file; version bump; PR | `todo` | — | what was run, reported honestly |
| B13 | **Scientific-Python review of the section diff** | `todo` | subagent | each finding lands as a numbered row below B13 |

---

## Section C — Schema inference: the delta against what already exists

### C.0 The feature shipped; the first draft planned to rebuild it

`util/schema_infer.py` (347 lines) already implements separator selection from
`_ - .` by modal token count, `well` via `canonical_well_label`, `timepoint` via
`parse_hours`, `experiment` as the constant slot, `channel` as the low-cardinality
alphabetic slot, a `_SAMPLE_LIMIT = 400` cap, an `ignore` floor, and a refusal
when no consistent token count exists. It is in the manifest
(`services/remote_manifest.py:66`), wired at `analyze/domain/input.py:34,170`,
covered by `tests/test_schema_infer.py` and three further files, and documented
at `ARCHITECTURE.md:5403-5450` and `README_WellViewer.md:296-331`.

**One first-draft design choice would have been a regression.** It proposed "an
exact search; no heuristic tie-breaking is needed and none is used".
`util/schema_infer.py:214-220` and `ARCHITECTURE.md:5428-5434` document the
production bug that reasoning caused: `f01`–`f12` are valid row-F well labels, so
the FOV and well slots both score 1.0, the tie went rightmost, and **every image
on the plate was attributed to well `F01`.** The fix was strictest-predicate-first
ordering plus leftmost-on-tie. The module's own comment says ties are "the normal
case, not an edge case". That design is not revisited.

`analyze/domain/input.py:170` is also more careful than the first draft assumed:
it applies the guess only when the inference calls itself `confident` (both a
well and a channel slot placed), because a single-channel plate yields `ignore`
where the channel belongs and writing that produces a schema the form's own
validator rejects.

### C.1 What genuinely remains

Four things, all of them §0.5 — making a silent success visible:

1. **The inference runs at folder-selection time and nowhere else.** There is no
   way to ask for it again after typing over the result.
2. **There is no match count.** `input.py:160-168` records the concern in its own
   words: the guess "overwrites two fields whose new values look no different
   from hand-entered ones, so without saying so it is invisible until it turns up
   in a sidecar after a run." A readout — *"experiment:channel:well:fov:timepoint,
   separator `_` — 1,248 of 1,250 sampled filenames parse"*, with the two that did
   not — is the whole of the fix, and the exceptions are what tell a user whether
   to trust it.
3. **Import Loose does not call it**, though it asks the same question through
   the same `SchemaPicker` and already walks the folder.
4. `analyze/form.py` has **33 lines of headroom** (§0.6), which a button, a
   worker hop and a readout will not fit inside.

### C.2 Rows

| # | Row | State | Place | Evidence |
|---|---|---|---|---|
| C1 | Reconcile the first draft's C1–C3 against the tree and mark them | `done` | this file | `util/schema_infer.py`, `services/remote_manifest.py:66`, `tests/test_schema_infer.py` — all pre-existing |
| C2 | The "exact search, no tie-breaking" design: **declined**, with the reason at the site and here | `declined` | `util/schema_infer.py:214-220` | it would reintroduce the documented plate-wide `F01` misattribution |
| C3 | Match-count readout: what was matched, out of how many, and the exceptions | `done` | `util/schema_infer.py` + `analyze/schema_section.py` + `analyze/domain/input.py` | `tests/test_schema_infer.py` — seven tests, each naming the wrong implementation it excludes. A slot is checked with **the predicate that claimed it**, not one looked up by field name: the bare-number FOV pass claims a slot `_is_fov` rejects, so a name-keyed report would call every name on such a plate unparsed |
| C3a | A corpus whose malformed names outnumber its well-formed ones infers **nothing** — the modal token count decides the layout, so `infer_schema` returns `None` rather than describing the minority. Found by writing C3's capping test with ten junk names against eight good ones and watching the whole inference refuse. Both hosts must render that `None` before they render a summary. | `done` | `util/schema_infer.py::infer_schema` | `test_a_corpus_whose_majority_does_not_split_is_refused_outright` |
| C4 | Re-run control — `detectRequested` + `setDetection` on the picker, **opt-in** via a `detect_label` argument so a host that will not answer the signal never shows a button | `done` | `widgets/schema_picker.py` | `test_architecture_contracts.py` green (no app import added); `tests/test_analyze_schema_detect.py` presses the real button |
| C5 | Extract the Filename Schema section into `analyze/schema_section.py` | `done` | `analyze/schema_section.py` (new, 132 lines) | `test_monolith_budgets.py` with both budgets **ratcheted down**: `analyze/form.py` 994→936, `analyze/tab.py` 855→781. `analyze/tab.py` turned out to be the tighter of the two at 853/855, which the plan had not measured: the section's widgets were built in `form.py` while the function that fills them lived in `tab.py`, so it could not grow in either. New work on the section goes in the new module |
| C6 | Analyze host: the button calls **the same scan browsing calls**, so the two cannot disagree. Left synchronous to match it — the scan is capped at 400 sampled names, and giving the button a worker the browse path does not have would be two behaviours, not one | `done` | `analyze/schema_section.py` | `test_the_browse_path_writes_the_same_readout_as_the_button` |
| C6a | `setDetection` was inserted between an `@property` and its `def`, so the method existed, read correctly, and raised `TypeError` on the first press. Found by pressing the button; no unit test of any piece would have seen it. This is why every test in the new file drives the real control (§7.6). | `done` | `widgets/schema_picker.py` | `tests/test_analyze_schema_detect.py` module docstring records it |
| C7 | Import Loose host: the same inference, **a different verdict**. Analyze applies a guess only when it is `confident` (a well *and* a channel slot), because it must name the channels it quantifies. This page only groups by well, and a single-channel plate carries a constant channel token so no channel slot can be placed — copying Analyze's rule would decline a layout that groups perfectly well. The rule lives in `import_loose_compute.detection_for_grouping`, pure and tested without the page | `done` | `datasets/import_loose/` | `tests/test_import_loose_schema_detect.py` — seven tests, four of them pressing the real button |
| C8 | Refusal wording for each refusal, surfaced rather than swallowed | `done` | both hosts — empty field, unreadable folder, no layout detected, and (Import Loose only) a layout with no well slot. Each is styled as a problem so a refusal cannot read as a quieter success, and each states that the schema below is unchanged | `test_an_empty_input_field_is_answered_rather_than_scanned`, `test_a_folder_with_no_detectable_layout_says_so_and_changes_nothing` |
| C9 | Extend `render_views.py` to enter the **Datasets pane**, which it has never rendered (§0.7) | `done` | `tests/visual/render_views.py` | eleven new PNGs: seven Datasets tool pages the audit had never covered, plus three detection states on the two hosts. Rendered holding a **real result**, not the resting state — the readout is the whole feature, and an empty-state grab shows nothing a grab from before it existed would not |
| C9a | `QLabel#Error` is styled by **no stylesheet**, so the refusal readout — and the two pre-existing schema-validation labels, the only other users of that object name — rendered in ordinary body colour. A refusal was distinguishable from a success only by being *less dim*. Found by looking at the rendering. | `done` | `ui/theme.py` | `QLabel#Error` now takes `Colors.danger`, the palette's single "this did not work" tone. Blast radius is exactly three labels, counted |
| C9b | The detect button set `objectName("Secondary")`, which styles a `QLabel` and not a `QPushButton`. Buttons take `widgets._support.set_variant`. | `done` | `widgets/schema_picker.py` | `QPushButton[variant="secondary"]` exists in the sheet; the object name matched nothing |
| C9c | On a refusal the run dashboard kept the **previous** scan's line, so once the picker gained a readout the two surfaces openly contradicted each other — the dashboard naming a schema it had applied, the readout saying none was detected. Both refusal paths returned before `_report_scan_guess`. | `done` | `analyze/domain/input.py` | visible in the rendering, invisible to every test, because no test reads two widgets at once |
| C10 | **UX review against the rendered PNGs** — both hosts, detection and refusal states | `done` | subagent, reading the PNGs | fourteen findings, five of them blocking. Rows C10a–C10h below |
| C10a | **The window contradicted itself, and the false line was the louder one.** The layout readout promised `✓ Will group 8 loose TIF file(s) by well` directly below a refusal saying no layout could be read — and `✓ Will group 25` where only 24 carried a well. `plan_input_layout`'s own docstring already describes this defect and its remedy for the *fold-loose* branch; the zip-root branch still counted with a glob. Now counted with the schema, and the tick is dropped when nothing matches | `done` | `services/input_resolution_service.py::groupable_tif_count` | `tests/test_input_layout_counts_what_it_can_group.py` |
| C10b | Success and refusal were **the same picture in greyscale** — muted grey and danger red measured at relative luminance 0.241 and 0.233, a 3% difference, with hue carrying the entire signal for a state the user must not miss | `done` | `ui/theme.py::QLabel#Notice` | a tinted panel with a left rule, so the block changes **shape**; verified in the re-rendered PNGs |
| C10c | Red was the wrong tone anyway: in this app red means a run died (`Failures recorded`, the Wells legend). A scan that declined to guess broke nothing | `done` | `ui/theme.py` | `Notice` takes `Colors.warn`, the app's existing "not what you wanted" tone. `QLabel#Error` stays red for the schema *validation* labels, which are errors |
| C10d | The one sentence that matters — which files will not be analysed — was set in the **same grey as the example-filename boilerplate** beneath it | `done` | `util/schema_infer.py::shortfall`, `widgets/schema_picker.py` | the readout has two registers; `test_the_readout_names_the_files_the_guess_does_not_parse` asserts the shortfall lands in the note |
| C10e | "the schema below is unchanged" — it is **above**. Every schema control sits above the button | `done` | `analyze/domain/input.py` | "your settings above are unchanged" |
| C10f | "parse" is programmer vocabulary and the app already had a word — the Zip page has always said "didn't match the schema". "slot" invents a synonym for **Position N**, which the dropdowns are labelled with six rows above | `done` | `util/schema_infer.py` | `test_the_readout_says_matched_rather_than_parsed` forbids both words |
| C10g | The button was live from launch in the Analyze host, where it could only produce a refusal until a folder was chosen | `done` | `analyze/schema_section.py` | a standing hint; `test_the_control_says_what_it_is_for_before_it_is_pressed` |
| C10h | On the Zip page the new readout **repeated the page's own line** — same count, same filename, two vocabularies, 26px apart | `done` | `datasets/import_loose/import_loose_compute.py` | that host passes no note; `test_the_page_states_the_shortfall_once_not_twice` |
| C10i | Deferred, with the reason: move the Detect button to the **top** of the section (detecting is what a user should do first, and "settings below" would then be true), and align it with the schema field on the wide page. A layout change to a shared control, worth doing, not worth folding into this PR | `todo` | `widgets/schema_picker.py` | — |
| C10j | The help-topic guard scanned `analyze/form.py` **alone**, so extracting the Filename Schema section made a live help key report as naming a section that no longer exists. §7.5 #3: the scan surface is half the rule | `done` | `tests/test_help_topics.py` | now walks every `analyze/` module; caught by the full fast lane, not by the guard family |
| C11 | Mutation on HEAD (not via `prove_guard.sh` — the code is on `main`, so the base-ref proof measures nothing, §0.9): make the readout always report a full match; a named test must fail | `todo` | worktree of HEAD | the failing test named here |
| C12 | ARCHITECTURE.md §6/§6b + README_WellViewer.md — **edit the existing prose**, which already describes this feature | `todo` | `_Docs/Markdowns/` | `tests/test_doc_version_sync.py` |
| C13 | Guard family + affected files + fast lane; version bump; PR | `todo` | — | what was run, reported honestly |
| C14 | **Scientific-Python review of the section diff** | `todo` | subagent | each finding lands as a numbered row below C14 |

---

## Sequencing

**C, then B's curve, then A** — and the three are *not* independent, contrary to
the first draft:

- A and B both touch `pipeline/stardist.py`'s prediction path and both want
  `process_image_group`'s nucleus count.
- A and B both want plateau / robust-statistics logic that
  `pipeline/threshold_optimize.py` already implements, and the layering rules
  forbid a Review-side module from importing it. Deciding where that logic lives
  is a shared prerequisite, not a per-section detail.

**C first** because it is now genuinely small, carries no numeric risk, and its
Datasets-pane render extension (C9) is a prerequisite the other sections' UX
rows also want.

**B second, curve only.** Its blocked rows (B1–B3, B8) need a machine with
TensorFlow and StarDist installed. Until one exists, B4–B7 can be built against
a recorded sweep and B8–B9 cannot run at all.

**A last**, because it is the largest by a wide margin — two forced module
extractions, a checkpoint schema bump, four silent-substitution paths to close,
and two rows (A1, A2) that are **blocked on real plates** and gate the rest.

Each section's first rows are measurements, and A may still end `declined` with
the measurement written down. That is a success condition for this plan.

## What would make this plan wrong

- **A** is wrong if, on real plates, the corrected focus statistic does not
  separate defocus from legitimate biological variation (A1). The synthetic
  measurement in A.2 says the signal exists and that the confound exists too; it
  cannot say which wins on real data, and nothing in this environment can.
- **B** is wrong if the curve itself is not affordable (B1, B2) — in which case
  the honest answer is to remove the section rather than ship a sweep over a crop
  presented as a sweep over a frame.
- **C** is wrong only in its scope estimate, which is now derived from reading
  the module rather than from imagining it.
