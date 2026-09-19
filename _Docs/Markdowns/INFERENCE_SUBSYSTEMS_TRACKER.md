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
| A2 | Fix the normaliser | `done` | `pipeline/field_quality.focus_score` | **Normalised Tenengrad** — gradient energy over image variance. Gain 0.25/1/4 → 27.34/27.47/27.48; pedestal 0/100/1000 → 27.48/27.47/27.43; cell count 20→600 → 27.37–27.55 (**1.4 %**); defocus σ 0/1/2/4 → 27.47/19.60/10.43/4.19. Across three seeds at five cell counts, sharp (27.27–27.64) and defocused σ=2 (10.16–11.84) **do not overlap**. The rejected `var(laplace)/mean²` spans 30× on gain, 707× on pedestal and 6.7× on cell count alone |
| A3 | Per-metric justification for "deviation from the plate implies artifact", and the split it produces | `done` | `field_quality.ACQUISITION_METRICS` / `BIOLOGICAL_METRICS`, justified in both module docstrings | three of six survived: focus, saturation, dynamic range. Nucleus count, median area and background are scored **within a well across its fields** and never plate-wide |
| A4 | Extract a cohesive module from `process_microscopy.py` | `done` | `pipeline/stardist_cache.py` — the pkg_resources shim, Keras cache lookup, cross-process download lock, dead-holder reclaim and corrupt-cache purge | 4048 → 3828, budget ratcheted down. Two module constants could not follow the re-export (a re-export is a binding); the recovery tests now patch the module that owns them |
| A5 | Extract a cohesive module from `services/remote_runner.py` to create headroom (§0.6) | `done` | `services/remote_conda.py` — how a command is built to run inside the remote's conda environment | one job asked in four places (single-run launch, batched driver, scheduler handoff, pre-flight probe) and needing the identical answer in all four: which env a host's `remote_python` resolves to, named or absolute prefix, what `conda run` argv that is, and the shell guard that proves it exists. 7326 → 7141, ratcheted down. Every name re-exported, so no call site and no test moved |
| A6 | `sat_frac` + `dyn_range` inside `load_2d_gray`, where the source dtype still exists | `done` | `pipeline/io.load_2d_gray_with_ceiling` — the old function is now a wrapper over the new one, so the array cannot drift | `test_field_quality_is_wired_into_a_run.py::test_saturation_is_measured_rather_than_defeated_by_the_float_cast` writes a **clipped** `uint16` frame, reads it through the loader, and asserts the exact fraction. Mutation: compare against the float array's own dtype max and it reports 0.0 — the defeated metric, indistinguishable from a plate with no clipping |
| A7 | Hold the pre-correction arrays for the focus metric, given `_correct_image_group` rewrites in place | `done` | measured in `_load_image_group`, before anything corrects | `test_focus_is_measured_before_the_correction_reshapes_the_variance` replaces the correction with one that blurs the frame beyond recognition and asserts the reported focus is the raw frame's — then asserts the blur really would have moved it, so the test is not satisfied by a correction that changed nothing (§7.8 rule 2). Cost measured rather than assumed: the three metrics are ~0.4 s on a 2048² frame against several seconds of segmentation for the same group |
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

### D.3 What it hangs off

The machinery already exists and it is the machinery the user named.
`crop/region.compute_crop_pass` decides per cell by centroid against a
rectangle, takes a `FrameScope` naming which wells / FOVs / timepoints it
applies to, and is ANDed at the same chokepoint. Segmentation's drag-delete
removes cells inside a dragged box by pixel overlap. Neither needs inventing;
what is missing is that the crop is a **single** rectangle for the whole
dataset, and that nothing routes a flagged field to either tool.

`Field_Pass` narrows rather than a new layer being added: the column already
means "this row's field is usable", and a per-tile verdict makes it mean "this
row's part of its field is usable". The projector, the chokepoint, the toggle,
the plate marker and the persistence are unchanged.

### D.4 Rows

| # | Row | State | Place | Evidence |
|---|---|---|---|---|
| D1 | `focus_map(image, tiles)` — the self-normalised tiled statistic, undefined where a tile holds no signal | `todo` | `pipeline/field_quality.py` | the D.1 table, re-measured against the shipped function rather than against the probe |
| D2 | **The density confound must not come back.** A guard that a uniformly sharp frame's map is flat across a thirty-fold range of cell count | `todo` | `tests/` | mutation: normalise by the frame variance and the guard fails at 0.02 |
| D3 | An undefined tile is `None`, never 0.0, and the region proposal skips it rather than treating it as soft | `todo` | `pipeline/field_quality.py` | §7 #3 |
| D4 | Per-cell verdict from a tile map + the cell's centroid, replacing the per-field one where a map exists | `todo` | `review/data/field_quality_cache.py` | the existing `Field_Pass` tests must still pass — this narrows the rule, it does not add a layer |
| D5 | The proposal surface: the flagged field on Segmentation with its soft tiles outlined and the numbers shown | `todo` | `review/features/image_browser/` | driven through the real control (§7.6) |
| D6 | Confirming writes a **per-field** region. Today there is one crop rectangle for the whole dataset (`ctx.state.dataset.crop_region`) | `todo` | `review/features/crop/` | the scope mechanism already exists (`FrameScope`); the cardinality does not |
| D7 | Whole-field exclusion stays, off and secondary — a frame with no usable tiles at all falls out of the per-tile rule anyway | `todo` | `review/features/qc/tab_view.py` | |
| D8 | **Where the map is computed.** Review, on demand, so every existing dataset benefits with no re-run — costs one decode per field inspected | `todo` | `review/` | the alternative (store it in the sidecar) needs a re-run of every plate and grows the block ~64× per field |
| D9 | Renders, UX review, scientific review, docs, version bump, PR | `todo` | — | |

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
