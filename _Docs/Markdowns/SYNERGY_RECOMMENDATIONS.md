# Synergy recommendations: leveraging Split View + cross-tab data

## Context

After reading `ARCHITECTURE.md`, the question raised was whether there are ways to
**better leverage the "two-tab view"** (Split View, §5.10) or to make **pairs of
tabs / pieces of data synergize** better across the app — explicitly *not* limited
to the already-shipped `ui_synergy.md` items (S1–S5). Two steers shaped this doc:
ideas may be novel, and **the well picker has different constraints in different
tabs** — which matters a lot once two tabs share one sidebar.

This is a *recommendations* doc. It records what is wired today (with code refs),
then proposes new synergies, each constrained to the ARCHITECTURE.md guardrails
(5-signal EventBus cap, no new loose `self._*`, typed state under
`core/`/`features/*/state.py`, Split View stays one-line removable).

---

## What exists today (verified against the code)

**Split View** (`well_viewer/core/split_view.py`): two *different* top-level tabs
side-by-side, both live. The right-pane chooser is an **uncurated** combo of every
other tab (`split_view.py:265-279`). Both panes redraw on
selection/channel/plot-option/dataset change via the `visible_centre_tabs`
provider hook (`tab_controller.py:351-369`). Sidebar follows the **focused** pane
(`split_view.py:104-105`, `tab_controller.py:337-340`). Same-tab×2 is out of scope
(singleton figures).

**No linked brushing.** A sub-selection made *inside* a pane is not pushed to the
other pane as a distinct highlight. Only **well-level** selection crosses, because
it mutates shared `SelectionState.selected_wells` (heatmap click → `on_heatmap_click`
→ `_set_selected_wells`, `heatmap/controller.py:629-668`).

**Cross-tab nav already wired:** Cell Browser→Segmentation and Review
CSV→Segmentation jumps set nucleus/well/fov/tp (`cell_browser/controller.py:243-287`,
`review_image/controller.py:280-329`); Scatter gate→Cell Gating `Included` mask
(`scatter/gating.py:343-370` `compute_gate_inclusion`); scatter point click→cell-image
slide-out (`scatter/callbacks.py:1107-1156`).

**Shared context (S2):** Channel/Property/Threshold shared across all plotting tabs
(`core/centre_view.py:249-449`, `ChannelState`).

**Fragmented / unlinked:**
- **Timepoint is per-tab** — Bar/Scatter multi-select dicts (`barplot/state.py:22-33`),
  Distribution combo (`distribution/tab_view.py:53-57`), Heat Map slider
  (`heatmap/tab_view.py:166-181`), Line = all. The one context axis the S2 bar does
  *not* unify.
- Scatter has **no reverse well-selection** (Heat Map does).
- Stats computes group significance (cached in `StatsState`) but never draws it back
  onto Bar/Line.
- smFISH spots never overlay on the image viewers; the tab collapses to one well on
  entry (`tab_controller.py:110-121`) and ignores the plate selection.
- Batch Export's `use_sidebar_groups` link exists but is never `True` in production
  (`batch_export/tab_view.py:97-106`).

**S3 blocker:** heatmap value→RGB is not a pure helper yet (value→(vmin,vmax) *is*:
`_cell_value` / `_compute_global_range` / `_resolve_color_scale`); colour application
is inline in `redraw_heatmap`.

**The well picker is polymorphic per tab.** `WellPlateSelector` exposes
`set_single_selection_mode` (smFISH + Review CSV force **single well**),
`setSelectionMode("passive"|"select")` (passive in saved-selection / rep mode),
`set_row_column_selectable(False)` (smFISH suppresses row/col headers), and
`set_enabled_wells(ids)` to constrain *which* wells are selectable
(`widgets/well_plate_selector.py:238-349`, `selection_controller.py:572-586`).
Because the **Split View sidebar follows the focused pane**, these constraints flip
on focus: focusing a single-well tab (smFISH / Review CSV) next to a multi-well tab
(Heat Map / plots) would **collapse the shared multi-well selection to one**,
corrupting the other pane. The constraints are also unused *information* about what
each tab needs.

---

## Recommendations

### Group 1 — Make Split View pull its weight (linked brushing)

**N1. Linked brushing: Scatter ⇄ Cell Browser (headline idea).** When these two are
split, drawing/selecting a scatter gate sources the Cell Browser gallery from exactly
the gated cells, so the panes become one drill-down: shape the population on the left,
see the actual cells on the right.
- *Reuse:* `compute_gate_inclusion` already returns a per-row mask
  (`scatter/gating.py:343-370`); Cell Browser already has a pluggable source seam
  (`collect.cell_browser_source_wells`) — add a "source = active gate" mode.
- *Compliance:* no new bus signal (reuse `plot_options_changed`/`redraw_requested`);
  state on Cell Browser's feature state, not a loose attr (G3/G5).

**N2. Linked brushing: Heat Map / Scatter ⇄ Segmentation-Image.** Click a hot well
(heatmap) or a point (scatter) → the image pane jumps to that well/cell.
- *Reuse:* the Cell Browser→Segmentation jump (`cell_browser/controller.py:243-287`)
  is the exact pattern; only the click *source* differs. Scatter→cell-viewer already
  proves the wiring (`scatter/callbacks.py`).

