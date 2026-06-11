# Fable_Tasks2.md — Structural-Debt Remediation Plan

> **Living working document.** Turns the post-work re-evaluation's six findings
> into a sequenced, individually-checkable, executable plan. Keep it updated as
> each task lands — it's the source of truth for this remediation round.
> Companion to `FABLE_PLAN.md` (P1–P5) and `Code_Diet.md` (housekeeping).

This plan addresses the structural debt the independent re-evaluation surfaced
*after* P1–P5 + Code_Diet. Those rounds fixed the targeted issues (domain/data
layering, monolith decomposition of `process_microscopy`/`image_table`, error
handling, dead code) and added 7 guardrails — but two fresh-eyes audits, **both
verified against the tree**, found the dominant remaining debt:

1. **`persistence/ → features/` upward import** (module-level, *unguarded* — the
   `test_layering` guard only covers `domain`/`data`).
2. **47% of `well_viewer` imports are function-local** cycle-workarounds
   (inter-feature cycles: export↔scatter, crop↔review_image, distribution→scatter).
3. **No metric registry** — adding a metric touches 6–8 files (the failed "acid test").
4. **Large orchestrators untouched** — `process_image_group()` (~360 lines),
   `analyze_tab.py` (1,887), and the `barplot`/`scatter`/`review_image`
   controllers (~1.3–1.4k) still mix data + render + state.
5. **No test job in CI** (Qt-offscreen tests hang the runner).
6. **Minor durability** — a few specific-type silent passes + shallow real-app
   integration testing.

Reuses the patterns proven in P1–P5: re-export shims, `*_core` extraction, the
**AST ratchet-guard** pattern, the `swallow()`/`swallowed()` helpers, byte-identical
output verification, and throwaway AST codemods for mechanical migrations.

---

## ⚠️ HOW TO USE THIS FILE
Update the status box the moment an item's state changes:
`[ ]` TODO · `[~]` IN PROGRESS · `[x]` DONE · `[!]` BLOCKED · `[-]` DROPPED (reason).
Update the item box **and** the progress table in the same change; record the
commit SHA + the test that proves it in the task's notes.

### Verification harness (CI is ruff-only — run tests locally until T5 lands)
- `ruff check .` stays green.
- Guardrails: `test_architecture_contracts.py`, `test_layering.py`,
  `test_no_silent_except.py`, `test_monolith_budgets.py`, `test_remote_manifest.py`,
  `test_guardrails.py`.
- The full Qt suite hangs under offscreen — run the touched subset with
  `--timeout=60 --timeout-method=signal`; the slow image_table (~106s) /
  playground (~4min) / real-StarDist `test_pipeline_e2e.py` runs separately.

---

## Progress

| ID | Task | Leverage | Risk | Status |
|----|------|----------|------|--------|
| T5 | Restore a bounded CI test job | Med | Low | `[ ]` TODO |
| T1 | `persistence/` → data-only; close the layering-guard gap | High | Med | `[ ]` TODO |
| T2 | Break inter-feature cycles; add a deferred-import ratchet | High | Med | `[ ]` TODO |
| T3 | Central metric registry (`domain/metric_registry.py`) | High | Med | `[ ]` TODO |
| T4 | Decompose remaining orchestrators (+budget ratchets) | Med | Med–High | `[ ]` TODO |
| T6 | Log specific-type silent passes; add a directed integration test | Low | Low | `[ ]` TODO |

**Order:** **T5 first** (cheap; gets the suite gating PRs again so T1–T4 are
guarded), then **T1 → T2 → T3** (the acid-test triad), then **T4** (largest, do
last and incrementally), then **T6**. T1–T3 are independent and can land as
separate PRs.

---

## T5 — Restore a bounded CI test job *(do first: cheap, makes everything else safer)*

**Why first:** the suite currently only runs locally, so T1–T4 would land unguarded.

**Steps**
1. `tests/pytest.ini` already registers the `slow` marker and `test_pipeline_e2e.py`
   is marked. Add `pytestmark = pytest.mark.slow` to `tests/test_playground.py`
   (~4 min), and tag the handful of app-construction Qt tests that hang under
   offscreen — identify via a one-off `--timeout=30 --timeout-method=signal` run;
   they block at the C level so `signal` can't interrupt, so **mark them `slow`,
   don't try to fix them**.
