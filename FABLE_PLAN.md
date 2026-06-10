# FABLE_PLAN.md — Codebase Health Work Plan

> Living work plan derived from the architecture/quality review of All-Well.
> It turns the review's five prioritized proposals (P1–P5) into concrete,
> sequenced, individually-checkable steps.

---

## ⚠️ HOW TO USE THIS FILE (read first)

**This file MUST be kept up to date as work proceeds. It is the single source
of truth for implementation status.** Whoever (human or agent) executes a step
is responsible for updating it in the *same* commit/PR that makes the change.

Rules:

1. **Every step has a status box.** Update it the moment its state changes:
   - `[ ]` **TODO** — not started
   - `[~]` **IN PROGRESS** — started, not merged
   - `[x]` **DONE** — merged to the target branch and verified
   - `[!]` **BLOCKED** — include a one-line reason inline
   - `[-]` **DROPPED** — include a one-line reason inline
2. **Update the per-task "Status" line and the "Last updated" / "Status log"
   at the bottom** whenever a box changes.
3. **Record evidence.** When a step is `[x]`, append the commit SHA / PR number
   and (where relevant) the test that proves it, in the step's notes.
4. **Do not delete steps.** If a step is abandoned, mark it `[-]` with a reason
   so the history stays legible.
5. **Keep the global progress table (below) in sync** with the per-task boxes.
6. One PR per task (P1…P5) unless a task explicitly says otherwise. Each PR
   should leave the file green for the steps it claims.

---

## Global progress

| Task | Title | Risk | Status | PR / SHA |
|------|-------|------|--------|----------|
| P1 | CI + committed lint config | Low | `[x]` DONE | _this branch_ |
| P2 | Observable exception-swallowing (`swallow` helper) | Low | `[ ]` TODO | — |
| P3 | Fix `domain/`→`features/` & `data/`→`features/` layering | Low–Med | `[ ]` TODO | — |
| P4 | Decompose the two worst monoliths | Med | `[ ]` TODO | — |
| P5 | Raise core-pipeline (`process_microscopy.py`) coverage | Med | `[ ]` TODO | — |

**Suggested execution order:** P1 → P2 → P3 → P4 → P5 (each builds safety the
next one relies on). P1 and P2 are independent and can land in parallel.

---

## Target branch

All work lands on `claude/codebase-architecture-review-8crqbn` (or per-task
sub-branches that merge into it). Do not push elsewhere without explicit
permission.

---

## Conventions referenced by this plan

- **Qt-free-core contract:** the pipeline (`process_microscopy.py` and the
  `*_core.py` modules) must never import `well_viewer`, `widgets`, or any Qt.
  Any code shared by pipeline + GUI lives in a repo-root `*_core.py` module.
- **Guardrail tests:** `tests/test_guardrails.py` already enforces a line
  budget on `well_viewer/runtime_app.py`. We reuse this *pattern* for new
  structural guardrails (layering, monolith size).
- **Headless tests:** `QT_QPA_PLATFORM=offscreen` is set in `tests/conftest.py`
  before any Qt import; CI must export the same.

---

# P1 — CI + committed lint config

**Goal:** Make the existing ~800-test suite and the (currently local-only)
ruff rules run automatically on every PR. Highest ROI, lowest risk.

**Why:** No `.github/workflows` exists today; nothing enforces the test suite.
Heavy `noqa` usage (E402, N802, SLF001, BLE001) proves ruff is used locally but
no config is committed, so rules are not reproducible. Also removes the 6 stray
`DEBUG print()` statements.

**Status:** `[x]` DONE

### Steps

