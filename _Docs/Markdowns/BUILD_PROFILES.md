# Build profiles — running and deriving feature subsets

The full app is the single source of truth. A **thin build is a *profile* of it**,
not a fork: changes to any surviving region (Plotting, Sample Definitions,
Segmentation, the pipeline) flow into the subset automatically because it is the
same code. This doc covers how to run a subset, how to produce a physically
stripped tree, and the remaining work to make derivation 100% mechanical.

## 1. Run the full tree as a subset (no deletion)

Every build is a profile chosen by the `ALLWELL_PROFILE` env var. Unset = the
full app.

```bash
ALLWELL_PROFILE=thin python -m allwell            # run the thin subset
ALLWELL_PROFILE=plotting_only python -m allwell   # Plotting alone
```

A profile is a manifest under `profiles/`:

```toml
# profiles/thin.toml
name = "thin"
features = ["Plotting", "Sample Definitions", "Image Browser"]

[subtabs]                              # omit a parent to keep all its sub-tabs
"Image Browser" = ["Segmentation"]
```

`well_viewer/core/profile.py` loads it; `build_feature_registry(profile)` keeps
only the admitted `FeatureDescriptor`s (a sub-leaf follows its `parent`), and the
removability contract (ARCHITECTURE §5.3) drops each excluded feature's rail
entry, dispatch, state slice, persistence, and fold-change scope for free. This
is the day-to-day mechanism — validate a subset *before* stripping anything.

Guarded by `tests/test_feature_profile.py` (the app boots + loads a dataset
under a subset profile).

## 2. Build a binary with only the chosen features

The PyInstaller build is profile-aware — you specify features, it builds a binary
with only those:

```bash
ALLWELL_PROFILE=thin scripts/build_executable.sh     # thin binary
scripts/build_executable.sh                          # full app (unset = full)
```

`all_well.spec` reads `ALLWELL_PROFILE` and, for a subset:

1. **Omits excluded features' source** from every bundled first-party package
   (`_pkg_datas` drops each catalog `paths` entry — `well_viewer/` tabs *and*
   non-Qt units like `pipeline/cellpose.py`).
2. **Drops their heavy deps from the binary — automatically, by feature.** Each
   catalog feature declares its unique heavy deps once (`full.toml`'s `deps`).
   Excluding the feature adds those to `_excluded_deps`, which does two things:
   (a) they go to PyInstaller `excludes` (pruned from the module graph), and
   (b) their `collect_all` / `collect_submodules` is **skipped**. Both matter —
   `excludes` alone does *not* drop a `collect_all`'d package, because
   `collect_all` emits `datas`/`binaries` tuples that `excludes` never filters;
   skipping the collect is what actually keeps the `.so`/`.dylib` payload out.
   So a StarDist-only (`thin`) binary drops `torch` / `torchvision` / `cellpose`
   (Cellpose) and `sklearn` / `umap` / `pynndescent` (Multivariate) with **no
   per-feature edit in the spec** — add a new dep-bearing feature to the catalog
   and its deps gate themselves. The pure decision math is
   `scripts/profile_build.py` (unit-tested in `tests/test_profile_build.py`).
3. **Bakes the profile** as `active_profile.txt` at the bundle root. A frozen
   binary has no `ALLWELL_PROFILE` env at run time, so `active_profile()` reads
   this marker and the runtime registry filters to match the bundled code.

The de-coupling guards (§6) are what let the excluded modules be physically
absent without the surviving code crashing.

## 3. Produce a physically stripped tree (source, not binary)

When you want the excluded code actually gone, `scripts/derive_profile.py` diffs
the removable-feature catalog (`profiles/full.toml`) against a target profile —
using the *same* `Profile.includes` predicate the runtime uses, so the stripped
tree and the runtime subset always agree.

```bash
python scripts/derive_profile.py --profile thin           # dry-run (review)
python scripts/derive_profile.py --profile thin --apply   # delete on a branch
```

It deletes each excluded feature's source `paths` and `tests` globs (`keep`
globs protect kept-code tests that merely share a feature's name). It does **not**
auto-edit `features/registry.py` or re-tighten the ratchet guards — those are
surgical and reported as follow-ups. Run the suite after `--apply`; the
profile-boot and feature-removal guards catch any missed cross-reference.

Guarded by `tests/test_derive_profile.py`.

## 4. Cross-version data safety

A dataset moved between the full and thin apps must not lose the other's saved
state. This already holds and is guarded by
`tests/test_thin_preserves_full_app_data.py`:

- unknown top-level `persistence.json` sections survive (each domain updates only
  its own key via `set_section`);
- unknown `view_state` per-tab blocks survive (`restore` stashes every tab into
  `pending`; a tab this build lacks is never popped, so it round-trips);
- foreign `pipeline_info.json` blocks (e.g. `smfish_thresholds`) survive
  (read-modify-write of only the owned block).

**Never** add a persistence path that rewrites the whole document.

## 5. Parallel maintenance workflow

- **Full = trunk.** All feature work lands there.
- **Thin = generated / rebased from full.** Shared-region changes flow in;
  excluded-feature changes simply aren't in the manifest.
- **CI is the drift alarm.** Boot each shipped profile
  (`ALLWELL_PROFILE=<p>`) in CI. A new hard-coded reference to an excluded
  feature fails the subset boot at PR time.

## 6. De-coupling: what's done, what's optional

**Done on this branch** — the kept→excluded references that would break a subset
binary are now guarded on the active registry (or replaced by a registry hook),
so a profiled-out feature's module can be physically absent:

- centre_view's Batch Export frame stash;
- the six plot tabs' Saved Plots wiring — now a **`plot_card_decorator`** hook
  Saved Plots registers; the plot tabs apply all registered decorators and never
  name the feature;
- plot sub-tabs enumerated from the registry (`sub_leaves("Plotting")`), not a
  hard-coded `_PLOT_SUBTABS` — dropping a plot type is a manifest edit;
- load_controller's post-load refresh — now an **`on_dataset_loaded`** registry
  fan-out; Image Table / smFISH register their own refresh, core names neither;
- view_state's Image Table snapshot + restore — now a **`view_state_io`** hook
  the feature owns (`image_table/view_state_io.py`); `view_state` fans out over
  the registry and never names Image Table;
- the **Cellpose** segmenter — `process_microscopy` imports `pipeline/cellpose.py`
  lazily (PEP 562 module `__getattr__` + a dispatch-branch import), so a
  StarDist-only build omits it and the spec drops `torch` / `torchvision` /
  `cellpose` (catalog `deps` → `excludes`, ML `collect_all` skipped);
- Image Browser sub-tabs (profile-filtered; each imports its module only when
  built).

**`runtime_app` proxies — verified inert.** Every removable-feature reference in
`runtime_app.py` is a lazy `_lazy(...)` proxy (imports its module only when
called) or a function-scoped import; the sole module-scope eager `features.*`
imports are of *kept-forever* support libs (`registry`, `export`, `preview`).
Confirmed by booting a subset with seven removable feature packages **and**
`pipeline/cellpose.py` physically removed — `runtime_app` imports and the app
builds its registry cleanly. So an excluded feature's proxy is dead weight, never
a broken import; nothing here blocks a subset binary.

**Optional polish** (pure cleanup, no decoupling value): migrating the residual
`runtime_app` proxy *methods* to their `ctx`-native call sites would let the dead
proxies vanish. `derive_profile.py --apply` runs today with only the ratchet-guard
re-tightening as manual follow-up.