2. `.github/workflows/ci.yml`: add a `test` job (Python 3.11 + the Qt system libs
   `libegl1 libgl1 libxkbcommon0 libdbus-1-3` + `tests/requirements-dev.txt`)
   running `pytest -m "not slow" --timeout=120 --timeout-method=signal` with a
   job-level `timeout-minutes: 15`. Keep coverage report-only.
3. Verify a PR shows green `ruff` + `test` checks.
- **Reuse:** the install steps from the original `ci.yml` (in git history) + the
  light-stack rationale documented in its header.
- **Risk:** Low. **Acceptance:** CI runs the fast suite and is green; `slow` tests
  remain a documented local/manual run.
- **Notes:** _(SHA / CI run URL)_

## T1 — `persistence/` becomes data-only; close the guard gap

**Problem (verified):** `persistence/qc_params.py:22-23`, `sample_definitions.py:17`,
`view_state.py:21` import *feature* code at module level
(`seed_qc_from_pipeline_info`, `QCParams`, `label_grid_refresh`,
`image_table_apply_dimensions`) to trigger UI refreshes after load — an upward
layering inversion the persistence layer shouldn't have.

**Key constraint:** the EventBus is **capped at 5 signals** by
`test_architecture_contracts.py::test_event_bus_signal_catalogue_is_capped`, so
the fix must **not** add a 6th signal. Use the existing post-load seam:
`well_viewer/persistence/registry.py` (`PersistenceRegistry`) +
`well_viewer/core/load_controller.py`.

**Steps**
1. Make each persistence module **return parsed data** (dicts / dataclasses) and
   stop calling feature functions. Move the "after load, refresh the feature"
   calls *up* into the owning feature's controller, or into `load_controller`'s
   post-load sequence (which already orchestrates load order).
2. `qc_params.py`: `QCParams` is a `@dataclass` in `features/qc/params.py` —
   relocate it to `domain/` (or `persistence/`) so persistence doesn't import a
   feature; `features/qc` re-exports it. `seed_qc_from_pipeline_info` is feature
   logic — call it from the qc controller's load hook, not from persistence.
3. Extend the layering guard: add `"persistence"` to `GUARDED_LAYERS` in
   `tests/test_layering.py:18`. It will fail until the feature imports are gone —
   that's the point; it then locks the fix permanently.
- **Reuse:** `PersistenceRegistry`, `load_controller.py` ordering, the
  re-export-for-compat pattern (P3/P4).
- **Verify:** `test_layering.py` (now incl. persistence) + `test_qc_*`,
  `test_sample_def_autosave.py`, `test_view_state_metric_combo_sync.py`,
  `test_qc_params_persistence.py`. **Risk:** Med (touches load order — verify the
  app still loads a dataset and the affected tabs refresh).
- **Notes:** _(SHA)_

## T2 — Break the inter-feature cycles; ratchet deferred imports

**Problem (verified):** 323/687 (47%) `from well_viewer` imports are
function-local cycle-workarounds. Concrete cycles:
- `scatter/controller.py:23` → `export/style.apply_export_style_to_current`
  **and** `export/service.py:526,624` → `scatter.controller.collect_scatter_data`.
- `crop/controller.py` ↔ `review_image/{controller,renderer}` (deferred both ways).
- `distribution/controller` → `scatter.get_all_timepoints`.

**Steps**
1. **Kill scatter→export:** move `apply_export_style_to_current`
   (`export/style.py:492`) into the neutral **`well_viewer/core/plot_style.py`**
   (already exists); `export/style` re-exports it; `scatter` imports it from
   `core` (downward). One edge gone.
2. **Kill export→scatter:** extract the scatter *data collectors*
   (`collect_scatter_data`, `get_all_timepoints` @ `scatter/controller.py:28,41`)
   into a new `scatter/collect.py` (pure data, no plotting); both scatter and
   `export/service` + `distribution/controller` import them downward.
3. **crop ↔ review_image:** extract the shared mask/refresh entry point into a
   small neutral module, or invert so one direction is a downward import; if a
   genuine bidirectional need remains, keep **one** documented deferred import.
4. **Promote** the now-acyclic deferred imports to module level.
5. **Add a ratchet guard** (mirroring `test_monolith_budgets`): count
   function-local `from well_viewer.features` imports and assert ≤ a budget that
   only moves **down** (seed at the post-fix count). Prevents the 47% regrowing.