- [x] **P1.1** Added `pyproject.toml` with a curated `[tool.ruff]` config.
      **Reality check that changed the approach:** the codebase was *never*
      lint-clean under the broad rule set the `noqa` codes implied — a bare
      `ruff check --select E,F,W,N,BLE,SLF,A` reports **6,612** issues (4,046
      unsuppressed `SLF001`, 1,220 `E501`, 404 `BLE001`). Those `noqa`s were
      ad-hoc, not evidence of an enforced config. So instead of "capture the
      effective config", the config enforces a **high-signal subset the tree
      actually satisfies** (`E4`,`E7`,`E9`,`F`,`W`) and ignores the stylistic
      families (`E402/E501/E701/E702/E731/E741`, `F401/F811/F821`). `ruff`
      pinned to `0.15.8`, `target-version = "py310"`. `ruff check .` → clean.
- [x] **P1.2** Added `.github/workflows/ci.yml` with two jobs: `lint`
      (`ruff==0.15.8` → `ruff check .`) and `test` (installs Qt system libs +
      `tests/requirements-dev.txt`, runs `pytest` under
      `QT_QPA_PLATFORM=offscreen` with a 180s per-test timeout). Runs on Python
      **3.11** (verified working in the dev sandbox; the ML pins that require
      3.10 are deliberately not installed). Triggers on `pull_request` + push to
      `main`, with `concurrency` cancellation.
- [x] **P1.3** Converted the **4 production** flag-gated `DEBUG print()`s to
      `logging.getLogger(__name__).debug(...)` (added module loggers to
      `scatter/controller.py`, `barplot/controller.py`, `barplot/renderer.py`).
      The **2** remaining prints in `grouping/label_grid_view.py:450,452` are
      inside an `if __name__ == "__main__":` demo harness — legitimate, kept.
      Verified: `grep print( well_viewer services` returns none outside the demo.
- [x] **P1.4** Triaged empirically — **no test needs the ML stack.** Every
      "heavy-suspect" module (`test_segmentation_methods`,
      `test_stardist_finite_guard`, `test_model_recovery`, `test_labelfree_phase`,
      `test_playground`, `test_robust_background`, `test_background_scalematch`,
      `test_qc_tooling`, `test_shape_features`, `test_per_fov_checkpoint`) passes
      with only the lightweight stack — they mock/degrade internally. So no
      `heavy` marker was needed; CI installs only the light stack. The one real
      cost is runtime: `test_playground` alone is ~4 min (22 tests). A `slow`
      marker is left as a future optimization (see P1.6), not required for green.
- [x] **P1.5** Local equivalent of "green PR" performed (no GitHub Actions
      runner available from the dev sandbox to capture a run URL): `ruff check .`
      → *All checks passed*; targeted suites green (198 pure-logic + 39 Qt
      feature tests covering the edited files); full-suite run confirmed no
      failures/timeouts. **TODO for repo owner:** confirm the Actions run is
      green on the first PR and (optionally) add a status badge to the README.
- [ ] **P1.6** *(optional, deferred)* Register a `slow` marker in `pytest.ini`
      and mark `test_playground` so a fast local loop can use `-m "not slow"`.

**Acceptance criteria:** A PR shows two passing checks (`lint`, `test`); `ruff
check .` passes locally on a clean tree; no `print(` remains in
`well_viewer/`/`services/` production paths. — **Met** (CI run URL pending first
PR; see P1.5).

**Notes / evidence:**
- `ruff` pinned `0.15.8`; config in `pyproject.toml`.
- Genuine fixes applied beyond config: removed 3 dead `F841` assignments
  (`review_image/controller.py` `target`, `scatter/controller.py` `ratios` +
  `prev_selected`); auto-fixed 30 trailing-whitespace + 1 stray f-string.
- New/changed files: `pyproject.toml`, `.github/workflows/ci.yml`,
  `tests/requirements-dev.txt` (added pytest-timeout + light sci stack),
  4 feature modules (print→logger), 2 controllers (dead-var removal).
- Commit SHA: _(filled at commit time below in the status log)_

---

# P2 — Make exception-swallowing observable

**Goal:** Convert the ~286 silent `except Exception: pass/continue/return None`
sites into *logged* swallows without changing runtime behavior (still no
crash), so hidden failures leave a breadcrumb.