**N3. Curated "Compare with…" presets.** Replace the alphabetical right-pane combo
with a smart default + a "Recommended" group first: Scatter→Cell Browser,
Heat Map→Segmentation, Bar/Line→Statistics, Review CSV→Segmentation. Cheap, localized
to `split_view.py:265-279`, makes the feature discoverable.

**N4. Persist split layout** in `view_state` so reopening a dataset restores the
comparison (today split auto-exits on load, `load_controller.load_directory`).

### Group 2 — Well-picker constraints

**N11. Reconcile picker constraints across Split View panes (latent-bug + synergy).**
Today the constraint set (single-vs-multi, passive/rep, row-col, enabled-wells)
follows only the focused pane, so a single-well pane focused beside a multi-well pane
silently collapses the shared selection. Define a reconciliation rule over the **two
visible panes** — take the **least-restrictive** constraint (multi wins over single,
row/col stays selectable if either pane allows it), or pin the broad constraint and
show an inline note in the narrow pane ("smFISH shows one well; selection is set by
the other pane"). Fixes a real corruption and makes the picker a shared surface that
respects both panes.
- *Touches:* constraint application in `selection_controller.py:572-586` (currently
  smFISH-specific), focus→sidebar in `split_view.py`/`tab_controller.py`. Use the
  existing `visible_centre_tabs` hook to know both panes.

**N12. Constraint-as-data: data-aware enabled wells.** Use `set_enabled_wells` to dim
wells with no data for the current (channel, property, timepoint) context, so the
picker itself communicates coverage consistently across tabs — instead of each tab
silently rendering empties. Composes with N5/N6 below: enabled + tinted wells = a
self-documenting plate that means the same thing in every tab.

### Group 3 — Unify the fragmented axis (timepoint) and unblock S3

**N5. Shared timepoint context (high-leverage).** Promote an "active timepoint" into
the shared context (a small `TimeState` under `core/`, or extend the S2 bar), so the
Heat Map slider and Distribution combo read one focus timepoint. Mirror the S4
scope-note pattern: share a *default* where tab models genuinely differ — multi-select
tabs (Bar/Scatter) keep their dicts but seed from the shared value; Line stays all-TP.
This is the one obvious coherence gap in the otherwise-unified context bar.
- *Compliance:* typed state under `core/` (G3/G4), reuse `plot_options_changed`.

**N6. S3 plate overlay, unblocked.** S3 (live-plate heatmap overlay) was parked on a
single question — "which timepoint to colour by." N5 answers it (the shared timepoint).
Then: extract the heatmap value→QColor mapping into a pure helper (the value→(vmin,vmax)
half already exists), add a sidebar "data overlay" toggle that tints the plate by the
active (channel, property, shared-timepoint) via
`WellPlateSelector.set_well_colors` + `set_colored_wells_lit` (API already present).

### Group 4 — Close cross-tab data loops

**N7. Significance brackets on Bar/Line from Statistics.** Stats already computes group
tests cached in `StatsState`; draw significance brackets/asterisks back onto the
bar/line plots from that cache — data computed in one tab powering another.

**N8. Scatter reverse well-selection.** Rubber-band a region of the per-cell scatter →
select those cells' wells into the sidebar (parity with Heat Map's `on_heatmap_click`).
Reuse `_set_selected_wells`. (Respect N11's constraints — only when the picker allows
multi-select.)

**N9. smFISH spot overlay in image viewers.** Surface detected smFISH spot tokens as an
overlay layer in Segmentation / Image Table so counts validate against the image; let
the smFISH tab honor the plate selection instead of collapsing to one well.

**N10. Batch Export linked-groups default.** Flip `use_sidebar_groups=True` (keep the
file-import fallback) so Batch Export consumes the unified S1 groups — completes the
group-unification story.

---

## Suggested sequencing (if/when implemented)

1. **N5 → N6** (shared timepoint, then S3 overlay) — unblocks parked work, foundational
   for N12.
2. **N11** (picker reconciliation) — fixes a latent Split View bug before adding more
   split-driven interaction.
3. **N1 / N2** (linked brushing) — the headline payoff, builds on stable selection.
4. **N3, N12** (curated presets, data-aware wells) — discoverability + polish.
5. **N7, N8, N9, N10** (data-loop closures) — independent, parallelizable.

## Highest-confidence leads

- **N1** — biggest "leverage the two-tab view" payoff, all the pieces already exist.
- **N5 → N6** — fixes the one fragmented context axis *and* unblocks parked S3.
- **N11** — corrects a real selection-collapse conflict in Split View.

## Guardrails any future implementation must hold (from ARCHITECTURE.md)

- EventBus stays at exactly 5 signals (reuse `selection_changed` /
  `plot_options_changed` / `redraw_requested`; status via `status_signal.py`).
- No new loose `self._*` on `WellViewerApp` (budget 71, ratchets down) — new state
  goes on a typed dataclass; behaviour is functions taking `app`/`ctx`.
- Split View stays one-line removable (extend via the `split_*_provider` hooks; the
  core must not import the feature).
- Per-item: `ruff` clean; `pytest tests/test_guardrails.py
  tests/test_architecture_contracts.py tests/test_split_view.py
  tests/test_redraw_fanout.py`; re-render via `tests/visual/render_views.py`; update
  `ARCHITECTURE.md` + `ui_synergy.md` for structural changes (G12).