- **Reuse:** `core/plot_style.py`, the C1/C3 "move + re-export" pattern, the
  ratchet-guard mechanism; verify acyclicity with a clean
  `python -c "import well_viewer.features.export.service, ...scatter.controller"`
  cycle check (as in P3).
- **Verify:** `test_scatter_smfish.py`, `test_export_formats.py`, the crop /
  review_image suites, and the new ratchet. **Risk:** Med (import order).
- **Notes:** _(SHA)_

## T3 — Central metric registry (`domain/metric_registry.py`)

**Problem:** metric lists/labels are spread across `data/metric_labels.py`,
`core/channel_state_controller.py::populate_metric_combo` (:566), and per-controller
collection logic — so "add a metric" is a 6–8-file change (the failed acid test).

**Steps**
1. Create `well_viewer/domain/metric_registry.py`: one declarative table mapping
   metric key → label, type (intensity / count / shape / ratio), and
   **gating-exemption** (counts/shape bypass the fluor gate — that rule is
   currently duplicated inline in scatter + barplot).
2. Migrate `metric_labels.py`, `populate_metric_combo`, the per-controller metric
   enumeration, and the gate-exemption checks to read the registry.
3. **Acceptance test:** add a throwaway metric to the registry inside a test and
   assert it surfaces in the scatter/barplot/image_table combos — proving the
   single-point-of-change (the acid test, now passing).
- **Reuse:** the `shape_features_core.py` "single-source catalog" precedent;
  `ui_helpers.set_combo_values_sectioned` for combo wiring.
- **Verify:** `test_barplot_collect.py`, `test_lineplot_collect.py`,
  `test_scatter_smfish.py`, `test_view_state_metric_combo_sync.py`. **Risk:** Med.
- **Notes:** _(SHA)_

## T4 — Decompose the remaining orchestrators *(largest; do incrementally, one PR each)*

Mirror the P4 pattern (extract cohesive sub-modules, re-export, ratchet the budget):
1. **`process_image_group()`** (`process_microscopy.py:1676`, ~360 lines mixing
   flat-field → background → segmentation → quantify → I/O): extract a
   `correct → segment → quantify → write` step sequence (the leaf helpers already
   live in `microscopy_background/stardist/cellpose/io`); the function becomes
   thin orchestration. Re-verify with the real-StarDist `test_pipeline_e2e.py`
   (byte-compare outputs) + the remote-manifest guard.
2. **`barplot`/`scatter`/`review_image` controllers** (1,415 / 1,264 / 1,389):
   split each into `collect.py` (data) + `renderer.py` (matplotlib) + keep
   `controller.py` for state/orchestration — the exact shape `image_table` already
   has. (T2 already starts scatter's `collect.py`.)
3. **`analyze_tab.py`** (1,887 god-class): carve form-build / validation / run /
   log-poll into helpers or a small view-model.
- **Each step:** add/lower the file in `tests/test_monolith_budgets.py`
  (currently `process_microscopy ≤ 4470`, `image_table ≤ 1494`).
- **Reuse:** the P4 extraction script + re-export + budget-ratchet playbook.
- **Risk:** Med–High (the big controllers run under Qt; lean on the feature
  suites + the e2e). **Do last, incrementally — never a big-bang.**
- **Notes:** _(SHA per sub-step)_

## T6 — Durability cleanups
1. Route the ~4 specific-type silent passes through `swallowed(...)` for
   diagnosability: `persistence/_io.py` (fsync + cleanup-unlink),
   `features/grouping/cell_gating_tab_view.py` (float parse),
   `services/pipeline_runner.py` (cleanup). These are *specific* excepts (not
   broad), so they don't violate `test_no_silent_except` — this is polish.
2. Add **one directed real-app integration test** (the gap both auditors flagged):
   load the `mini_dataset`, apply control-normalised fold-change, read bar values,
   assert error bars track the control mean — covering the tab-builder / app-loop
   path that smoke + characterization tests only shallowly touch.
- **Notes:** _(SHA)_

---

## Status log
> Append a dated line whenever a status box changes. Newest first.

- 2026-06-11 Plan created from the post-work re-evaluation (both findings verified
  against the tree). All tasks TODO.

**Last updated:** 2026-06-11 — plan authored; nothing executed yet.