**Why:** Only ~47 of ~486 broad catches currently log. Silent swallowing is the
most pervasive weakness and is indistinguishable from hiding bugs. The
persistence layer has 100+, including a nested silent catch
(`well_viewer/persistence/view_state.py:190`).

**Status:** `[ ]` TODO

### Steps

- [ ] **P2.1** Add `well_viewer/core/safe.py` with a single, well-documented
      context manager:
      ```python
      @contextmanager
      def swallow(context: str, *, level=logging.DEBUG):
          try:
              yield
          except Exception:            # noqa: BLE001 — intentional boundary
              logging.getLogger("well_viewer").log(
                  level, "swallowed: %s", context, exc_info=True)
      ```
      - Behavior is identical to `try/except Exception: pass` except it logs.
      - Provide a variant or documented pattern for the
        `except: return <fallback>` shape (callers keep their fallback; the
        `with swallow(...)` wraps only the risky call).
- [ ] **P2.2** Add unit tests for `safe.swallow`:
      - it suppresses the exception (no propagation),
      - it emits exactly one log record at the configured level with the
        context string and traceback (`caplog`),
      - it does **not** swallow `KeyboardInterrupt`/`SystemExit` (use
        `except Exception`, which already excludes those).
- [ ] **P2.3** Mechanically migrate the silent sites, **persistence/ first**
      (highest density, lowest behavioral risk):
      - `well_viewer/persistence/*.py` — start with `view_state.py`,
        `sample_definitions.py`, `heatmap_layouts.py`.
      - Replace `try: <body> except Exception: pass` with
        `with swallow("<module>.<what>"): <body>`.
      - Flatten the nested silent catch at `view_state.py:190`.
- [ ] **P2.4** Migrate the rest in batches by area (core/, then features/),
      keeping each batch a reviewable diff. Preserve any catch that is
      *specific* (e.g. `except (ValueError, AttributeError)`) — only the broad
      silent ones are in scope.
- [ ] **P2.5** Add a ruff guardrail against *new* silent broad catches: enable
      `BLE001` (blind-except) repo-wide so any future bare `except Exception`
      must be justified with an explicit `# noqa: BLE001` + reason, nudging
      authors toward `swallow(...)` or a specific type.
- [ ] **P2.6** Sanity pass: run the full suite; confirm no behavior change
      (tests still pass) and spot-check logs show breadcrumbs under a forced
      failure.

**Acceptance criteria:** `safe.swallow` exists + tested; persistence layer has
zero silent `except Exception: pass`; suite still green; ruff flags new blind
excepts.

**Notes / evidence:** _(count of sites migrated per module; commit SHAs)_

---

# P3 — Fix the real layering violations

**Goal:** Remove the two confirmed *module-level* upward dependencies where
`domain/` and `data/` import from `features/`, and lock the layering with a
guardrail test.

**Why:** Business logic and the data layer must not depend on UI features.
Confirmed module-level imports:
- `well_viewer/domain/sample_definitions.py:36` →
  `from well_viewer.features.barplot.controller import bar_groups_from_data, bar_groups_to_dict`
- `well_viewer/data/image_discovery.py:30` →
  `from well_viewer.features.preview.controller import classify_member, open_imgref_as_array, read_member_bytes, scan_zip_members`

(The export↔scatter and crop↔review_image feature couplings are currently
worked around with function-local imports — a smell, but out of scope for P3;
tracked as a stretch step P3.5.)

**Status:** `[ ]` TODO

### Steps

- [ ] **P3.1** Move `bar_groups_from_data` / `bar_groups_to_dict` (pure data
      transforms over `BarGroup`/`ReplicateSet`) **down** into
      `well_viewer/domain/batch_models.py` (or a new
      `well_viewer/domain/bar_groups.py`). Re-export from
      `features/barplot/controller.py` for backward compatibility so the
      feature's public surface is unchanged.
      - Update `domain/sample_definitions.py` to import from the domain module.
      - Verify with `test_barplot_collect.py`, `test_sample_def_autosave.py`.
