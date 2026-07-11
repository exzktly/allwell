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

## 2. Produce a physically stripped tree (for distribution)

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

## 3. Cross-version data safety

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

## 4. Parallel maintenance workflow

- **Full = trunk.** All feature work lands there.
- **Thin = generated / rebased from full.** Shared-region changes flow in;
  excluded-feature changes simply aren't in the manifest.
- **CI is the drift alarm.** Boot each shipped profile
  (`ALLWELL_PROFILE=<p>`) in CI. A new hard-coded reference to an excluded
  feature fails the subset boot at PR time.

## 5. Remaining work to reach 100% (must land in the *full* app)

The profile filter is feature-agnostic, but a few spots in the **full** codebase
still hard-code a specific feature outside the registry. Until these are made
registry/profile-driven, `derive_profile.py --apply` needs a small manual pass
(dropping the descriptor line + re-tightening ratchets). Each was hand-removed on
the thin branch; generalize the pattern on trunk:

| Coupling (full app) | Make it… |
|---|---|
| `centre_view.py` stashing named per-feature frames on `app` (`app._batch_export_tab_frame`) | features fetch their own frame via `ctx` |
| `centre_view.py` hard-coded `_PLOT_SUBTABS` list | plot sub-tabs enumerated from the registry (`parent == "Plotting"`) |
| `image_browser/{tab_view,controller}` hard-coded sub-tab names + `if name == …` branches | an Image Browser sub-registry of descriptors |
| plot tabs importing `saved_plots.wire_save_to_gallery` | a "card decorator" hook features *register*, not one tabs import |
| `load_controller` per-feature refresh blocks | an `on_dataset_loaded` registry hook (the descriptor field already exists) |
| `view_state` per-tab `_snapshot_*` / `_apply_*` | each tab registers its own snapshot/restore pair |
| `runtime_app` per-feature proxy methods | finish each feature's `ctx` migration; the proxies then vanish |
| `process_microscopy` `_CELLPOSE_METHODS` branches threaded through `_process_well`/argparse | a segmentation-method **strategy registry** (name → segmenter + CLI args + UI panel) |

Knock these out and the excluded set is entirely "a manifest," with no bespoke
edits and no ratchet surgery.
