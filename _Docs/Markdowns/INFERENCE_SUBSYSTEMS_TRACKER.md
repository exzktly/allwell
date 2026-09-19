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
| A2 | Fix the normaliser | `done`, then **superseded** | `pipeline/field_quality.edge_focus` (`focus_score` is deleted — see §D.2-2 and row D5) | The normalised Tenengrad this row shipped was measured on the axis it was fixed for and was sound there: gain 0.25/1/4 → 27.34/27.47/27.48; pedestal 0/100/1000 → 27.48/27.47/27.43; cell count 20→600 → 27.37–27.55 (**1.4 %**); defocus σ 0/1/2/4 → 27.47/19.60/10.43/4.19; across three seeds at five cell counts, sharp (27.27–27.64) and σ=2 (10.16–11.84) **do not overlap**. The rejected `var(laplace)/mean²` spans 30× on gain, 707× on pedestal and 6.7× on cell count alone. What this row never measured is nucleus **size**, where the same statistic moves 7.8× against a 3.7× defocus signal (§D.2-1) — which is what deleted it |
| A3 | Per-metric justification for "deviation from the plate implies artifact", and the split it produces | `done` | `field_quality.ACQUISITION_METRICS` / `BIOLOGICAL_METRICS`, justified in both module docstrings | three of six survived: focus, saturation, dynamic range. Nucleus count, median area and background are scored **within a well across its fields** and never plate-wide |
| A4 | Extract a cohesive module from `process_microscopy.py` | `done` | `pipeline/stardist_cache.py` — the pkg_resources shim, Keras cache lookup, cross-process download lock, dead-holder reclaim and corrupt-cache purge | 4048 → 3828, budget ratcheted down. Two module constants could not follow the re-export (a re-export is a binding); the recovery tests now patch the module that owns them |
| A5 | Extract a cohesive module from `services/remote_runner.py` to create headroom (§0.6) | `done` | `services/remote_conda.py` — how a command is built to run inside the remote's conda environment | one job asked in four places (single-run launch, batched driver, scheduler handoff, pre-flight probe) and needing the identical answer in all four: which env a host's `remote_python` resolves to, named or absolute prefix, what `conda run` argv that is, and the shell guard that proves it exists. 7326 → 7141, ratcheted down. Every name re-exported, so no call site and no test moved |
| A6 | `sat_frac` + `dyn_range` inside `load_2d_gray`, where the source dtype still exists | `done` | `pipeline/io.load_2d_gray_with_ceiling` — the old function is now a wrapper over the new one, so the array cannot drift | `test_field_quality_is_wired_into_a_run.py::test_saturation_is_measured_rather_than_defeated_by_the_float_cast` writes a **clipped** `uint16` frame, reads it through the loader, and asserts the exact fraction. Mutation: compare against the float array's own dtype max and it reports 0.0 — the defeated metric, indistinguishable from a plate with no clipping |
| A7 | Hold the pre-correction arrays for the focus metric, given `_correct_image_group` rewrites in place | `declined`, retired by §D.2-1b | `process_microscopy._load_image_group` still holds them for `dyn_range` | **This row's premise is gone.** It was true of the frame-level metric: a correction reshapes local variance, which is the whole of what a whole-image focus statistic reads, so the metric had to be taken before one ran. `edge_focus` measures the width of the intensity transition at a nucleus's own boundary, which a flat-field division and a background subtraction do not change — the same property that makes it comparable between plates — and it needs the segmentation, so it is measured on the corrected frame the labels came from. Caught by reconciling the rows against the tree rather than against each other (§6.1 #6): `_load_image_group`'s own docstring was still asserting the retired reason, four lines from code that no longer does it |
| A8 | `pipeline/field_quality.py` — metrics + per-well JSON drop/collect | `done` | new module | `tests/test_field_quality.py`, 24 tests |
| A9 | Manifest entries for `pipeline/field_quality.py` **and** `pipeline/stardist_cache.py` | `done` | `services/remote_manifest.py` | `tests/test_remote_manifest.py` |
| A10 | `process_image_group` return-contract change **with** the `_FOV_CHECKPOINT_SCHEMA` bump | `done` | returns `(records, quality)`; schema 1 → 2 | a version-1 checkpoint carries records and no quality, so a resume accepting it would produce a well whose block silently covered fewer fields than the well holds. Refused, at the cost of one FOV reprocessed. `test_per_fov_checkpoint.py::test_save_then_load_round_trip` also pins that a `None` metric survives the round trip rather than collapsing to 0.0 |
| A11 | **Resume produces a partial block, and the block says so** | `done` | the quality rides in the checkpoint file with the records | Better than partial: a resumed well is measured as completely as one that ran straight through, because the two are stored together and resumed together. The partial case that remains is a well that **failed** or aborted part way, and `measured_of` is the number of FOV-groups the well HAS rather than the number that produced a measurement. `test_a_well_whose_groups_failed_declares_the_shortfall`; mutation to `measured_of=len(quality_fields)` fails two tests |
| A12 | **Incremental runs produce a partial block, and the block says so** | `done` | at well granularity the gap is a **missing well**, not a partial one — and that is the remote fetch's problem, closed by A14 | An incremental local run skips whole wells, whose per-well records from the previous run are still in the sidecar and are not overwritten, so the block stays whole. It is the remote path that loses them, because a remote output directory starts empty and its sidecar replaces the local one. Measured against the code rather than assumed: `well_skip.incremental_for_well` skips at well granularity, and the local orchestrator merges into the existing `pipeline_info.json` rather than rewriting it |
| A13 | `merge_plates`: both blocks added, **and the guard that roster never had** | `done` | `merge_plates_compute.py`, `tests/test_sidecar_blocks_are_merge_rostered.py` | stated against the `*_BLOCK` constants the pipeline declares, not against a second hand-written list (§3). Mutation on HEAD — remove `field_quality` from the roster and the parametrised case fails |
| A14 | Merge across a remote fetch | `done` | `field_quality.merge_fetched_field_quality` + `read_field_quality_block`, called at the same site as the run-report pair | The same defect one block over with a worse consequence: losing coverage records loses history, but losing quality records produces a **wrong number** — the acquisition metrics are robust z against the plate, so a block reduced to the wells one remote incremental run touched gives each of them a z against its own tiny median, and the shortfall accounting cannot catch it because each of those wells is individually complete. Three tests, all mutated: drop the prior wells, carry the derived fields over instead of recomputing them, and capture the block after the rsync rather than before |
| A15 | `review/domain/field_quality_scores.py` — the split scoring and its refusals | `done` | new module | 16 tests. **The headline test initially passed against the mutation it exists to catch**: every control field reported exactly 300 nuclei, so the pooled MAD was zero and a plate-wide scorer *refused* rather than flagging. The fixture now varies every metric and asserts its own premise first |
| A16 | **`Field_Pass` consults `dataset.fov_integrity` and refuses on a degenerate-FOV well** | `done` | `review/data/field_quality_cache.py` | The refusal is per well and is **named on screen**, because a well silently exempt from a filter and a well that passed it look identical in a plot. Mutation: `skip = set()` and `test_a_well_whose_fov_labels_are_untrusted_is_refused_not_joined` fails |
| A17 | Register the projector in `registry.py` (the one module allowed to import a feature) | `done` | `registry._register_cache_projectors`, fourth in the list | `load_and_project` runs the list without naming anything, so the projector is reached the same way the other three are. `test_feature_removal.py` and `test_feature_profile.py` green |
| A18 | Cell Gating panel, exclusion toggle **off by default** | `done` | `QCParams.exclude_flagged_fields` + the panel's own row | Three tests build the panel through its real constructor and press the real box; mutations that disconnect it, never refill it, or leave it enabled with nothing to apply each fail one. The sentence has four states and only two wear amber — a finding is a result, not a failure, and the first render had them dressed alike |
| A19 | Plate-map indication within the closed colour vocabulary (§A.5) | `done` | `plate_panel.FLAGGED_FIELD_GLYPH` + `paint_field_quality` | A **marker**, not a ninth colour: the palette is closed under a written invariant, `accent2` is reserved and forbidden for anomaly use, and `threshold` was measured indistinguishable from `warn`. Orthogonal on its own terms too — a well can be complete and out of focus, and a colour says one thing at a time. The render showed the glyph had **no legend row**, which breaks that plate's own invariant that no well is left without a row to explain it; it has one now, and only when the plate carries a mark |
| A20 | End-of-run + preflight line, composed by a pipeline-side summariser and **quoted** by preflight (§0.3) | `done` | `field_quality.summarize`, quoted by `analyze.preflight._field_quality_line` | the pre-flight test asserts equality with `summarize`'s own output rather than against a copy of the words, so a second composition fails it. Mutation: give the pre-flight a sentence of its own and two tests fail |
| A21 | Extend `render_views.py` to cover the new panel and plate map with flagged fields present | `done` | `tests/visual/render_views.py::_capture_field_quality` | two PNGs, drawn from a block shaped like a real plate. The harness got it wrong twice and both failures were silent: Cell Gating is a sub-tab of Home rather than a rail page, so the first grab was skipped, and the refresh was handed the window rather than the ReviewApp, so the second came back byte-identical from a refresh that never ran. Both now report rather than produce a shorter manifest |
| A22 | **UX review against the rendered PNGs** | `done` | subagent, measuring the pixels rather than reading the code | fifteen findings, one blocking. Rows A22a–A22j |
| A22a | **BLOCKER — the checkbox was live on a plate that refuses to be scored.** It was gated on "is there a block", which is strictly weaker than "would this exclude anything": a partly measured plate HAS a block and is refused, so the box ticked, stayed ticked, and dropped nothing — every number in the app then read by someone who believed a quality filter was on. §7's cardinal case, arriving through the control rather than through the data. The function's own docstring already claimed the stronger predicate | `done` | `field_quality_cache.exclusion_can_apply` | `test_the_box_is_dead_on_a_plate_the_scorer_refuses` walks all four block shapes through the real constructor; the disabled box also says why in its tooltip |
| A22b | The muted register was unreachable — it was chosen by testing the sentence for emptiness, and every branch returns text — so "this dataset was analysed before field quality was recorded" was painted in the same ink as a finding. The app's own house style for that class of statement is two panels away | `done` | `REGISTER_MUTED` / `REGISTER_PLAIN` / `REGISTER_PROBLEM`, returned rather than inferred | mutation on each of the three; the no-block state asserts `REGISTER_MUTED` specifically |
| A22c | **The sentence read as the heading of the radio group below it.** Measured: it sat at the checkbox's *indicator* column, 26 px left of the label it belongs to, in full primary ink — the brightest, left-most non-heading line in the block. The panel already has a sub-status idiom ("No gates drawn yet — draw one on the Scatter Plot tab.") indented to its control's label and muted | `done` | `_FQ_TEXT_INDENT`, applied with `QLabel.setIndent` | on the labels, not the container: a negative margin on the head does not work because Qt clamps those to zero, and the render showed the whole row shifted right. Re-measured: both checkbox labels at x=399, both sentences at x=400 |
| A22d | **Ticking the box produced no visible confirmation.** The finding is reported either way, which is right — and left the panel with no statement of effect at all, beside a debris gate that carries a live "flagged N / M" readout | `done` | `exclusion_effect_text`, a second muted line that is the only thing here that moves with the checkbox | "Currently kept — 1 field would be excluded." / "Currently excluding 1 field." |
| A22e | **The marker was a 2 px hairline.** Measured on the render: one bright column and one half-lit one, a 2 px half-filled gap between stem and dot on a 17 px well, and heavy red/cyan subpixel fringing that reads as a rendering fault. At 10× the `!` is visible; at 1× it is a tick or a smear | `done` | `_MARKER_HEIGHT_FRAC` 0.62 → 0.78, `_MARKER_BOLD` | the plate is the surface where a mark has to be **found**, on one of ninety-six wells |
| A22f | **The marker's ink was chosen by a proxy that fails on one of the six state fills.** `ink_dark if group_color is not None else text_primary` stands in for "the fill is bright", and against `text_muted` (#5F6B7C) a dark ink is 3.88:1 — below AA, and hopeless for a thin stroke | `done` | `well_plate_selector.marker_ink`, measuring the fill's relative luminance | the test asserts the **rule** (whichever of the two inks is more legible) rather than a number, because measured, no ink in this palette reaches 4.5 on that fill — the best is 4.45 — and holding it to 4.5 would assert something the palette cannot satisfy |
| A22g | **The legend key was visually absent.** Measured: 12×12 solid swatches beside a 2×9 glyph, about 15 % of the area, so the last row looked as if it had no key. And its count was a **well** count under a field noun, in a column that otherwise partitions the plate — so it double-counted a well already inside another row | `done` | `plate_panel.legend_marker_style` + "Wells with a flagged field", separated by a spacer | the outlined box is the vocabulary the plate's own neutral state already established, so the row gains a key of the same visual mass while still reading as a different channel |
| A22h | **A flagged well was a dead end.** The tooltip gave the metric, the z and the raw value but **not the field id**, and the panel gave the count but not the well — so neither surface yielded the `(well, field)` address the Image Browser needs to show the picture | `done` | both surfaces | the tooltip names the worst field ("worst: field 2, focus 3.4 MADs below the median"), and the panel sentence ends with the wells |
| A22i | *"1 of 18 field(s) flagged — focus (1)"* put the number 1 in one sentence twice with two meanings, and the second reads far too easily as a focus score. The driver count carries no information at all when there is one driver, `focus` is the pipeline's variable name, and "field(s)" sat in front of a count of exactly 1 | `done` | `METRIC_WORDS` + `_n_of` | "1 of 18 fields flagged, all out of focus — in A02." |
| A22j | **Only one of the four registers had ever been rendered**, and two of the findings above lived in the ones nobody had looked at | `done` | `tests/visual/render_views.py` | `-declined` and `-none` join the finding state |

| A23 | Mutation: delete the `Field_Pass` AND at the chokepoint; a named test must fail | `done` | worktree of HEAD | `test_the_chokepoint_honours_the_column` |
| A24 | Mutation: `MAD == 0` returns `0.0` instead of `None`; a named test must fail | `done` | worktree of HEAD | `test_a_metric_with_no_spread_reports_nothing_rather_than_zero`. Fourteen mutations in total across this section, each watched to fail |
| A25 | ARCHITECTURE.md + README_WellViewer.md | `done` | `_Docs/Markdowns/` | a new §6.7 carrying the measurements behind the focus statistic and the acquisition/biological split, because those are the decisions a later reader will want to reverse; the pipeline tree, the remote-fetch section and the conda section updated in place; the manual gains the control, the three refusals and the plate marker |
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
| B1 | Can instances be resolved from a cached `(prob, dist)` at *M* thresholds? **Yes.** Unblocked by installing TF 2.13 + StarDist 0.8.5 into an isolated venv, leaving the app's own numpy 1.24.4 untouched. `model.predict(frame)` returns `(prob, dist)`; `model._instances_from_prediction(shape, prob, dist, prob_thresh=…)` resolves labels, and the counts are **identical to `predict_instances` at every threshold** across two fixtures. Private API, so the shipped path returns `None` and falls back rather than raising. **Tiling not measured** — the fast path is skipped when tiling is asked for, rather than used in a configuration nobody tested | `done` | `services/frame_preview.py::_resolve_at_thresholds` | the shipped function run against a real model, not a rewrite of it |
| B2 | Measure the speedup rather than promise it. The first numbers (10.1× and 5.1×) were taken on a **probe**, not on the shipped function, and did not survive contact with it: the probe normalised its own fixture, and the shipped path both prepares the frame and no longer borrows the model with a discarded full segmentation. Re-measured end to end on the shipped `sweep_frame` against nineteen `segment_nuclei` calls on the same frame and grid: 2.73 s → 0.31 s, **8.8×**, identical counts at every threshold. That one number is what the docstring and ARCHITECTURE now carry. Clean synthetic frames, so the resolve cost is a **floor** — a noisy frame emits more candidates at the low-threshold end where NMS works hardest | `done` | `services/frame_preview.py` | the shipped function timed against the shipped comparison, after B13a |
| B3 | Is count-vs-threshold monotone? **On the frames measured, yes** — every step delta ≤ 0 across 19 thresholds. That is one frame shape, not a proof: NMS can in principle let two suppressed polygons survive when a dominant one is removed. So nothing in the shipped code *relies* on monotonicity, which is the safe response to a property observed but not guaranteed | `done` | probe against a real model | deltas `[0×12, -1, -4, -23, -43, -30, -12]` |
| B4 | Curve rendering: count vs threshold, current value marked, **no suggestion** | `done` | `playground/sweep_curve_view.py` (new) — a non-modal popup modelled on the Cell Gating optimizer's, so the app's two threshold curves look like one idea | `test_the_figure_carries_the_curve_and_the_users_own_threshold` asserts the **drawn lines**, and `test_nothing_in_the_figure_recommends_a_threshold` names the thing a later reader will try to add back |
| B5 | Sweep execution on the existing Playground worker, bounded and cancellable | `done` | `playground/worker.py` + `services/frame_preview.py` | `should_stop` reaches **inside** the loop and a stopped sweep returns what it measured; `test_a_stop_reaches_inside_the_fast_loop_and_keeps_what_it_had` |
| B6 | Crop fallback, with the crop named in the readout whenever it is used | `declined` | removed from `services/frame_preview.py`, `playground/worker.py`, `review/domain/threshold_sweep.py` | **Nothing in the application ever passed a crop.** `crop_px` defaulted to 0 at every layer and no caller set it, so the fallback was a parameter, a helper, a docstring, a test and a rendered screenshot describing a branch the app could not take — CLAUDE.md §7.7 exactly. The caveat sentence was also wrong for a non-square frame, naming a square crop the crop was not. Deleted rather than wired up: 8.8× made the affordability argument it existed for moot, and a crop that a user cannot ask for is not a fallback. `centre_crop`, `ThresholdCurve.caveat` and both crop tests went with it |
| B7 | Refusals: a sweep that never ran, no nuclei at any threshold, a count that never moves — each named and each **drawn as the figure** | `done` | `review/domain/threshold_sweep.py` | one test each, plus `test_a_declined_sweep_draws_its_reason_rather_than_empty_axes` |
| B8 | Render the curve for review | `done` | `tests/visual/render_views.py` | three PNGs — a normal sweep, one stopped early, and a refusal (a fourth, the cropped sweep, went with B6). Drawn from counts shaped like the real measurement rather than from a live sweep, because the app env still installs no TensorFlow; the **drawing** is what a UX reviewer has to see |
| B9 | **UX review against the rendered PNGs** | `done` | subagent, reading the rendered states | eleven findings, four blocking. Rows B9a–B9f |
| B9a | **The stopped sweep asserted a finding it had not earned.** The x-axis rescaled to the eight points it got, the curve filled the panel, and the title read *"400 nuclei down to 397"* — which reads as *the count barely moves on this frame*, the exact conclusion a scientist would act on, and an artifact of stopping before the knee. The complete sweep of that frame goes to zero. The honest sentence existed but went to the **status line in a different window**, and into nothing a user saves | `done` | `review/domain/threshold_sweep.py`, `sweep_curve_view.py` | the axis now spans what was *asked for* so the unmeasured range stays visibly empty; an amber hollow marker ends the data; the title says "Stopped after 8 of 19 … so far". `test_a_stopped_sweep_says_so_and_says_so_far` |
| B9b | The **"every threshold detected the same N"** refusal is a verdict on the *frame*. Derived from the three points a user got before pressing Stop — likely, since the first sweep also loads TensorFlow — it is wrong | `done` | `threshold_sweep.py` | scoped to what was measured; `test_a_flat_run_cut_short_is_scoped_rather_than_ruled_on` |
| B9c | **Nothing to compare against.** The premise is that the form ships 0.2 where the model says ~0.479, and the plot drew only the user's value. The one comparison that makes the question answerable was absent | `done` | `frame_preview.model_prob_threshold` | read off the loaded model, never written down — a literal would be a fourth copy of a number living in the weights' own `thresholds.json` |
| B9d | **No provenance.** No well, FOV, timepoint or channel anywhere, on a window that repaints in place across runs and has a Save button — three sweeps became three indistinguishable pictures, exported with no attribution. The sibling optimizer curve has always titled itself this way | `done` | `controller._sweep_subject` | two-line title |
| B9e | The crop caveat **ran off the right edge of the figure, clipped mid-word** — the one sentence saying how far to trust the numbers. `tight_layout` shrinks axes; it does not wrap a title | `declined` | — | The finding was real and its fix (out of the title, into an in-axes annotation) shipped first. It then went with B6: the only sentence `caveat()` ever produced was the crop note, so an always-empty method with a renderer branch that could not fire is the same §7.7 defect one layer down. The **lesson survives in the code that remains**: the stopped-sweep hedge lives in `describe()`, which is drawn as the title and wraps because it is composed on two lines, and the flat-region warning is a footer under the axes rather than an appendix to the title |
| B9f | *"A flat region means the count is insensitive to the threshold — not that the detections are right."* A frame the model confidently segments as 350 nuclei plus 50 pieces of debris draws the same plateau, and a user who came to validate their threshold reads the flat shoulder as validation | `done` | `sweep_curve_view.py` | a footer under the axes |
| B9g | Deferred with the reason: making the curve's points **clickable** to re-segment the frame at that threshold. The reviewer's highest-value item and correct — the real answer is the picture, not the plot — but it is a feature, not a fix, and it belongs in its own change | `todo` | `playground/` | — |
| B10 | Three tests build the Playground through its **real constructor** and press the **real** button (§7.8 rule 3) | `done` | `tests/test_playground.py` | mutation: leave the button unconnected → the first fails. The third exists because the CSV-only fixture has no frame to sweep, which turned a failing assertion into a second behaviour worth pinning |
| B11 | ARCHITECTURE.md (the Playground row) + README_WellViewer.md | `done` | `_Docs/Markdowns/` | both carry the measurement behind *suggesting nothing*, because that is the decision a later reader will want to reverse |
| B12 | Guard family + affected files + the Playground slow-lane file; version bump; PR | `done` | PR #918, based on #917 | full fast lane green at `bf85167`, the branch head: **9,248 passed, 8 skipped, 7m44s**, run after the last commit rather than across the work. `ruff check .` clean. The guard family and every affected file were run on each round in between. Version 9.7.16 → 9.7.17, one line |
| B13 | **Scientific-Python review of the section diff** | `done` | subagent, reading the pushed diff adversarially | nine findings, two of them blocking. Rows B13a–B13i |
| B13a | **BLOCKER — the sweep fed `model.predict` an un-normalised frame.** `predict` takes `normalizer=None` and assumes its input is already in [0, 1]; `segment_nuclei` normalises on the 1st/99.8th percentiles first, and the fast path did not. Measured against the Segment button on the same frame and the same grid, it returned **6 nuclei falling to 4** across the whole range where the truth was **86 falling to 0**, **did not decline**, and drew the flat line the manual tells the reader means the threshold does not matter. The only evidence ever offered for the fast path was a probe whose fixture was already normalised — the fixture did the work the code did not (CLAUDE.md §7.8 face one) | `done` | new `pipeline.stardist.prepare_stardist_input`, called by both `segment_nuclei` and `sweep_frame` | the two paths run against a real model at all 19 thresholds: `[86, 86, …, 33, 10, 0]` from each, exact agreement. `_FakeModel.predict` in the unit tests now asserts `frame.max() <= 1.0`, so the fixture can no longer hide it |
| B13b | **BLOCKER — the near-constant-field refusal was bypassed.** `segment_nuclei` declines a field with no spread because it is the classic trigger for StarDist's ClipperLib abort, which is uncatchable and takes the whole GUI process down. Reaching `predict` directly skipped that guard, and a sweep is nineteen NMS passes per press — nineteen times one Segment press's exposure | `done` | `is_near_constant`, extracted beside `prepare_stardist_input` and called by `sweep_frame` | a near-constant frame returns `[0, 0, 0]` without touching the network, and `curve_from_counts` then declines it |
| B13c | A test that would have caught B13a and B13b. The 130-odd existing tests could not: every one of them stubbed the model | `done` | `tests/test_frame_preview_service.py` | the stub asserts its input's range, so the un-normalised call fails at the boundary rather than returning a plausible number |
| B13d | **The model borrow ran a full discarded segmentation.** `sweep_frame` called `segment_frame` at `grid[0]` purely to get a loaded model into `_GUI_STARDIST_MODEL`, then threw the labels away — 0.42 s against the 0.30 s the nineteen resolves cost together | `done` | `services/frame_preview.ensure_model` | split out as its own function; the re-measured 8.8× in B2 is with this removed |
| B13e | Two bare `except Exception: return None` in the new code | `done` | `services/frame_preview.py` | `swallowed()`, per CLAUDE.md §5 |
| B13f | `zip(thresholds, counts, strict=False)` silently truncates to the shorter list, which is exactly the "computed as zero versus not computed" conflation §7 warns about when a stopped sweep hands back fewer counts than thresholds | `done` | `review/domain/threshold_sweep.curve_from_counts` | `strict=True` behind an explicit length guard that trims the grid to the counts *deliberately* and says so |
| B13g | **The sweep's result sentence was written and immediately overwritten.** The controller wrote it with `_set_status` and then called `playground_on_change` to redraw the frame at the new thresholds; that fan-out rebuilds the read-out from `_status_text`, which had never heard of the sweep. Three of its four exits are early returns that never reach `_status_text` at all. So the result, the "Stopped after N of M … so far" form of it, the stage-mismatch note and "Threshold curve failed: …" were each written and lost within one turn of the event loop. Measured on the real widget: `_playground_status` read **"No raw image for the selected frame."** beside an open dialog full of curve | `done` | `controller._describe_sweep` + `_set_frame_status`, which every exit from the fan-out now goes through | `test_the_sweeps_result_survives_the_repaint_that_follows_it` and `test_a_failed_sweep_says_so_on_the_read_out_and_restores_the_buttons` build the tab through its real constructor and press the real button; both reproduce that exact string against the pre-fix controller |
| B13h | `st.sweep_curve` was **write-only** — set on every sweep and read by nothing, so a reviewer could not tell whether it was state or residue | `done` | `controller._describe_sweep` | it is now the reader: the curve carries the subject it was measured on, and when that no longer matches the frame on screen the read-out says whose frame it is rather than leaving the old counts under a new picture. `clear_caches` clears `sweep_status` with it on a dataset swap |
| B13i | ARCHITECTURE said the sweep costs "one inference" without saying what it feeds that inference, quoted the two probe speedups as though they described the shipped path, and did not mention the ClipperLib exposure at all | `done` | `_Docs/Markdowns/ARCHITECTURE.md`, `README_WellViewer.md` | the Playground row now carries B13a's measured wrong numbers as the reason the preparation is shared; the pipeline section documents `prepare_stardist_input` / `is_near_constant` as the two steps before the network; the manual gains the flat-region caveat and what a stopped sweep looks like |
| B13j | **BLOCKER — the curve was attributed to whatever was selected when it finished.** `subject` and the user's own threshold were read in the completion callback, and nothing disables the tab while a sweep runs — nineteen segmentations plus a 10-30 s TensorFlow load on the first press, which is exactly the window in which someone clicks elsewhere. So a curve measured on B03 could be titled `GFP · C04 …`, saved to a notebook from the dialog's own Save button, and pass the staleness check, which compared that title against itself. The test that should have caught it built the curve by hand and never went through the dispatch (§7.8 face one) | `done` | `controller.playground_sweep_now` captures before the worker starts | `test_the_curve_is_attributed_to_the_frame_it_was_dispatched_on` presses the real button and moves the selection from **inside** the sweep; mutated back to the late read and it fails |
| B13k | The staleness check was keyed on the frame's address alone. Change the background method, the mesh ratio, the background well or the **NMS threshold** — which moves every count on the curve and sits three rows above the button — and the read-out went on asserting the old numbers with no warning. §7.7 #3: a reuse decision keyed on less than what invalidates it | `done` | `controller._sweep_key` | the address **and** `_seg_key` minus the probability threshold (which the sweep varies by design). Both halves are needed: `_seg_key`'s frame identity is refreshed only by the render fan-out, so a key built from it alone is stale in exactly the window where a selector has moved and nothing has repainted. `test_a_curve_measured_on_other_pixels_says_so_rather_than_sitting_there` asserts the frame case, the settings case and that typing a probability does **not** stale it |
| B13l | **Segment and the sweep could run at once, on one module-global model.** Neither looked at the other's flag. On the first load of a session both threads find the model `None` and both call the pretrained loader, whose cross-process advisory lock is keyed on the holder's pid — the holder being this very process, the dead-holder reclaim never fires and the second thread waits out the full 120 s timeout, then loads a second copy of the weights. After the load it is the ordinary TF hazard: no thread-safety guarantee for concurrent `predict` on a shared model | `done` | `frame_preview._MODEL_LOCK` (reentrant, held across the load **and** the inference) plus a mutual button gate and a named refusal on each entry point | `test_a_running_sweep_greys_out_segment_as_well_as_the_sweep_button`, `test_neither_press_starts_while_the_other_is_running` |
| B13m | **A near-constant frame loaded TensorFlow before being refused, and then reported the refusal as a measurement.** `ensure_model()` ran first, so declining cost a 10-30 s import and a gigabyte of weights; and the refusal returned `[0] * len(grid)`, which `curve_from_counts` turned into *"No nuclei were detected at any threshold in the sweep"* — computed-as-zero standing in for not-computed (§7), indistinguishable from a genuinely empty well the network really did scan. The test that should have caught the ordering was written `pytest.fail(...) if False else object()`, a branch that can never run | `done` | `frame_preview.sweep_frame` returns `None` before the load; `threshold_sweep.NOT_SWEPT_FLAT` is a fourth refusal taken in preference to every rule about counts | `test_a_near_constant_field_is_refused_rather_than_swept` (the stub now fails the test if a model is loaded) and `test_a_frame_with_no_range_is_not_reported_as_a_frame_with_no_nuclei`, which asserts the two sentences differ |
| B13n | Three `isVisible() is False` assertions were vacuous. The fixture builds the tab into a `QWidget` that is never shown, and a child of an unshown parent answers `isVisible() == False` whatever its own flag says — measured: default gives `visible=False hidden=False`, after `setVisible(False)` gives `visible=False hidden=True`. Deleting `sweep_stop_btn.setVisible(False)` left Stop offered permanently and all three green | `done` | `tests/test_playground.py` | `isHidden()`; mutated by deleting that line and the real-button test fails |
| B13o | The fake model ignored `_instances_from_prediction`'s `shape`, so no test could tell the frame's shape from the grid-subsampled `prob.shape`. A wrong one is not an error: `polygons_to_label` rasterises through `skimage.draw.polygon(*c, shape)`, which **clips** an out-of-bounds polygon to nothing — a smaller count, no exception, no marker | `done` | `_FakeModel` asserts the shape, and its `predict` returns a prob half the frame's size so the two are distinguishable | mutation: resolve onto `prob.shape` and three tests fail |
| B13p | `tile_px` was a branch the app never takes — the controller never passed it, so the tiling fallback and its test guarded something unreachable (§7.7) | `declined` | removed from `sweep_frame` and the worker | The fast path's measurement was taken untiled and tiling changes the shape of what `predict` returns, so the two cannot share a code path; with no caller, the parameter was a keyword guarding a fallback it could not reach. The memory cost that would justify tiling is now **stated** instead: `dist` is `(H/2, W/2, 32)` float32, ~134 MB at 2048² and ~537 MB at 4096², in the GUI process. If a frame ever arrives that large the answer is a control the user can see |
| B13q | Smaller things the same review found: two fast-path breadcrumbs logged at the helper's DEBUG default, where the app's own level leaves nothing for a bug report to carry — the opposite of the reason the comment beside them gives; a delegation test that asserted against its own fake's stop loop and claimed in its name to cover the real one; *"The sweep did not run"* shown to a user who pressed Stop after one full network pass; two docstrings saying `_status_text` reads `sweep_status` when `_set_frame_status` does; the numpy fallback's percentiles returning float64 under NEP 50 where csbdeep's `normalize` returns float32; and the "identical counts" claim, which holds only for frames whose dimensions are multiples of the network's `div_by` — `predict_instances` selects candidates on the **padded** probability map while `predict` crops first, so the two exclude different ≤4 px border bands | `done` | across the section | each fixed at the site, with the claim qualified rather than restated in the docstring and in ARCHITECTURE |
| B14 | Nine assertions the renderer and controller could have failed silently — the model-default marker, the requested-span x-axis, the stopped marker, the flat-region footer, the subject in the title, the scoped truncated refusal, the staleness hedge, the running line, and the dataset-swap clear | `done` | `tests/test_playground_threshold_curve.py` | each mutated on HEAD in a worktree (§7.7 #2) and watched to fail; writing the positive case first caught one of my own assertions filtering on `"none"` where matplotlib answers `"None"`, which had made the complementary test pass vacuously |

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
| C10k | Widening that guard then tripped a **harness** guard: it parsed each file itself instead of going through `tests._repo_scan.parse_source`, which parses once per process and shares the tree (CLAUDE.md §2 rule 2). Routed through the cache — but the *file list* still globs from this module's own `REPO_ROOT`, because `test_guards_bite` proves this guard against a synthetic tree by patching that name, and a list resolved from `_repo_scan`'s root would walk the real repository and leave the proof measuring nothing | `done` | `tests/test_help_topics.py` | `test_no_guard_parses_a_source_file_for_itself` and `test_help_topic_guard_catches_an_analyze_section_with_no_topic` both green |
| C10j | The help-topic guard scanned `analyze/form.py` **alone**, so extracting the Filename Schema section made a live help key report as naming a section that no longer exists. §7.5 #3: the scan surface is half the rule | `done` | `tests/test_help_topics.py` | now walks every `analyze/` module; caught by the full fast lane, not by the guard family |
| C11 | Mutations on HEAD (not `prove_guard.sh` — the inference is on `main`, so a base-ref proof measures nothing, §0.9). Four run, all biting with real `AssertionError`s: count token agreement only → 3 fail; look the slot predicate up by field name → the bare-number FOV test fails reporting `0` matched on a well-formed plate; leave the detect button unconnected → **all 6** host tests fail; silence the refusal → its test fails; count the layout readout with a glob → 2 fail | `done` | worktrees of HEAD | named in each test's docstring |
| C12 | ARCHITECTURE.md + README_WellViewer.md — existing prose **rewritten**, not appended to | `done` | `_Docs/Markdowns/` | the Analyze auto-fill section now describes two surfaces and the two-host verdict split; the `SchemaPicker` widget row describes opt-in detection; §8 gains `QLabel#Notice` with the luminance measurement behind it; the directory map gains `analyze/schema_section.py`; the manual's five-click walkthrough describes the readout and the button in the user's terms |
| C13 | Guard family + affected files + fast lane; version bump; PR | `done` | — | version 9.7.15 → 9.7.16. **Full fast lane green at commit `29331e4`: 9,194 passed, 8 skipped, 7m44s** — run after the last commit, not across the work, per CLAUDE.md §2. Two earlier lanes each found something the guard family alone did not: the blind help-topic scan (C10j) and the harness's parse-once rule (C10k). PR [#917](https://github.com/exzktly/allwell/pull/917), opened once the lane was green and every review finding acted on |
| C14 | **Scientific-Python review of the section diff** | `done` | subagent, every numeric claim scripted against HEAD | five defects, two of them wrong in the direction this section exists to prevent. Rows C14a–C14g |
| C14a | **Detect bricked the Zip Loose Folder page on a single-channel plate.** `detection_for_grouping` reasoned from what grouping needs in principle — a well and nothing else — and applied a channel-less guess. `_can_run` blocks on `SchemaPicker.errors()`, which requires `("channel", "well")`, so one press left Run and Queue dark with the explanation filed under *success*. The comment six lines from that check already said why (§7.3 #6). Reproduced on the real page before fixing | `done` | `datasets/import_loose/import_loose_compute.py` | `test_pressing_detect_never_leaves_the_page_unable_to_run` asserts `errors()`, which the first test never looked at |
| C14b | **My own fixture was not the shape its docstring claimed** (§7.8 rule 2). "A single-channel plate carries a constant channel token" — the fixture emitted `exp_A01_f0_t0`, four positions with *no* channel token, not the five-position constant-token shape. It never exercised the layout it was written about, and passed against the code it was written to constrain | `done` | `tests/test_import_loose_schema_detect.py` | the fixture is now `exp_GFP_A01_f0_t0` |
| C14c | **`shortfall()` announced files lost that the pipeline processes in full.** The pipeline validates the **channel** slot and nothing else — `_collect_available_schema_values` reads timepoint and FOV positionally, unvalidated. A plate written `fov01` instead of `f01` reported eight files "will be skipped" and processed forty of forty. The README stated it to the user as fact | `done` | `util/schema_infer.py::shortfall` | the headline states the mismatch; the consequence is claimed only for the well position, which has one. `test_the_shortfall_claims_a_consequence_only_for_the_well_position` |
| C14d | **`shortfall()` reported a sample as an absolute count.** `sampled` is capped at `_SAMPLE_LIMIT = 400` by stride; a 2,304-image plate with one row mis-named reported **4** of a real 288, unqualified, in the register styled so it cannot be skimmed | `done` | `util/schema_infer.py` | `test_the_shortfall_says_the_count_is_of_the_sample` |
| C14e | **Half-swept (§7.5 #5).** The `GROUP_LOOSE` branch — the one a folder *named* `in` takes, the common layout — still globbed after its zip-root sibling was converted, so one function held two definitions of the same word | `done` | `services/input_resolution_service.py` | `test_the_named_in_folder_branch_counts_the_same_way` |
| C14f | **`groupable_tif_count` invented a third spelling of "where is the well"**, `strip().lower()` beside `parse_schema` and `process_microscopy`'s own. Measured disagreeing with the zipper on `WELL` and `" well"`, and it substituted `"_"` for an empty separator the zipper raises on | `done` | `services/input_resolution_service.py` | routed through `parse_schema`; the mirror test is now a **differential against `_extract_well_from_filename` itself**, over 27 name×schema pairs — the old one checked my reading of the zipper, not the zipper |
| C14g | Fixtures the branch lacked and now has: `.ome.tif` double extensions (which silently cost the timepoint position), a corpus larger than `_SAMPLE_LIMIT`, mixed-case and empty well tokens, a schema shorter than the name | `done` | `tests/` | — |
| C14h | Two smaller notes taken: the Import Loose detect walked the folder twice per press (`setSchema` already emits `changed`), and the `set_variant` import sat between the two PySide6 imports | `done` | — | — |

---

## Section D — Which *part* of a field is out of focus

### D.0 Why this section exists, and the measurement that forced it

Section A measures focus once per field and offers one action: exclude the
whole field. Two things are wrong with that, and the second was found by
measuring rather than by argument.

**A field is not uniformly focused.** A single FOV can hold a gradient — one
edge sharp, the other soft — and excluding the whole frame throws away the
cells that were fine. Rarely is a whole frame genuinely unusable.

**And the whole-frame statistic barely notices.** Measured on a 512² synthetic
field of 400 nuclei, half of it blurred at σ=2:

| frame | whole-frame normalised Tenengrad |
|---|---|
| fully sharp | 27.44 |
| **half defocused at σ=2** | **23.59** |
| fully defocused at σ=2 | ~10.4 (A2's table) |

A **14 %** move. Section A flags a field at `|z| ≥ 5` against the plate, and a
14 % shift will not reach that on any plate with normal field-to-field spread.
**So the feature as built misses exactly the case this section is for.** That
is not an argument for a lower threshold — it is an argument for measuring
where in the frame the softness is.

### D.1 The statistic, measured twice

The obvious tiled form — each tile's gradient energy over the **frame's**
variance — localises beautifully and is unusable:

| frame | tiles |
|---|---|
| half sharp / half soft | sharp half 43.2, soft half 5.0 — **8.7×** |
| uniformly sharp, 200 cells | min/median **0.02** |

The second row is the whole problem. A tile holding no cells has almost no
gradient energy, so it scores like a badly blurred one — the cell-density
confound A2 rejected `var(laplace)/mean²` for, reintroduced at tile scale, and
worse. An empty corner of a perfectly good frame would be excluded.

Normalising each tile by **its own** variance fixes it, and the fix is
measured rather than assumed:

| check | frame-variance | self-normalised |
|---|---|---|
| uniformly sharp, 40 / 200 / 800 cells (min ÷ median) | 0.26 / 0.02 / 0.26 | **0.86 / 0.87 / 0.87** |
| gain ×0.25…×4, pedestal 0…1000 | exact | **exact** (max rel-diff 0.0000) |
| half sharp / half soft, ratio between halves | 8.7× | 2.49× |
| smooth gradient 0→2.5 across the frame | 40.2 → 2.9 | 27.7 → 9.1, monotone |

Self-normalising costs separation (8.7× → 2.49×) and buys density invariance,
which is the trade A2 already made once at frame scale. A tile whose variance
is a small fraction of the frame's is reported **undefined**, not zero —
three of sixty-four tiles at 200 cells — because "nothing to measure here" is
not "measured and found soft".

### D.2 No automatic cut, and this is measured too

Across four seeds × three cell densities, the **worst** tile of a sharp frame
sits at 0.73–0.91 of its own frame's median. Defocused tiles sit at
0.41–0.57 — except one at **0.804**, which is the tile straddling the focus
boundary and is genuinely intermediate.

So the populations nearly separate and do not cleanly separate, on synthetic
data, with the boundary tile overlapping. A fixed ratio would misclassify, and
one synthetic fixture is not a calibration (§0.10). The subsystem's existing
rule applies unchanged: **report the map, propose the region, let the user
confirm.** Nothing is excluded that the user has not seen.

### D.2-0 The score may not be frame-relative — and may not be the raw Tenengrad either

A frame-relative score cannot see a frame that is *uniformly* soft: every cell
deviates from its neighbours by nothing. Measured, relative-to-field median is
exactly **1.000** at σ 0, 1.5 and 3.0. So the verdict has to be on a number
that means the same thing from one frame to the next.

**And the statistic shipped at the time did not.** Measured on sharp
frames, varying only nucleus radius:

| | span | |
|---|---|---|
| defocus σ 0 → 3 (the signal) | 27.4 → 7.4 | **3.7×** |
| nucleus radius 0.9 → 3.6 px (a confounder) | 34.9 → 4.5 | **7.8×** |

The confounder is **2.08×** the signal. With seed, density and radius all
varied, sharp frames span 4.66–35.38 and σ=2 frames span 3.61–16.28 —
complete overlap. A plate-wide cut on the raw normalised Tenengrad reads a
bigger nucleus as a softer one.

A two-scale band ratio (Tenengrad after a small pre-blur over Tenengrad after
a larger one) tests the right idea — defocus attenuates *high* frequencies
specifically while a larger object shifts all scales together — and halves the
problem (confounder/signal 2.08 → **1.33**) without solving it: sharp 1.544–4.121
against soft 1.423–2.432, still overlapping.

**The blind image statistic cannot do this.** A small defocused nucleus and a
large sharp one genuinely produce similar gradient statistics.

### D.2-1 The mask is the missing information

Defocus is a property of the **optics**: it sets the width of the intensity
transition at an edge, and that width is the same whatever the object's size.
The pipeline already computes a nuclear mask, so the edge is known.

> **edge sharpness** = mean \|∇I\| on the nuclear boundary ÷ (inside − outside contrast)

Size-independent by construction (a property of the edge, not the area) and
gain/pedestal-independent (the contrast normalises both away). Measured:

| | span |
|---|---|
| defocus σ 0 → 3 | 2.745 → 1.368, **2.01×** |
| nucleus radius 3 → 13 px (4.3× size range) | 2.566 → 2.807, **1.10×** |
| confounder / signal | **0.55** (against 2.08 blind, 1.33 two-scale) |

And the overlap test, with nucleus size, cell count and seed all varied:

| population | range |
|---|---|
| sharp | 2.564 – 2.808 |
| σ = 2 | 1.518 – 1.763 |
| **overlap** | **none — a 1.45× gap** |

Nuisance parameters do not move it: gain ×0.25 and ×4 and pedestal 1000 give
2.7445 against a baseline of 2.7445; 20 vs 80 cells give 2.7465 / 2.7462; 3×
noise gives 2.7542.

So on *this* fixture the score needed no frame-relative normalisation and
therefore saw a uniformly soft frame, which is what the whole objection asked
for. The fixture is the qualifier: it gave every disc the same amplitude and
only uniform additive noise, which is the one regime in which an edge-energy
statistic is amplitude-free. §D.2e is what happens when that assumption is
dropped, and §D.2f is the statistic that does not need it.

**It is computable only per cell**, because it needs the mask. Recording focus
per cell is not a convenience or an optimisation: it is the only place a
trustworthy focus number exists. Cost measured: **323 ms for 400 nuclei on a
2048² frame** (807 µs/cell), against seconds for the StarDist inference on the
same frame.

**Two caveats, both real.** The fixtures are discs with hard edges; real nuclei
have soft, textured chromatin edges, so the separation above is an upper bound
until it is measured on real plates — that is A1, and this sharpens its
question from "does focus separate" to "does *edge sharpness* separate, and by
how much against nucleus-to-nucleus scatter". And the mask comes from StarDist
run on the possibly-soft image, so at severe defocus segmentation degrades and
the metric is **undefined exactly where defocus is worst**. That is
self-reporting rather than silent (no cells → nothing measured → the
field-level metric is what covers that case), and it is why the two layers stay
complementary.

### D.2-1b The per-cell estimator, chosen by sweep — and replaced

**This section records how the first per-cell estimator was chosen. It is not
what ships; §D.2f is.** The sweep below was run over band-and-ring geometries
measuring edge *energy*, and its conclusion — that a per-cell edge statistic
beats every whole-frame one on the size confounder — survives and is why the
column is per cell at all. What did not survive is the family it searched
within: every edge-energy statistic entangles amplitude with blur, which is
what §D.2e measured and §D.2f fixes by measuring a width directly.

Per cell we know where the edge is and what the cell's own contrast is, so the
estimator can be built around the quantity defocus actually is: the width of
the intensity transition at the boundary. Four candidates were swept across
reducer (mean / p75 / p90 / p95), band half-width (1–4) and operator, each
scored on four axes at once.

| estimator | monotone | defocus span σ 0→3 | nucleus size, 4× range | noise, 15× range | sharp-vs-σ2 gap |
|---|---|---|---|---|---|
| mean \|∇I\| ÷ C, band 1 | yes | 2.13× | 1.05× | 1.01× | 1.60× |
| p95 \|∇I\| ÷ C, band 4 | yes | 2.85× | **1.01×** | **1.00×** | 2.11× |
| p95 \|∇²I\| ÷ C, band 3 | **inverts above σ≈3** | 3.60× | 1.08× | 1.86× | 3.10× |
| **p95 \|∇²I\| ÷ C, band 3, noise floor removed** | **yes, to σ=4** | **9.63×** | 1.08× | 1.26× | **4.77×** |

**The Laplacian is the right operator and the reason is dimensional.** At a
step edge blurred by a Gaussian of width σ the peak gradient scales as 1/σ
while the peak Laplacian scales as 1/σ² — twice as sensitive in log terms.
Measured, that is 3.60× against 2.85× raw, and 9.63× once its one weakness is
removed.

**That weakness is a noise floor, and the background ring already measures
it.** Noise contributes additive Laplacian energy, so a dim noisy cell reads
as *sharper* than it is — the false-negative direction, and the same defect
that disqualified `var(laplace)/mean²` at frame scale (A2). Subtracting the
floor in quadrature from the ring already computed for the contrast
denominator costs one extra percentile and fixes both symptoms: the inversion
above σ≈3 (which would make two very different focus states give one number)
and most of the drift, 1.86× → 1.26×.

So the estimator is:

```
floor = p95(|∇²I| on the background ring)
edge  = p95(|∇²I| on the boundary band)
focus = sqrt(max(0, edge² − floor²)) / (median_inside − median_outside)
```

Each piece earns its place:

* **Laplacian, not gradient** — 1/σ² against 1/σ (the table above).
* **At the boundary** — defocus is a property of the *optics*, so the
  transition width is the same whatever the object's size. This is what kills
  the confounder: **1.08× over a 4× nucleus-size range**, against **7.8×** for
  the blind frame-level statistic. Confounder ÷ signal is **0.11**, against
  **2.08** for what ships today — nearly twenty-fold better.
* **Normalised by the cell's own contrast** — removes gain and pedestal
  exactly.
* **p95, not the mean** — the peak is where the transition is; the mean
  dilutes it with band pixels that are not on the edge.
* **Noise floor removed** — as above.

It was claimed **absolutely comparable**, and the principle behind that — that
nothing anywhere in the decision path is normalised against the frame — is
right and still holds. The claim about this particular statistic was not:
§D.2e measured it false on brightness, and §D.2f is the form that earns it.

**Two artefacts caught while measuring, both recorded because each fooled me
once.** A 1.85× "noise drift" attributed to the p95 reducer was neighbouring
discs contaminating the background ring; it vanished at 3.0× spacing. The
Laplacian's inversion was then re-checked on that same clean fixture in case
it had the same cause — it did not, and is real at every band width, which is
what sent the search to the noise floor.

### D.2-2 What this did to the metric already built

Section A shipped `focus_score`, the frame-level single-scale Tenengrad,
carrying the 2.08 confounder above. **It is deleted rather than mitigated.**
The mitigation available to it was that a plate is normally one cell line and
the score is a robust z within a timepoint across the plate, so a confounder
roughly constant across the plate largely cancels — which fails on a plate with
two cell lines, and fails where confluence changes nuclear size between
conditions, and which cannot in principle see a plate that is uniformly soft.

The field's `focus` is now the **median of its cells' own boundary values**
(`field_focus`), so the field-level and cell-level numbers are the same
measurement at two poolings rather than two different statistics that can
disagree. That is what closes A.2: the field metric no longer carries a
confounder for the plate-wide robust z to cancel, and it is comparable across
plates (§D.2f), so a uniformly soft plate is visible to it.

### D.2a Per cell as well as per field — measured

**Yes, and it makes the rest simpler.** One column, `focus`, beside the
per-cell shape features the pipeline already emits. Only focus is a per-cell
property at all: `dyn_range`, `bg_level`, `nuc_count` and `nuc_area_median`
are field aggregates by definition, and per-cell clipping is already
derivable from the max-intensity columns each channel writes.

**The size worry does not materialise for one column.** Measured at the
precision the pipeline writes, on the 120,000-cell well
`process_microscopy`'s own docstring names as a big one:

| precision | per well | per cell |
|---|---|---|
| full `repr` | 2.22 MB | 18.5 B |
| **4 significant figures** | **0.71 MB** | **5.9 B** |
| 3 significant figures | 0.58 MB | 4.8 B |

Against a CSV already at ~154 B/row, one 4-sig-fig column is about 4 % of a
17-column file and less of a real one. A *vector* per cell would be the thing
to refuse; one number is not.

**Store the raw self-normalised value, not a "Focused" score.** The gateable
form is the value relative to its own field's median — but that is derivable
from the raw values plus the centroid columns already in the CSV, while the
raw value is **not** recoverable from the relative one. And the difference is
not academic:

| frame blurred uniformly at σ | raw per-cell median | relative-to-field median |
|---|---|---|
| 0.0 | 28.36 | **1.000** |
| 1.5 | 15.73 | **1.000** |
| 3.0 | 8.44 | **1.000** |

Normalising at write time destroys the only reading that can see a uniformly
soft frame. Writing the raw number keeps both.

### D.2b Why the Tenengrad could not be gated per cell

**Superseded for the shipped metric by §D.2c, and kept because the reason it was true is the reason the estimator changed.** Everything below is a measurement of the *Tenengrad*. `edge_focus` is gated per cell, and §D.2c is the re-measurement that permits it.

A per-cell score separates sharp from soft *on average* — across a smooth
focus gradient in one frame, the relative medians run 1.554 → 1.212 → 0.842
→ 0.604 from the sharp edge to σ≈2.2. But a single cell is far too noisy to
decide on. On a **uniformly sharp** frame with cell radii 0.9–2.4 px and
amplitudes 600–6000:

| population | p5 | median | p95 |
|---|---|---|---|
| sharp cells, varied size and brightness | **0.619** | 1.000 | 1.707 |
| cells at σ≈2.2 in the gradient frame | — | **0.604** | — |

The fifth percentile of perfectly sharp cells and the median of badly
defocused ones are the same number. A 25×25 patch is not enough pixels, and
any per-cell threshold would remove a large share of good cells and keep a
large share of bad ones.

**So: measure per cell, decide on a pooled statistic.** Pooling roughly
thirty cells into a tile is exactly what pulled the sharp population to
0.86–0.87 and the soft to 0.41–0.57 in §D.1 — the same statistic at n≈30
rather than n=1.

**And pooling from the CSV removes the decode.** With a `focus` column beside
`shape_centroid_row` / `shape_centroid_col`, Review can build the tile map
from the measurements alone: no image read, no `_out.zip` open, no re-run for
the *map*. The exclusion is then per cell by centroid — the "sections of
fields" behaviour — with no change to the crop's cardinality at all, which
retires D6 before it is written. The one real cost is that the column is a
new per-cell measurement, so a dataset only gets it by being re-run; D8's
image-decode route stays the fallback for plates nobody will re-run.

### D.2c Per-cell gating, re-measured on the metric that replaced the Tenengrad

§D.2b said **do not gate per cell**, on a measurement of the *Tenengrad*: the
fifth percentile of perfectly sharp cells (0.619) and the median of badly
defocused ones (0.604) were the same number, which is an AUC of about 0.5 —
no discrimination at all. That finding does not transfer, and it has been
re-measured on the shipped `edge_focus`.

The fixture is deliberately harder than the one that chose the estimator. A
perfect step edge is not what a microscope produces, and the first attempt at
this measurement had every in-focus cell at 2.0087 ± 0.02 — the discrete
Laplacian of an ideal step divided by its own height, which is a constant and
not a measurement. So each nucleus carries its **own** boundary softness,
σ_bio drawn uniformly over 0.8–1.8 px (a 2.25× range, standing in for
chromatin at the envelope, a nucleus tilted in z, and the envelope itself),
and the field's defocus adds in quadrature: σ_total = √(σ_bio² + σ_def²).
Radii 4–16 px, amplitudes 500–6000, per-field read noise 8–25, six fields per
population, ~290 cells each.

| σ_def | p5 | p25 | median | p75 | p95 | AUC vs in-focus |
|---|---|---|---|---|---|---|
| 0.0 | 0.0698 | 0.1088 | 0.1569 | 0.2367 | 0.3124 | — |
| 0.5 | 0.0718 | 0.1048 | 0.1367 | 0.1933 | 0.2548 | 0.578 |
| 1.0 | 0.0615 | 0.0804 | 0.1005 | 0.1279 | 0.1552 | 0.777 |
| 1.5 | 0.0339 | 0.0568 | 0.0708 | 0.0869 | 0.1165 | **0.913** |
| 2.0 | 0.0228 | 0.0416 | 0.0540 | 0.0683 | 0.1037 | **0.952** |
| 3.0 | 0.0123 | 0.0322 | 0.0470 | 0.0738 | 0.1486 | 0.916 |

**So per-cell gating becomes legitimate, and stays imperfect.** At a cut placed
at the fifth percentile of the in-focus population (0.0698), σ_def = 2.0 cells
survive 23.2 % of the time and σ_def = 1.5 cells 51.8 %. The gate removes most
of what is badly defocused and it keeps a real tail, so the surface has to
report what it keeps and what it drops rather than implying the population is
now clean.

**Pooling is still strictly better**, exactly as §D.2b said, and both are kept
for that reason. The median of 30 cells:

| σ_def | p5 | median | p95 |
|---|---|---|---|
| 0.0 | 0.1351 | 0.1655 | 0.1950 |
| 1.0 | 0.0913 | 0.1018 | **0.1164** |
| 1.5 | 0.0618 | 0.0712 | 0.0806 |
| 2.0 | 0.0483 | 0.0540 | 0.0589 |

p5 of in-focus (0.1351) clears p95 of σ_def = 1.0 (0.1164) — **no overlap at
one stop of defocus**, where the per-cell populations overlap heavily at the
same σ. The per-cell column is what removes a gradient *within* a field; the
pooled field and well numbers are what decide whether a well needs a human.
They are two poolings of one measurement and the app uses both.

The σ_def = 3.0 row is non-monotone against 2.0 at p75 and p95, which
reproduces on this harder fixture the bound already written into
`edge_focus`'s docstring: it is not a severity scale past σ ≈ 3.

### D.2d No threshold can be identified automatically, and this is measured

§B.0 refused to recommend a StarDist threshold, with the measurement that every
plateau rule degenerates on a monotone curve. The same question was put to the
focus column, and the answer is the same refusal for a different reason.

Otsu's method on log₁₀ of the per-cell focus values — the natural choice, since
the population is multiplicative and the raw scale has a long sharp tail — run
over seven populations built from the fixture above. `η²` is Otsu's own
separability, the between-class variance over the total:

| population | Otsu cut | η² | good cells dropped | bad cells kept |
|---|---|---|---|---|
| **clean** — nothing defocused | 0.1363 | 0.627 | **39.5 %** | — |
| clean, twice as many cells | 0.1363 | 0.627 | 39.5 % | — |
| 25 % of cells at σ_def = 2 | 0.0890 | 0.655 | 12.0 % | 8.0 % |
| 50 % of cells at σ_def = 2 | 0.0890 | 0.671 | 12.0 % | 8.0 % |
| 50 % of cells at σ_def = 1.5 | 0.1127 | 0.637 | 26.8 % | 5.8 % |
| 50 % of cells at σ_def = 1.0 | 0.1278 | 0.607 | 34.0 % | 25.3 % |
| **everything** at σ_def = 2 | 0.0399 | 0.516 | 1.0 % | **76.1 %** |

Two of those rows are the whole finding. On a **clean** plate the rule returns a
number and throws away 39.5 % of good cells. On a **uniformly defocused** plate
it splits the bad population against itself, returns 0.0399, and keeps 76 % of
the cells it exists to catch — reporting almost nothing wrong about the worst
plate in the table. Both are the silent substitution §7 is about, arriving
through a recommendation rather than through a bug.

And there is no refusal available. η² spans 0.516–0.671 across all seven, with
the **clean** plate (0.627) scoring higher than three genuinely mixed ones. The
statistic that would have to say "a cut exists here" cannot tell the two
degenerate cases from the useful ones, so a rule gated on it would fire exactly
where it must not.

**So the Playground offers the distribution, not a number** — which is what
Section B made accessible for StarDist, and the same thing made accessible for
this metric: the evidence, the user's current cut drawn on it, and a live count
of what that cut keeps and drops. A user looking at the plot can see whether
their threshold sits in a valley or in the middle of one population, which is
better evidence than a number they would have to trust.

### D.2e Why the edge-energy estimator was abandoned

**This section records why the estimator changed. It measures the form that
was replaced; §D.2f measures the one that ships.** An adversarial scientific
review found the defect and it reproduced on an independently written
fixture — and every claim it retracted has since been re-earned by a different
statistic rather than argued back.

`edge_focus` is claimed "absolutely comparable between cells, fields, wells and
plates". It is comparable across **gain and pedestal (exactly)**, across
**nucleus size (1.16×, the design's central goal, achieved)** and across
**orientation (1.08×)**. It is **not** comparable across **brightness**, and
fluorescence microscopy is photon-limited, so brightness and noise are the same
axis.

Measured, identical geometry and identical σ_def = 1, Poisson shot noise,
varying only the nucleus amplitude:

| amplitude | 200 | 500 | 1000 | 2000 | 4000 | 8000 | 16000 |
|---|---|---|---|---|---|---|---|
| `edge_focus` | 0.5131 | 0.3715 | 0.3393 | 0.2964 | 0.2668 | 0.2485 | 0.2433 |

**2.1× at constant focus**, and the noise-free control is *exactly flat*
(0.2337 at amplitude 200, 2000 and 16000), so every bit of that spread is the
noise term the quadrature floor exists to remove. Dim reads **sharper**, which
is the false-negative direction.

And it crosses over, which is the fatal form:

| | σ_def = 1 | σ_def = 2 |
|---|---|---|
| amplitude 400 | 0.4274 | **0.3000** |
| amplitude 10000 | **0.2489** | 0.1014 |

A dim cell at two stops of defocus reads **0.3000**; a bright cell at one stop
reads **0.2489**. Any absolute cut between them keeps the badly defocused cell
and discards the better one. Stain brightness varies well to well for entirely
ordinary reasons — DNA content, apoptotic condensation, uptake, bleaching
across the read order, illumination roll-off — so this is a *directional* bias
correlated with treatment. It is the same class of defect
`review/domain/field_quality_scores.py` is proud of avoiding by keeping
`nuc_count` out of plate-wide scoring, reintroduced through `focus`.

**Why the quadrature noise floor does not fix it.** `far` is a 3 px ring only
3 px outside the dilated mask, so it sits on near-zero background while `band`
straddles the bright interior where shot noise scales as √signal. Measured, the
`far` p95 underestimates the band's real noise by **8–10×**. It is also
contaminated by *signal*: on a noise-free image the floor is 41–89 rather than
0, peaking at σ≈2, so the subtraction removes edge signal in a
defocus-dependent way.

**Why no test caught it.** `tests/test_field_quality.py::_discs` — the fixture
behind every `edge_focus` test — adds **only spatially uniform additive
Gaussian noise** and gives **every disc the same amplitude**. In that one
regime the far ring really is a valid floor, and the 1.31×/1.86× numbers in
§D.2-1b reproduce. Swap to shot noise and the same experiment gives 2–3×. This
is §7.5 #8 exactly: my own differential, on my own fixtures, reporting the one
noise model under which the design is sound.

**What this invalidates**: `Focus_Pass` as an absolute per-cell cut (D12, D13),
the well composite's median-against-cut rule (D14), the Analyze threshold field
as "a number about a microscope, the same on every plate from that instrument"
(D10, D11), and the Playground's premise that a user can choose one number for
their scope. **What survives**: the per-cell measurement as *evidence*, the
size-confounder improvement over the frame-level Tenengrad, the gain/pedestal
invariance, and the undefined-is-not-zero discipline throughout.

Four further confirmed findings, each measured — and **all four are gone with
the geometry that produced them**, because a distance transform has no ring to
mis-size, no noise floor to contaminate and no band width to fail:

* **A hole in the measurable range.** Equivalent radius 4.41–4.78 px (area
  ≈ 62–78) returns `None` unconditionally, while both smaller (49–61) and
  larger (≥ 81) measure fine — `round(r·0.34)` reaches 2 before `erode(m, w+1)`
  can clear `_EDGE_MIN_PX`. The docstring claims every nucleus at or above
  `min_nucleus_area_px = 50` is measurable; it is false, and
  `test_every_nucleus_the_pipeline_keeps_is_measurable`'s radius grid
  (4, 5, 6, 8, 16) steps straight over the gap.
* **`score_well` mis-counts a generator**: `total=0, unmeasurable=-2`, and a
  well that is half unmeasurable is silently not flagged. Its own docstring
  says an iterable is acceptable.
* **The composite's median rule is strictly dominated** by its share rule:
  `median < cut` implies >50 % soft, which always exceeds `SOFT_FRACTION` of
  10 %. The pooled statistic §D.2c cites as the justification is never the
  thing that fires.
* **Non-monotonicity is far worse than documented** — it recovers 84 % from its
  minimum and at σ=7 reads *above* the σ=2 value, on isolated cells with no
  neighbour contamination. The cause is the contrast denominator collapsing
  faster than the numerator, not the ring width the docstring blames.

### D.2f The estimator that ships: a width, not an energy

The cause of everything in §D.2e is one thing: **amplitude and blur are
entangled in every edge-energy statistic.** A dim cell and a blurred cell both
carry little gradient per pixel, and no normaliser separates them, because the
quantity being normalised already mixes them. Two fixes were tried within the
energy family and both are recorded here because each looked promising and
neither worked. Pushing the contrast measurement away from the blur halo and
weighting the noise floor by the band's real composition together cut the
rebound from 2.24× to 1.78× without removing it. Requiring the edge to clear
the noise floor by a margin removed it completely — by refusing the low-signal
cells, which at one stop of defocus left **6 %** of dim cells measurable
against **100 %** of bright ones. That is the brightness bias again wearing a
refusal, and it is worse than the defect it fixes, because a refusal
correlated with brightness is invisible in the output.

What defocus actually changes is the **width** of the transition, and a width
is a distance in pixels — independent of gain, pedestal and amplitude by
construction rather than by correction. The signed distance transform gives
every pixel's distance from the outline; binning intensity by that distance
gives the transition profile; the distance from 20 % to 80 % of the cell's own
contrast is the classic knife-edge width, and the score is its reciprocal.

Measured on a shot-noise fixture at the cell size the user gave us (20 px
across), five seeds, against the form it replaced:

| varying only | energy | width |
|---|---|---|
| brightness, 80× in amplitude | 1.52× | **1.03×** |
| nucleus size, 14–28 px | 1.09× | **1.01×** |
| read noise, 2 → 30 | 1.02× | 1.01× |
| neighbours, 30 px → contact | 1.14× | **1.02×** |
| chromatin texture, 0 → 0.8 | 1.50× | **1.14×** |
| saturation, clipped to 60 % | 2.87× | **1.45×** |
| gain and pedestal | exact | exact |
| rebound past σ = 2 | **2.24×** | **1.00×** |
| cells measurable at σ = 1, dim vs bright | 100 % | 100 % |

Independently re-measured when this section was written, on a separately
written fixture: σ 0 → 8 reads 0.8329 / 0.6457 / 0.3666 / 0.2933 / 0.2374 /
0.1773 / 0.1491 / 0.1325 / 0.1271 — falling at **every** step, sharp-to-σ2
3.51×, brightness 1.03× over 200–16000, size 1.05× over radius 7–14, crowding
1.03× to contact, gain and pedestal identical to six decimal places.

**The cost, stated plainly.** The dynamic range is narrower: sharp-to-σ2 is
3.5× here against 2.71× for the energy form on its own fixture, but the energy
form's *apparent* range came partly from the rebound it was about to suffer. A
gap that never inverts is worth more than a large one that does.

**What is left, and it is not nothing.** Clipping biases the value upward, and
this one is not error — a flat-topped nucleus genuinely does have a steeper
transition. Measured against the ceiling, with the nuclei at amplitude 2000
over a background of 100:

| ceiling | `sat_frac` | focus | vs unclipped |
|---|---|---|---|
| 2400 | 0.00000 | 0.3666 | 1.00× |
| 2100 | 0.00910 | 0.3669 | 1.00× |
| 2050 | 0.01659 | 0.3751 | 1.02× |
| 1900 | 0.02291 | 0.3996 | 1.09× |
| 1600 | 0.02576 | 0.4801 | 1.31× |
| 1400 | 0.02796 | 0.5318 | 1.45× |

**`sat_frac` cannot be used to bound it**, which is the part worth carrying
forward: it is capped by the fraction of the frame the nuclei cover — 2.4 %
here — so it stops rising while the bias keeps climbing. A sparse plate can
have every nucleus flat-topped at a `sat_frac` of two percent. The honest
statement, and the one every surface now makes, is that a cut chosen on a
well-exposed plate is too aggressive on a saturated one; there is no threshold
in `sat_frac` that makes that check mechanical.

Chromatin texture at 1.14× is the largest remaining confounder on an
unsaturated plate.

### D.3 What it hangs off

The machinery already exists and it is the machinery the user named.
`crop/region.compute_crop_pass` decides per cell by centroid against a
rectangle, takes a `FrameScope` naming which wells / FOVs / timepoints it
applies to, and is ANDed at the same chokepoint. That chokepoint —
`df_included_mask` — already ANDs four independent 0/1 columns, so a fifth
costs nothing structurally and inherits the fail-open contract every one of
them has.

**Two layers, not one, and they are not alternatives.** §D.2c measures why:

* `Field_Pass` stays as it is — the run's own six-metric robust-z verdict,
  per field, off by default, which is what sees a field that is anomalous for
  reasons other than focus (clipped, low contrast, unlike the plate).
* `Focus_Pass` is new — an **absolute** cut on the per-cell `qc_edge_focus`
  column. It is what removes a gradient *within* a field, which is the
  behaviour the user asked for and which no whole-field verdict can express.

Neither subsumes the other, and the per-cell one cannot be derived from the
per-field one: a field whose median is fine can still have a soft third.

### D.4 Where the threshold comes from, and who applies it

A cut on this column is mostly a number about a **microscope** rather than
about a dataset: it depends on the PSF, the pixel size and the nuclear
contrast, and §D.2f measures it moving under 5 % across brightness, nucleus
size and crowding, so a cut carries between plates taken on one instrument at
one magnification. Two residuals qualify "mostly" and every surface that
states the claim now states them too: clipping biases it upward to 1.45×, and
chromatin texture reaches 1.14×. So the number belongs in the Analyze form
beside the other acquisition-shaped settings, and §D.2d says it cannot be
guessed.

That gives three places and one number:

1. **Analyze / Quality Control** — `Measure per-cell focus`, **on by default**,
   and a threshold field that is **blank by default**. The run records both:
   the column, and the threshold as the dataset's own stated cut.
2. **The run records; it does not cut.** Ground rule §0.3. A pipeline that
   dropped the cells would make the decision irreversible and would need a
   re-run to revisit — and the threshold is exactly the number a user gets
   wrong the first time. The recorded value seeds Review.
3. **Review / Cell Gating** applies it, reversibly, and says what it did.

### D.4a A control that exists is not a control that works, again

Building the third gate found the second one broken, and it had already
shipped.

The cache projectors run at **load**. The pass that runs when a control on the
Cell Gating panel moves — `qc.controller.recompute_qc_all` — re-projected
`QC_Pass` and nothing else, which was correct for as long as that panel held
only the debris gate and silently wrong from the moment it grew a second
switchable layer. Measured, on a plate with one field genuinely flagged:
ticking **Exclude fields the run flagged** wrote no `Field_Pass` column at all,
the chokepoint had nothing to AND, and the row stayed included until the
dataset was reopened. The panel reported the flag correctly the whole time, so
the control looked like it had worked.

Its mirror image was there too. A projector whose switch is off returned early
and wrote nothing — right for a frame that never carried the column, wrong for
one that does, because the previous setting's verdict stays on the frame.
Unticking left the rows excluded while the panel said "currently kept" over
them.

Both are fixed, and the fix is structural rather than a third call added by
name: `recompute_qc_all` now re-runs **every registered projector**, so a
verdict layer added later is re-projected by having been registered, and a
switched-off layer **removes** its column rather than merely not writing it.

Two things about how it was found are worth keeping, because neither was the
test suite:

* **The first probe measured nothing and said so confidently.** It used a
  `SimpleNamespace` ctx whose projector registry was empty, so *no* layer
  projected and the "before" and "after" agreed — which reads exactly like a
  working feature. It took a real `AppState`, a real `AppRuntime` and a real
  `build_feature_registry()` before the numbers meant anything. §7.3 #1: a
  harness that fails open will happily confirm anything.
* **Nothing in the fast lane could see it.** Every test of the field-quality
  layer asserted the verdict, the sentence, or the column given to a
  projector called directly. Not one ticked the box and asked the chokepoint,
  which is §7.6 #1 exactly: asserting a control is *offered* is not asserting
  it *works*.

### D.5 Rows

| # | Row | State | Place | Evidence |
|---|---|---|---|---|
| D1 | The tiled self-normalised statistic | `declined` | — | Retired by D3a. Two independent reasons, each sufficient. The statistic is **frame-relative**, which §D.2-0 measured as blind to a uniformly soft frame (relative median exactly 1.000 at σ 0, 1.5 and 3.0). And a tile is a worse pooling unit than a cell: it has no idea where the cells are, so a tile's value moves with how many nuclei happen to fall in it. The per-cell column pools the same information over the objects the question is actually about |
| D2 | The density confound must not come back | `done` | `tests/test_field_quality.py::test_focus_does_not_track_how_many_cells_are_in_the_field` and `::test_focus_does_not_track_how_BIG_the_nuclei_are` | measured on the shipped function: cell count 20→600 moves it <1.15×, nucleus radius 4→16 moves it 1.36× against 7.8× for the frame-level Tenengrad. The blind control in the size test scores >2× |
| D3 | An unmeasurable outline is `None`, never 0.0 | `done` | `pipeline/field_quality.edge_focus` | §7 #3. `tests/test_field_quality.py::test_an_outline_that_cannot_be_measured_reports_nothing_rather_than_zero` |
| D3a | **One per-cell `qc_edge_focus` column** — the noise-corrected boundary Laplacian of §D.2-1b | `done` | `pipeline/field_quality.edge_focus` + `process_microscopy._quantify_image_group` | the §D.2-1b sweep, re-measured on the shipped function: span 7.4× over σ 0→3, size confound 1.36×, noise drift 1.31× over a 15× range, sharp-vs-σ2 separation 2.71× |
| D3b | Undefined where the boundary cannot be measured — no contrast, too few interior or exterior pixels | `done` | `pipeline/field_quality.edge_focus` | §7 #3, and the ring scales with the nucleus so that radius 4 (the pipeline's own `min_nucleus_area_px` of 50) is measurable rather than silently `None` |
| D3c | **No frame-relative normalisation anywhere in the decision path** | `done` | `tests/test_focus_gate.py` | Behavioural rather than an AST scan, deliberately: a scan for a division has to name the shapes it knows about and is blind to the next one, while a uniformly soft frame driven through the real path cannot be evaded by spelling. Three mutations, one per layer, each caught by its own test — normalise the gate by the frame's median and the uniform frame stops being excluded; score a well against the plate and a uniformly soft *plate* marks nothing; normalise the plot's x-axis and the drawn curve stops being the values. §D.2-0 measured the relative median at exactly 1.000 for σ 0, 1.5 and 3.0, which is the number these pin |
| D3d | Pooling still applies, for noise rather than for the confounder — and both poolings ship | `done` | §D.2c | re-measured on this metric: per cell AUC 0.913 at σ_def 1.5 and 0.952 at 2.0, against ≈0.5 for the Tenengrad §D.2b measured. Pooled at n=30 the in-focus p5 (0.1351) clears the σ_def=1.0 p95 (0.1164) outright |
| D4 | The field's `focus` is the **median of its own cells' boundary values**, so the two poolings cannot disagree | `done` | `pipeline/field_quality.field_focus` | `tests/test_field_quality_is_wired_into_a_run.py::test_the_fields_focus_is_the_median_of_its_cells_own_outlines` |
| D5 | `focus_score` — the frame-level Tenengrad — is **deleted**, not kept beside its replacement | `done` | `pipeline/field_quality.py` | §D.2-2. Two statistics answering one question is how they come to disagree |
| D6 | A per-field crop region, needing a crop cardinality this app does not have | `declined` | — | Retired by D3a before it was written. With a per-cell column the exclusion is per cell, which is what "remove only the soft sections" means and needs no rectangle at all — a focus gradient or a coverslip tilt is not a box. The crop tool keeps its single region for the manual case it already serves |
| D7 | Whole-field exclusion stays, off and secondary, beside the per-cell gate rather than replaced by it | `done` | `review/features/qc/tab_view.py` | §D.3: neither layer subsumes the other, and a field whose median is fine can still have a soft third |
| D8 | **Where the gate reads from.** The CSV column the run already wrote — no image decode, no `_out.zip` open | `done` | `review/data/focus_gate.py` | the one real cost of the column is that a dataset gets it only by being re-run, which the panel must say rather than reading as "nothing was flagged" |
| **D10** | **Analyze / QC: `Measure per-cell focus`, on by default and toggleable, plus the threshold field** | `done` | `analyze/qc_section.py` (extracted — `analyze/form.py` is at 935 of its 936-line budget) + `fields.py` / `pinfo.py` / `cli_args.py` / `pipeline_service.py` / `settings_digest.py` | §D.4, and `tests/test_focus_setting_reaches_the_pipeline.py`, which drives the real form through the whole seven-file chain the debris gate already takes — a setting that reaches six of the seven is a control that does nothing. Seven mutations, one per link, each caught by the test named for it; the restore was genuinely broken when the test was written, because a v2+ sidecar carries the parser's **negative** `no_focus_metric` while the older block carries the positive `measure_focus`, and reading one spelling restores nothing for half the datasets in existence |
| **D11** | The run **records** the threshold and does not apply it | `done` | `process_microscopy.py` | §0.3 and §D.4 #2. Mutation: have the pipeline drop the cells and the decision becomes irreversible without a re-run |
| **D12** | `Focus_Pass` — the per-cell 0/1, ANDed at the same chokepoint as its four siblings | `done` | `review/data/focus_gate.py` | §D.3, and `tests/test_focus_gate.py`. Five mutations, each caught by the test named for it: drop the re-projection, leave a switched-off layer's column behind, stop the chokepoint ANDing it, treat an unmeasurable cell as soft, or never register the projector |
| **D13** | Review / Cell Gating: the third gate, its threshold seeded from the run, with a live keep/drop readout | `done` | `review/features/qc/tab_view.py` | §D.2c — the gate keeps a real tail (23.2 % of σ_def=2 cells at a cut set to the in-focus p5), so the panel names the unmeasurable separately and says they are **kept**. Driven through the real control (§7.6): building the row and asserting the checkbox exists is what let the sibling defect below ship |
| **D14** | **The well composite**: one per-well verdict over the soft fraction, the unmeasurable fraction and the field flags, marking wells that need a human | `done` | `review/domain/well_composite.py` + `review/data/focus_gate.wells_needing_review` + Home's plate map | §D.2c's pooled table for the median rule, and §D.2c's own five-per-cent false-positive rate for the share bound — twice it, so the bound cannot be the cut's own noise. `tests/test_well_composite.py` holds a well on each side of the same number, so an off-by-one in the comparison shows |
| **D15** | **Playground: the per-cell focus distribution**, the current cut drawn on it, and **no recommendation** | `done` | `review/domain/focus_distribution.py` + `review/features/image_browser/playground/focus_curve_view.py` | §D.2d. The refusal is asserted against the figure (no second vertical line), against the sentence, and against the module's own surface — a later `suggest_threshold` would otherwise pass every numeric test in the file. Driven through the real button in `tests/test_playground.py` |
| D16 | Renders, UX review, scientific review, docs, version bump, PR | `done` | `tests/visual/render_views.py`, ARCHITECTURE §6.7, README | Nine renders across the three new surfaces, and reviewing them found three defects the tests could not: the Analyze grab came out **byte-identical** to an unrelated one because the form opens scrolled to the top and the new controls were off screen; the distribution figure's title was clipped off **both** edges (`set_title` does not wrap) and its footnote overflowed the panel (matplotlib's `wrap=True` wraps to the *figure*, not the axes); and the plate's legend still read "Wells with a flagged field" over a glyph now driven by a composite, so it named a cause a marked well need not have |

### D.6 The pressure test, and the rows it opened

The user asked for an adversarial review of the whole feature — the
calculation, the interface, and every toggle that changes what is measured,
what is visible and where the cut sits. It returned twelve findings across the
estimator, four surfaces, the persistence layer and the documentation. Finding
1 is closed by the estimator rewrite §D.1 now describes; the rest are the rows
below, one per finding, sub-lettered where the finding itself had parts.

**Every row here needs a test in the slow lane or it has no test at all.** That
is the structural lesson of the review: about two hundred fast-lane assertions
cover this feature and not one of them constructs an application, so a panel
reading state the shell has just emptied, a plate that never repaints, and a
toggle that draws nothing in one of its two modes were all invisible to the
lane that gates every push. CLAUDE.md §7.8 names the price this repository
already paid for exactly that. `tests/test_focus_tools_in_a_real_app.py` is
where those tests go.

| # | Row | State | Place | Evidence |
|---|---|---|---|---|
| D17 | **The estimator is non-monotone past two stops of defocus**, so a floor cut keeps the worst cells and drops milder ones. Measured 2.24× recovery from the minimum; the guard stopped at four stops and bounded the tail rather than requiring each step to fall | `done` | `pipeline/field_quality.edge_focus` | Closed by the rewrite: the statistic is now the **width** of the intensity transition rather than its energy. Re-measured independently on a shot-noise fixture at the cell size the user gave (20 px across), 5 seeds: σ 0→8 reads 0.8329 / 0.6457 / 0.3666 / 0.2933 / 0.2374 / 0.1773 / 0.1491 / 0.1325 / 0.1271 — **strictly falling at every step**, so the rebound is 1.00×. `tests/test_field_quality.py` walks the grid to eight stops and requires each step to fall |
| D18 | **Four tooltips, two docstrings, the manual, ARCHITECTURE ×2 and §D.4 still assert the comparability claim §D.2e retracted** — and §D.2e itself was out of date, because it measured the estimator that was replaced | `done` | fifteen sites across `analyze/`, `review/`, `pipeline/`, `process_microscopy.py`, both live documents and §D.2-0 / §D.2-1b / §D.2-2 / §D.2e / §D.4 here; §D.2f is new and holds the measurement | Neither a blanket retraction nor the original claim. Re-measured independently on a separately written shot-noise fixture before a word was rewritten: brightness 1.03×, size 1.05×, crowding 1.03×, texture 1.02× over 0–0.3, gain and pedestal identical to six decimals, σ 0→8 falling at **every** step. Clipping is the one real residual at 1.45×, and `sat_frac` does not bound it — it is capped by the nuclear area fraction, so a sparse plate can be wholly flat-topped at 2 %. `tests/test_focus_claims_stay_measured.py` holds it: a text block claiming comparability about focus must carry the clipping caveat within reach. **A phrase ban would not have worked** — the exact wording that shipped is a sentence this repository still wants to write — and the positional rule found three sites the hand sweep had missed, including one in the Playground tooltip. Proven by mutating HEAD (the base ref has none of these files), and eight cases in `test_guards_bite.py`, four violations and four clearances |
| D19 | **A Gating or Curation reset makes the panel deny the dataset was ever measured.** The reset empties the row cache; every focus predicate reads the cache; the panel then names a remedy — re-run the whole plate — that is not needed. `focus_gate`'s own docstring calls this the one distinction that must never collapse | `done` | `review/data/focus_gate.dataset_has_focus_column` returns `bool \| None` | `tests/test_focus_tools_in_a_real_app.py`, two tests, one per route in. Mutation: return `False` for an empty cache instead of `None` and both fail with the false sentence in the message |
| D20 | **The persisted `qc_params` block permanently overrides the run's recorded cut.** Touching any Cell Gating control writes the block; the lazy `from_pipeline_info` seed is then never reached again, so a later re-run that records a different number leaves Analyze and Review showing two numbers for one dataset with no warning | `todo` | `review/persistence/qc_params.py` | §7: a silent substitution. The box's own tooltip asserts a provenance that has by then stopped being true |
| D21 | **The Playground measures a different channel and a differently-corrected frame from the column the cut applies to.** Its curve is the selected channel with the Playground's own corrections; the column is always the nuclear channel with the run's. Measured 1.78× on the median and 8× on the p5–p95 spread between channels; ≤2.6 % for five of seven corrections and **1.27×** for `fft_bandpass` | `todo` | `playground` focus button + `_sweep_subject` | A cut read off the wrong channel can exclude 100 % of the nuclear population. The saved figure does not record the correction modes, so it cannot be told from one measured differently |
| D22 | **The Home plate's quality markers do not repaint when the cut changes.** `wells_needing_review` is a function of the cut and is reached only through `refresh_home`, which no cut-change path calls — so Overview marks wells against the previous cut, with tooltips quoting the old number | `todo` | `review/features/home/controller.py` | Two surfaces on one page, one cut, two answers |
| D23a | **The overlay draws nothing in Binary mode**, while its button still says "Why removed: On". The ink block sits in the `else` of the binary split, and the reasons are computed and discarded — a full `get_rows` and verdict walk per refresh, wasted | `todo` | `review_image/renderer.py` | §7.6 exactly |
| D23b | **Two layers are the same pixel value.** "Outside the crop" and "outside the scatter gates" both map to `text_muted`; CIE76 ΔE = 0. The guard asserts every layer *has* an ink and that none is the reserved tint — it passes with all six identical | `todo` | `_layer_inks()` + its guard | §7.8 rule 1: name the wrong implementation that still passes this |
| D23c | **Green means excluded.** "Out of focus" maps to `success`, the app's own good token, and on a green-tinted channel the outline is close to invisible | `todo` | `_layer_inks()` | — |
| D23d | **The "readout" three docstrings and a tooltip promise does not exist.** The hover emits position, channel value, cell id and MFI, and nothing about exclusion, so a cell failing three layers is drawn in one colour and the other two cannot be learned | `todo` | `review_image/controller.py` hover | The tuple `collect` builds is discarded except for `layers[0]` |
| D23e | **No legend and no documentation.** The colour→layer mapping appears on no surface, and neither document mentions the control — which CLAUDE.md §6 requires of a new user-visible one | `todo` | — | The Home plate marker, one feature over, refuses to draw a glyph without a legend row. The same invariant is missing here |
| D23f | **Nothing-excluded is indistinguishable from broken.** With the toggle on and nothing excluded the picture is unchanged and no surface says so | `todo` | — | §0.5 |
| D24 | **The focus checkbox can end up checked, disabled and armed.** Disabling does not uncheck, so a user who lowers the cut until nothing is soft cannot untick a box that is still persisted as on — and raising the cut later fires the gate without them re-arming it. An unparseable value leaves the box and the params disagreeing with no correction | `todo` | `review/features/qc/tab_view.py` | The field-quality box has the same shape |
| D25 | **The well composite counts debris and already-excluded rows as unmeasurable.** `per_well_focus` walks every row and consults none of the four sibling verdicts, so a debris-heavy plate marks every well for review about cells the user has already filtered out — and `focus_effect_text`'s count overstates the effect by the same factor | `todo` | `review/data/focus_gate.py` | The marker means least on exactly the plates that need it most |
| D26 | **Analyze accepts a threshold that deletes the plate, with no feedback**, and does not remember the one number its own tooltip calls instrument-level. A threshold typed with the metric unticked is collected, dropped and never mentioned | `todo` | `analyze/qc_section.py`, `analyze/domain/fields.py` | Review says what a cut would do; Analyze, where the number is typed, says nothing |
| D27 | **`load_and_project` runs the projector list unguarded while `recompute_qc_all` guards each one** — same list, two failure contracts, and the unguarded path is the one that decides whether a well enters the cache at all. Latent rather than live. Related: the registration docstring claims a profile that strips a feature strips its projector, and the registration is unconditional | `todo` | `review/data/data_loading.py`, `review/features/registry.py` | — |
| D28 | **The Playground draws a distribution for a dataset that has no column at all**, cut marked and counts live, and the resulting number cannot be applied to anything without re-running the plate. Cell Gating says so; the Playground does not | `todo` | `playground` focus button | The specific shape the user asked about: a number appearing with no way to act on it |


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