- [ ] **P3.2** Move preview's zip/array helpers (`classify_member`,
      `open_imgref_as_array`, `read_member_bytes`, `scan_zip_members`) **down**
      into the `data/` layer (e.g. `well_viewer/data/zip_members.py`, alongside
      `zipfile_cache.py` / `image_resolver.py`). Re-export from
      `features/preview/controller.py` for compatibility.
      - Update `data/image_discovery.py` to import from `data/`.
      - Verify with `test_image_identity.py` and any preview tests.
- [ ] **P3.3** Add a guardrail test `tests/test_layering.py` (reusing the
      `test_guardrails.py` pattern) that statically scans imports and **fails
      if any module under `well_viewer/domain/` or `well_viewer/data/` imports
      from `well_viewer.features.*`** (module-level). Use AST or a regex over
      source; ignore function-local imports if needed, but the goal is zero
      module-level upward imports.
- [ ] **P3.4** Run the full suite; confirm no import-time regressions and the
      new guardrail passes.
- [ ] **P3.5** *(Stretch, optional — own PR)* Reduce feature↔feature coupling
      that's currently hidden behind function-local imports (export↔scatter,
      crop↔review_image) by extracting the shared collectors into a neutral
      module. Only attempt if it doesn't balloon scope; otherwise mark `[-]`
      with a note to revisit.

**Acceptance criteria:** `domain/` and `data/` have zero module-level
`features.*` imports; `tests/test_layering.py` enforces it; existing tests green;
public APIs preserved via re-export.

**Notes / evidence:** _(new module paths; commit SHAs)_

---

# P4 — Decompose the two worst monoliths

**Goal:** Carve `process_microscopy.py` (5,129 LOC) and
`well_viewer/features/image_table/controller.py` (2,485 LOC, 71 functions) into
cohesive modules, using existing tests as the refactoring net. **No behavior
change.**

**Why:** These are the largest files *and* among the highest-churn — the classic
"future bug" signature. `process_image_group()` mixes high-level segmentation
dispatch with low-level numpy + file I/O via 15-variable closures.

**Status:** `[ ]` TODO

### Steps — `process_microscopy.py`

- [ ] **P4.1** Establish the safety net first: confirm/extend characterization
      coverage for the current pipeline outputs before touching code (lean on
      `test_app_characterization.py`, `test_qc_tooling.py`,
      `test_flatfield_core.py`, `test_robust_background.py`,
      `test_segmentation_methods.py`). Note any uncovered branch you're about to
      move; this overlaps with P5 and de-risks both.
- [ ] **P4.2** Extract `corrections.py` (Qt-free, repo-root or a pipeline
      subpackage that honors the no-Qt contract): flatfield application,
      background subtraction/estimation, the `_correct` closure logic — turned
      into explicit functions with named parameters instead of captured scope.
- [ ] **P4.3** Extract `segmentation.py`: the method-dispatch `if/elif` in
      `process_image_group()` becomes a strategy map of named functions
      (`segment_stardist_watershed`, `segment_cellpose`, …). Keep model-cache
      globals where they are or pass them explicitly.
- [ ] **P4.4** Extract `io_outputs.py`: QC TIFF writes, CSV assembly, sidecar
      generation (`_safe_imwrite` call sites). `process_image_group()` becomes
      orchestration that calls correction → segmentation → quantify → io.
