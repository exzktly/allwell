# Feature-Registry Migration Plan

**Goal:** make every Review-side tab-feature *non-destructively removable by
commenting out a single line* — the property §5.10 already gives Split View,
generalized to all features.

**Status:** plan / not yet implemented. Companion to `ARCHITECTURE.md`
(§5.2 page stack, §5.3 tab builders, §5.5 controllers, §5.7 persistence,
§5.9 event bus + DI context, §5.10 Split View one-line removability).

---

## 1. Why this isn't greenfield

The decoupling machinery already exists in four places; this migration
generalizes them into one feature registry rather than inventing a pattern:

| Existing precedent | File | What it proves |
|---|---|---|
| `PersistenceRegistry` | `persistence/registry.py` | Named callables registered in one `build_persistence_registry(app)`; domains addressed by name. |
| `register_default_scopes` | `core/scope_registry.py` | Descriptors (`FoldChangeScope`) carrying redraw callables; the domain layer holds the registry, a feature-aware module does the wiring. |
| `EventBus` + `wire_event_bus` | `core/event_bus.py`, `core/bus_wiring.py` | Coordinators subscribe to a capped signal catalogue at one composition root. |
| Split View provider hooks | `core/split_view.py`, §5.10 | A feature that is **fully absent until one `install_*` line runs**, with resolvers collapsing to default behaviour when uninstalled. |

## 2. Current coupling (the sites this PR rewires)

A feature today is wired into core in five hard-coded places:

1. **Construction** — `core/centre_view.py:726–753`, the `groups` list of
   `(title, builder)` tuples; each builder closure imports the feature's
   `build_*` entry point.
2. **Activation dispatch** — `core/tab_controller.py:8–16` (top-level feature
   imports) + the `on_tab_change` `if tab == "…"` ladder.
3. **Selection refresh** — `core/selection_controller.py:6–9` (imports),
   `_refresh_tab_view` ladder, and the `_PER_WELL_TABS` / smFISH single-well
   string checks.
4. **Channel-change fan-out** — `core/channel_state_controller.py`,
   `recalculate_threshold` / `set_active_channel`: `hasattr`-guarded lazy
   imports calling scatter / distribution / heatmap / stats / cell-gating
   refreshers. **The messiest site.**
5. **Dataset-load fan-out** — `core/load_controller.py:10–14` (imports) + the
   load refresh calls.

Plus the inverted dependency: **`core/app_state.py:21–26` imports feature
`State` classes** (`BarPlotState`, `StatsState`, `ImageTableState`,
`PlaygroundState`, `PreviewState`, `ReviewImageState`). This is why deleting a
feature package today breaks app startup, regardless of UI wiring.

Two registries also enumerate feature entries: `build_persistence_registry`
(`persistence/registry.py:68`) and `register_default_scopes`
(`core/scope_registry.py:15`).

## 3. The descriptor

New `core/feature_registry.py`:

```python
@dataclass(frozen=True)
class FeatureDescriptor:
    name: str                    # leaf tab name, e.g. "Scatter Plot"
    group: str                   # "Analysis" | "Images" | "Data"
    build: Callable              # build(ctx, frame) — tab body
    parent: str | None = None    # e.g. "Plotting" for the nested sub-stack
    # optional hooks — all default None/():
    state_attr: str | None = None             # e.g. "bar"
    state_factory: Callable | None = None      # () -> BarPlotState
    on_activate: Callable | None = None        # was the tab_controller elif branch
    on_selection_changed: Callable | None = None
    on_channel_changed: Callable | None = None # was the channel fan-out
    on_dataset_loaded: Callable | None = None  # was the load fan-out
    persistence: tuple = ()                    # (name, save, load) specs
    fold_change_scope: object | None = None    # FoldChangeScope
    lazy_only: bool = False
    per_well_selection: bool = False           # replaces _PER_WELL_TABS membership
    single_well_selection: bool = False        # replaces the smFISH string check
    icon: str | None = None
```

`FeatureRegistry` holds `list[FeatureDescriptor]` with `get(name)`,
`in_group(g)`, `iter()`.

The **single feature-importing module** — the one place allowed to import
every feature (exactly as `build_persistence_registry` /
`register_default_scopes` already do) — is `features/registry.py`:

```python
FEATURES = [
    barplot.DESCRIPTOR,
    scatter.DESCRIPTOR,
    heatmap.DESCRIPTOR,
    smfish.DESCRIPTOR,        # ← comment this line to remove the smFISH tab
    review_csv.DESCRIPTOR,
    ...
]
```

