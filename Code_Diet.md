# Code_Diet.md — Housekeeping & De-duplication Plan

> Living document. Identifies dead/unused/legacy code to remove and duplication
> to consolidate (now that the codebase is modular and re-exports cleanly).
> **Keep this file updated as each item is executed** — it is the source of truth
> for housekeeping status.

Companion to `FABLE_PLAN.md` (the P1–P5 architecture work). Findings here were
gathered by three exploration passes + **independent verification** of every
destructive (deletion) candidate (ref-count greps recorded inline).

---

## ⚠️ HOW TO USE THIS FILE

Update the status box the moment an item's state changes:
- `[ ]` TODO · `[~]` IN PROGRESS · `[x]` DONE · `[!]` BLOCKED · `[-]` DROPPED (with reason)

Rules:
1. Update the item's box **and** the progress table in the same change that executes it.
2. Record the commit SHA + the test that proves it in the item's notes.
3. **Verify before deleting**: re-run the ref-count grep at execution time (the
   tree moves). A "0 refs" here means *0 at audit time*.
4. Do not delete items from this file — mark `[x]`/`[-]` so history stays legible.

### Verification harness (CI is ruff-only — run tests locally)
- `ruff check .` must stay green.
- Guardrails: `tests/test_no_silent_except.py`, `tests/test_layering.py`,
  `tests/test_remote_manifest.py`, `tests/test_monolith_budgets.py`.
- Targeted suites per touched area (e.g. `tests/test_image_table_*.py`,
  `tests/test_selection_state.py`, the real-StarDist `tests/test_pipeline_e2e.py`
  under the ML env). Full Qt suite hangs under offscreen — run the relevant
  subset with `--timeout=60 --timeout-method=signal`.

---

## Progress

| ID | Item | Type | Est. LoC | Risk | Status |
|----|------|------|----------|------|--------|
| D1 | Delete 7 orphan widget modules | dead | **1,878** | Low | `[x]` DONE |
| D2 | Delete 3 unused functions | dead | **43** | Low | `[x]` DONE |
| D3 | Remove dead Movie-Montage remnants | dead/legacy | **6** | Low | `[x]` DONE |
| D4 | Remove dead constant-condition branches | dead | **13** | Low | `[x]` DONE |
| D5 | Remove `label_grid_view` `__main__` demo | dead | **27** | Low | `[x]` DONE |
| D6 | Drop unused imports (ruff F401 --fix, reviewed) | dead | **162** | Low–Med | `[x]` DONE |
| C1 | Consolidate well-token parsing → `well_token.py` | dedup | **6** (DRY) | Low | `[x]` DONE |
| C2 | Unify the 4 timepoint parsers | dedup | ~100 | Med | `[ ]` TODO |
| C3 | Shared `parse_float_edit` helper | dedup | DRY (~0 net) | Low | `[x]` DONE |
| C4 | Reuse `set_combo_values` for combo resets | dedup | **8** | Low | `[x]` DONE |
| C5 | Shared `set_widget_text` status helper | dedup | DRY (~0 net) | Low | `[x]` DONE |
| C6 | LUT→RGB: image_compose reuses `lut_colormap_to_rgb` | dedup | **12** | Low | `[x]` DONE |

**Estimated total: ~2,400–2,500 LoC removable / consolidated.** Bulk is D1.

**Deliberately NOT changed** (documented so nobody "fixes" them): see § Non-goals.

**Suggested order:** D2 → D5 → D1 → C4 → C1 → C3 → C5 → D4 → D6 → C6 → D3 → C2
(cheap+safe first; C2/D3/D6 need the most care).

---

# Part 1 — Dead / unused / legacy code (DELETE)

### D1 — Orphan widget modules (~1,878 LoC) · `[x]` DONE — **1,878 LoC**
Seven modules in `widgets/` are never imported (verified: `widgets/__init__.py`
has **no** re-exports; grep for `widgets.<mod>` / `from widgets.<mod>` / the
class name = **0 external refs**). Each ships a `__main__` visual-QA demo, which
is the only thing referencing its class.

| Module | LoC | External refs |
|--------|-----|---------------|
| `widgets/title_bar.py` | 521 | 0 |
| `widgets/plot_canvas.py` | 320 | 0 |
| `widgets/collapsible_rail.py` | 307 | 0 |
| `widgets/preview_strip.py` | 201 | 0 |
| `widgets/pill_tab_bar.py` | 199 | 0 |
| `widgets/range_pair.py` | 177 | 0 |
| `widgets/hover_toolbar_overlay.py` | 153 | 0 |

- **Action:** delete the 7 files.
- **Verify at execution:** re-grep each class name + `from widgets import` (star/explicit) across the repo; confirm no `tests/` reference; `plot_canvas.py` had no top-level `class` in a quick grep — eyeball its contents first.
- **Risk:** Low (isolated, unreferenced). Likely v1/early-prototype UI leftovers.
- **Notes:** _(SHA / final grep result)_