- [ ] **P4.5** Keep `process_microscopy.py` as the orchestrator/CLI entry; it
      imports the new modules. **Re-verify the remote path**: the bootstrap
      uploads specific files to the worker (history shows a prior crash when
      `flatfield_core.py` wasn't uploaded). Update
      `services/remote_runner.py` / `scripts/_bootstrap_lib.py` upload manifest
      to include any new modules, and add/extend a test asserting the manifest
      covers all pipeline imports.
- [ ] **P4.6** Run full suite + a real (or synthetic) end-to-end pipeline run;
      diff outputs against pre-refactor to confirm bit-stability where the
      contract requires it (StarDist determinism note in requirements).

### Steps — `image_table/controller.py`

- [ ] **P4.7** Split into focused modules under
      `well_viewer/features/image_table/`:
      - `image_compose.py` — LUT/colormap tinting + RGB composition.
      - `grid_layout.py` — grid building / cell distribution.
      - `export.py` — CSV writing / cell snapshot serialization.
      - `controller.py` — retains event handling + orchestration only.
      Preserve public function names (re-export) to avoid touching callers.
- [ ] **P4.8** Add an `image_table` line-budget guardrail to
      `tests/test_guardrails.py` (same mechanism as `runtime_app.py`) so the
      controller can't silently re-bloat.
- [ ] **P4.9** Verify with `test_image_table_layout.py`,
      `test_image_table_ui.py`, `test_image_table_*`; suite green.

**Acceptance criteria:** Both files materially smaller and cohesive; all tests
green; remote upload manifest covers new pipeline modules; end-to-end output
unchanged; new size guardrail in place.

**Notes / evidence:** _(new module list, before/after LOC, output-diff result,
commit SHAs)_

---

# P5 — Raise core-pipeline coverage

**Goal:** Lift `process_microscopy.py` from ~40–50% toward solid coverage,
especially an **end-to-end** pipeline test, now tractable after P4's split.

**Why:** The core pipeline is the largest under-tested surface; a silent
regression here is the costliest. Currently only specific image-processing
functions are tested, not orchestration.

**Status:** `[ ]` TODO

### Steps

- [ ] **P5.1** Build a synthetic-image fixture mirroring
      `tests/fixtures/mini_dataset.py`'s determinism philosophy: generate a
      tiny set of input TIFFs (a couple of wells × timepoints × channels) with
      *known* synthetic nuclei so segmentation/quantification outputs are
      predictable enough to assert on (counts, rough intensities, column
      presence). Keep it runnable without GPU.
- [ ] **P5.2** Add unit tests for the modules extracted in P4
      (`corrections`, `segmentation` dispatch, `io_outputs`) — these are now
      pure/injectable and easy to cover.
- [ ] **P5.3** Add one **end-to-end** test: run the pipeline (or its non-ML
      path / a stubbed segmenter) over the synthetic fixture into a temp dir,
      then assert the produced `*.csv`, `pipeline_info.json`, and per-well
      outputs match expectations. Gate the StarDist/TF path behind the `heavy`
      marker from P1.4 so default CI stays fast; provide a stub-segmenter path
      that runs everywhere.
- [ ] **P5.4** Add a coverage step to CI (`pytest --cov=process_microscopy`)
      reporting (not necessarily failing) the number, so the trend is visible.
      Optionally set a floor once a baseline is known.
- [ ] **P5.5** Record before/after coverage % in the notes.

**Acceptance criteria:** Deterministic synthetic fixture exists; extracted
modules unit-tested; at least one end-to-end pipeline test runs in default CI
(stub path) with the ML path behind `heavy`; coverage trend visible.

**Notes / evidence:** _(coverage before/after, fixture path, commit SHAs)_

---

## Status log

> Append a dated line every time a status box changes. Newest at top.

- 2026-06-10 **P1 DONE** — ruff config (`pyproject.toml`) + CI workflow +
  print→logger + dead-var cleanup. `ruff check .` clean; targeted test suites
  green; full suite shows no failures. Approach note: enforced a high-signal
  ruff subset rather than the (never-clean) broad set; no ML `heavy` marker
  needed (no test requires the ML stack).
- 2026-06-10 Plan created; all tasks (P1–P5) TODO. — initial commit of FABLE_PLAN.md

**Last updated:** 2026-06-10 — P1 complete; P2 next.