Each feature package exposes `DESCRIPTOR` in its `__init__.py`, wrapping its
existing `build_*` / controller functions — the feature code itself barely
changes.

## 4. Commit-by-commit (single PR, behaviour-preserving)

Every step replaces a hard-coded call with the *same* call reached via the
descriptor, so each commit keeps the suite green.

1. **Infra, no behaviour change.** Add `core/feature_registry.py` +
   `features/registry.py`; each feature gets a `DESCRIPTOR` wrapping existing
   functions. Nothing consumes the registry yet.
2. **Construction (step 1).** Rewrite `centre_view.build_centre`'s `groups`
   list to iterate `registry.in_group(...)`; builders call
   `desc.build(ctx, frame)`; `lazy_only` from the descriptor. The nested
   **Plotting** sub-stack stays a composite; its five children are descriptors
   with `parent="Plotting"`.
3. **Dispatch (steps 2–3).** Replace the string ladders with
   `registry.get(tab)` hook calls in `tab_controller`, `selection_controller`,
   and the channel fan-out in `channel_state_controller`; convert
   `_PER_WELL_TABS` / smFISH checks to descriptor flags; route
   `load_controller`'s fan-out through `desc.on_dataset_loaded`. Delete the
   top-of-file feature imports from those core modules. *The channel fan-out
   gets its own sub-commits — migrate feature-by-feature with the old block
   still callable, diffing redraw behaviour before deleting.*
4. **State inversion (step 4, load-bearing).** `app_state.py` stops importing
   feature `State` classes. `AppState` keeps its **core** slices (`channel`,
   `selection`, `dataset`, `plot`, `image_view`) as real fields; the registry
   attaches feature slices via `setattr(state, desc.state_attr,
   desc.state_factory())`. Existing `app._app_state.bar` access is unchanged;
   a custom `__getattr__` raises a clear *"feature 'bar' is disabled"* error if
   a removed feature's state is touched.
5. **Persistence + fold-change (completes step 4).**
   `build_persistence_registry` and `register_default_scopes` pull their
   feature entries from `desc.persistence` / `desc.fold_change_scope`;
   non-feature domains (`notes`, `view_state`) stay in core.
6. **Guardrail + docs (makes it permanent).** Extend
   `tests/test_architecture_contracts.py`:
   - `test_core_does_not_import_features` — AST check: no module-scope
     `well_viewer.features.*` import under `core/`, `data/`, `domain/`,
     `persistence/`; `features/registry.py` is the sole allowed importer.
   - `test_disabling_leaf_feature_still_boots` — drop "Review CSV" from
     FEATURES, assert the app builds and the tab is absent.
   Document the descriptor contract here + in `ARCHITECTURE.md` §5.3.

## 5. Out of scope (explicit)

- **`ctx.app` escape hatch stays.** Builders still call `app.*` methods — that
  is `feature → core` coupling, orthogonal to removability.
- **smFISH-as-a-data-concept.** Removing the smFISH *tab* via the registry
  works; removing the smFISH *measurement type* still touches `data/`,
  `domain/`, `persistence/` (~30 files) — a separate effort.
- **`scatter` / `barplot` / `image_browser`** become removable in principle
  (State + hooks move to descriptors) but remain real dependencies of the
  shared `export` / `grouping` / `preview` services; "removing" them is
  meaningful only once those services tolerate their absence. Follow-up.

## 6. Risk & verification

- **Behaviour-preserving by construction**; each commit stays green.
- **Safety net:** 155 test files; CI fast lane (~800 tests, ~40s) + existing
  layering guardrails (`test_layering`, `test_architecture_contracts`,
  `test_monolith_budgets`, `test_event_bus_signal_catalogue_is_capped`). Run
  the `slow` full-app Qt suite + `tests/visual/render_views.py` locally since
  this touches construction/dispatch.
- **Biggest risk:** commit 3's channel fan-out (order-sensitive,
  `hasattr`-guarded). Mitigated by feature-by-feature migration behind the
  registry.
- **Effort:** ~3 new files, ~6 core files rewired, ~16 thin `DESCRIPTOR`
  definitions. Broad in file count, small in logic.

## 7. Acceptance

Commenting one line in `features/registry.py` removes that feature's tab with
no other edits and no startup error — verified by
`test_disabling_leaf_feature_still_boots` — and no `core/`, `data/`, `domain/`,
or `persistence/` module imports `well_viewer.features.*` at module scope,
verified by `test_core_does_not_import_features`.