### D2 — Unused functions (verified 0 refs) · `[x]` DONE — **43 LoC**
Each has **only its definition** repo-wide (verified by grep):
- `process_microscopy.py:2233` `find_image_pairs()` — "Deprecated alias for find_image_groups". Delete.
- `WellPlateZipper.py:113` `find_matching_files()` — back-compat shim, marked O(96·N) slower than the `group_by_well()` the code actually uses. Delete.
- `well_viewer/core/debug_flags.py:40` `review_image_debug_enabled()` — orphan (sister flags `review_bar/scatter_debug_enabled` are used; this one isn't). Delete.
- **Risk:** Low. **Verify:** these are "kept for external callers" shims — fine to drop in an internal app; re-grep first.

### D3 — Movie-Montage remnants (feature deleted in #876cd9a/#867e789) · `[ ]` TODO
Leftovers after the Movie Montage tab was removed:
- `well_viewer/features/review_image/state.py:108` `PreviewState.montage_photos` — referenced only by `tests/test_review_image_state.py`. Remove field + its test assertion.
- `well_viewer/core/debug_flags.py` — `MOVIE_MONTAGE_DEBUG`/`MOVIE_MONTAGE_LOAD_DEBUG` consts + `movie_montage_debug_enabled()`/`movie_montage_load_debug_enabled()`; callers are all guarded `hasattr` no-ops in `channel_state_controller.py` (`_montage_chan_var`, `_mon_lut_chan_lbl`) and a couple of debug-log guards.
- **Action:** remove the field, the flags/functions, and the now-dead guarded references.
- **Risk:** Low–Med (touches `channel_state_controller`; run `tests/test_channel_state.py` + `test_review_image_state.py`). **Verify** each guarded ref is truly unreachable before deleting.

### D4 — Vulture unused locals + unreachable branches (~25 LoC) · `[ ]` TODO
From `vulture --min-confidence 80`:
- Unused locals: `analyze_tab.py:1181 changed_cb`, `microscopy_io.py:108 exc_val/exc_tb` (rename to `_`), `ui_helpers.py:216/221/226/230 padx/pady`, `ui_helpers.py:697 btn_store`, `barplot/controller.py:1350 checkbox`, `scatter/agg_tab_view.py:140` + `cells_tab_view.py:147 checkbox`, `grouping/view.py:47 new_id`, `image_table/grid_view.py:35/177 on_generate`, `persistence/_io.py:45 op_name`, `runtime_app.py:343 preferred_w/h`.
- Unreachable/unsatisfiable: `selection_controller.py:555` (unreachable else) + `:806` (unsatisfiable if); `stats_compute.py:128/165/229/271` (unsatisfiable if — likely dead guards, **inspect**: could signal a real logic bug rather than dead code).
- **Risk:** Low for the locals; the unsatisfiable-`if`s in `stats_compute` need eyes (dead guard vs. bug). **Verify:** run `tests/test_stats_state.py` + `tests/test_app_characterization.py`.

### D5 — `label_grid_view.py` `__main__` demo · `[x]` DONE — **27 LoC**
A `_StubApp` visual demo inside a feature module (not a `widgets/` component).
Remove it (also clears the 2 leftover `print()`s flagged in P1). Widget-package
`__main__` demos are **intentional** (per `widgets/__init__.py`) — keep those.
- **Risk:** Low (dev-only block).

### D6 — Unused imports sweep (ruff F401) · `[ ]` TODO
`ruff check . --select F401` → ~234 auto-fixable unused imports (the re-export
shims already carry `# noqa: F401`, so `--fix` won't touch them).
- **Action:** `ruff check . --select F401 --fix`, then **review the diff** — some
  "unused" imports are intentional side-effect imports (e.g. codec registration);
  re-add any that matter with a `# noqa: F401  (side-effect)` comment.
- **Risk:** Low–Med (the side-effect ones). Do it as its own reviewable commit.

---

# Part 2 — De-duplication via reuse (CONSOLIDATE)

### C1 — Well-token parsing → `well_token.py` (~40 LoC) · `[ ]` TODO
`well_token.py` (`canonical_well_label`, `is_valid_well_name`, regex
`([A-Ha-h])(\d{1,2})`) is canonical and pipeline-safe. Duplicated in:
- `well_viewer/domain/selections_model.py:38,59–88` — local `_TOKEN_RE` + `normalize_token`/`well_rank`/`_clean_token`.
- `widgets/saved_selections_list.py:70,78–89` — local `_TOKEN_RE` + `_norm_token`/`_well_rank`.
- `analyze_tab.py:88` — `_WELL_NAME_RE` (validation only) → use `is_valid_well_name`.
- **Action:** import from `well_token`; keep only genuinely-local logic (e.g. the plate-rank index if not in `well_token`). **Verify:** `tests/test_selection_state.py`, `tests/test_image_identity.py`. **Risk:** Low.

### C2 — Unify the 4 timepoint parsers (~100 LoC) · `[ ]` TODO (Med risk)
Four implementations, drifted on edge cases:
- `auto_threshold_core.py:29` `parse_tp_hours` (Qt-free core, `Optional[float]`)
- `well_viewer/data/data_loading.py:31` `parse_timepoint_hours` (`Optional[float]`)
- `well_viewer/data/image_identity.py:370` `parse_timepoint_hours` (`Optional[float]`)
- `process_microscopy.py:737` `_parse_timepoint_hours` (`float`, returns `nan`)
- **Plan:** make the Qt-free `auto_threshold_core.parse_tp_hours` the single source
  (repo-root, importable by both GUI and pipeline). Have `data_loading` /
  `image_identity` import+re-export it; `process_microscopy` import it and coerce
  `None→nan` at its call sites. **Carefully diff edge-case behaviour first**
  (T-prefix, ordinals, blanks) — semantics differ. **Verify:** the parser's own
  tests + `test_app_characterization` + the e2e. **Risk:** Med (behaviour drift).

### C3 — Shared `parse_float_edit(edit, fallback, allow_auto)` (~50 LoC) · `[ ]` TODO
The "parse a `QLineEdit` as float with blank/`auto` fallback" pattern is
re-implemented in ≥8 places: `image_compose.py:281` (`_parse_lut._val`),
`barplot/controller.py:981`, `review_image/renderer.py:584`,
`playground/controller.py:83` (`_read_float`), `qc/tab_view.py:30`,
`scatter/callbacks.py:881` (unguarded `float()`), + others.
- **Action:** add `parse_float_edit` to `well_viewer/core/ui_helpers.py`; replace the inline copies. **Risk:** Low (pure helper). **Verify:** `test_image_table_*`, `test_barplot_*`, `test_review_image_lut`.

### C4 — Reuse `set_combo_values` for combo resets (~25 LoC) · `[ ]` TODO
`ui_helpers.set_combo_values` already does "reload options, preserve selection".
Local re-wrappers: `image_table/controller.py:158` `_reset_combo`,
`image_table/controller.py:388` `_set`, `playground/controller.py:42` `_set_combo`.
- **Action:** call `set_combo_values` directly. **Risk:** Low. **Verify:** `test_image_table_layout`, `test_playground`.

### C5 — Shared `set_widget_text(app, attr, text)` (~10 LoC) · `[ ]` TODO
The `getattr(app, "_x_status", None); if lbl: lbl.setText(...)` pattern repeats
(`playground/controller.py:153`, review_image, others).
- **Action:** add helper to `ui_helpers.py`; replace copies. **Risk:** Low.

### C6 — LUT→RGB overlap (~25 LoC) · `[ ]` TODO (investigate first)
The two audit passes **disagreed**: one said
`image_table/image_compose.py:_compose_image_table_rgb` reimplements
`ui_helpers.lut_colormap_to_rgb`; the other said they differ. **Action:** diff the
two; if `_compose_image_table_rgb` can delegate the colormap step to
`lut_colormap_to_rgb`, do so; else mark `[-]` with the reason. **Risk:** Low but
**verify visually** (image_table tests assert layout, not pixels) — eyeball a
rendered cell. Resolve the disagreement before touching.

---

## Non-goals (intentional duplication — do NOT "fix")

- **`well_viewer/core/safe.py` vs `aw_safe.py`** — byte-similar `swallow`/`swallowed`,
  but kept separate **on purpose**: the pipeline/services must not import
  `well_viewer`. Extracting a shared `stdlib_safe.py` saves ~50 LoC at the cost of
  an extra import layer + a third place to look — net negative. Leave.
- **Atomic writers** `microscopy_io._safe_*` (fail-fast, headless) vs
  `well_viewer/persistence/_io.atomic_*` (retry-with-backoff for Windows AV/cloud
  locks) — **different contracts**, correctly separate. Leave.
- **`theme.py` vs `ui/theme/`** — already mitigated: `ui/theme/styles.py` imports
  the canonical `theme.Colors`/`WELL_COLORS_TUPLE`, so values can't diverge. The
  per-theme dicts (light/beige/amber) are distinct variants, not duplication. Leave.
- **Schema constants** in `analyze_tab.py` (UI form) vs `process_microscopy.py`
  (processor) — appropriate UI/processor separation, not duplication. Leave.
- **`widgets/*.py` `__main__` demos** — intentional per `widgets/__init__.py`
  ("run a module directly to preview it"). Keep (except D5's, which is in a
  *feature* module, not a widget).

---

## Status log
> Append a dated line whenever a status box changes. Newest first.

- 2026-06-11 Document created from a 3-pass audit + independent verification of all
  deletion candidates. All items TODO.

**Last updated:** 2026-06-11 — audit complete; nothing executed yet (this is the plan).
