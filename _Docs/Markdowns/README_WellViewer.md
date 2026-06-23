# All-Well

All-Well is a desktop application for fluorescence-microscopy quantification
and analysis. It bundles a StarDist-based segmentation pipeline and an
interactive multi-channel data viewer into a single window, so you can take a
plate of raw images all the way to publication-ready plots without leaving
the app.

```
   Raw images                     Per-well CSVs
       │                       (+ pipeline_info.json,
       ▼                          + per-well image zips)
  ┌──────────┐                          ▲
  │ Analyze  │ ──── process_microscopy ─┤
  │   tab    │                          │
  └──────────┘                          │
       │                                ▼
       ▼ (auto-handoff on success) ┌──────────┐
                                   │  Review  │
                                   │   tab    │
                                   └──────────┘
                                   line / bar / scatter / distribution / heatmap
                                   image table · segmentation · smFISH
                                   statistics · review CSV · batch export
```

This README is the user-facing one — installing, launching, running the
pipeline, exploring results, troubleshooting. For internals (where things
live in the code, how to add features, how to debug), see
[`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## What's new

The 5.5.x series layers new analysis capabilities on top of the 5.4 line.
Newest first:

- **7.3.1 — Saved Plots polish & optimizer fix.** Patch release: export all saved plots as a multi-page **PDF report** (cover page + one full-quality page per plot with its parameters); **save the Image Browser grid** to Saved Plots (downsampled, brightness-preserving so contrast isn't washed out); and a fix to the **Optimize ThreshFracOn** metric under fold-change *subtraction* (the validation curve no longer inverts / picks the wrong threshold).

- **7.3.0 — Saved Plots, per-tab axis properties & clearer fold-change titles.**
  - **Saved Plots tab.** Hover any plot panel's top-right corner and click the new **save** button to send that panel — plus the exact parameters/thresholds that produced it — to a per-dataset **Saved Plots** gallery (with Restore / Export / Delete). The gallery now has its own sidebar icon.
  - **Properties no longer leak across tabs.** Axis **limits** and **log-scale** toggles set in a plot's Properties sidebar are now scoped to that plot — changing them on one tab no longer silently changes another tab. (Global publication style — fonts, line/marker, grid, legend — is still shared.)
  - **Fold change is named in the graph title.** When a plot is fold-change-normalised, every affected panel title (not just the first) now says so and names the operator, e.g. *(fold change vs DMSO (ratio), vs t0 (subtraction))*.

- **7.2.0 — Legend labels, fold-change panels, persistence & Image Table columns.** A round of plotting and review-side fixes:
  - **Sample-definition labels in every legend.** The **Scatter Plot** and **Distribution** legends now show your custom well / group labels (set on **Sample Definitions → Well Labels**) instead of the raw well token or group name — matching what Line Graphs and Bar Plots already did.
  - **Fold change now rescales all three Line Graphs panels.** Turning on fold change previously only rescaled the **mean** panel; the **fraction-on** and **N-events** panels stayed on raw values. Now all three normalize together (including in FOV-spread mode, where the N panel shows per-FOV mean counts).
  - **More selections persist across a reopen.** The **Heat Map** timepoint slider position and the **FOV** (per-FOV spread) toggle on Line Graphs / Bar Plots now come back where you left them.
  - **Image Table column management.** New one-click **×** buttons remove any row or column, and an **Add column…** item in the **More ▾** menu mirrors the existing **Add row…** (clone a column, varying channel / timepoint / FOV / well uniformly or stepped).
  - **Active channel restored on reopen.** Reopening a dataset no longer reverts the plot channel to the first one — your last-selected channel is restored.
  - **Cell Gating tab tidy-up.** Clearer section dividers, and the **Optimize ThreshFracOn** tool can now optimise over a **fold-change-adjusted** fraction-on — pick a Control and/or Baseline (its own controls, independent of the plot tabs) so the threshold is chosen on the normalised metric.
  - **Log-scale images.** Image Table rows have a new **Log** toggle to display the image on a log10 intensity scale (high-dynamic-range stains read better); it persists with the rest of the row's display settings.

- **7.1.1 — ThreshFracOn optimizer + persistence fixes + Plot Groups on Image Table.** A consolidated round of Cell Gating fixes plus reach-back across the rest of the app.

  *Cell Gating sub-tab (Sample Definitions).*
  - **Optimize ThreshFracOn** scans the QC + Crop-valid population (not the *Included* gate it's trying to set) for a fraction-on cut that **maximises the relative gap** between two wells: `|fa - fb| / max(fa, fb)`. Surfaces a 0.05 vs 0 split (1.0 relative gap) over a 0.96 vs 0.99 split (≈0.03), which is what a fraction-on cut is supposed to discriminate. Renames the *Steps* control to **# Points** (it's the number of thresholds scanned, not a step size — a small value collapses the scan to its endpoints) and floors it at 10.
  - **Validation popup** opens after every Optimize, plotting `fraction-on(A)`, `fraction-on(B)`, and `|Δ| / max(fa, fb)` as functions of the scanned threshold with a dashed line at the applied value. Non-modal — stays open so you can iterate.
  - Tab reflows top-down: QC controls → FluorGating + *Auto-threshold all channels (ThreshFracOn)* button → ThreshFracOn / scatter-gate toggles → optimizer panel → CDFs at the bottom.

  *Persistence.* The active channel and Bar Plots timepoint multi-selection now round-trip correctly across app restarts. The channel restore previously no-op'd silently behind a bogus `hasattr` guard; the Bar timepoint set restored to internal state but never repainted the popup / count label.

  *Image Browser.* The **Plot Groups** drawer is now reachable from the Image Table sidebar (read-only — authoring stays with the plotting workflow). Loading a Plot Group from any sidebar drives both the plotting selection and the Image Table's tab-local `active_wells`. Image Table rows also now default to **grayscale (Greys)** when built from a channel rather than picking a per-row palette tint (Reds / Greens / Blues), which was implying a meaning the data may not carry; recolor a row via its LutSelector to opt back in.

- **6.12.0 — Separate gate selections + axis-correct gates.** Cell inclusion (gating) and fraction-on (thresholding) now have **independent** gate pickers — each its own multi-select *Gates* checkbox menu — so you can gate on one set of gates and compute fraction-on from a different set. Also fixes a bug where gates drawn on one X/Y axis pair stayed visible (and editable) after switching the Scatter Plot to different axes; gates now only show on the axes they were drawn on.

- **6.11.0 — Multi-select filtering gates.** The Cell Gating "filtering gate" picker is now a **multi-select checkbox menu** (the *Filtering gates* button) instead of a single-choice dropdown — tick any combination of named gates and they're combined as a union, applied to both inclusion and fraction-on. Defaults to all gates (new gates auto-included).

- **6.10.0 — Scatter gate as a fraction-on threshold.** On the **Cell Gating** tab the scatter gate can now be applied two independent ways: **inclusion** (the existing checkbox — cells outside the gate drop out) and/or **thresholding** (a new checkbox — cells *inside* the gate count as "on" for fraction-on stats, replacing the per-channel ThreshFracOn cutoff; no cells are removed). Both use the same filtering-gate dropdown and can be on at once. Also fixes a bug where a persisted QC debris gate (e.g. min/max circularity) loaded its values into the fields on reload but didn't re-apply to the dataset.

- **6.9.0 — Scatter "Color by time".** The per-cell **Scatter Plot** has a new **Color by time** toggle (beside the Layout control). Turn it on to colour each point by its timepoint instead of by well — so a well's cell cloud shows how it moves through the X/Y feature space over time (early timepoints purple, late timepoints yellow), with a per-timepoint legend. Select a single well to read one cloud's evolution on one set of axes; with several wells selected the view automatically stacks into one panel per well so the time-coloured clouds don't overlap into noise.

- **6.8.0 — Scatter-gate cell inclusion.** The rectangular gates you draw on the **Scatter Plot** can now define which cells count as *included*, not just label a percentage. Each gate is **auto-named** (`Gate 1`, `Gate 2`, …) as you draw it and shows its name on the plot; rename it in the **Scatter Plot** Properties sidebar's gate list. On the **Cell Gating** tab tick **"Use scatter-plot gates for cell inclusion"** and pick a **filtering gate** from the dropdown — a single named gate, or *All gates (union)* — to replace the per-channel FluorGating thresholds. A cell is kept if it falls inside the chosen gate(s), on whichever metric axes (intensity, ratio, or shape) each gate was drawn. The gated population takes effect immediately as you draw, edit, or clear gates, and flows into every plot, statistic and export. The per-cell Scatter Plot keeps showing the full cloud (with the gates drawn over it) so you can always see and refine your gates, and the gates are saved with the dataset so they come back on reload. The **QC debris gate** (size / solidity bounds) also now lives on the Cell Gating tab as a fundamental layer that is *always* applied, on top of FluorGating and any scatter gates — it moved here from the Segmentation tab (which still highlights the excluded cells in its overlay).

- **6.1.x — Background-correction Playground & UI polish.** The **Segmentation** tab is now split into **Review Image** and a new **Playground** sub-tab — an interactive testbed for the pipeline's background-flattening / subtraction methods on a single raw frame chosen by well/channel/timepoint/FOV. Pick any flat-field + background method, tune its parameters, and see the result with the same LUT / colormap / binary / mask-overlay controls as the review image, plus a Raw/Flat-field/Corrected toggle, click-drag pan, per-source LUT memory, a frame-local binary threshold, a hover pixel-intensity readout, and a QC size-cleanup preview — all without re-running the pipeline (it shares the pipeline's exact correction code). Alongside: Bar Plots is now the default Plotting tab and Cell Gating leads Sample Definitions; the cell-gating CDFs respect the crop region + QC gate; and a batch of review-image fixes (crop button auto-deselects, thicker crop outline, zoom kept when switching wells).

- **5.5.6 — Label-free segmentation (§A).** Segment unlabelled / phase channels: Cellpose and a classical engine are selectable with per-method parameter panels, and channels can be designated as phase on ingestion.

- **5.5.5 — Editable QC layer (§B).** Debris removal is now non-destructive — cells are *flagged, not deleted* — and editable after the run from the **Cell Gating** tab (moved there from Segmentation in 6.8.0): tune the size / solidity gate and the cells it removes highlight live on the image while every plot, statistic and the Review table re-flag, with no re-segmentation. The QC verdict is the single source of area/shape gating and composes with manual curation (a cell counts only when manually kept **and** it passes QC). A **Max region area** bound additionally rejects bright debris that StarDist shatters into many small "cells" — they share one over-large bright region that per-cell size can't see. Flat-field correction stays a run-time option.

- **5.5.2 — Quality control (§B).** New **Quality Control** section in Analyze: optional flat-field correction, a **background-subtraction method** (`none` / `top_hat` / `rolling_ball` / `empty_well` — rolling-ball gives a smooth baseline without the square top-hat's halos; empty-well subtracts a measured per-channel background, and when flat-field is *also* empty-well the two combine into one correct `(raw − B)/(B/mean(B))` pass), and debris removal (min/max area + solidity gates), so noisy plates clean up before quantification.

- **5.5.1 — Per-cell shape features (§C).** The pipeline can emit per-cell morphology columns (area, perimeter, solidity, eccentricity, …); pick them from the **Shape Metrics** dropdown in Analyze, and they appear in Review as a sectioned *Shape Features* virtual-channel group for plotting.

- **5.5.0 — Feature-stack foundation (PR1).** Locks the empirically-verified dependency set and bundles the optional engines (PyTorch, Cellpose, btrack, trackastra) in the PyInstaller build, adds the `shape_features_core` module, and makes dataset reads backwards-compatible with pre-5.5 CSVs.

---

## Contents

- [What's new](#whats-new)

1. [What you get](#what-you-get)
2. [Install](#install)
   - [macOS pre-built app](#macos-pre-built-app)
   - [Running from source](#running-from-source)
3. [Quick start](#quick-start)
4. [Analyze mode — running the pipeline](#analyze-mode--running-the-pipeline)
5. [Review mode — exploring results](#review-mode--exploring-results)
6. [Filename schema](#filename-schema)
7. [CSV output format](#csv-output-format)
8. [Performance and GPU usage](#performance-and-gpu-usage)
9. [Building the macOS bundle](#building-the-macos-bundle)
10. [Troubleshooting](#troubleshooting)
11. [Keyboard shortcuts](#keyboard-shortcuts)
12. [Where to read next](#where-to-read-next)

---

## What you get

- **Analyze tab.** A form-driven launcher for the StarDist nuclear-segmentation
  pipeline. Pick an input folder, set the filename schema and channel tokens,
  pick a segmentation method, hit Run. The pipeline streams its log into the
  window and writes per-well CSVs plus per-well processed-image ZIPs to your
  output directory.
- **Review tab.** Open the output folder and explore: an 8×12 plate-map well
  picker, line / bar / scatter / distribution / heat-map plots, an image
  table for thumbnail comparison, a per-FOV segmentation reviewer with cell
  editing, an smFISH spot-detection view, pairwise statistics, batch export
  to CSV + figures.
- **One window, one dataset folder.** Analyze writes a folder; Review opens
  it. The hand-off is automatic when an Analyze run completes successfully.
- **Persistence baked in.** Channel-threshold defaults, saved selections,
  ratio metrics, heat-map layouts, per-cell `Included` overrides, and figure
  styling all live next to your data in `pipeline_info.json` and a small set
  of sibling JSON files; reopening the folder restores everything.

---

## Install

### macOS pre-built app

If someone handed you `AllWell.app` (typically inside `AllWell-mac.zip`),
nothing else is required — the bundle ships its own Python + every
dependency.

1. Unzip; move `AllWell.app` to `/Applications` (optional).
2. The first time you launch it, macOS may complain it's "damaged" because
   the bundle is unsigned. Clear the quarantine attribute and re-sign with
   an ad-hoc signature:
   ```sh
   xattr -cr /Applications/AllWell.app
   codesign --force --deep --sign - /Applications/AllWell.app
   ```
3. Double-click to launch.

About **4 GB free RAM** is recommended for StarDist inference. On Apple
Silicon the bundled `tensorflow-metal` uses the GPU automatically.

### Running from source

You'll need:

- **Miniforge** (or another conda/mamba distribution). On Apple Silicon:
  ```sh
  curl -LO https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-MacOSX-arm64.sh
  bash Miniforge3-MacOSX-arm64.sh
  ```
- **Xcode Command Line Tools** (Apple Silicon, for native wheel builds):
  ```sh
  xcode-select --install
  ```

Create the environment. The version pins here matter — most of the post-pin
ones are because TensorFlow 2.13 (the macOS-Metal build) won't accept newer
NumPy or `setuptools`:

```sh
mamba create -n allwell python=3.10.14 setuptools=69.5.1 \
    "numpy>=1.24,<1.25" scipy scikit-image matplotlib pillow imageio h5py \
    -c conda-forge -y
mamba activate allwell
```

Verify `pkg_resources` works before going further (if it doesn't, you've got
a too-new `setuptools` — see Troubleshooting):

```sh
python -c "import pkg_resources; print('OK')"
```

Install the pip-managed packages. **Always pass `--no-build-isolation`** so
pip honours the mamba-managed pins above:

```sh
pip install --no-build-isolation -r _Docs/requirements.txt
```

On Apple Silicon, confirm TensorFlow sees the Metal GPU:

```sh
python -c "import tensorflow as tf; print(tf.config.list_physical_devices('GPU'))"
# expected: [PhysicalDevice(name='/physical_device:GPU:0', device_type='GPU')]
```

Run it:

```sh
python all_well.py
python all_well.py --data_dir /path/to/results   # pre-load a dataset
```

The pipeline alone (no GUI) can be invoked directly — useful for CI or
batch processing on headless workers:

```sh
python process_microscopy.py --help
```

### Windows (from source)

Native Windows runs CPU-only — TensorFlow dropped native Windows GPU
support after 2.10, and AllWell pins `tensorflow==2.13.0` for stack
consistency. For GPU on Windows, use WSL2 (out of scope here).

You'll need:

- **Miniforge** (gives you `mamba`). Download the Windows installer from
  <https://github.com/conda-forge/miniforge/releases/latest> and run it.
- **Git for Windows** — already required if you also want to drive a
  remote Linux host (see
  [`REMOTE_EXECUTION_WINDOWS.md`](REMOTE_EXECUTION_WINDOWS.md)). Install
  from <https://git-scm.com/download/win> with the default options.
- **Python 3.10 or 3.11.** TensorFlow 2.13.0 has Windows wheels for
  those two versions only; 3.12+ will fail at the pip step. The
  `mamba create` line below pins 3.10.14 so you don't have to think
  about this.

Open the **Miniforge Prompt** (start menu) and `cd` to the repo root.

If an `allwell` env already exists from a previous attempt, remove it
first — pip cannot uninstall conda-installed Qt packages to satisfy the
`PySide6==6.7.3` pin (see the shiboken6 Troubleshooting entry below):

```powershell
mamba env remove -n allwell -y
```

Create the env:

```powershell
mamba create -n allwell python=3.10.14 setuptools=69.5.1 "numpy>=1.24,<1.25" scipy scikit-image matplotlib-base pillow imageio h5py vs2015_runtime -c conda-forge -y
mamba activate allwell
```

The command is one line on purpose: PowerShell's continuation character
is `` ` ``, the Miniforge Prompt's is `^`, and bash's `\` works in
neither — keeping it single-line works in all three.

Two non-obvious package choices:

- `matplotlib-base` (not `matplotlib`). The conda-forge `matplotlib`
  meta-package pulls in `pyside6` as a transitive dep (its preferred
  Qt binding), which drags in the latest `shiboken6`. Pip then can't
  downgrade it to `shiboken6==6.7.3` because conda packages have no
  RECORD file. `matplotlib-base` is all of matplotlib without any Qt
  binding; PySide6 from pip supplies the Qt backend at runtime.
- `vs2015_runtime` brings the MSVC redistributable DLLs PySide6 needs
  without requiring admin rights to install vcredist system-wide.

Install the pip-managed packages. Use the full path to the requirements
file from repo root:

```powershell
pip install --no-build-isolation -r _Docs\requirements.txt
```

Verify TensorFlow is CPU-only as expected:

```powershell
python -c "import tensorflow as tf; print(tf.config.list_physical_devices('CPU'))"
# expected: [PhysicalDevice(name='/physical_device:CPU:0', device_type='CPU')]
```

Run it:

```powershell
python all_well.py
python all_well.py --data_dir C:\path\to\results
```

To produce a distributable Windows bundle (optional — only if you want
`AllWell.exe` for users who don't have the env):

```powershell
.\scripts\build_executable.ps1 -Clean
```

The output is `dist\AllWell\AllWell.exe`. The spec's macOS `.app`
`BUNDLE` step is a no-op off macOS, so no `AllWell.app` is produced.

If you also want to drive a remote Linux host from this Windows machine,
see [`REMOTE_EXECUTION_WINDOWS.md`](REMOTE_EXECUTION_WINDOWS.md) — the
remote-execution bootstrap reuses the same `allwell` env (and the same
Git-for-Windows `ssh.exe` / `rsync.exe`).

---

## Quick start

A common end-to-end run, from raw images to plots, takes five clicks:

1. Launch the app and switch to **Analyze** (title-bar segmented control).
2. **Open input folder** — point at the folder of raw TIFs (or a folder of
   per-well ZIPs).
3. Fill the schema and channel tokens. Click **Run**.
4. When the log prints "Processing complete" the app auto-switches to
   **Review** and loads the output folder.
5. Pick wells in the plate map; the plots redraw immediately.

Reopen a previous output folder any time with the `Open…` button in Review
or `Ctrl+O`.

---

## Analyze mode — running the pipeline

The Analyze tab is one tall scrollable form. Top to bottom:

### 1. Filename schema

A colon-separated list of field names, applied left-to-right against the
underscore-separated tokens in each filename. The `Schema string` field is
editable directly; the picker chips above it mirror it.

Recognised field names: `experiment`, `channel`, `well`, `fov`, `timepoint`,
`ignore`.

- `channel` and `well` are **required** and must appear exactly once.
- `fov` is optional — single-FOV acquisitions work fine without it.
- `ignore` consumes a token without using it (e.g. an experiment-version
  prefix).

The well token is normalised: `A1` and `A01` mean the same well.

**Common examples:**

| Filename | Schema | Sep |
|---|---|---|
| `Exp01_NIR_B03_F001_02d04h30m.tif` | `experiment:channel:well:fov:timepoint` | `_` |
| `A1_w1_T01.tif` | `well:channel:timepoint` | `_` |
| `Scan-A01-0001-GFP.tif` | `ignore:well:fov:channel` | `-` |
| `A01_DAPI.tif` | `well:channel` | `_` |

**Timepoint formats** the pipeline parses to hours:

```
DDdHHhMMm   02d04h30m → 52.5 h
Standalone  48h, 2d, 30m, 90min
Pure number 24 or 1.5  (interpreted as hours)
Prefixed    T01, day2, tp_3  (numeric suffix → ordinal)
Any string  preserved as-is and sorted lexicographically
```

### 2. Channel tokens

- **Nuclear (segmentation) channel.** The token used to find the image
  StarDist runs against (also gets quantified — its intensities appear in
  the CSV like any other channel).
- **Fluorescent channels.** One or more tokens whose intensities you want
  quantified. Each token gets its own CSV column set (`<token>_mean_intensity`,
  `<token>_total_intensity`, etc.).
- **smFISH channels** (optional). Channels processed for spot detection.
  Each smFISH channel appears with both a regular intensity column set and
  a `_smfish_count` column.

### 3. Folders

The input folder is resolved by
[`services/input_resolution_service.py`](../services/input_resolution_service.py):

- A folder named `in/` → input = that folder, output = `../out`.
- A folder containing an `in/` subfolder → use it, output = `../out`.
- A folder of loose TIFs → `WellPlateZipper` is invoked to group them into
  per-well ZIPs according to your schema, then the pipeline runs against
  those.
- A folder of per-well ZIPs already → used directly.

You can also explicitly override the output folder.

### 4. Pipeline options

- **Segmentation method.** `stardist_nuclei` (default — segment nuclei
  only), `stardist_seeded_watershed_cell` (StarDist nuclei + watershed
  cytoplasm segmentation).
- **Cytoplasm token** (only used by the seeded-watershed method).
- **Top-hat radii.** Nuclear and per-fluor radii in pixels. `0` or
  `--no_tophat_*` flags disable per-channel.
- **Min nucleus area** in pixels (rejects garbage detections).

### 5. Quality control — field flatness & background correction

The **Quality Control** section bundles optional, pre-measurement clean-up that
runs **per channel at quantification time** (so it changes the pixel values that
get measured and can only be changed by re-running the pipeline). There are two
independent correction stages plus a debris filter:

1. **Flat-field (illumination) correction** — *multiplicative*. Models the slow
   illumination profile (vignetting / uneven excitation) as a smooth field `F`,
   normalizes it to **mean 1.0**, and computes `corrected = image / F`. Because
   the field is mean-1, overall intensity magnitudes stay comparable to the raw
   image (so gating thresholds still make sense). The mean-1 field is **floored
   at 0.1**, which caps the amplification of any dim region at 10× — this stops a
   near-black patch from exploding to out-of-distribution values that would crash
   segmentation. Real vignetting is < ~3× so legitimate correction is untouched.

2. **Background subtraction** — *additive*. Removes a baseline offset, then clips
   the result at 0 so downstream intensity sums stay non-negative.

The normal order is **flat-field (divide) → background (subtract)**, with one
special combined case described below.

#### Flat-field correction modes

| Mode | What it does |
|------|--------------|
| **`none`** | No illumination correction. |
| **`gaussian`** | Self-referential, per-image: heavily Gaussian-blurs each image (σ ≈ 50 px) to estimate its own illumination field, then divides. No reference well needed; flattens slow gradients while preserving cell-scale structure. |
| **`basic`** | Pure-numpy BaSiC-style. Samples up to ~30 images **per channel across the whole plate**, takes the **per-pixel median** (robust to cells/debris that move between fields), Gaussian-smooths it, normalizes to mean 1, and divides. One field per channel. Approximates BaSiC without its L1/low-rank optimisation (no `jax`/`basicpy`). |
| **`empty_well`** | Same median-based estimator, but sampled **only from a designated empty/background well** (the *Background well* field) — that well's measured intensity *is* the illumination profile. Requires a Background well. |

#### Background-subtraction methods

| Method | Operation | Notes |
|--------|-----------|-------|
| **`none`** | Image unchanged. | — |
| **`top_hat`** | `img − grey_opening(img, square)`, square footprint of side `2·radius+1`. | Fast (separable 1-D passes) but the square footprint leaves anisotropic, *terraced* residuals — square halos/hotspots next to bright objects. Legacy method. |
| **`sliding_disk`** | Isotropic-disk white top-hat at full resolution: `img − opening(img, disk)`. | **Default & recommended.** A disk of the given radius flattens regions wider than ~2·radius (treated as background) and preserves narrower ones (kept as signal), isotropically — no square terracing. Built from a decomposed disk so it stays fast (~2 s on a 1024² frame). |
| **`fov_median`** | `img − median(img)`. | Subtracts a **single scalar**, the median intensity of the whole field of view. Most pixels in a field are background, so the median is a robust flat-background estimate (insensitive to bright cells/debris). Removes a **uniform offset only** — it does *not* flatten spatial gradients (use a morphological method or `empty_well` for that). No radius and no reference well needed; the cheapest correction. |
| **`empty_well`** | `img − B`. | Subtracts a *measured* per-channel additive background `B` — the **per-pixel median** of the empty/background well's images, Gaussian-smoothed and kept at its measured level (not normalized). No-op if no field is available for the channel. Requires a Background well. |

The **Nuclear radius** / **Fluor radius** entries set the background length scale
(in px) for the morphological methods only (`top_hat` / `sliding_disk`); these
controls are hidden for `none` / `fov_median` / `empty_well`, which take no radius.
Each channel has a **Disable** checkbox to skip background subtraction for that
channel.

#### Combined empty-well correction (the special case)

When **both** flat-field and background are set to `empty_well`, the pipeline does
**not** run them separately. Doing so would mix scales — the additive background
`B` is at raw scale but a separate flat-field would already have rescaled the
image. Instead a single physically-correct pass is applied:

```
corrected = (raw − B) / (B / mean(B))
```

i.e. subtract the measured additive background `B` first, then divide by the
mean-1 illumination field (which is just `B / mean(B)`). This assumes the additive
background **tracks the illumination** — true for media autofluorescence under
uneven excitation — and then recovers the underlying signal while mapping the
empty well itself to ~0.

#### Background well

Any `empty_well` choice (flat-field and/or background) reads its reference images
from the well(s) named here. Enter **one** well (e.g. `H12`) or **several
comma-separated** (e.g. `H12, G12`) — multiple blank wells are **pooled** (their
frames combined before the per-pixel median) for a cleaner, less noisy estimate.
Each well is located in whichever input layout holds it — a per-well ZIP, a
per-well folder, or loose TIFs. The run is blocked if an `empty_well` mode is
selected but the Background well field is blank or none of the named wells are
present in the input folder.

**Per-timepoint correction.** Background drifts over the course of a timecourse,
so the field is built **separately for each timepoint**: the empty-well frames at
a given timepoint correct the sample frames at that same timepoint. Timepoint
identity is normalized the same way everywhere — `T01`, `1`, `1.0`, and `1h` all
collapse to one key — so empty-well and sample frames match regardless of label
style. If the blank wells have **no frame at some timepoint**, that timepoint
falls back to the **global pooled median** (the field built from all of the blank
wells' frames across every timepoint), which is the historical whole-timecourse
behavior. Single-well, single-timepoint datasets therefore behave exactly as
before.

#### Debris removal

The **Remove debris** toggle drops segmented regions whose size / shape fall
outside the bounds below — **min / max area** (px) and **min solidity** (0–1).
Unlike the pixel corrections above, this gate is *non-destructive and editable
after the run*: cells are flagged rather than deleted, and the Segmentation tab
recomputes the verdict live as you tune the thresholds, with no re-segmentation
(see [§B Editable QC layer](#whats-new)). A cell counts only when it is manually
kept **and** it passes QC.

### 6. Compute options

- **TF threads.** TensorFlow intra-op parallelism (CPU only; ignored on
  Metal). Defaults to 4.
- **Workers.** Number of wells processed in parallel.
  - On Apple Silicon Metal the default is 2 — Metal serialises GPU calls
    across processes, so more workers don't help GPU throughput.
  - CPU-only: defaults to `floor((cpu_count − 1) / tf_threads)`.
- **CPU-only.** Forces TensorFlow to ignore the GPU.
- **Force re-run.** Re-processes wells whose output ZIPs already exist.

### 7. Run / Stop / Log

- Click **Run**. The log streams the pipeline's stdout/stderr live.
- **Stop** signals the entire pipeline process group (sub-workers
  included).
- On success the app switches to Review and loads the output folder.

### What the pipeline writes

```
<output_dir>/
├── <well>_out.zip          ← masks + tophat-corrected fluorescence + overlays
├── <prefix>_<well>.csv     ← per-well measurement CSV (one row per nucleus)
├── pipeline_info.json      ← schema + channel tokens + FOVs + timepoints +
│                              gating thresholds + saved selections, etc.
└── (transient tmp_<well>/  ← deleted on success)
```

---

## Review mode — exploring results

Open a results folder with the Open button (or `Ctrl+O`, or
`python all_well.py --data_dir <path>`). The viewer accepts:

- A folder of per-well CSVs (flat).
- A folder containing an `out/` subfolder of CSVs.
- A folder with both `in/` (source ZIPs) and `out/` (results).

Per-well image ZIPs (`<well>_out.zip`) are discovered on demand when an
image-using tab is open. `pipeline_info.json` is read on every open and
keeps the viewer in sync with the schema + channel tokens.

### The window in one diagram

```
┌─────────────────────────────────────────────────────────────────┐
│ All-Well   [Review|Analyze]    ↻  ⛶  ⌂  i                       │  ← title bar
├──────────────┬──────────────────────────────────────────────────┤
│              │  Channel: [GFP ▾]                  ── ctxbar ──  │
│ SECTION      │  ┌────────────────────────────────────────────┐  │
│  ⊙ Plotting  │  │                                            │  │
│   Statistics │  │            (plot canvas)                   │  │
│   Image …    │  │                                            │  │
│   Segment …  │  └────────────────────────────────────────────┘  │
│   Review CSV │         Export CSV  Copy SVG  Save  Properties   │
│   Sample …   │                                                  │
│   Batch …    │                                                  │
│              │                                                  │
│ ┌──────────┐ │                                                  │
│ │  Plate   │ │                                                  │
│ │  8×12    │ │                                                  │
│ │  picker  │ │                                                  │
│ └──────────┘ │                                                  │
│ Select all   │                                                  │
│ Select none  │                                                  │
├──────────────┴──────────────────────────────────────────────────┤
│ ● Ready.                       ⌘O Open  ⌘E Export  ⌘W Close     │  ← status bar
└─────────────────────────────────────────────────────────────────┘
```

### The Review tabs

The left-rail "SECTION" navigator selects one of the centre tabs:

| Tab | What it shows |
|-----|---------------|
| **Plotting** | Five sub-tabs (default **Bar Plots**): **Bar Plots** (bar / beeswarm / violin, drag-to-reorder), **Line Graphs** (mean ± SD/SEM + fraction above threshold + CDF), **Scatter Plot** (per-cell scatter and aggregate scatter; per-cell points can be coloured by well or, via the **Color by time** toggle, by timepoint), **Distribution** (histogram / KDE / violin of per-cell values at one timepoint), **Heat Map** (custom layout heatmap). Bar Plots and Line Graphs additionally support **Fold-change normalization** — divide every bar / curve by a control well or replicate set, by each member's own t0, or both (see [Fold-change normalization](#fold-change-normalization)). |
| **Statistics** | Pairwise tests across saved selections (t-test, Mann-Whitney, KS) with KS CDF. |
| **Image Table** | Grid of per-FOV image thumbnails; configurable rows/columns/channels; bulk export. Hosts a read-only mirror of the **Plot Groups** drawer so a saved well set can drive the image grid (loads also propagate to the plotting selection). Per-row LUT defaults to grayscale; recolor via the row's LutSelector. |
| **Segmentation** | Two sub-tabs. **Review Image** — per-FOV overlay viewer: click a nucleus to flag/unflag it for inclusion in stats; draw a **crop rectangle** to keep only cells whose centroid is inside (applied dataset-wide across all FOVs/timepoints/channels/wells, with a Reset that restores every cell and a Clear crop that removes just the box); a Raw / Flat-field / Corrected source toggle; tune the LUT and the QC debris gate. **Playground** — an interactive testbed to try any background-flattening / subtraction method (with adjustable parameters) on a single chosen raw frame, with Raw/Flat-field/Corrected, LUT/colormap, binary + precomputed-mask overlay, pan/zoom, per-source LUT memory, a frame-local threshold, a hover intensity readout, and a QC size-cleanup preview — no re-run needed. |
| **smFISH** | Spot detection, parameter sweep, and **Apply to All** threshold. Its own rail tab (formerly a Segmentation sub-tab). |
| **Review CSV** | Tabular view of the loaded per-well CSVs; supports filtering, jumping to the Segmentation tab for any row. |
| **Sample Definitions** | Sub-tabs led by **Cell Gating** (per-channel inclusion-threshold defaults; its fluorescence CDFs anchor the bottom of the tab and respect the crop region + QC gate; or tick *Use scatter-plot gates* to let the Scatter Plot's rectangular gates define inclusion instead). Cell Gating also hosts **Auto-threshold all channels (ThreshFracOn)** (Otsu pass over per-cell + matched-background pixels) and **Optimize ThreshFracOn** — pick two wells, a timepoint, and a Min / Max / # Points range to scan; the optimizer finds the threshold that maximises the **relative** fraction-on gap `|fa - fb| / max(fa, fb)` and opens a non-modal popup graphing the curve so you can sanity-check the pick. After Cell Gating come the saved-selection / replicate-set editor (Groups / Well Labels / Ratios), Pipeline Info, and Notes. |
| **Batch Export** | Define export groups (or use the Sample Definitions groups), pick timepoints, run a bulk export of CSVs + figures + ZIPs. |

### Channel selector

The single channel dropdown at the top of every plotting sub-tab is shared
across the whole window — picking a different channel re-renders every
plot, every axis label, every threshold range, and every export against
the new channel. Ratio channels (e.g. `GFP/MCHERRY`) defined under the
Sample Definitions ratio editor appear here as virtual channels.

### Plate-map well picker

The 8×12 plate map in the left sidebar drives every plot and table. Click
a well to toggle it; drag across wells to multi-toggle; click a row letter
or column number to toggle the whole row / column; **Select all** /
**Select none** clear or fill in one click. The selection survives across
tabs and is what every plot / aggregate / export consumes.

### Fold-change normalization

The Bar Plots and Line Graphs tabs (and their Batch Export panels) carry
two parallel dropdowns on the controls row:

```
Fold change:  Control [— ▾]   Baseline [— ▾]
```

- **Control** — pick a loaded well or replicate set to divide every bar
  / curve by its mean at the matching timepoint. `—` disables this
  axis. (The Bar Plots control combo also includes any replicate sets
  defined in Sample Definitions.) When a rep-set name happens to match
  a well token, the well's combo entry is suffixed with `(well)` so
  the two are unambiguous.
- **Baseline** — pick `t0 (first timepoint)` to divide every bar / curve
  by its own value at the earliest available timepoint, so each
  member's first point becomes 1.0. `—` disables this axis.

Both axes are independent and may be combined. With both active the
math is ΔΔCt-style: `(X(t)/C(t)) / (X(0)/C(0))` — each member's value
normalized to the control at the same timepoint, then re-normalized to
its own pre-control baseline at t0.

On Line Graphs the normalization applies to all three stacked panels —
**mean**, **fraction-on**, and **N-events** — each divided by the
control's / baseline's value for that same quantity (in FOV-spread mode
the N panel uses per-FOV mean counts).

The Bar Plots tab applies normalization in all three view modes — bar,
violin, and beeswarm. In violin and beeswarm, each well's per-cell
values are divided by the resolved denominator before plotting, so the
distribution lives on the fold-change axis.

State is shared between the Bar Plots and Line Graphs tabs — toggling
on one is reflected on the other when you switch back. The
batch-export panels keep their own copies so a batch job isn't tied
to whatever the on-screen plot has set.

Normalized figures gain a "(fold change vs …)" suffix on the mean
axis title / label, and the batch-export figure suppresses the raw
threshold reference line on the mean panel (it's in raw-fluorescence
units and would be misleading on a normalized axis; the CDF panel's
threshold line stays because per-cell distributions aren't
normalized).

When a control is requested but has no sample at one of the plotted
timepoints, the affected bars / points are rendered as dashed
placeholders and the status bar names the missing timepoints — no
silent NaN drops.

**CSV exports — additive schema.** Both the line and bar exporters
keep the standard `mean_<ch>_<metric>` and `err_<band>_<ch>_<metric>`
columns carrying the RAW values, and append four columns when
fold-change is active:

* `fold_change_mean` — normalized mean
* `fold_change_<sd|sem>` — normalized spread
* `fold_change_mode` — `control`, `t0`, or `control+t0`
* `fold_change_control` — the chosen well / rep-set name

(Earlier versions of the bar CSV overwrote the raw mean column with
the normalized value; downstream tools that depended on that behaviour
should switch to reading the `fold_change_mean` column.)

**Statistical consistency.** The control denominator uses the same
averaging statistic as the displayed bar / curve — mean-of-per-well-means
for rep-set controls, single-well aggregation for solo wells, per-FOV
pool when the Aggregate-FOVs toggle is on. Earlier versions mixed
statistics across numerator and denominator, which produced biased
ratios; bar / curve values for fold-change vs multi-well controls may
shift slightly after this fix.

### Properties (figure styling)

The **Properties** button on each plot tab's controls row opens a slide-out
sidebar that styles the active figure: axis label / tick / title sizes,
grid on/off + alpha + linestyle, legend, line / marker widths, axis
limits + log-scale, layout engine, and an export-profile picker. Settings
are per-figure and persist across redraws.

### Saved figures and CSV exports

Every plot tab carries the same action buttons on its controls row:

- **Export CSV** — writes the underlying data behind the current plot.
- **Copy SVG** — puts a vector copy of the figure on the clipboard
  (paste into Illustrator / Inkscape / Keynote / Slides). On macOS the
  clipboard is written natively (PDF + SVG) so Illustrator pastes editable
  vector; this needs `pyobjc-framework-Cocoa` (see `requirements.txt`).
- **Save figure** — saves to disk; the file dialog picks the format from
  the extension (`.svg` / `.pdf` / `.eps` / `.png`).
- **Properties** — opens the styling sidebar described above.

### Batch export

Define one or more groups (replicate sets + solo wells), pick a set of
timepoints, and the Batch Export tab generates one figure + one CSV per
group per timepoint into a folder of your choosing. Useful for paper
figures where every condition needs the same plot at the same time
points. The bar and line batch panels also expose the same
[Fold-change normalization](#fold-change-normalization) dropdowns, with
panel-local state so a batch run isn't tied to whatever the on-screen
plot has set.

---

## Filename schema

The schema is a colon-separated list of field names applied left-to-right
to filename tokens (split on the chosen separator). See
[Analyze mode → Filename schema](#1-filename-schema) above for the
recognised field names and worked examples.

`pipeline_info.json` records the schema you ran the pipeline with, so the
viewer doesn't need you to re-enter it.

---

## CSV output format

One CSV per well, one row per nucleus. Standard columns:

```
filename, experiment, channel, well, fov, timepoint, timepoint_hours
nucleus_id, area_px, Included
<token>_total_intensity      ← one set per quantified channel
<token>_mean_intensity
<token>_max_intensity
<token>_min_intensity
<token>_std_intensity
<token>_smfish_count          ← only if <token> is configured as smFISH
```

`<token>` is the lowercase channel token (`gfp`, `mcherry`, `w2turq`, …).
The nuclear/segmentation channel is also quantified, so it appears as a
normal channel in viewer selectors.

`Included` is `0` or `1` and starts as the cell-gating-default verdict;
the Segmentation tab and the Cell Gating sub-section let you override it
per cell, with overrides persisted to `cell_overrides.json` next to the
CSVs.

---

## Performance and GPU usage

The pipeline spawns one worker per well, up to a configurable maximum. Each
worker loads StarDist once and reuses it across every FOV in that well.

- **Apple Silicon (Metal) — the default.** Workers default to 2. Metal
  serialises GPU calls across processes; more workers do not improve GPU
  throughput, they only add RAM pressure. Two workers overlap I/O and
  CPU preprocessing with GPU inference.
- **CPU-only.** Workers default to `floor((cpu_count − 1) / tf_threads)`,
  with `tf_threads` defaulting to 4. Adjust under Compute Options if your
  machine has very many cores.

Temporary directories (`_tmp_extract_*`, `_tmp_images_*`) are removed when
each well completes, even after a worker error. Stragglers from a crashed
run are cleaned up at the start of the next run.

---

## Building the macOS bundle

PyInstaller produces a binary for the host architecture; the build itself
must run on a Mac. The `allwell` mamba environment must be active.

```sh
mamba activate allwell
chmod +x _Docs/_Installation/build_all_well.sh
_Docs/_Installation/build_all_well.sh
```

The finished bundle lands at `dist/AllWell.app`. To distribute it:

```sh
cd dist && zip -r AllWell-mac.zip AllWell.app
```

For a Universal binary (Intel + Apple Silicon in one bundle):

```sh
TARGET_ARCH=universal2 _Docs/_Installation/build_all_well.sh
```

This needs a `universal2` Python from python.org — the Miniforge Python is
architecture-specific and can't produce a universal binary.

---

## Troubleshooting

**`AllWell.app is damaged and can't be opened.`** — macOS quarantine on
an unsigned bundle. Fix:
```sh
xattr -cr /Applications/AllWell.app
codesign --force --deep --sign - /Applications/AllWell.app
```

**The app opens and immediately closes.** — Run the bundle from a terminal
to see the actual error:
```sh
dist/AllWell.app/Contents/MacOS/AllWell 2>&1 | head -100
```

**`No module named 'pkg_resources'`.** — `setuptools` was upgraded past v70
in the mamba environment. Recreate the environment from scratch. Don't
`pip install setuptools` and don't `mamba update --all`.

**Windows: `Cannot uninstall Shiboken6 6.x.y, RECORD file not found`
during `pip install`.** — A conda-installed `shiboken6` (or `pyside6`)
is sitting in the env, and pip cannot uninstall it to satisfy the
`PySide6==6.7.3` pin (conda packages have no `RECORD` manifest pip can
read). Two common sources:

1. An older `mamba create` line listed `pyside6` explicitly.
2. The conda-forge `matplotlib` meta-package pulled `pyside6` in as a
   transitive dep. **This is the gotcha.** The fix is to use
   `matplotlib-base` instead — same matplotlib, no Qt binding.

Bulletproof recovery (one block, paste into Miniforge Prompt at repo
root):

```powershell
mamba deactivate
mamba env remove -n allwell -y
mamba create -n allwell python=3.10.14 setuptools=69.5.1 "numpy>=1.24,<1.25" scipy scikit-image matplotlib-base pillow imageio h5py vs2015_runtime -c conda-forge -y
mamba activate allwell
pip install --no-build-isolation -r _Docs\requirements.txt
```

Do NOT include `pyside6` or `matplotlib` in the mamba step — both pull
shiboken6 in via conda where pip can't remove it. PySide6 is installed
by pip from the requirements file; `vs2015_runtime` from conda supplies
the MSVC DLLs PySide6 needs.

**Windows: `VCRUNTIME140_1.dll` was not found / PySide6 import crashes.**
— The conda-forge MSVC redistributable isn't visible to PySide6. Verify
`vs2015_runtime` is in the env (`mamba list vs2015_runtime`) and
re-activate the env so the conda `DLLs` directory is on PATH:
```powershell
mamba deactivate
mamba activate allwell
python -c "from PySide6 import QtWidgets; print('OK')"
```

**Windows: `pip install` fails on tensorflow with "no matching
distribution".** — TensorFlow 2.13.0 ships Windows wheels for Python
3.10 and 3.11 only. Confirm with `python --version`. If you're on
3.12+, recreate the env pinning `python=3.10.14`.

**Windows: `scripts\build_executable.ps1` reports "PyInstaller is not
importable".** — The active env doesn't have the deps installed.
Activate the env first and re-run pip:
```powershell
mamba activate allwell
pip install --no-build-isolation -r _Docs\requirements.txt
```

**`No GPU device found` / TensorFlow not using Metal.** — Install
`tensorflow-metal`:
```sh
pip install tensorflow-metal==1.1.0
python -c "import tensorflow as tf; print(tf.config.list_physical_devices('GPU'))"
```

**Segmentation / smFISH / Image Table show "No images found".** — Check
that your results directory contains `<well>_out.zip` files. The viewer
reads top-hat-filtered fluorescence (`*_tophat.tif`), masks
(`*_labels.tif`), overlays (`*_overlay.png`), and smFISH-processed
channels (`*_smfish.tif`) from inside those ZIPs. If channel tokens differ
from what the viewer expected, re-open the directory — `pipeline_info.json`
is re-read on every open.

**Auto-threshold reports `timepoint unknown`.** — `pipeline_info.json`
doesn't have a schema that names a `tp` or `timepoint` field. Re-run the
pipeline with the correct `--filename_schema` so the JSON sidecar carries
the right field map.

**Bar-plot timepoint dropdown is empty.** — Either no wells are selected,
or the CSVs don't have a `timepoint_hours` column. Confirm the schema you
ran the pipeline with included a `timepoint` field. Single-timepoint
experiments show one entry (e.g. `0`).

**`WellPlateZipper` produces no zip files.** — Schema or separator does
not match the filenames. Make sure `well` points at the right token, the
separator is right, and the token is a valid plate position (`A01`–`H12`
or `A1`–`H12`). The schema used is logged at the start of every run.

**StarDist model download fails (SSL error).** — Pre-download the model on
an unrestricted machine and copy `~/.keras/models/` across.

**`No module named 'stardist'` in the built app.** — The build forgot the
PyInstaller hooks. Make sure `_Docs/_Installation/hooks/` (notably
`hook-stardist.py`) was on the spec path.

**The built bundle is > 900 MB.** — Normal. TensorFlow + Numba + LLVMLite
+ SciPy + Matplotlib account for the bulk.

**Channel dropdown shows a different channel than the plot.** — Should be
fixed; if you ever see it again it means the active channel state and the
combo got out of sync. Refresh the dataset (Open the same folder again).
File a bug.

---

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `⌘O` / `Ctrl+O` | Open a results folder |
| `⌘E` / `Ctrl+E` | Export the active figure (drives the visible plot card's Save figure action) |
| `⌘←` / `Ctrl+←` | Back through tab history |
| `⌘→` / `Ctrl+→` | Forward through tab history |
| `⌘W` / `Ctrl+W` | Close window |

The bottom status bar shows these chip-style hints at all times. The header
**`info`** icon opens a help drawer with the same list plus a few more
quick-reference notes.

---

## Where to read next

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — the developer-oriented
  architectural overview: what each module does, how the pieces fit
  together, where to add features, and how to debug.
- [`ui_synergy.md`](ui_synergy.md) — the living implementation plan for the
  UI/UX synergy + usability improvements (design-review follow-ups),
  status-tracked with each item tied to the architecture guidelines.
- [`process_microscopy.py`](../process_microscopy.py) — the pipeline. It's
  also a usable CLI (`python process_microscopy.py --help`).
- [`_Docs/_Installation/`](../_Docs/_Installation/) — the PyInstaller spec
  + build script for producing the macOS bundle.
- [`_Docs/requirements.txt`](../_Docs/requirements.txt) — the pinned
  pip-managed dependencies, with environment-setup comments.
