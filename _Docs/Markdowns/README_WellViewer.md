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
pipeline, exploring results. For internals (where things live in the code, how
to add features, how to debug, and build / environment troubleshooting), see
[`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## Contents

1. [What you get](#what-you-get)
2. [Install](#install)
   - [macOS pre-built app](#macos-pre-built-app)
   - [Running from source](#running-from-source)
3. [Quick start](#quick-start)
4. [Analyze mode — running the pipeline](#analyze-mode--running-the-pipeline)
5. [Review mode — exploring results](#review-mode--exploring-results)
   - [The Review window at a glance](#the-review-window-at-a-glance)
   - [Controls shared by every tab](#controls-shared-by-every-tab)
   - Plotting: [Bar Plots](#bar-plots) · [Line Graphs](#line-graphs) · [Scatter Plot](#scatter-plot) · [Distribution](#distribution) · [Heat Map](#heat-map)
   - [Statistics](#statistics) · [Multivariate analysis](#multivariate-analysis) · [Spatial analysis](#spatial-analysis)
   - [Image Browser](#image-browser) (Segmentation · Image Table · Cell Browser · Playground) · [smFISH](#smfish) · [Review CSV](#review-csv)
   - [Sample Definitions](#sample-definitions) · [Saved Plots](#saved-plots) · [Batch export](#batch-export)
6. [Filename schema](#filename-schema)
7. [CSV output format](#csv-output-format)
8. [Performance and GPU usage](#performance-and-gpu-usage)
9. [Building the macOS bundle](#building-the-macos-bundle)
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
a too-new `setuptools` — see Troubleshooting in
[`ARCHITECTURE.md`](ARCHITECTURE.md)):

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
python allwell.py
python allwell.py --data_dir /path/to/results   # pre-load a dataset
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
  remote Linux host. Install
  from <https://git-scm.com/download/win> with the default options.
- **Python 3.10 or 3.11.** TensorFlow 2.13.0 has Windows wheels for
  those two versions only; 3.12+ will fail at the pip step. The
  `mamba create` line below pins 3.10.14 so you don't have to think
  about this.

Open the **Miniforge Prompt** (start menu) and `cd` to the repo root.

If an `allwell` env already exists from a previous attempt, remove it
first — pip cannot uninstall conda-installed Qt packages to satisfy the
`PySide6==6.7.3` pin (see the shiboken6 Troubleshooting entry in
[`ARCHITECTURE.md`](ARCHITECTURE.md)):

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
python allwell.py
python allwell.py --data_dir C:\path\to\results
```

To produce a distributable Windows bundle (optional — only if you want
`AllWell.exe` for users who don't have the env):

```powershell
.\scripts\build_executable.ps1 -Clean
```

The output is `dist\AllWell\AllWell.exe`. The spec's macOS `.app`
`BUNDLE` step is a no-op off macOS, so no `AllWell.app` is produced.

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
(the debris gate is edited from the [Cell Gating](#cell-gating) tab in Review).
A cell counts only when it is manually kept **and** it passes QC.

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

Review is where you explore an already-analyzed dataset. Open a results folder
with the **Open** button (or `Ctrl+O`, or launch with
`python allwell.py --data_dir <path>`); a finished Analyze run hands off here
automatically. The viewer accepts:

- A folder of per-well CSVs (flat).
- A folder containing an `out/` subfolder of CSVs.
- A folder with both `in/` (source ZIPs) and `out/` (results).

Per-well image ZIPs (`<well>_out.zip`) are discovered on demand the first time
an image-using tab is opened. `pipeline_info.json` is re-read on every open, so
the viewer always tracks the schema + channel tokens the dataset was built with
— if you re-run the pipeline with different tokens, just re-open the folder.

### The Review window at a glance

```
┌─────────────────────────────────────────────────────────────────┐
│ All-Well   [Review|Analyze]    ↻  ⛶  ⌂  i                       │  ← title bar
├──────────────┬──────────────────────────────────────────────────┤
│              │  Channel: [GFP ▾]                  ── ctxbar ──  │
│ SECTION      │  ┌────────────────────────────────────────────┐  │
│  ⊙ Plotting  │  │                                            │  │
│   …          │  │            (plot canvas)                   │  │
│   …          │  │                                            │  │
│   …          │  └────────────────────────────────────────────┘  │
│              │         Export CSV  Copy SVG  Save  Properties   │
│              │                                                  │
│              │                                                  │
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
**Getting around the window.**

- **Title bar** — the `[Review | Analyze]` segmented control switches modes;
  the icons to its right are Refresh (re-read the folder), presentation
  toggle (flip every plot Screen ↔ Publication), Open, and the **`i`** Help
  drawer.
- **Section rail** — the left "SECTION" navigator lists every centre tab
  (Plotting, etc.), grouped into **Analysis**, **Images**, and **Data**. Tabs
  that own sub-tabs (e.g. Plotting) show a sub-tab bar across the top of the
  centre pane. `Ctrl+B` collapses the rail.
- **Plate map + Plot Groups** — pinned to the bottom of the left rail; the
  well selection they drive is what every tab plots (see below).
- **Context bar** — the row directly above the plot canvas: the shared channel
  selector plus the current tab's own controls and figure-action buttons.
- **Status bar** — a live status dot on the left, keyboard-shortcut chips on
  the right.
- **Tab history** — `Ctrl+←` / `Ctrl+→` step back and forward through the tabs
  you have visited, like a browser's back/forward.

### Controls shared by every tab

These affordances are global — set them once and every tab honours them.

#### Channel selector

The single channel dropdown at the top of every plotting sub-tab is shared
across the whole window — picking a different channel re-renders every
plot, every axis label, every threshold range, and every export against
the new channel. Ratio channels (e.g. `GFP/MCHERRY`) defined under the
Sample Definitions ratio editor appear here as virtual channels.

#### Plate-map well picker

The 8×12 plate map in the left sidebar drives every plot and table. Click
a well to toggle it; drag across wells to multi-toggle; click a row letter
or column number to toggle the whole row / column; **Select all** /
**Select none** clear or fill in one click. The selection survives across
tabs and is what every plot / aggregate / export consumes.

#### Plot Groups and Split View

- **Plot Groups** — the collapsible drawer under the plate map saves the
  current well selection under a name so you can reload it in one click. Plot
  Groups are *plotting selections only* — not replicate sets or stats groups —
  and loading one drives both the plate selection and, where a tab keeps its
  own well set, that tab's set too. The Image Table sidebar mirrors the drawer
  read-only, so a saved set can drive the image grid as well.
- **Split View** — the **Split** toggle in the top bar puts two *different*
  centre tabs side-by-side, both live (e.g. Scatter Plot beside Cell Browser).
  Click into a pane to focus it — the focused pane drives the sidebar and the
  well picker, and both panes refresh on a selection or channel change. It
  needs a second tab to show something; a tab can't be split against itself.

#### Figure actions — Export CSV · Copy SVG · Save · Properties

Every plot tab carries the same action buttons on its controls row:

- **Export CSV** — writes the underlying data behind the current plot.
- **Copy SVG** — puts a vector copy of the figure on the clipboard (paste into
  Illustrator / Inkscape / Keynote / Slides). On macOS the clipboard is written
  natively (PDF + SVG) so Illustrator pastes editable vector; this needs
  `pyobjc-framework-Cocoa` (see `requirements.txt`).
- **Save figure** — saves to disk; the file dialog picks the format from the
  extension (`.svg` / `.pdf` / `.eps` / `.png`).
- **Save to Saved Plots** — hover a plot panel's top-right corner and click the
  **save** button to send that panel, plus the exact parameters that produced
  it, to the per-dataset [Saved Plots](#saved-plots) gallery.
- **Properties** — opens a slide-out sidebar that styles the active figure:
  axis label / tick / title sizes, grid on/off + alpha + linestyle, legend,
  line / marker widths, axis limits + log-scale, layout engine, and an
  export-profile picker. Axis **limits** and **log-scale** are scoped to the
  one plot; global publication style (fonts, line/marker, grid, legend) is
  shared across tabs. Settings persist across redraws.

#### Fold-change normalization

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

### Bar Plots

**What it shows.** Per-well summaries of a metric at one or more timepoints, as
grouped **bars**, a **violin** of the per-cell distribution, or a **beeswarm**
of individual cells. This is the default Plotting sub-tab.

- **Needs** — at least one selected well and a channel. The **Timepoints**
  dropdown (`Select Timepoints`) fills from the selected wells'
  `timepoint_hours`; tick any set.
- **Controls** (the **Display ▾** menu gathers the view options):
  - **Grouping** — how bars are grouped (default *Well*; a replicate set groups
    its members).
  - **Pooled** — collapse each replicate set's wells into one column (off = one
    column per well). For **bars** this averages the per-well means; for
    **violin** / **beeswarm** it pools every cell across the set into a single
    distribution. Enabled once ≥1 replicate set is active.
  - **Violin** / **Beeswarm** — swap the bars for a per-cell distribution
    (violin) or every individual cell (beeswarm). **Smooth** is active only
    when Violin is on and tunes the KDE bandwidth.
  - **Significance** (On / Off) — draw pairwise significance brackets.
  - **Reset Order** — restore the default order after you **drag bars to
    reorder** them.
  - The shared [Fold-change](#fold-change-normalization) dropdowns, applied in
    all three view modes.
- **Optimal use** — reorder so your control condition reads first; switch to
  violin / beeswarm when the story is spread or per-cell scatter rather than a
  mean; normalize vs a control well or replicate set to put every bar on a
  fold-change axis.

### Line Graphs

**What it shows.** A metric over time as three stacked panels — **mean ±
SD/SEM**, **fraction above threshold** (fraction-on), and **N events** (cell
count) — plus a CDF.

- **Needs** — selected wells spanning more than one timepoint to see a trend
  (single-timepoint data draws a single point).
- **Controls** (under **Display ▾**) — the **error band** (SEM / SD); the
  **per-FOV spread** toggle, which treats each field of view as a replicate so
  the band reflects field-to-field variability; the shared
  [Fold-change](#fold-change-normalization) dropdowns (all three panels — mean,
  fraction-on, and N — normalize together).
- **Optimal use** — turn on FOV spread when you have several fields per well
  and want the error band to reflect field-to-field variability; normalize vs
  **t0** to collapse every curve to a shared start of 1.0 for shape comparison.

### Scatter Plot

**What it shows.** Two sub-views on a sub-tab bar — **Per-cell points** (one
point per cell, X vs Y feature) and **Per-well aggregate** (one point per
well/timepoint). Per-cell points can be coloured **by well** or, via **Color by
time**, by timepoint.

- **Needs** — a well selection, a **Channel**, and an **X-axis** and **Y-axis**
  metric (any intensity, ratio, or `shape_*` column) chosen from the axis
  dropdowns. A **Timepoints** multi-select (`Select Timepoints`) restricts the
  frames drawn.
- **Per-cell controls** — X-axis / Y-axis / Channel pickers; **Layout**
  (*Overlay* all wells on one axis, or *Separate axes* one panel per well);
  **Color by time** (one well → a single cloud's trajectory; several wells →
  auto-stacked one panel per well so the time-coloured clouds don't overlap);
  **Show gates**; **draw rectangular gates** by dragging on the canvas.
- **Aggregate controls** — X-axis / Y-axis pickers over aggregate metrics
  (e.g. *Mean Fluorescence*, *Fraction above threshold*) with an **error band**
  (SEM / SD) on the points.
- **Gates (definition).** Each gate you draw is auto-named (`Gate 1`, `Gate 2`,
  …), shown on the plot, and renameable in Properties. Gates become selectable
  **cell-inclusion** or **fraction-on** filters on the [Cell Gating](#cell-gating)
  tab, on whichever metric axes they were drawn. They are saved with the
  dataset and reload with it.
- **Optimal use** — draw a gate around a population of interest, then open Cell
  Gating and tick *Use scatter-plot gates* to make that gate define which cells
  count in every plot, statistic, and export.

### Distribution

**What it shows.** The distribution of a per-cell metric at **one timepoint**,
as a histogram, KDE, or violin.

- **Needs** — a well selection, a channel, and a timepoint (**t (h)**).
- **Controls** — **Mode** (*Histogram*, *Histogram + KDE*, or *Violin (per
  group)*); **Layout** (*Overlay* all wells, or *Faceted (one per group)*);
  **Bins** count; **log x** / **log y** toggles; the timepoint picker; the
  channel-wide threshold reference line.
- **Optimal use** — compare a handful of wells at a single timepoint to read
  how a treatment shifts the *whole* population, not just its mean; facet when
  overlaid distributions get too crowded to read.

### Heat Map

**What it shows.** A custom-layout heatmap of one metric across wells — you
place cells in a grid that mirrors your plate or your experimental design.

- **Needs** — a well selection and a channel; a timepoint (**t (h)**) chosen
  with the slider.
- **Controls** —
  - **t (h)** timepoint slider (its position persists across reopen).
  - **Aggregation** — how multiple cells/wells in a grid cell combine (e.g.
    *Rep-set avg*).
  - **Color map** (e.g. *Greys*, *RdYlBu_r*, *Muted*).
  - **Scale** — *Auto*, or *Fixed* with explicit **vmin** / **vmax**.
  - **Log scale** toggle.
  - **⇄ Transpose** — swap the rows and columns of the layout.
  - The layout editor: drag wells into the grid and **Edit label** on any row /
    column header.
- **Optimal use** — build a layout once that matches your plate, pin the colour
  **Scale** to *Fixed* vmin/vmax so frames are comparable, then scrub the
  timepoint slider to watch the metric evolve.

### Statistics

**What it shows.** Pairwise significance tests across your saved selections /
replicate sets — **t-test**, **Mann-Whitney**, and **KS** — with a KS CDF plot.

- **Needs** — at least two groups. On first visit the tab auto-populates from
  the replicate sets defined on [Sample Definitions](#sample-definitions).
- **Controls** — the group editor (sidebar); the test selection; the timepoint;
  an FOV-replicate indicator that mirrors the plot tabs' FOV mode.
- **Optimal use** — define replicate sets first so the tests run on
  biologically meaningful groups rather than single wells.

### Multivariate analysis

The **Multivariate** tab analyses *many* per-cell features together, on the
cells in your current well selection (honouring the same inclusion + gating +
crop masks as every other plot). Where the plotting tabs show one metric at a
time, this tab is for questions like "which features move together?", "do the
cells fall into distinct phenotypes?", and "what separates my conditions in
high-dimensional space?".

**Workflow.** Select wells (and optionally replicate sets) as usual, open the
tab, then:

1. **Features** — click **Features…** and tick the columns to analyse
   (intensity/count metrics per channel, `shape_*` morphology features, and any
   ratio channels). This feature set is the matrix every mode operates on.
2. **Timepoint** — restrict to one or more timepoints, or leave as *all*.
3. Pick a **mode** (segmented control): **Correlation**, **Dim-reduction**,
   **Clustering**, or **Faceting**.
4. **Export** / **Copy** / **Save** (row-1 buttons) write the data / figure out
   (see below).

**Standardize (z-score).** The **Standardize** checkbox z-scores every feature
column — subtract its mean, divide by its standard deviation — *before* PCA and
clustering. This is on by default and almost always what you want: multivariate
features live on wildly different scales (raw intensities in the thousands next
to solidity in `[0, 1]`), and both PCA and Euclidean-distance clustering are
dominated by whichever feature has the largest raw numbers unless you put them
on a common scale first. Turn it **off** only when your features are already
comparable and you deliberately want the high-variance ones to dominate.
Constant features (identical in every cell) collapse to zero rather than
divide-by-zero.

**The four modes.**

- **Correlation** — a feature×feature Pearson correlation heatmap (scale
  invariant, so unaffected by Standardize), or a scatter-plot matrix (SPLOM)
  via the view toggle.
- **Dim-reduction** — project every cell to 2-D. **PCA** (default, always
  available), or **UMAP** / **t-SNE** if `umap-learn` / `scikit-learn` are
  installed. Colour the points **by group / well / timepoint / cluster**. For
  PCA the axis captions report each PC's explained-variance %, and a **PCA
  loadings** panel is drawn beside the scatter (see below).
- **Clustering** — **k-means** or **hierarchical** clustering into **k** groups
  (the `k` spinbox). Three panels: a PCA scatter coloured by cluster; a stacked
  bar of each well/group's **phenotype composition** (fraction of its cells in
  each cluster); and a **cluster feature-profile** heatmap (see below).
  Hierarchical clustering on large selections is fit on a random subsample and
  the rest assigned to the nearest cluster (a footnote says how many cells were
  sampled).

> **t-SNE / UMAP performance.** These run on the UI thread and scale poorly, so
> on a large selection they're capped to a seeded random subsample of the cells
> (a footnote reports "N sampled cells (of M)"); a wait cursor shows while they
> compute. PCA is exact and never capped.
- **Faceting** — pivot one feature into a row-factor × column-factor grid
  (e.g. well × timepoint), aggregated by mean / median / count / std.

#### Extracting PCA weightings (loadings)

A PCA score plot shows *where* cells land but not *why*. The **loadings** are the
answer: each principal component is a weighted combination of your original
features, and the loading is that weight — a feature with a large-magnitude
loading on PC1 is one of the things PC1 is measuring; same-sign features move
together along that axis, opposite-sign features trade off.

- **On screen** — in **Dim-reduction → PCA** mode, the panel to the right of the
  scatter is a **PCA loadings** heatmap: rows are your features, columns are
  PC1 / PC2, and each cell is that feature's loading (value-annotated,
  red = positive, blue = negative).
- **In a file** — click **Export** and, alongside the per-cell matrix CSV, a
  companion **`<name>_pca_loadings.csv`** is written whenever the matrix supports
  PCA (≥ 2 features, ≥ 2 cells). It has one row per feature, one column per
  principal component (`PC1`, `PC2`, …), and a final `explained_variance_ratio`
  row giving the share of total variance each PC captures. Loading vectors are
  unit-norm per PC (they're the SVD right-singular directions).

#### Making t-SNE / UMAP actionable

t-SNE and UMAP are *non-linear* embeddings, so — unlike PCA — they have **no
loadings**: the axes aren't a weighted combination of features, and "tSNE-1 =
high GFP" has no meaning. You read them a different way:

- **Interpret the clusters, not the axes.** In **Clustering** mode the far-right
  **cluster feature-profile** heatmap shows each cluster's mean value per feature
  (z-scored across the population: red = above average, blue = below). That's the
  interpretable substitute for loadings — it tells you *what each region/cluster
  actually is* ("cluster 2 = high GFP, low area"). Exported as a companion
  **`<name>_cluster_profiles.csv`** (rows = clusters, columns = features).
- **Export the coordinates.** In **Dim-reduction** (or Clustering) mode, **Export**
  adds each cell's embedding coordinates as `pca_1/pca_2`, `tsne_1/tsne_2`, or
  `umap_1/umap_2` columns — matching exactly what's plotted (including any t-SNE /
  UMAP subsample) — so the map is quantifiable downstream.
- **Colour by cluster** and read the **phenotype-composition** bars to see what
  fraction of each condition falls in each region.

#### Which cluster each cell belongs to

In **Clustering** mode, click **Export** to write the per-cell feature matrix
with a **`cluster`** column appended — one row per cell, giving its `well`,
`group`, `timepoint_hours`, every feature value, its embedding coordinates, and
its integer cluster id (`0 … k-1`). That's the raw phenotype assignment for
downstream analysis (count cells per cluster per condition, cross-tabulate
against treatment, etc.). The cluster ids match the colours in the on-screen
scatter and the phenotype-composition bars.

### Spatial analysis

The **Spatial** tab (Analysis rail, next to Multivariate) analyses per-cell
positions in the well plane — the pipeline emits `shape_centroid_row` /
`shape_centroid_col` for every segmented cell, and this tab uses them to
answer questions the plotting and multivariate tabs can't: *"Do my cells
form colonies, and are they growing or sparse?"*, *"Do edge cells behave
differently from interior cells?"*, *"How much of the GFP variability is
between colonies versus inside each one?"*.

Everything runs on the **currently selected well and one timepoint** at a
time (spatial statistics are per-frame). Pick a well in the plate map, open
Spatial, and use the top-row controls:

- **Level** — the scale of the analysis:
  - **Cell** — per-cell distance metrics and clustering statistics in the
    well plane (nearest-neighbour distance, k-NN density, Ripley's L).
  - **Colony** — one aggregate per auto-detected colony (size, hull area,
    packing density, per-colony metric spread).
  - **Intra-colony** — each cell's position relative to its colony
    (distance to centroid, distance to hull edge, boundary flag).
- **Viz** — the visualisation. The dropdown shows only the visualisations
  that make sense at the current level; hover any option for its meaning.
- **Metric** — the per-cell CSV column used as the signal in colour maps,
  radial profiles, per-colony violins, and boundary/interior splits. Any
  `<channel>_<property>` intensity column, any `shape_*` morphology column,
  or any user-defined ratio is eligible.
- **Timepoint** — which frame to analyse. *First available* uses the
  smallest finite `timepoint_hours` in the well.

The second row controls **colony detection** — colonies are computed, not
manually annotated:

- **Method** — **DBSCAN** (default) is density-based: cells whose
  ε-neighbourhood contains ≥ min-samples other cells seed a colony; the
  rest are marked *noise* (grey on the map). **Single-linkage** chains any
  two cells closer than ε into the same colony — simpler, but doesn't
  reject outliers.
- **ε × median NND** — the colony-membership radius as a multiple of this
  frame's median nearest-neighbour distance. Self-scaling to density: the
  same multiplier gives roughly the same "grain" of clustering whether the
  well is packed or sparse. Larger multiplier → fewer, bigger colonies.
- **Min. samples** — DBSCAN's min-neighbours threshold (ignored by
  single-linkage). Higher → tighter, purer colonies + more noise cells.
- **Boundary %** — the bottom-N-percentile of *distance to the colony hull
  edge*, per colony, that counts as boundary for the Boundary-vs-Interior
  viz.

**Which visualisation for which question**

| If you want to answer… | Level | Viz | What you see |
|---|---|---|---|
| Are my cells clustered, dispersed, or random? | Cell | Ripley L(r) − r | Sample curve vs a Monte-Carlo CSR envelope. Above the envelope = clustered at that radius; below = dispersed; inside = consistent with random. |
| How closely packed are my cells? | Cell | NND histogram | Distribution of each cell's nearest-neighbour distance, with a median marker (the reference scale for the ε multiplier). |
| How many colonies did I get, and how big? | Colony | Colony size histogram | One bar per colony-cell-count bucket. |
| **How much of my GFP (or any metric) is between colonies vs inside them?** | Colony | **Per-colony distribution** | One violin per colony, sorted by median. The top-left callout gives `ICC = σ²_between / σ²_total`, `σ²_between`, `σ²_within`, and the cell / colony count. **ICC → 1** means variability is almost entirely between colonies; **ICC → 0** means variability lives inside colonies. |
| Do edge cells look different from centre cells? | Intra-colony | Radial profile | Metric ± SEM binned by normalised radius `r/R` (0 = colony centre, 1 = furthest cell). |
| Do boundary cells look different from interior cells? | Intra-colony | Boundary vs interior | Split violin of the metric partitioned by the Boundary % percentile. |
| Where are my cells / colonies in the well? | any | Spatial map | Scatter of centroids in the well plane, coloured by metric / colony id / r/R, with optional convex-hull overlays per colony. |
| **How do wells / timepoints compare *grossly*?** | **Summary** | **Summary panel** | Per-(well, timepoint) matrix of one summary scalar (median NND, colony count, mean colony size, packing density, ICC on the picked metric, …). Renders as either a wells × timepoints heatmap or one line per well over time. Click **Export CSV** to write one long-format row per (well, timepoint) with every summary column. This is the cross-condition view — everything else on the tab is a single-frame view. |

Every metric name, control label, and viz option carries a hover tooltip
with the same one-line reading — the table above is what's on screen when
you rest the mouse.

**Summary panel — the "gross comparison" viz.** Selecting Level → *Summary*
switches the tab into an aggregate mode: instead of one well + one
timepoint, it walks every well in your plate selection × every loaded
timepoint and computes a small dataclass (`FrameSummary`) of scalar
summaries per frame — cell count, colony count, median NND, median k-NN
mean distance, median local density, mean colony size, median hull area,
median packing density, and (if you picked a per-cell Metric) the
between-vs-within ICC on that column. Pick which one to plot from the
**Summary metric** dropdown and choose *Heatmap* or *Line plot* from
**Display**. **Export CSV** writes every summary column at once (long
format: one row per `well × timepoint_hours`), so downstream analysis
in pandas or Excel isn't restricted to whichever summary is currently
plotted. The single-timepoint picker in row 1 is disabled at Summary
level — timepoints are already the X-axis.

### Image Browser

The **Image Browser** rail entry (Images group) hosts four image-review
sub-tabs on a sub-tab bar: **Segmentation**, **Image Table**, **Cell Browser**,
and **Playground**. All read the per-well processed-image ZIPs, loaded on
demand — if a sub-tab reports "No images found", confirm the results folder
holds `<well>_out.zip` files.

#### Segmentation

**What it shows.** A per-FOV overlay of the segmentation on the raw image, for
reviewing and curating individual cells.

- **Needs** — a loaded well with image ZIPs; pick the FOV / timepoint / channel
  from the in-tab pickers.
- **Navigational controls** — click a nucleus to **flag / unflag** it for
  inclusion; **draw a crop rectangle** to keep only cells whose centroid is
  inside it (applied dataset-wide across every FOV/timepoint/channel/well),
  with **Reset** to restore every cell and **Clear crop** to remove just the
  box; click-drag to pan and scroll to zoom (zoom is kept when you switch
  wells); a **Raw / Flat-field / Corrected** source toggle; LUT + colormap +
  binary + mask-overlay controls; the QC debris gate.
- **Definition.** *Included* is the per-cell keep/drop flag every plot and
  export honours. A cell counts only when it is manually **kept** *and* it
  passes QC. Overrides persist to `cell_overrides.json` next to the CSVs.
- **Optimal use** — exclude a well's edge artifacts in one gesture with the
  crop rectangle; click away obvious mis-segmentations.

#### Image Table

**What it shows.** A configurable grid of per-FOV image thumbnails for
side-by-side comparison — now across **any combination** of wells / channels /
timepoints / FOVs, including more than two at once (e.g. a time-course of every
channel for every well).

- **Needs** — image ZIPs present, and a well set (drive it from the plate
  picker, or save/pick a **Plot Group** in this sidebar's Plot Groups panel).
- **Build with the shelf** — the top **Build** shelf is a drag-and-drop tree.
  Drag a **channel** from the **Channels** tray onto the **Rows** or **Columns**
  zone; drag a **root** dimension (Well / Timepoint / FOV) onto a zone to make that
  axis vary by it, with its channels nested underneath (*root ↓ channels* for Rows,
  *root → channels* for Columns). Then press **Generate** to render. Editing the
  shelf only stages the layout — **Generate** draws the images. More than two
  dimensions can vary at once (e.g. Rows = *Well › Channels*, Columns = *Timepoint*).
- **Fixed panel** — every root dimension **not** on an axis is listed under
  **Fixed**, pinned to a single value you choose. Drag a root out of Fixed onto an
  axis to make it vary; click the **×** on a root to drop it back to Fixed. Wells
  come from the sidebar plate.
- **Per-channel render options** — each channel chip has a **⚙** menu: **Raw /
  Corrected**, **Mask**, **≥ threshold** (renders that channel's ThreshFracOn
  threshold-binary, positive cells white), a **boundary** outline overlay, and a
  **colormap + LUT** min/max. The options travel with the channel wherever it sits,
  so the same channel looks identical on either axis. Remove a chip with its **×**.
- **Separate ↔ Overlay** — a switch on each root's channel group: **Separate** shows
  one cell per channel; **Overlay** additively blends that root's channels into one
  composite cell (LUT-coloured, clipped to white).
- **Wrap cols** folds the columns into vertically-stacked bands of *N* (handy for a
  long single-dimension column axis). **Clear builder** empties the shelf and starts
  fresh (it leaves the rendered images — use **Clear images** for those).
- **Plate A–H × 1–12** lays each selected well's image out in its true 96-well-plate
  position — rows labelled **A–H**, columns **1–12** — using the first channel in the
  builder as the image. Toggle it on in the shelf header; wells come from the plate
  selection (a partial selection renders a correctly-labelled partial plate), and it's
  mutually exclusive with **Wrap cols**. Prefer this over well-on-columns + Wrap when
  you want a plate view: Wrap only labels the first band, whereas Plate labels every
  row and column correctly.
- **Action strip** — **Generate** · **Reset table** (back to defaults) · **Clear
  images** · **Crop** (drag a region on any image) · an **ON threshold** value with
  a **channel** dropdown choosing which channel it applies to (the same ThreshFracOn
  shared with Cell Gating and the plot tabs) · **Export** (to file · Copy PNG / SVG ·
  Save to Saved Plots) · **Zoom** · a **Properties** button that slides out a
  right-side panel for the grouped view's **spacing gaps** and **label font sizes**,
  plus the **Scale bar** and **Cell labels** options below.
- **Scale bar** — enter the **pixel size (µm/px)** and a **bar length (µm)** in the
  Properties panel to burn a scale bar into the images (pixel size isn't stored by
  the pipeline, so you supply it). Choose which cell(s) show it (**every cell**, or a
  single grid corner), where in the image it sits, and its colour. The bar is drawn
  into the pixels, so the live grid and the exported figure match exactly.
- **Cell labels** — set **Mode → Identity** to burn each cell's *well · channel ·
  timepoint* label into the image (respecting custom Well Labels), with the same
  which-cell / in-image-corner / colour / background-box controls as the scale bar —
  handy for a single-panel figure where the axis strips don't apply.
- **Matched brightness** — a channel left on **Auto** LUT now shares **one** intensity
  window across every cell of that channel (pooled over the grid), so the same channel
  is directly comparable cell-to-cell; set an explicit LUT min/max on the channel to
  override.
- **Pixel readout** — hover a rendered cell to see that pixel's intensity (prefixed
  with the cell's combo identity); the readout now stays put when the cursor comes
  to rest instead of reverting to the plain cell tooltip.
- **Per-cell tweak** — right-click any rendered cell to override just that cell's
  well / channel / timepoint / FOV.
- **Well labels** — where a well would show its `A01` position (axis / group labels,
  cell tooltips, exported titles), its custom label is shown instead when one is set
  under Sample Definitions → Well Labels.
- **Optimal use** — drag **Well** onto Rows and nest a couple of channels, drag
  **Timepoint** onto Columns → one row per well×channel, one column per timepoint,
  for a treatment's full time-course at a glance. Flip a root to **Overlay** for a
  merged view; if the grid gets large, narrow the well/timepoint selection, then
  save the composed grid to Saved Plots. To read a single channel across the whole
  plate at a glance — e.g. a GFP reporter — select all wells, drag that channel into
  the builder, and toggle **Plate A–H × 1–12**.

#### Cell Browser

**What it shows.** A scrollable gallery of individual segmented-cell
thumbnails, filtered by criteria — for eyeballing what a gate or phenotype
actually looks like at the single-cell level.

- **Needs** — a well selection (the gallery is scoped to the selected wells)
  and image ZIPs.
- **Controls** — the sidebar **criteria** filter (per-metric ranges) plus
  timepoint / FOV pickers; centre-row **channel toggles**, a **boundary**
  toggle, a **page size**, and **Load next** (the gallery fills incrementally).
- **Optimal use** — pair it with the Scatter Plot in Split View: gate a
  population on the scatter and watch the matching cells populate the gallery.

#### Playground

**What it shows.** An interactive testbed for the pipeline's
background-flattening / subtraction methods on a **single raw frame**, without
re-running the pipeline (it shares the pipeline's exact correction code).

- **Needs** — a loaded dataset with image ZIPs.
- **Controls** — pick a well / channel / timepoint / FOV; choose any flat-field
  + background method and tune its parameters; a **Raw / Flat-field /
  Corrected** toggle; LUT / colormap / binary + precomputed-mask overlay;
  pan / zoom; per-source LUT memory; a frame-local binary threshold; a hover
  pixel-intensity readout; a QC size-cleanup preview.
- **Optimal use** — dial in a correction method + radius here on a
  representative frame *before* committing to a full Analyze re-run with those
  settings.

### smFISH

**What it shows.** Single-molecule FISH spot detection on channels you
configured as smFISH, with a parameter sweep and an **Apply to All** threshold.
Operates on **one well at a time** (spot detection is per-frame).

- **Needs** — at least one channel configured as smFISH at pipeline time (its
  CSV carries a `_smfish_count` column) and image ZIPs. Selecting several wells
  collapses the view to a single well.
- **Controls** — the detection parameters + threshold sweep; **Apply to All**
  to push one threshold across the whole dataset.
- **Optimal use** — sweep the threshold on one representative well, confirm the
  spot overlay, then Apply to All.

### Review CSV

**What it shows.** A tabular view of the loaded per-well CSVs — the raw
per-nucleus rows behind every plot.

- **Controls** — the **Well** picker; column filtering; **double-click a row**
  to jump to that cell in the Segmentation tab.
- **Optimal use** — sanity-check outliers: filter to a suspicious metric range,
  then double-click into Segmentation to see the actual cell.

### Sample Definitions

**Sample Definitions** is where you tell the app what your wells *mean*. Its
sub-tabs are led by **Cell Gating**:

#### Cell Gating

**What it is.** The single place that decides which cells are *included* and
which count as *on* for fraction-on statistics — the gating layer every plot,
statistic, and export consumes. Its fluorescence CDFs anchor the bottom of the
tab and respect the crop region + QC gate, so you can place thresholds by eye.

- **QC debris gate** (min/max area + solidity) — the fundamental layer, *always*
  applied on top of everything else.
- **Inclusion** — per-channel **FluorGating** thresholds, or tick **Use
  scatter-plot gates** and pick a filtering gate to let the [Scatter
  Plot](#scatter-plot)'s rectangular gates define inclusion instead.
- **Fraction-on** — per-channel **ThreshFracOn** cutoffs, with
  **Auto-threshold all channels** (an Otsu pass over per-cell + matched-
  background pixels) to derive sensible defaults, and **Optimize ThreshFracOn**
  — pick two wells, a timepoint, and a Min / Max / # Points range, and the
  optimizer finds the threshold that maximises the *relative* fraction-on gap
  `|fa − fb| / max(fa, fb)`, opening a non-modal validation graph so you can
  sanity-check the pick. Independent Control / Baseline pickers let you optimize
  on a fold-change-adjusted fraction-on.
- Inclusion and fraction-on have **independent** gate pickers, so you can gate
  on one set of gates and compute fraction-on from a different set.
- **Optimal use** — set the QC gate first, auto-threshold to get baseline
  ThreshFracOn values, then use Optimize on the one channel that separates your
  conditions.

#### Groups, Well Labels & Ratios

- **Groups (replicate sets)** — name a set of wells as replicates; every plot,
  the Statistics tab, and Batch Export can then treat them as one condition. A
  group **is** exactly one condition — its wells are its replicate set (no mixed
  sub-sets or solo wells inside a group). Build one with **+ Add** (a graphical
  plate picker — rubber-band select, row/column headers), or let **Quick
  Replicates** chunk the current selection into groups of a chosen size via the
  **Replicates per group** spinbox (default 2), walking the wells along the
  Rows / Columns direction. On the Groups plate every well is tinted by its
  **group's** colour, so all wells in a group read as one condition and match
  the group's row swatch.
- **Well Labels** — give wells / groups human-readable names; these show in
  every plot legend instead of the raw well token.
- **Ratios** — define a ratio channel (e.g. `GFP/MCHERRY`); it appears as a
  virtual channel in the channel selector everywhere.

#### Pipeline Info & Notes

Read the schema + channel tokens + FOVs + timepoints the dataset was built with
(**Pipeline Info**), and keep free-text **Notes** saved next to the data.

### Saved Plots

**What it is.** A per-dataset gallery of plots you have captured. Hover any plot
panel's top-right corner and click the **save** button to send that panel —
plus the exact parameters / thresholds that produced it — here.

- **Per-card controls** — **Restore** (reload the plot with its saved
  parameters), **Export**, **Rename**, **Delete**, and a collapsible parameter
  summary.
- **Export all** — write the whole gallery as a multi-page **PDF report** (a
  cover page plus one full-quality page per plot with its parameters). The
  Image Table grid can be saved here too.
- **Optimal use** — capture each figure as you finalize it, then export the
  gallery as one PDF to hand off alongside your analysis.

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

The finished bundle is named for its version, so a full build and a thin
feature-subset build are distinguishable at a glance: `dist/AllWell-8.8.9.app`
for the full app, or `dist/AllWell-8.8.9-<profile>.app` for a subset (e.g.
`AllWell-8.8.9-thin.app`). The internal executable stays `AllWell`, so
launching is unchanged. To distribute it:

```sh
cd dist && zip -r AllWell-mac.zip AllWell-8.8.9.app
```

For a Universal binary (Intel + Apple Silicon in one bundle):

```sh
TARGET_ARCH=universal2 _Docs/_Installation/build_all_well.sh
```

This needs a `universal2` Python from python.org — the Miniforge Python is
architecture-specific and can't produce a universal binary.

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
- [`process_microscopy.py`](../process_microscopy.py) — the pipeline. It's
  also a usable CLI (`python process_microscopy.py --help`).
- [`_Docs/_Installation/`](../_Docs/_Installation/) — the PyInstaller spec
  + build script for producing the macOS bundle.
- [`_Docs/requirements.txt`](../_Docs/requirements.txt) — the pinned
  pip-managed dependencies, with environment-setup comments.
