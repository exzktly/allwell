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
   - [Home](#home) (Overview · Cell Gating · Groups · Well Labels · Ratios · Pipeline Info)
   - [The Review window at a glance](#the-review-window-at-a-glance)
   - [Controls shared by every tab](#controls-shared-by-every-tab)
   - Plotting: [Bar Plots](#bar-plots) · [Line Graphs](#line-graphs) · [Scatter Plot](#scatter-plot) · [Distribution](#distribution) · [Heat Map](#heat-map)
   - [Statistics](#statistics) · [Multivariate analysis](#multivariate-analysis) · [Spatial analysis](#spatial-analysis)
   - [Image Browser](#image-browser) (Segmentation · Image Table · Cell Browser · Playground) · [smFISH](#smfish) · [Review CSV](#review-csv)
   - [Saved Plots](#saved-plots) · [Batch export](#batch-export)
   - [Starting over](#starting-over)
6. [Datasets mode — dataset-level tools](#datasets-mode--dataset-level-tools)
   - [Convert OME-TIFF](#convert-ome-tiff) · [Concatenate datasets](#concatenate-datasets) ·
     [Validate & Repair Dataset](#validate--repair-dataset) ·
     [Subset / Split Dataset](#subset--split-dataset) ·
     [Zip Loose Folder](#zip-loose-folder) ·
     [Merge Plates](#merge-plates) ·
     [Job Queue](#job-queue)
7. [Filename schema](#filename-schema)
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
- **Datasets tab.** Dataset-level operations that work on whole folders rather
  than one plate at a time: **Convert OME-TIFF** de-interleaves a single packed
  OME-TIFF into a dataset folder; **Concatenate datasets** appends one
  dataset's timepoints after another's — for when the same plate was imaged in
  two separate sessions and you want one continuous timelapse; and
  **Validate & Repair Dataset** checks whether an Analyze run actually
  finished and fixes metadata drift without re-running the pipeline; and
  **Subset / Split Dataset** copies a filtered slice of wells/timepoints/FOVs
  into a new folder, leaving the source untouched; **Zip Loose
  Folder** groups a folder of loose TIFs into per-well zips with a live
  preview before converting; and **Merge Plates** joins two different plates
  along the well axis into one dataset.
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

The output is `dist\AllWell-<version>\AllWell.exe`, where `<version>` is
the string in `version.py` (a feature-subset build built with
`$env:ALLWELL_PROFILE` appends the profile name too). The spec's macOS
`.app` `BUNDLE` step is a no-op off macOS, so no `AllWell.app` is
produced.

---

## Quick start

A common end-to-end run, from raw images to plots, takes five clicks:

1. Launch the app and switch to **Analyze** (title-bar segmented control).
2. **Open input folder** — point at the folder of raw TIFs (or a folder of
   per-well ZIPs). All-Well briefly scans the filenames and **auto-fills the
   filename schema and channel tokens** it detects (including a best-guess
   nuclear/fluorescent/phase role per channel). The status line under the run
   controls names what it found and which channel it gave the segmentation role,
   because the filled-in fields otherwise look exactly like ones you typed —
   check them and adjust if the guess is off. Note that channels are listed in
   filename order, which is usually alphabetical rather than the order they were
   acquired in.
3. Confirm the schema and channel tokens. Click **Run**.
4. When the log prints "Processing complete" the app auto-switches to
   **Review** and loads the output folder.
5. Pick wells in the plate map; the plots redraw immediately.

Reopen a previous output folder any time from the dataset control at the top of
Review's left rail — it names the folder you have loaded and drops `Open` — or
with `Ctrl+O`.

---

## Analyze mode — running the pipeline

The Analyze tab is one tall scrollable form. Top to bottom:

### 1. Filename schema

A colon-separated list of field names, applied left-to-right against the
underscore-separated tokens in each filename. The `Schema string` field is
editable directly; the **Position** dropdowns above it are the same value seen
the other way round, and the two follow each other — pick a field and the
string rewrites, type a string and the dropdowns reset. When you pick an input
folder All-Well guesses this schema (and the separator) from a sample of the
filenames and fills it in — a starting point you can correct, not a lock.

The Datasets pane's [Zip Loose Folder](#zip-loose-folder) tool asks for the
schema with this same control, since the archives it builds are what Analyze
reads next.

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

### 2. Folders

The input folder is resolved by
[`services/input_resolution_service.py`](../services/input_resolution_service.py).
**Either the dataset folder or the `in/` inside it works** — the two are read the
same way, so you never have to pick the inner one:

- A folder named `in/` → input = that folder, output = the sibling `out/`.
- A folder containing an `in/` subfolder → input = that `in/`, output =
  `<selected folder>/out`.
- Inside whichever of those is the input: per-well ZIPs or per-well folders are
  used as they stand; loose TIFs are grouped into per-well folders first;
  loose TIFs dropped next to existing per-well ZIPs are folded into those ZIPs
  (that is how you add a new timepoint or FOV to a plate you have already run).
- A folder of loose TIFs with no `in/` → `WellPlateZipper` is invoked to group
  them into `in/` according to your schema, then the pipeline runs against
  those.

The line under the folder field always says which of those will happen, and it
is generated by the same code the Run button uses — if it shows a `✓`, Run will
do what it says. Two cases have no `✓`: an `in/` folder that exists but is empty
(it tells you so, rather than claiming there is no `in/` folder at all), and a
selected folder with nothing usable in it. An `in/` holding files All-Well
cannot recognise as wells is still accepted and handed to the pipeline; the line
says that nothing well-named was recognised, so a run that then finds no images
has said why before it started.

If your plate arrived as a **single packed OME-TIFF** (one `.ome.tif` holding
every well, channel, Z-plane and timepoint), Analyze can't process it directly —
convert it to a dataset folder first in the **Datasets tab → Convert OME-TIFF**
(see [Convert OME-TIFF](#convert-ome-tiff)), then open that folder here. Pointing
Analyze at a raw `.ome.tif` just shows a reminder to convert it.

You can also explicitly override the output folder.

**If that output folder already holds a run, a card above the form says so
before you press anything.** It names when the last run against this folder
started and how it ended — completed, failed, stopped, suspended, or *never
finished*, which means All-Well was closed while it was going — followed by how
many wells it recorded as finished, failed, or produced on a remote machine and
never downloaded. A moment later a second sentence adds the real coverage: how
many wells are complete, and how many of the input's field groups the output
already accounts for, so you can see how much a re-run has left to do. If new
images have been dropped in since, it also says what the run will do about them,
in the same words the run log will use.

**Load these settings** fills the form with the settings that produced that
output — from the run's own record where there is one, otherwise from the
`pipeline_info.json` beside it, so it works on plates you ran long ago. Nothing
changes until you press it, so anything you have already typed is safe. The
reprocessing options — **force**, the per-well force list, and **incremental** —
are deliberately *not* restored: they describe what the last run was told to do,
not what you want this one to do. A plate that was forced once would otherwise
be forced again the moment you loaded its settings back. Both buttons sit on
their own row beneath the report, so the text has the full width of the sidebar.
**Dismiss** hides the card for that folder; it comes back if you point somewhere
else, or once a run has written there again.

### 3. Channel tokens

- **Nuclear (segmentation) channel.** The token used to find the image
  StarDist runs against (also gets quantified — its intensities appear in
  the CSV like any other channel).
- **Fluorescent channels.** One or more tokens whose intensities you want
  quantified. Each token gets its own CSV column set (`<token>_mean_intensity`,
  `<token>_total_intensity`, etc.).
- **smFISH channels** (optional). Channels processed for spot detection.
  Each smFISH channel appears with both a regular intensity column set and
  a `_smfish_count` column.
- **Phase channel** (optional). Tick **Phase** on the fluorescent-channel row
  holding your brightfield / DIC image. That channel skips flat-field and
  background correction entirely — those corrections have nothing to remove from
  a brightfield frame and only add artefacts — and is saved from the original.
  It is still quantified like any other channel. At most one channel can be
  Phase, and it cannot also be smFISH.

  **All-Well pre-ticks it when a channel's name reads as brightfield** — `DIC`,
  `BF`, `Phase`, `Brightfield`, `Trans` and the usual spellings around them,
  including prefixed and numbered forms like `w1BF`, `BF2` and `DIC1`. A short
  hint has to stand on its own between the letters around it, so a fluorescence
  channel that merely contains those letters — `Ca-Indicator`, `Transferrin647`,
  `BFP` — is left as an ordinary channel. The guess is only a
  starting point: check the tick before you run, because a fluorescence channel
  marked Phase is quantified from uncorrected images and nothing downstream says
  so.

  **The Phase channel cannot also be the segmentation channel**, except under the
  `cellpose_labelfree` method, which exists to segment it. Because Phase skips
  correction, segmenting it with a fluorescence routine means running that
  routine over an uncorrected brightfield image; the run would finish and write a
  complete-looking set of CSVs measured on the wrong picture. Setting both to the
  same channel is refused before the run starts, with a message naming the
  channel.

### 4. Pipeline options

- **Segmentation method.** `stardist_nuclei` (default — segment nuclei
  only), `stardist_seeded_watershed_cell` (StarDist nuclei + watershed
  cytoplasm segmentation).
- **Cytoplasm token** (only used by the seeded-watershed method).
- **Top-hat radii.** Nuclear and per-fluor radii in pixels. `0` or
  `--no_tophat_*` flags disable per-channel.
- **Min nucleus area** in pixels (rejects garbage detections).
- **Nucleus probability** and **Overlap (NMS)** — the two StarDist detection
  thresholds, shown for both StarDist methods. A field left **blank** means the
  run uses the threshold built into the pretrained model.
  - **Nucleus probability** (0–1) is how confident StarDist must be that
    something is a nucleus. **Turn it down** when the run is missing real cells —
    dim, out-of-focus or faint nuclei come back. **Turn it up** when debris is
    being counted as cells.
    The field **starts at 0.2**, which is more sensitive than the pretrained
    model's own ~0.479 — so a run you start without touching it finds more
    nuclei, faint ones included, than the model's default would. Clear the field
    to go back to the model's own value. The Playground's matching control still
    starts blank, so an untouched preview there is *not* what an untouched run
    here would produce.
  - **Overlap (NMS)** (0–1) is how much two detected nuclei may overlap before
    they are merged into one. **Turn it up** on confluent, touching cells so
    neighbours stay separate; **turn it down** if one nucleus is being counted
    twice.
  - Both take a value between 0 and 1; anything else is ignored and the model's
    own default is used. Rather than guessing, tune them against a real image in
    **Review → Image Browser → [Playground](#playground)**, which segments with
    these same two values and can open any image straight from disk — then type
    what worked into the two fields here.

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

### 6. Auto-threshold — picking each channel's "on" cut

Collapsed by default. When a run finishes, All-Well can choose a default **on**
threshold for each channel and store it with the dataset, so the Cell Gating tab
opens with sensible values instead of a flat 50 everywhere.

- **Calculate cell-gating thresholds when the run finishes** — on by default.
  Turn it off and no thresholds are written; the Cell Gating tab starts from its
  own defaults and you set them by hand.
- **Method** — how each threshold is chosen:
  - **Otsu (whole dataset)** — the default. Splits each channel's per-cell
    signal from image background, pooled across the whole plate. Needs no
    controls, but it cannot tell which cells are *biologically* positive, so it
    works when a channel's cells are cleanly brighter than background and not
    otherwise.
  - **Positive / negative controls** — the cut that best separates a well you
    know is ON from one you know is OFF. Usually the better answer, because it
    is anchored to biology rather than to pixel statistics.
- **Positive control well** / **Negative control well** — shown for the control
  method, which needs both. Switching back to Otsu hides them but keeps what you
  typed, so you can flip between the two methods without re-entering wells.

Under the control method the run is blocked if only one well is filled in, if
either is not a valid 96-well position, if the two are the same well, or if
either is missing from the input folder. Under Otsu the fields are dormant and
never checked.

**The segmentation channel is always set to 0.** That channel exists to find
cell boundaries, not to be gated on, so a threshold for it is meaningless. Zero
means every cell counts as "on" for that channel, which is how it is
neutralised — it is not the same as leaving it unset. Like every other value
here it is only a default: a threshold you have already set on the Cell Gating
tab is never overwritten by a later run.

One thing the software cannot check for you: the two control wells are compared
symmetrically, so entering them the wrong way round still produces a threshold.
The run log warns when the positive control ends up with the *lower* fraction of
on cells, which is the tell.

**Read the control method's log line as two numbers, not one.** It reports the
threshold it chose, the fraction of cells on either side of it, and a *supported
gap* — how well the cut separates the two wells, weighted by how many cells are
actually behind the claim, so a cut that only one or two positive cells clear
scores low rather than perfect. A second line follows whenever several candidate
thresholds scored identically: it says how many, and across how wide a range of
intensities. Fraction-on changes in steps, so that is common and not a fault —
but it means the reported number is the lowest of a range rather than a single
best answer, and a range spanning a large part of the channel's intensities is
worth a look before you trust the default.

**If the Cell Gating tab opens with no thresholds, read the end of the run log.**
The Otsu pass takes its samples from the per-well output archives, pairing each
channel's corrected image with the segmentation mask of the same field and
timepoint — which means reading those coordinates out of the filenames using the
filename schema you set. It does not require the schema to name every slot: a
layout with no field token, or no timepoint token, works exactly as well. What it
cannot do is work with a schema that does not describe the filenames at all, and
in that case it says so, naming the schema and an example filename. The lines to
look for, in order of what they mean:

| line | meaning |
|---|---|
| `Auto-threshold defaults written for N channel(s): …` | worked; those values are now the Cell Gating defaults |
| `no threshold could be estimated for any of …` | nothing was computed — the lines above it say why |
| `none of its N image member(s) could be placed by the run's filename schema …` | the schema does not match the filenames |
| `… holds N member(s) but no '_labels' segmentation image` | that well's archive carries no segmentation mask, so no cell could be sampled from it |
| `produced no new defaults (every channel already has a stored value)` | nothing to do; the dataset already had thresholds |
| *(no `Auto-threshold` line at all)* | the run ended before the pass — check for an earlier failure |

The last routine case is worth knowing: thresholds already stored for a channel
are never overwritten, so re-running a plate into an output folder that already
has them changes nothing. Clear the values on the Cell Gating tab first if you
want a re-run to recompute them.

### 7. Shape metrics

Collapsed by default. Per-cell morphology, measured from each nucleus mask at
quantification time and written as `shape_…` columns in the measurements CSV.
The section holds a single dropdown; tick the features you want.

- **Selectable features** — Area, Perimeter, Eccentricity, Solidity, Extent,
  Major Axis Length, Minor Axis Length, Orientation. **Area, Eccentricity,
  Solidity and Perimeter** are ticked when the form opens.
- **Always measured, whatever the dropdown says** — the two centroid columns
  (`shape_centroid_row` / `shape_centroid_col`), `shape_circularity`, and
  `shape_solidity`. Features elsewhere in the app depend on them, so they are
  not left to a per-run choice: centroids are what cross-timepoint cell tracking
  links on and what the [Cell Browser](#cell-browser)'s region delete and the
  crop rectangle test cells against on frames whose masks are not loaded, and
  the Quality Control debris gate filters on size, **solidity** and
  **circularity** — a gate you can edit after the run precisely because those
  columns are always there. Solidity appears in the dropdown as well (it is
  ticked by default); unticking it slims nothing, since the run adds it back.
- **Choosing** — these are measured once, at run time, and cannot be added to a
  dataset afterwards without re-running the pipeline, so tick anything you might
  plot. The cost is a few extra CSV columns, not extra segmentation work. What
  you tick here is what the Review pane's [Multivariate](#multivariate-analysis)
  and [Spatial](#spatial-analysis) tabs can offer as an axis.

### 8. Output options

Collapsed by default.

- **CSV prefix.** The stem of the per-well measurements files the run writes;
  `measurements` by default, giving `measurements_<WELL>.csv`. Change it to keep
  two differently-configured runs distinguishable inside one output folder.

- **Check completeness when the run finishes.** On by default. After the last
  well, the run compares what each well's input archive held against what its
  measurements CSV and output archive actually cover, and records the answer in
  `pipeline_info.json`. It costs seconds — the files were written moments
  earlier and are still in the operating system's cache — and it is what lets
  Review tell you afterwards whether the plate is whole. This matters because a
  well that lost some of its fields still writes a CSV and an archive that look
  exactly like a complete well's on disk; without the record there is nothing to
  compare them against. Turning it off leaves the dataset with no completeness
  record, and Review then says the check was turned off rather than guessing.

Packaging is not configurable: inputs are always read from per-well `.zip`
archives and outputs are always written as per-well `.zip` archives. See
[What the pipeline writes](#what-the-pipeline-writes) for the full layout.

### 9. Compute options

- **TF threads.** TensorFlow intra-op parallelism (CPU only; ignored on
  Metal). Defaults to 4.
- **Workers.** Number of wells processed in parallel.
  - On Apple Silicon Metal the default is 2 — Metal serialises GPU calls
    across processes, so more workers don't help GPU throughput.
  - CPU-only: defaults to `floor((cpu_count − 1) / tf_threads)`.
- **CPU-only.** Forces TensorFlow to ignore the GPU.
- **Start even if the disk looks too small.** Overrides the space check that
  refuses a run it estimates will not fit. Leave it off by default. Tick it when
  a refusal looks wrong — that estimate sizes from the largest image in the
  plate and gives no credit for compression, and the free-space figure it is
  compared against is what the filesystem reports as claimable *right now*,
  which on macOS reads lower than Finder's (Finder counts purgeable caches and
  snapshots that are not claimable until something asks for the space). So the
  two numbers can meet and refuse a run that would have finished. When the
  volume really is tight, pointing **Scratch disk** at a roomier one or lowering
  **Workers** is the better fix: workers multiply the staging need directly.
  This box is the one Compute option that is *not* restored by **Open
  pipeline_info…** — it disarms a check that exists because a volume filling
  part-way costs hours of work, so it is a decision you make per run rather than
  one a folder you happened to open makes for you.
- **Force re-run.** Re-processes wells whose output ZIPs already exist. It also
  discards the per-FOV checkpoints a stopped run left behind, so forcing really
  does recompute: it used to re-run the well and then resume those checkpoints,
  which meant changing a threshold and ticking Force gave you back numbers
  identical to the ones you were trying to replace. On a remote run it carries
  all the way to the end: the run will not decide the remote's existing outputs
  make the work unnecessary, the fetch will not skip a well because its local
  copy looks complete, and — if a well's output file comes back with the same
  size and timestamp it had before the run — the run says so by name and fails
  rather than reporting success over an artefact it never replaced.

  **You usually will not need it for a settings change.** A well records which
  configuration produced it, and a re-run whose segmentation method, thresholds,
  channel set, corrections or filename schema differ recomputes that well on its
  own rather than reporting it up to date. Wells written by a version of All-Well
  that predates that record carry no such marker and are still treated as up to
  date, so for those — a plate you last analysed some time ago — Force is still
  how you make a settings change take effect.
- **Force these wells.** Comma-separated wells to reprocess in full, whatever
  else the run decides. This is the only thing that recovers a well whose
  `<WELL>_out.zip` exists but whose measurements CSV is missing: its own
  processed images count as already-done work, so an incremental run reports
  that well "up to date" and skips it forever. Forcing it re-segments the well
  and overwrites its outputs; every other well is unaffected. Validate &
  Repair's completion handoff fills this in for you with exactly those wells.
- **Incremental — process only missing FOVs.** Finishes a partial or crashed
  run instead of skipping past it. Normally a well whose `<WELL>_out.zip` and
  CSV both exist is skipped whole, however few of its `(FOV, timepoint)` pairs
  actually made it in — so pressing Run again after a crash does nothing for
  the wells that need it. With this ticked, each well's already-recorded pairs
  are read from the output folder and only the missing ones are processed,
  then merged into the existing CSV and archive. Wells with no output at all
  process in full; complete wells are left untouched. Datasets → *Validate &
  Repair* → **Complete this run in Analyze** ticks this for you. Force re-run
  overrides it.

### 10. Scratch disk

Collapsed by default. One field plus **Browse…**, and the last option above the
Run controls.

A run does most of its file churn somewhere other than your input and output
folders: each well's archive is extracted, intermediate images are written, and
per-FOV checkpoints are kept so a crashed run can be completed later. All of
that lands on the scratch disk.

- **Leave it blank** and the run uses `~/.allwell/scratch`. That is the right
  answer on a machine with room on its system volume.
- **Keep it on fast local storage.** Pointing it at a network drive (NAS, SMB
  share) puts the run's GB-scale temporary I/O on a volume whose dropped
  sessions invalidate every open file handle — the configuration behind wells
  failing with *Bad file descriptor*. The field deliberately does **not** follow
  the input folder for this reason.
- **It is also the fix for a run that will not start on space.** When the
  pre-run space check refuses a run, moving scratch to a roomier volume (or
  lowering **Workers**, which multiply the staging need directly) is the real
  fix; *Start even if the disk looks too small* under
  [Compute options](#9-compute-options) only disarms the check.
- **It fills up.** A run that dies mid-way with a disk-full message, or that
  finishes with wells reported incomplete, usually ran out of room here rather
  than on the output volume — scratch defaults to your home drive, so a dataset
  on a roomy external disk can still exhaust a full internal one. The per-FOV
  tracebacks in `<output folder>/errors/` say so. Free space or move scratch,
  then re-run with **Incremental**.

### 11. Running on a remote host

The last thing the form asks before the run buttons is a **Run on:** dropdown.
It lists `Local` plus every SSH host configured in
`~/.config/allwell/remotes.toml` (on Windows, `%APPDATA%\allwell\remotes.toml`
takes precedence, and the `ALLWELL_REMOTES` environment variable overrides
both), and it defaults to the first configured remote when there is one.
Picking a remote changes nothing
about the form above it — the same schema, channels, folders and options are
sent — only where the segmentation runs. Results come back into the same output
folder, so Review cannot tell the difference afterwards.

**Whatever you pick stays picked.** Adding, editing or deleting a host — or
running a bootstrap, successful or not — rebuilds the dropdown from the file,
and the rebuild keeps the target that is on screen. A remote you have selected
that still exists stays selected; one you have just deleted drops back to
`Local`. The "first configured remote" default above decides the *opening*
state only, when the pane is built. It is never re-applied afterwards, so
opening **Manage…** to set a cluster up for later cannot move today's run onto
it.

**Manage…** opens the remote-host dialog: add, edit, delete, test, or bootstrap
a host (bootstrap installs the pipeline and its Python environment on a machine
that has never run All-Well). A malformed `remotes.toml` never blocks the app —
the run log carries a warning and the dropdown stays on `Local`. Inside the
dialog, a file that cannot be read or parsed is reported in a message naming the
error rather than leaving Edit…, Test and the list silently doing nothing.

**Test** in that dialog, and **Test SSH** in the add/edit form, both run in the
background: an unreachable host takes up to fifteen seconds to time out and the
rest of the app — including a running plate's log — keeps working meanwhile.
The same is true of **Verify** in the SSH-key wizard.

**On a node it schedules** requires a scheduler command. Saving that choice with
the command box empty would mean *run on the SSH host itself*, which on an HPC
login node is the one thing the setting exists to avoid, so the form refuses it
and says so. Switch **Run compute** back to *On the remote machine itself* if
that is what you meant.

**Remote scratch dir**, under *Advanced (remote paths)* in that dialog, is worth
setting on a cluster. It is where the pipeline keeps its per-well working files
*on the remote* — every extracted input image and roughly six uncompressed
intermediates per field, for as many wells as run at once. Left empty it uses
the remote's home directory, which on an HPC system is exactly the volume with
the small quota, and a run that overflows it fails partway through with a
message about space. Set it to your site's real scratch: `$TMPDIR`,
`$SLURM_TMPDIR` and `/scratch/$USER` all work as written — the value is expanded
on the remote, not here. This is separate from **Scratch disk** in the form
above, which governs local runs and is not used when the pipeline runs remotely.

**Empty-well correction works with batching.** If you set background or flat-field correction to **empty_well** and name a blank well, that well is uploaded once, on its own, before the batches start — every batch then corrects against the same reference. A blank that still needs processing is run once, with the last batch; one that a re-run has already completed is uploaded as a reference only and not processed again. The blank is never diffed or skipped by the incremental filters either, since it is the input the correction is built from rather than a well whose output you are waiting for. If it cannot be found at all, the run stops and says which well it looked for and where, instead of quietly producing uncorrected numbers.

**"Rewriting N well archive(s)" in the log** means the plate holds images this
run does not want — most often a transmitted-light or brightfield channel that
is in the files but not in your channel list, and on a re-run it can instead be
fields a previous run already processed. Those archives are rebuilt without them
so the upload is smaller, and the log now names which channel and how many
frames. If the saving would be trivial — a couple of small frames out of a large
well — it says so and uploads the archive untouched instead, because copying it
locally to save that would cost more than it saves. An archive with nothing to
drop is never copied and never mentioned.

Your own machine's home volume is not used by a remote run either. Wells arrive
into a hidden `.allwell-transit` folder inside your chosen output folder and
move into place from there, so the only volumes a remote run needs room on are
the ones holding your input and your output. If that output folder cannot take
the folder — a read-only share, say — the run falls back to `~/.allwell/scratch`
and says so in the log rather than doing it quietly.

Choosing a remote reveals a **Remote options** group, hidden for a local run
because none of it applies:

- **Batch size (0=off).** `0` uploads the whole plate up front and runs the
  pipeline once across it — the lowest total runtime. Any positive `N` processes
  `N` wells at a time, which bounds peak disk on the remote to roughly `N` wells
  of input plus about one well of output, at the cost of reloading the
  segmentation model once per batch (a few seconds each). Set it from the
  remote's free disk, not from its core count.
- **Auto-delete remote files when done.** On by default. After a run the app
  verifies succeeded, it deletes both the per-run output directory and the
  cached input on the remote, so a shared machine does not fill up. It never
  deletes failed work. Unticking it leaves everything in place.
- **Dry-run cleanup.** Only selectable while auto-delete is on. Logs the path,
  file count and total bytes of every remote directory that *would* be removed
  at end of run, and removes nothing — worth one run on a shared host before
  trusting auto-delete on it.

Both toggles keep what you set them to. Switching **Run on:** to `Local` hides
the group, because none of it applies to a local run, and switching back shows
it exactly as you left it — a dry-run tick is not dropped, and an auto-delete
you turned off is not turned back on, by a glance at `Local`.

What a remote run does before it uploads (which archives it rebuilds, which it
sends untouched) and what it reports on the way back are covered by the run log
and by `remote_fetch_report.txt` in the output folder.

### 12. Run / Suspend / Stop / Log

- Click **Run**. The log streams the pipeline's stdout/stderr live.
- The **plate map** above the log colours each well by the stage it has reached.
  A well that has started is drawn as a dial rather than a flat circle: the
  darker part of it is the work already done, the lighter part what is left,
  filling clockwise from twelve o'clock. So you can see at a glance whether a
  slow well is nearly through or has barely started. A well with a dial carries
  no cog — the dial and its colour already say it is working. That holds for
  every plate map in the app, including the ones on the Datasets tools.
- The dial follows whichever step the well is actually in. While the well is
  being computed it fills with the fields measured; while its input archive is
  being uploaded, or its results downloaded, it fills with the bytes moved. It
  never runs backwards: once a well's archive is uploaded the dial stays full
  while it waits its turn on the remote — its colour is what says the well is
  waiting rather than finished — and it empties only when that well actually
  starts computing, then fills again with the fields measured. Wells still to
  be started have no dial, and neither does a failed well: a part-filled ring
  beside the ✕ would suggest it was still going.
- **The headline says what the run is doing now.** It follows the run from step
  to step rather than holding the last thing that had something to say —
  *Grouping 12 / 40 wells*, then *Grouped — preparing the upload…*, *Uploading 6
  / 40 wells*, *Queued on the scheduler — waiting for a node…*, *Processing 18 /
  40 wells*, and *Processed 40 well(s) — finalizing…* while the auto-threshold
  pass, the last downloads and the remote cleanup finish. A local run goes
  straight from grouping to processing. Results are fetched in the background
  while later wells are still being computed, so the headline keeps counting the
  wells being processed and leaves the fetching to the Download row on the stage
  rail and to the status line.
- The line under the headline reports the plate-wide transfer while one is
  running (`Transferring… 42%`), and clears itself the moment the pipeline
  starts computing rather than lingering on whatever percentage the transfer
  last reported.
- Hover a well for its exact numbers — the stage it is in plus its field count
  (`FOV 7/12`) or transfer percentage. On a remote run an uploaded well waiting
  its turn has its own colour, distinct from one being computed — see the
  **Wells** legend beside the plate.
- The **headline and progress bar** count wells, and count each well once. A
  well already finished by an earlier run is counted too — it is part of the
  plate you asked for, so a run that only has ten wells left to compute still
  reads against the whole plate rather than against the ten. If the same well
  reports finishing twice (which a batched remote run can do when a batch's
  input files outlive it), the log says so and the count does not move: the bar
  reaching the end always means every well is accounted for.
- When a run ends with wells unaccounted for, the headline says which — "Complete
  — 28 of 40 well(s) processed; 12 never reported completion" — and the bar is
  left short of full, with a warning in the log pointing at the `errors/` folder
  in the output directory. A finished-looking bar over a part-finished plate is
  the one thing the run view will not show you.
- **The full log sits under the dashboard, open.** Everything the run says,
  line by line, appears there as it happens — and some of it appears nowhere
  else: why a run refused to start, which volume ran out of room, the path of
  the `errors/` folder when wells failed. Untick **Show raw log** in the
  Pipeline Output header if you would rather watch the dashboard alone; the
  app re-opens it on its own only when a run fails with an explanation you
  would otherwise not see, and hands the toggle back at the next run.
- During a remote transfer the **status line** beside the progress bar carries
  the live figure (how much has come across, how long it has taken) and updates
  in place. The log gets the events — each well starting, each one landing, the
  final total — plus a "still transferring" note about once a minute, so a run
  left overnight still leaves a readable trail without burying it.
- **Waiting for a cluster scheduler.** If your remote host runs the pipeline
  through a scheduler (an `srun …` compute launcher — set up in the remote host
  dialog), the run does not start the moment the upload finishes: it joins a
  queue, and on a busy cluster that can be minutes or hours. The headline says
  so — *Queued on the scheduler — waiting for a node…* — the status line beside
  it counts up, *Waiting for the scheduler — 4m12s (job 4812345)*, and the log
  repeats it every couple of minutes together with the exact request that was
  made. A batched run queues once per batch, and the log names
  which batch is waiting.
  - **Use the job number.** It is the one you would pass to `squeue -j 4812345`
    on the cluster to see why the job is pending. `Priority` or `Resources`
    usually means simply wait; anything naming a limit or a partition usually
    means the request cannot be satisfied as written — which is why the log
    repeats the request beside the wait.
  - All-Well does not query the scheduler itself, so it can tell you the run is
    queued and for how long, but not *why*. The number is what lets you ask.
- **Suspend** winds the run down cleanly. It finishes the fields already being
  computed, then lets every well that got any work done write its results, and
  remembers what you were running. Use it when the run is fine but you need the
  machine back — it takes a little longer to stop than **Stop** does, because it
  is waiting for those fields to land. You can close All-Well afterwards; next
  time you open the Analyze pane a card at the top of the sidebar offers to
  carry on. It reads top to bottom: what state the run is in (*Suspended run*),
  which plate and when (*Plate_07 · suspended 24 Aug 14:32*), what resuming will
  do, and what the run recorded. **Resume**, at the bottom right, continues it,
  computing only the field/timepoint groups still missing. **Discard** beside it
  forgets the offer and deletes nothing — the results already written stay
  exactly as they are, and you can still finish the plate later by re-running
  that input with **incremental** ticked.
- **Stop** signals the entire pipeline process group (sub-workers included) and
  discards whatever is in flight. Press it a second time during a Suspend if you
  do not want to wait for the in-flight fields.
- Either way the wells already finished keep their results, so nothing you have
  waited for is thrown away — Suspend just also keeps the fields in progress and
  saves you re-entering the form.
- **A run that does not finish is offered back the same way, whatever ended it.**
  You do not have to have pressed Suspend: a run that failed, one you stopped,
  and one cut short because All-Well closed or crashed all leave the wells that
  got through on disk, and all three are offered. The card's first line names
  which of them it was — *Failed run*, *Stopped run*, *Suspended run*, or
  *Interrupted run* for one cut short because All-Well closed or crashed — and
  the lines under it name the plate, say when the run started and how it ended,
  and report how many wells it recorded as finished, failed, or produced on a
  remote machine but never downloaded. **Resume** picks it up from there.
- The offer is about **the folder in the input field**. If you have not chosen
  one yet — the state the pane opens in — it offers the most recent unfinished
  run on any plate, which is how you carry on with something from yesterday.
- The card directly beneath it reports what the folder already holds, and while
  the offer above is describing the same folder it does not repeat it: it drops
  its own account of the run and shows only what the offer cannot — how many
  wells are already complete, and what this run would do about images added
  since the last one. Dismiss the offer and that account comes back, since the
  report is then the only thing left saying what happened.
- On success the app switches to Review and loads the output folder.
- The **Job Queue** (Datasets pane) works the same way. **Pause queue** winds
  the running job down and keeps what it finished; the job stays in the list
  marked *suspended*, and **Start queue** picks it up again — including after
  you quit and reopen All-Well. **Stop job**, beside it, cancels outright. If
  All-Well is closed or crashes while a job is running, that job comes back
  marked *interrupted* and is resumed the same way; jobs chained after it wait
  rather than being written off.

### What the pipeline writes

```
<output_dir>/
├── <well>_out.zip          ← masks + tophat-corrected fluorescence + overlays
├── <prefix>_<well>.csv     ← per-well measurement CSV (one row per nucleus)
├── pipeline_info.json      ← schema + channel tokens + FOVs + timepoints +
│                              gating thresholds + saved selections +
│                              the completeness record, etc.
├── errors/                 ← one traceback per failed field, when any fail
└── (transient tmp_<well>/  ← deleted on success)
```

**The completeness record.** `pipeline_info.json` gains a `run_check` block at
the end of every run, holding one entry per well: how many
`(field, timepoint)` groups its input held, how many its measurements CSV
covers, how many the CSV and the output archive cover between them, which are
missing, and whether that adds up to complete, partial, or images-with-no-
measurements. It is written *after* the last well, which is why its presence
means something on its own — the rest of `pipeline_info.json` is written before
the first image is read, so a run that died in its first seconds still leaves
that file behind looking perfectly normal.

The same block is written by [Validate & Repair](#validate--repair-dataset) when
its check finishes, which is how a dataset produced before this record existed
gets one. It carries which of the two wrote it, so a scan from last week never
reads as a run from last week.

Three things worth knowing about it. It describes the **whole dataset**, not
just the wells this run touched, so re-running to add a timepoint still leaves a
record covering every well. It is a **snapshot of the moment the run ended** — if
you repair a dataset by hand afterwards, the record still describes it as it was,
which is why the date it was taken is shown alongside it. And a run that was
**stopped** part-way records that it was stopped, rather than reporting the wells
it never got to as though they had failed.

Every archive the app writes — the `<well>_out.zip` results and the
`in/<WELL>.zip` inputs the Analyze tab packs for you — is built alongside its
final name as `__aw_tmp_<name>.pid<N>`, checked, and only then moved into place.
A run you kill (or a network share that drops) mid-write therefore leaves a
`__aw_tmp_…` file you can delete, never a half-written `<WELL>.zip`. A well with
no images to pack gets no archive at all rather than an empty one: an empty ZIP
is the file macOS refuses to open with *"Unable to expand … it is in an
unsupported format."* If you do hit that message on a `<WELL>.zip`, the archive
was truncated or emptied outside the app — delete it and re-run that well from
the original TIFs.

**Working over a network drive.** A NAS or file-server share is not a reliable
disk while a plate is being processed: under the load of a run its session can
drop and reconnect, and on macOS every file the app had open at that moment
becomes invalid — the error reads `[Errno 9] Bad file descriptor`. All-Well
keeps the bulk of a run's file churn off your chosen folders (its scratch
defaults to `~/.allwell/scratch`; the *Scratch disk* field overrides it), and
every remaining write on your input or output folder is retried a few times
before it is allowed to fail. You will see a `[retry] …` line in the log when
that happens — one or two is the app absorbing a blip, a steady stream means the
share is struggling and the run is at risk. If a write does fail for good, the
message names the folder and says that a network mount is the likely cause.
Remote runs are no different in this respect: the analysis happens on the
cluster, but your own machine still reads the whole plate off the input folder
to upload it and writes every finished well back to the output folder. The
reliable setup on a flaky share is to keep both folders on a local disk for the
run and copy the results across afterwards.

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

### Home

The page Review opens on. It answers the four things you want to know before
doing anything else: what this plate is, whether the run that produced it
actually finished, what it was run with, and what you have already done to it.

**Before you open anything** it shows the same "Load a directory." prompt every
other page in Review shows when there is no dataset — with an **Open a
dataset** button on it, since this is the page the app starts on. Everywhere
else that prompt points you at the **Open** button at the top of the left rail
(⌘O); here the button on the placeholder does the same thing, so the sentence is
left off rather than sending you across the window to press the other one. There
is one folder picker behind both. Nothing else on the page is drawn until a
dataset is open, because there would be nothing in it to read.

**It reports; it does not measure.** Working out how much of each well's input
its output covers means opening an archive and reading a whole CSV for every
well — far too slow for a page you pass through constantly, and a cached answer
that quietly ages is worse than none. So the run records the answer when it ends
(see [What the pipeline writes](#what-the-pipeline-writes)) and this page reads
it. That has one consequence worth holding on to: **the page describes the
moment the run finished.** If you have repaired or edited the dataset by hand
since, the page still shows it as it was, which is why the date the record was
taken is on the masthead. *Check this dataset now* hands off to
[Validate & Repair](#validate--repair-dataset), which re-reads it as it stands
and records what it finds — so a dataset that has never been checked fills this
page in once you have run that tool on it, and the masthead then reads *checked
\<date\>* rather than *run finished \<date\>*, because the two describe different
moments.

What is on it:

- **The plate.** One dot per well, coloured by what the run actually produced:
  complete (green), partial (amber, with an arc showing how much of it landed),
  failures recorded (red), images with no measurements behind them (blue — the
  same colour the Datasets tools use to flag an odd well), staged but never
  processed (grey). Those colours are the ones every other well map in the app
  uses for the same things, so a colour you have learnt once reads the same
  here. A well the record says nothing about — because no completeness check has
  been run, or because it produced nothing at all — is left plain, uncoloured:
  the legend beside the plate has a hollow swatch and a count for it, and the
  well's tooltip says which of the two it is. Every colour on the plate has a
  legend row, and no two rows share one.
  Clicking a well selects it, the same selection every other page uses — it does
  not navigate, so you can pick several. Hovering one says what its numbers are.
  There is no All / Invert / Clear row here, unlike the plate in the rail: those
  act on what gets plotted, and nothing on this page plots.
- **Notes.** The project notes, and the only place they are edited. Type and
  click away; it saves on its own. Whatever the numbers will not tell you in six months
  belongs here — which plate had the incubator alarm, which columns were
  re-plated, which channel bled.
- **The numbers.** Wells, timepoints, fields per well, shape features, and how
  much of the input the output covers, with the actual counts underneath rather
  than a bare percentage. A figure the page could not work out shows **—** and
  says why. It never shows a zero it did not measure: "nothing was recorded" and
  "the answer is none" are different, and a page that blurred them would be the
  exact problem this one exists to solve.
- **Health.** Only what needs attention. A dataset with nothing wrong says so in
  one line instead of listing zeros.
- **Run parameters.** What the run was told to do — segmentation method and its
  thresholds, the background method named in full and the flat-field mode,
  **the settings each of those used**, debris gating, where it ran and for how
  long. A threshold that came from the model's own defaults says so, rather
  than being shown as a number somebody chose. The settings shown are the ones
  that method actually reads: a radius for the morphological methods, the mesh
  ratio and sigma for `robust_mesh`, the small-structure cutoff for
  `fft_bandpass`, and the wells for an `empty_well` background or flat-field.
  Nothing else is listed, because a number beside a method that never consults
  it reads as though it had an effect. Where the nuclear and fluorescence
  channels were given different values, both are shown. A reference-well
  correction with no well named is flagged: nothing was measured, so the frames
  went through uncorrected, and the images look no different for it.
- **Your work here.** Replicate sets, plot groups, labelled wells, smFISH
  thresholds, ratios, notes — with what you have not started yet reading "None
  yet", because that is a real answer rather than a missing one.

Above all of it, one line saying what most needs doing next, and a button that
does it.

**When something is missing, that button finishes the run.** Failed wells,
partial wells, wells with images but no measurements, wells that were staged and
never processed — all of them open **Analyze** already set up to complete this
dataset: pointed at the project folder, configured from the run's own recorded
settings, with *Incremental* on so only the missing fields are processed, and
with the wells an incremental run would otherwise skip forever listed for a full
reprocess. You do not have to remember what the original run was told to do; the
handoff carries it, and Analyze says on its dashboard what it just set up.

**Opening a dataset always brings you here first**, whichever page you were on
when you last quit. Everything else about that session is restored as before —
it is only arriving with a new dataset that starts on Home, since that is the
moment there is most to tell you. Restoring a saved plot from **Saved Plots**
does not come here: it takes you to the tab that plot came from.

#### Telling the app what your wells mean

The rest of Home's tabs are where you *declare* things, rather than read them:
which cells count, which wells are replicates of each other, what to call them,
and what ratios to derive. **Import**, at the right end of the page's controls
row, merges definitions from a JSON file into what is already there; it is
hidden on Overview, where there is nothing to merge into. Everything on these
tabs autosaves to the dataset folder as you edit, so there is no Save button to
look for; to discard definitions, use **Reset analysis** in the title bar
([Starting over](#starting-over)), which lets you pick exactly which kinds to
drop.

#### Cell Gating

**What it is.** The single place that decides which cells are *included* and
which count as *on* for fraction-on statistics — the gating layer every plot,
statistic, and export consumes.

The tab is laid out as the two questions it answers, in the order they are
applied, and each question offers the same two sources as a pair of radio
buttons — you pick one or the other, because choosing the scatter option
*replaces* the per-channel numbers rather than adding to them.

- **Cell inclusion** — which cells count at all. The **QC debris gate** leads and is
  marked *always applied*: it composes with everything else on the tab. It reads
  as one row per measured quantity — Area, Solidity, Region area, Circularity —
  with its lower and upper bound in the `min` and `max` columns. Solidity has no
  upper bound and Region area no lower one, so those cells show an em-dash
  rather than an empty box you might think you were meant to fill in. Then
  choose the source of the inclusion decision —
  the per-channel **intensity minimum** (the `Include ≥` column below), or the
  rectangular gates drawn on the [Scatter Plot](#scatter-plot). Until you have
  drawn a gate there, the scatter option is greyed out and says so.

  **Area and Region area are not the same measurement**, and the difference is
  the reason both exist. **Area** is the size of the one cell — how many pixels
  that cell's own outline encloses. **Region area** is the size of the connected
  bright blob the cell is sitting in: the app takes every bright pixel, joins the
  ones that touch, and asks how big the resulting island is.

  They only differ when the segmenter has been fooled. It draws small, roughly
  round shapes, so it cannot represent one large lump of debris as a single
  object — it shatters the lump into many small "cells" instead. Each fragment
  then looks like a perfectly normal cell by size, which is exactly what an area
  gate cannot see through:

  ```
                        Area        Region area
    a real nucleus      253 px         253 px      ← sits alone
    one debris fragment  64 px         729 px      ← 9 of these share one blob
  ```

  No `max area` setting can remove that debris: each fragment (64 px) is
  *smaller* than the real nucleus (253 px), so any rule big enough to catch them
  deletes real cells first. A `max region area` of, say, 400 removes all nine
  fragments and keeps the nucleus, because it is judging the lump rather than the
  pieces. In one line: **Area is how big I am; Region area is how big the crowd
  I am standing in is.**

  The same reasoning is the caution. Cells that genuinely touch each other also
  form one crowd, so on a dense or confluent plate a tight `max region area` can
  start discarding real cells that happen to be packed together. Change it in
  steps and watch the QC line in the inclusion funnel below the table: if the
  number it removes jumps far beyond the amount of visible debris, the bound is
  catching neighbours rather than junk. Datasets processed before this
  measurement existed have no region area recorded, and the bound is ignored for
  them rather than excluding everything.

  The other two bounds are about shape, not size. **Solidity** is the cell's area
  divided by the area of a rubber band stretched round it, so low values mean
  ragged or fragmented outlines; there is no upper bound, because a perfectly
  smooth cell is not a defect. **Circularity** is 1.0 for a perfect circle and
  falls as an outline becomes jagged or elongated, and it is bounded on both
  sides — a maximum is useful when something in the image is *too* perfectly
  round to be a cell.
- **Fraction-on** — which of the included cells count as ON. Removes no cells. Choose
  the per-channel **ON cutoff**, or a scatter gate. The two questions have
  **independent** gate pickers, so you can include cells by one set of gates and
  compute fraction-on from a different set.

  This question has **two** cutoffs per channel, because the answer depends on
  what you are plotting. `ON ≥ (ThreshFracOn)` is the brightness a cell must
  reach, and applies when the selected Property is an intensity.
  `ON > (ThreshCountOn)` is the number of spots it must carry, and applies when
  the Property is an smFISH Count. They are separate values on purpose: one asks
  how *bright* a cell is, the other how many *spots* it holds, and the two live
  on scales that share no useful number. A channel with no spot-count column has
  no count cutoff to set, and shows an em-dash in that column.

  ThreshCountOn starts at **-1**, below the smallest tally a cell can have, so
  every cell counts as on — including cells carrying no spots at all, which are
  a real measurement and not missing data. Raise it to N to plot "the fraction
  of cells with more than N spots"; the mean, the fraction-on panel and the
  events panel all follow it.

Each question has **its own table** directly under it, with a row per
fluorescence channel and ratio metric. They are kept apart on purpose: the
numbers have the same shape but answer different questions — the inclusion floor
decides whether a cell is in the dataset at all, the ON cutoffs only decide how
the cells already in it are counted — and a single table serving both would need
a heading that names neither. The inclusion floor is always measured against
per-cell mean fluorescence intensity, whichever Property is selected elsewhere. A
readout channel's ON cutoff starts at 50; the nuclear (segmentation) channel
starts at 0, because an ON/OFF brightness cut has no meaning for the channel
nuclei are *found* with — every segmented cell is by construction a detection in
it, so a non-zero cut there only hides nuclei from the Image Table's
"≥ threshold" view. Whichever channel it is, a value you set yourself always wins
over the starting one.

Two helpers sit beside the fraction-on section:

- **Auto-threshold all channels** — an Otsu pass over per-cell + matched
  background pixels, to derive sensible ON cutoffs for every channel at once.
- **Optimize ON cutoff…** — opens the optimizer in its own window (which you can
  leave open while you read the CDFs behind it). Pick two wells, a timepoint and
  a Min / Max / # Points range, and it applies the threshold that maximises the
  *relative* fraction-on gap `|fa − fb| / max(fa, fb)`, opening a validation
  graph so you can sanity-check the pick. Independent Control / Baseline pickers
  let you optimize on a fold-change-adjusted fraction-on.

Below the table is the readout:

- **The inclusion funnel** — each layer applied on top of the one above it, so
  you can see *which* layer removed what rather than only the final total:

  ```
  20,000 segmented cells
     Curation + intensity gate: −1,240  →  18,760
     QC debris gate: −1,204  →  17,556
     Crop region: not applied
     Scatter gates: not applied
  ```

  A layer reads "not applied" when it is not in play at all — which is different
  from a layer that ran and removed nothing, and is never shown as `−0`. Manual
  curation and the intensity gate share a line because they write the same
  underlying column and cannot be told apart after the fact.
- **A well whose verdict could not be recomputed is named.** If applying the QC
  gate, or a crop, to a particular well fails — a measurements column that is not
  numeric, most often — that well's verdict is dropped rather than left showing
  the previous settings' answer, which means every one of its cells then counts.
  That is the honest outcome but an invisible one, so a warning names the wells
  in a message and in a pop-up notice: the controls in front of you would
  otherwise say those wells are filtered when they are not. The crop's own
  "Crop region set…" line carries the warning along with it, so writing the
  outcome does not wipe it off the status line a moment later.
- **Included for analysis** — a live count under the gate controls, e.g.
  `Included for analysis: 12,345 / 20,000 cells  ·  1,204 failed QC`. The first
  number is the population every plot, statistic, and export actually uses, so
  it is the quickest way to confirm a gate did something; the total is every
  segmented object across all loaded wells (not just the selected ones the
  curves below show). The QC figure is a diagnostic alongside, not a separate
  slice — a cell can fail QC *and* be gated out. It updates as you edit
  thresholds, toggle the scatter-gate modes, or change the QC gate, and reads
  `updating…` while a pass is running. The ON cutoff deliberately does not move
  it: that cutoff decides which included cells count as *on*, not which cells
  count.

The fluorescence **CDFs** come next, directly under the numbers they explain, so
you can place a threshold and see where it falls without scrolling. They respect
the crop region and the QC gate.

**Optimal use** — set the QC gate first, auto-threshold to get baseline ON
cutoffs, then use the optimizer on the one channel that separates your
conditions.

#### Groups

Name a set of wells as replicates; every plot, the Statistics tab, and Batch
Export can then treat them as one condition. A group **is** exactly one
condition — its wells are its replicate set (no mixed sub-sets or solo wells
inside a group). Build one with **+ Add** (a graphical plate picker —
rubber-band select, row/column headers), or let **Quick Replicates** chunk the
current selection into groups of a chosen size via the **Replicates per group**
spinbox (default 2), walking the wells along the Rows / Columns direction. On
the Groups plate every well is tinted by its **group's** colour, so all wells in
a group read as one condition and match the group's row swatch.

**Editing a group on a partly-loaded plate keeps the wells you cannot see.** If
you opened a folder that holds only some of the plate's wells, the picker can
only paint those — but the group's other members are still part of it. Adding
or removing a well changes only what you clicked; the members the plate cannot
show stay in the group.

#### Well Labels

Give wells / groups human-readable names; these show in every plot legend
instead of the raw well token. On screen the Line Graphs legend names the first
twelve traces and ends with a row saying how many it did not list — a legend
with one entry per well of a full plate overruns the panel it is meant to
annotate, and drawing it was slower than drawing all of the data. Every trace is
still plotted. The same applies to the cumulative-distribution panel, which is
drawn from a sample of each well's cells on screen; the cell count printed beside
each curve is always the well's real one.

**Exporting repaints the figure at full detail first**, so a saved image, a
copied vector, a plot filed in Saved Plots and a batch export all name every
trace and draw every cell — from Line Graphs' own Export menu as well as from the
Batch Export tab. The two screen shortcuts are for the panel on your monitor, not
for the file. You may notice the plot flicker as it redraws during an export;
that is the full-detail repaint and it goes back to the screen version
afterwards. **Add Prefix** and **Add Suffix** apply to the
wells currently selected on the plate beside the tab.

Labels are stored with the dataset, and editing one on a load that holds only
part of the plate leaves the rest of the plate's labels alone. Clearing a label
on a well that *is* loaded still deletes it, and
[Starting over](#starting-over) → **Sample definitions** still clears every
label on the plate, loaded or not.

#### Ratios

Define a ratio channel (e.g. `GFP/MCHERRY`) computed at read time as
*numerator / max(denominator, ε)*; it appears as a virtual channel in the
channel selector everywhere. **ε** is the floor the denominator is clamped to
before dividing rather than a constant added to it, which matters on
background-subtracted channels: those read slightly negative for some cells,
and adding ε there flips the divisor's sign and produces an arbitrarily large
ratio, while clamping keeps it positive. A cell whose denominator reads 0
yields `NaN` and drops out at ε = 0, or survives with the ratio biased toward 0
at a small positive ε. **Apply** commits the table and persists it with the
dataset.

#### Pipeline Info

Read the schema + channel tokens + FOVs + timepoints the dataset was built with.
It is read-only — it reports what the run recorded, not something you set here.

### The Review window at a glance

```
┌─────────────────────────────────────────────────────────────────┐
│ All-Well                       ↻  ⛶  ⌂  i                       │  ← title bar
├──────────────┬──────────────────────────────────────────────────┤
│ Review Analyze Datasets │ Bar Plots  Line Graphs  Scatter  …  ⊟ │  ← one band
├──────────────┼──────────────────────────────────────────────────┤
│ 📁 my_run  ▾ │  Channel [GFP ▾] Property [Mean ▾]  Display ▾ ⚙  │  ← controls
│  ⊙ Plotting  │  ┌────────────────────────────────────────────┐  │
│   …          │  │                                            │  │
│   …          │  │            (plot canvas)                   │  │
│   …          │  └────────────────────────────────────────────┘  │
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

- **Title bar** — the app name and the version.
- **Top band** — one row carrying everything that steers the window. On the
  left, the `[Review | Analyze | Datasets]` selector switches modes; beside it
  are the current page's sub-tabs; and at the far right the dataset actions —
  Refresh (re-read the folder), presentation toggle (flip every plot
  Screen ↔ Publication), **Reset analysis**, reveal the dataset folder, and
  **Reset view**. Those five act on the loaded dataset or on the plots drawn
  from it, so they appear in Review only; the two resets are described under
  [Starting over](#starting-over). The **`i`** Help drawer button sits to their
  right and stays in that same spot in all three modes, because the manual it
  opens documents all three; `Ctrl+/` opens it too. Opening it from a Datasets
  tool lands on that tool's section of the manual, and from Analyze on the form
  section you have scrolled to. A build that ships a reduced feature set trims
  the manual to match, so the drawer describes only what that build has; if the
  trim cannot be applied it says so at the top of the manual rather than
  quietly showing you the full one.
- **Section rail** — the left navigator lists every centre tab (Plotting, etc.),
  grouped into **Analysis**, **Images**, and **Data**, under the dataset control
  at its head. Tabs that own sub-tabs (e.g. Plotting) show them in the top band,
  beside the mode selector. `Ctrl+B` collapses the rail, as does the small
  button floating on the rail's right edge — it stays put when the rail hides,
  so it is also how you bring the rail back. The mode selector is the top of
  that same column and collapses with it, so while the rail is hidden the
  sub-tabs run to the window's left edge; bring the rail back to switch between
  Review, Analyze, and Datasets.
- **Plate map + Plot Groups** — pinned to the bottom of the left rail; the
  well selection they drive is what every tab plots (see below).
- **Controls band** — the row directly above the plot canvas: what is shown on
  the left (channel, property, timepoint, threshold), what to do with it on the
  right (`Display ▾`, `Export ▾`, Properties).
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
Home → Ratios editor appear here as virtual channels.

#### Plate-map well picker

The 8×12 plate map in the left sidebar drives every plot and table. Click
a well to toggle it; drag across wells to multi-toggle; click a row letter
or column number to toggle the whole row / column; **Select all** /
**Select none** clear or fill in one click. The selection survives across
tabs and is what every plot / aggregate / export consumes.

Every plate map in the app — this one, the pickers on the Statistics, Image
Browser, Preview and Home panels, the Datasets tools' own well
pickers, and the status map on a running job's dashboard — is the same control
at the same size, centred in whatever panel holds it. A plate looks the same
wherever you meet it, so a well you can find on one is in the same place on
the next.

#### Plot Groups and Split View

- **Plot Groups** — the collapsible drawer under the plate map saves the
  current well selection under a name so you can reload it in one click. Plot
  Groups are *plotting selections only* — not replicate sets or stats groups —
  and loading one drives both the plate selection and, where a tab keeps its
  own well set, that tab's set too. The Image Table sidebar carries the same
  drawer, so a saved set can drive the image grid as well — and **Add** there
  saves the wells picked on the Image Table's own plate, which is a separate
  selector from the one on the plotting sidebar. When Sample
  Definition groups are defined, the plots draw one trace per *visible*
  replicate set rather than per well, and the drawer follows suit in both
  directions: **Add** saves every set that is currently shown (plus any solo
  wells), however many that is, and loading the group shows exactly those sets
  again and hides the rest — any well the group names that belongs to no set is
  carried alongside them. Only visibility changes; no replicate set's membership
  is edited.
- **Split View** — the **Split** toggle in the top bar puts two *different*
  centre tabs side-by-side, both live (e.g. Scatter Plot beside Cell Browser).
  Click into a pane to focus it — the focused pane drives the sidebar and the
  well picker, and both panes refresh on a selection or channel change. Each
  pane keeps its own row of page tabs at its top, so a page's sub-tabs stay
  where its content is and either side can be switched without focusing it
  first; the shared row across the top of the window is empty while you are
  split. It needs a second tab to show something; a tab can't be split against
  itself.
  A Review-only control — it's hidden while Analyze or Datasets is active,
  and switching to either exits an active split automatically.

#### One band across the top

The window's top row is one band: **Review / Analyze / Datasets** on the left,
spanning the width of the panel below it, and the current page's tabs — *Bar
Plots · Line Graphs · Scatter Plot · …*, or *Image Table · Segmentation · Cell
Browser · Playground* — immediately to their right on the same line. Batch Export's four
modes — *Line · Bar · Scatter (cells) · Scatter (aggregate)* — sit there too.
Pages without sub-tabs (Statistics, Spatial, smFISH, Review CSV, Saved Plots)
leave that side empty; the band keeps its height so nothing below it shifts when
you switch pages. In Split View the band is empty too — each pane draws its own
page's tabs at the top of that pane instead, since one row cannot name two
pages.

The mode selector stays the width of whatever panel sits under it, so the two
read as one column: Review's well-picker rail, the Analyze form (which you can
drag wider in its splitter), and the Datasets rail are three different widths,
and the bar follows each. Analyze and Datasets leave the tab side of the band
empty — those tabs name Review's pages.

#### One controls row on every tab

Every tab in the Review pane states its controls the same way, in one row above
the content: **what is shown** on the left — channel, property, timepoint,
threshold, and whatever else scopes the data — and **what to do with it** on the
right, flush to the edge: `Display ▾`, `Export ▾`, and the **Properties** button.
Nothing is centred; which view you are looking at is the tab bar's job. When the
pane is too narrow the left side wraps onto a second line rather than squeezing
its controls, so nothing ever becomes unreachable.

The Image Table is the one deliberate exception. Its crop and ON-threshold
controls act on the images directly below them, so they stay in a strip beside
the images rather than moving to a row at the top of the tab.

One row means one row. The five Plotting tabs share it: the controls common to
all of them — channel, property, threshold — stay put as you move between Bar
Plots, Line Graphs, Scatter, Distribution and Heat Map, and only the part that
belongs to the tab you are on changes beside them.

That row is also where every page starts. It is drawn at the same height on
every page, in all three modes, and a page with no controls of its own keeps the
row empty rather than starting its content higher — so moving between pages
never shifts the top of what you are looking at. It grows only when it has to:
on a narrow window, where the left side wraps onto a second line.

#### Figure actions — the Export dropdown · Properties

Every plot tab (Bar Plot, Line Graphs, Scatter, Distribution, Heat Map,
Spatial, Multivariate) carries the same **Export ▾** dropdown on its controls
row, holding four actions:

- **Export CSV…** (label varies by tab) — writes the underlying data behind
  the current plot.
- **Copy SVG** — puts a vector copy of the figure on the clipboard (paste into
  Illustrator / Inkscape / Keynote / Slides). On macOS the clipboard is written
  natively (PDF + SVG) so Illustrator pastes editable vector; this needs
  `pyobjc-framework-Cocoa` (see `requirements.txt`).
- **Save Figure…** — saves to disk; the file dialog picks the format from the
  extension (`.svg` / `.pdf` / `.eps` / `.png`).
- **Save to Saved Plots** — sends the whole figure, plus the exact parameters
  that produced it, to the per-dataset [Saved Plots](#saved-plots) gallery.
  The same capture is also reachable by hovering a plot panel's top-right
  corner and clicking the floating **save** button, which saves just that one
  panel rather than the whole figure — handy on a multi-panel tab (Bar Plot,
  Line Graphs) when you only want one of the stacked subplots.
- **Properties** — opens a slide-out sidebar that styles the active figure:
  axis label / tick / title sizes, grid on/off + alpha + linestyle, legend,
  line / marker widths, axis limits + log-scale, layout engine, and an
  export-profile picker. Axis **limits**, **log-scale** and the **line / marker
  styling** are scoped to the one plot — the Scatter tab's Cells and
  Wells views are separate plots here, so sizing markers for five
  aggregate points won't blow up a fifty-thousand-cell cloud. **Typography,
  grid, legend and ticks stay shared** across tabs, so one setting still gives
  a whole figure set a consistent look. On the Bar Plots tab (a Value /
  Fraction / Events panel stack) axis limits are stored **per panel**, so a
  limit set on the Fraction or Events panel sticks instead of that panel
  auto-scaling. Settings persist across redraws.

#### Interval per timepoint

Under **Properties → Time**, **Interval per timepoint** states how far apart the
timepoints really are, and multiplies the numbers printed on every time axis to
match. It is there for plates whose filenames don't encode the real acquisition
cadence: a run numbered `T01 … T12` but imaged every four hours is read by the
pipeline as 1, 2, 3 … hours, and setting the interval to `4` makes the axes read
4, 8, 12 … instead. `1` — the default — turns it off.

The value is a multiplier on the hours the pipeline parsed out of the filenames,
which is the interval itself only while those filenames number the timepoints
1, 2, 3 …. On a plate whose names already carry real hours it acts as a
correction factor instead — a run whose names say 4, 8, 12 with the interval set
to `4` draws 16, 32, 48.

It changes **labels only**. The plotted points, the **Timepoint** dropdowns, the
Statistics tab and every exported CSV keep the hours read from the filenames, so
while a factor is in force a bar reading `t=16` sits under a Timepoint menu
reading `4`. That is deliberate — nothing that another part of All-Well reads is
touched — and it is why any axis the factor changes names it in the axis label,
so a rescaled figure can't be mistaken for one drawn on the hours the run
recorded. The factor is saved with the rest of the view state.

Applies to the Line Graphs panels, the Bar Plots tick labels and panel titles,
the Spatial summary panel, and every Batch Export figure — including the scatter
figures, which have no time axis and so name the timepoint in their title
instead. Batch **CSV** columns and output **filenames** are unaffected: those hold
what the pipeline measured and what you picked from the timepoint list, so a
downstream script reads the same numbers whatever this is set to.

The Heat Map and Distribution tabs pick a timepoint from a menu rather than
drawing a time axis, so their titles keep naming the filename's own value,
matching the menu the choice came from.

#### Fold-change normalization

The Bar Plots and Line Graphs tabs (and their Batch Export panels) carry
two parallel dropdowns on the controls row:

```
Fold change:  Control [— ▾]   Baseline [— ▾]
```

- **Control** — pick a loaded well or replicate set to divide every bar
  / curve by its mean at the matching timepoint. `—` disables this
  axis. (The Bar Plots control combo also includes any replicate sets
  defined on Home → Groups.) When a group name happens to match a well
  token, the well's entry is shown as `A01 (well)` so the two can be told
  apart on screen. The suffix is only a label: the entry records which of
  the two you picked, so a group named `A01 (well)` is still selectable
  and still means the group, and hiding a group cannot make its name
  capture a well. Titles and CSV exports print the plain name.
- **Per-sample control…** — the entry directly under `—`. Instead of one
  control for the whole figure, each plotted sample names its own, so a
  plate carrying four conditions each with its own paired control goes on
  one fold-change axis. Picking it opens the editor described below; once
  active the dropdown reads `Per-sample control (22/24)`, counting the
  samples whose control actually resolved.
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

When a control is requested but cannot be used — nobody assigned one, the
one assigned no longer exists, or it has no sample at the plotted timepoint
— the affected bars / points are rendered as dashed placeholders and the
status bar says how many fell short and why. They are never drawn raw: a
value that is not a fold change has no business on an axis that says it is.

##### Per-sample controls

Picking **Per-sample control…** opens a window listing one row per plotted
sample, in the order the plot draws them:

| Column | What it says |
| --- | --- |
| Sample | The bar or curve, in its own colour |
| Control | Its control — the same list the single-control dropdown offers |
| Status | `paired`, `own control`, `no control set`, `control not found`, or `no data at this timepoint` |
| Resolves to | The wells actually used as the denominator |

Read **Resolves to** when checking your work: the other columns describe what
was asked for, and this one shows what happened.

Edits apply to the plot immediately, so the window stays open beside it —
there is no OK/Cancel, only **Close**. Two bulk actions sit in the footer:
**Set all to ▾** assigns one control to every sample (which is exactly the
single-control behaviour, and the axis title says so), and **Auto-pair ▾**
walks the samples in blocks of 2, 3, 4 or 6 and makes the first or last of
each block that block's control — the layout a plate of "control then
treatments" repeats.

A sample that is itself another sample's control draws as a flat 1.0 (0 for
subtract) with a propagated error bar, which is the ΔΔCt convention; the
**Controls: hide from plot** toggle leaves it out instead. There is no
option to draw it raw, because a raw control beside ratio bars would put two
units on one axis.

Assignments are kept, never silently reset. A control whose group is deleted
shows as `Veh_old (missing)` and comes back when the group does; an
assignment for a sample that is no longer plotted collects under **Unused
assignments** rather than disappearing. Renaming a group follows it through
every assignment that names it.

The axis title says how many controls are in play: one is named as usual,
two are both named, and three or more read `vs own control` — with the CSV's
`fold_change_control` column carrying which sample used which.

A Batch Export panel inherits the per-sample mapping in force when you open
it, so a batch run reproduces the figure on screen, and its Control dropdown
says so: while the inherited mapping is what the panel will export, the dropdown
reads **Per-sample (from the plot tab)**. The mapping itself is edited on the
plot tab, not here — picking any single control in the panel switches that panel
to that one control and the inherited entry disappears from the list. A group member the inherited mapping does not
name is exported blank rather than falling back to one control, since the
panel's groups are its own and need not match the plot tab's. A control naming a replicate
set in a *different* batch group works: one group per condition, with the
control as its own group, is the layout batch export is for.

**CSV exports — additive schema.** Both the line and bar exporters
keep the standard `mean_<ch>_<metric>` and `err_<band>_<ch>_<metric>`
columns carrying the RAW values, and append four columns when
fold-change is active:

* `fold_change_mean` — normalized mean
* `fold_change_<sd|sem>` — normalized spread
* `fold_change_mode` — `control`, `t0`, or `control+t0`
* `fold_change_control` — **that row's** control (they differ under
  per-sample controls)
* `fold_change_outcome` — why the row is what it is: `ok`, `self`,
  `unmapped`, `control_unresolved`, or `control_no_sample`. All but the
  first two leave `fold_change_mean` empty, and this column is what tells
  a normalization that failed from one that was never asked for.

(Earlier versions of the bar CSV overwrote the raw mean column with
the normalized value; downstream tools that depended on that behaviour
should switch to reading the `fold_change_mean` column.)

**Events columns.** `n_above_threshold` keeps its long-standing meaning — the
integer count of events, summed across a replicate set's wells. The number the
Events panel *draws* is the per-well mean, which is a different quantity and
gets its own columns:

* `n_above_replicate_mean` — the plotted value
* `err_n_above_replicate_<band>` — its spread across replicates, empty when
  there was only one and nothing could be computed
* `n_above_replicate_unit` — `well` or `fov`, empty for a single well

The unit column matters because nothing else in the row records whether
Aggregate-FOVs was on, which would leave the other two ambiguous. A single well
is not a replicate aggregation, so its row leaves the spread and the unit
**empty** — a `0.000000` there would read as "the replicates agreed" on a row
that had no replicates — and reports the count itself under both
`n_above_threshold` and `n_above_replicate_mean` (the per-FOV mean under both,
with Aggregate-FOVs on).

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
  button (reads e.g. `Timepoints: 3/8 selected`) fills from the selected wells'
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
  - **Significance** (On / Off) — draw pairwise significance brackets from the
    comparisons the [Statistics](#statistics) tab last ran. A bracket appears
    over the two bars its comparison was computed from, whether those bars are
    replicate sets or individual wells — a group holding a single well brackets
    that well's own bar. When a comparison doesn't line up with any bar (a group
    spread across several bars, say), the status line says so instead of the
    bracket quietly going missing.
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
SD/SEM**, **fraction above threshold** (fraction-on), and **N events** — plus a
CDF. For a replicate set, **N events is the mean of its per-well counts**, the
same way the mean and fraction panels average their wells, so all three panels
describe one well's worth of signal and the three can be read together. A
two-well set of 5 events each plots 5, not 10. The Bar Plots tab reports the
same number, so the two tabs can be compared directly; the cross-well total is
still exported (see below).

- **Needs** — selected wells spanning more than one timepoint to see a trend
  (single-timepoint data draws a single point).
- **Controls** (under **Display ▾**) — the **error band** (SEM / SD); the
  **per-FOV spread** toggle, which treats each field of view as a replicate so
  the band reflects field-to-field variability (for a multi-well group every
  FOV across *all* its wells is pooled as an independent replicate — see the
  pseudo-replication note under [Statistics](#statistics)); the shared
  [Fold-change](#fold-change-normalization) dropdowns (all three panels — mean,
  fraction-on, and N — normalize together).
- **Optimal use** — turn on FOV spread when you have several fields per well
  and want the error band to reflect field-to-field variability; normalize vs
  **t0** to collapse every curve to a shared start of 1.0 for shape comparison.
- **When a spread can't be computed** — a bar or band with only one replicate
  has no spread to show. That is *not* zero, and the app no longer draws it as
  though it were: the bar carries a small **`n/a`** marker, and the exported CSV
  leaves the `err_…` cell **empty** instead of writing `0.000000`. A bar with no
  marker and no whisker (with error bars switched on) means the spread really is
  too small to see.
- **Remote runs: what the log shows while it stages** — before anything uploads,
  a remote run reads your input archives and builds the smaller set it is
  actually going to send (only the channels you selected, and with **incremental**
  only the fields not already processed). The log narrates it: `scanning input`,
  `planning N well archive(s)`, then one `staged (i/N)` line per well that had to
  be rebuilt. A well with nothing new to process is skipped entirely, and a well
  whose archive is already exactly what would be sent is uploaded from your input
  folder untouched — nothing is copied to your local disk for it, and the log
  says how many archives took that route. Only wells that genuinely lose
  something (a channel you did not select, or fields **incremental** has already
  done) are rebuilt, into `~/.allwell/scratch` unless the *Scratch disk* field
  says otherwise. If your data is on a network or external drive, this pass reads
  only the images it will upload, so it is roughly as fast as the upload itself.
  Whether an archive counts as unchanged is judged on its images only, so the
  `.DS_Store` and other stray files a zip picks up never cause a rebuild.
- **If the app dies, the error dialog holds the whole traceback** — open
  *Show Details* and copy it; that text names the exact line that failed and is
  the single most useful thing to put in a bug report. It is also written to a
  log file, whose path the dialog gives you: `~/.cache/allwell/allwell-boot.log`
  on macOS and Linux, `%LOCALAPPDATA%\allwell\allwell-boot.log` on Windows. That
  file is normally rewritten each time the app starts, **but a copy carrying a
  crash is kept** — as `allwell-boot.log.1`, `.2`, `.3`, newest first — so
  relaunching to try again no longer throws away the record of what went wrong.
- **You can look at the finished wells while the rest are still running** —
  Analyze and Review are two panes of one window, and the run is a separate
  process (or on the cluster), so switching to Review does not touch it. Open
  the run's output folder there and you get every well that has finished so far;
  press the refresh button in the title bar to pick up the ones that have
  landed since. Wells that have not finished yet are simply absent. Nothing is
  read half-written: each well's CSV and `_out.zip` appear complete or not at
  all. On a remote run the plate becomes openable a few seconds after the job
  starts on the cluster (not when it is still queued); until the run ends, the
  dataset description it reads is the running job's own, which for a batched
  run covers that batch's wells rather than the whole plate — so the timepoint
  and FOV lists can be short until the run finishes.
- **Remote runs: what came back, in a file you can read later** — a remote run
  writes `remote_fetch_report.txt` into the output folder listing every well the
  remote produced output for and what happened to it: `fetched`, `skipped`
  (with the reason its local copy was judged already complete), or
  `never-announced` (no completion marker was seen for it, so nothing was
  pulled). The run log says the same thing while it runs, but the log drawer
  closes and this does not — if a well's numbers look like the previous run's,
  this file is the first thing to read.
- **Grouping loose TIFs: the folders go away as they are packed** — when a run
  has to group your images into per-well folders and pack each into a
  `<well>.zip`, each folder is deleted as soon as its own archive is written, so
  the input folder never holds two full copies of the plate. A folder is kept in
  two cases: it contained no TIFs (nothing was packed, so nothing replaces it),
  or that well already had a zip from an earlier run (that archive is yours and
  was not checked against the folder). If a run dies partway, the wells already
  packed keep their archives — restart and it picks up from there.
- **"N well(s) are incomplete" at load** — those wells' results cover fewer
  fields of view than their input holds: FOV-groups failed during the analysis
  run, and the well still wrote a CSV and an `_out.zip` from the ones that
  succeeded, so nothing about the files looks wrong. Every per-field number for
  those wells describes only the fields that made it through. The run records
  this itself now, and the analysis log names the failures; the per-FOV
  tracebacks are in `<output folder>/errors/`. The usual cause is the scratch
  disk filling mid-run — **scratch is not the folder your data is in**, it
  defaults to your home drive, so a dataset on a roomy external disk can still
  exhaust a full internal one. Free space (or point the Analyze tab's **Scratch
  disk** field at the roomy volume) and re-run with **incremental**, which
  reprocesses only the missing groups and merges them in.
- **"No space left on device" when there is clearly space free** — the yardstick
  is misleading rather than the disk. Your `in/` archives are compressed, and
  nothing else in a run is: while a well is being processed its staging holds
  the extracted frames plus about six intermediate images per field of view, all
  uncompressed, for as many wells as are running at once — and every finished
  `<well>_out.zip` stays on the output volume for the rest of the plate. A run
  can therefore need many times the size of its own input folder, and it can
  start with room and run out at the fortieth well. Three things say so rather
  than leaving you to find out: before it reads an image the run sizes
  itself against the volumes it will write to and warns (or, if there is not
  room for even one well, stops — see **Start even if the disk looks too
  small** under Compute options for when that refusal is wrong and how to
  overrule it); as wells complete it measures what they
  actually wrote and warns once if the rest of the plate will not fit; and if a
  volume does fill, the error names *which* volume and how much is left on it
  instead of the bare system message. Free space, or point **Scratch disk** at a
  roomier volume, and re-run with **incremental** — the wells already written are
  complete and are not redone.
- **"Unusable FOV labels" at load** — a warning naming specific wells means those
  wells' rows carry no usable field-of-view label, so all their fields pooled
  into one and nothing per field can be computed for them: no error bars, and
  the **N events** panel shows no bar rather than a total pretending to be a
  per-field mean. Their mean and fraction-on are unaffected — those don't depend
  on the field labels. The cause is upstream, in the analysis run: the image
  filenames didn't split into as many parts as the **filename schema** has
  fields, so the pipeline had no field token to record (it now warns about this
  during the run too). Re-run those wells with a schema matching their
  filenames to restore per-field statistics.

### Scatter Plot

**What it shows.** Two views, chosen with the **View** toggle at the left of the
controls row — **Cells** (one point per cell, X vs Y feature) and **Wells** (one
point per well/timepoint). Cells can be coloured **by well** or, via **Color by
time**, by timepoint.

The Channel, Property and ON cutoff controls that the other plot tabs share
disappear from the controls row here, and the View toggle takes their place.
That is because this tab picks its channels per axis, from the X-axis and
Y-axis dropdowns below — the shared controls have nothing to act on. Each axis
still uses its own channel's cutoff from Cell Gating; since the quick-edit is
hidden here, that tab is where you change it.

In **Wells** view, a plot that comes back empty says which of the two reasons it
is empty for: no cells at the timepoints you picked, or cells that were all cut
by an axis's ON cutoff — naming the channel, the cutoff and how many of how many
cells it kept.

- **Needs** — a well selection, a **Channel**, and an **X-axis** and **Y-axis**
  metric (any intensity, ratio, or `shape_*` column) chosen from the axis
  dropdowns. A **Timepoints** multi-select button (reads e.g.
  `Timepoints: All 5 selected`) restricts the frames drawn.
- **Per-cell controls** — X-axis / Y-axis / Channel pickers inline, with
  **Layout** (*Overlay* all wells on one axis, or *Separate axes* one panel per
  well), **Color by time** (one well → a single cloud's trajectory; several
  wells → auto-stacked one panel per well so the time-coloured clouds don't
  overlap) and **Apply gates** (below) together under **Display ▾**. **Show
  gates** is in the Properties sidebar; **draw rectangular gates** by dragging
  on the canvas.
- **Apply gates** (under **Display ▾**). This plot is the one place that shows the *whole* cloud even
  when your gates are filtering everything else — that is deliberate, because
  you draw the gates here and need to see what falls outside them to judge or
  resize a box. Switch **Apply gates** on to plot only the cells inside the
  boxes shown here; the title gains a `· gate-filtered` tag so a copied or saved
  figure says which it is — and so a filter left on inside a popover still
  announces itself. It filters against the boxes on screen, so it always
  agrees with what you are looking at, and it needs nothing switched on
  elsewhere. Two things to know: it is a *view* filter — it changes nothing
  about which cells other tabs count, which is the Cell Gating tab's job — and
  it steps aside while you are drawing or resizing a gate, so the cloud you are
  aiming at cannot vanish mid-drag. Gates drawn on a different X/Y metric pair
  are neither drawn nor applied here.
- **Aggregate controls** — X-axis / Y-axis pickers over aggregate metrics
  (e.g. *Mean Fluorescence*, *Fraction above threshold*) with an **error band**
  (SEM / SD) on the points.
- **Gates (definition).** Each gate you draw is auto-named (`Gate 1`, `Gate 2`,
  …), shown on the plot, and renameable in Properties — where each entry also
  states the x and y metrics that gate was drawn on, so a list built up across
  several axis pairs stays readable. Gates become selectable
  **cell-inclusion** or **fraction-on** filters on the [Cell Gating](#cell-gating)
  tab, on whichever metric axes they were drawn. They are saved with the
  dataset and reload with it.
- **Optimal use** — draw a gate around a population of interest, then open Cell
  Gating and tick *Use scatter-plot gates* to make that gate define which cells
  count in every plot, statistic, and export. Watch the **Included for analysis**
  count there to confirm it took effect. **Apply gates** back on the scatter is
  a separate, view-only convenience for looking at just the gated cells.

### Distribution

**What it shows.** The distribution of a per-cell metric at **one timepoint**,
as a histogram, KDE, or violin.

- **Needs** — a well selection, a channel, and a timepoint (**t (h)**).
- **Controls** — **Mode** (*Histogram*, *Histogram + KDE*, or *Violin (per
  group)*); **Layout** (*Overlay* all wells, or *Faceted (one per group)*);
  **Bins** count; **log x** / **log y** toggles; the timepoint picker; the
  channel-wide threshold reference line.
- **No error band** — this tab draws the whole population per group rather than
  reducing it to a mean, so there is no spread for a band to describe and no
  SEM/SD control on its controls row. The SEM/SD choice itself is app-wide: set
  it on Bar Plots or Line Graphs (under **Display ▾**), or in the **Properties**
  panel under *Statistics* → *Error bars*, and every tab that draws a band
  follows.
- **Optimal use** — compare a handful of wells at a single timepoint to read
  how a treatment shifts the *whole* population, not just its mean; facet when
  overlaid distributions get too crowded to read.

### Heat Map

**What it shows.** A custom-layout heatmap of one metric across wells — you
place cells in a grid that mirrors your plate or your experimental design.

- **Needs** — a well selection and a channel; a **Timepoint** chosen from the
  dropdown.
- **Controls** — on the controls row:
  - **Aggregation** — how multiple cells/wells in a grid cell combine (e.g.
    *Rep-set avg*). **Mean above threshold** and **Fraction above threshold**
    are offered only for a Property that has an ON cutoff of its own — Mean
    Intensity (ThreshFracOn) and smFISH Count (ThreshCountOn). They grey out for
    Total / Max / Min / Std intensity, because the cutoff is a *mean* intensity
    and cutting a total with it would compare two different scales; those fall
    back to *Mean (all cells)*.
  - **Timepoint** dropdown (reads e.g. `Timepoint: 4.0h`). The timepoint you
    were on is remembered with the dataset and comes back as *the same hour*, not
    the same position in the list — so extending a plate with more timepoints
    and reloading it (an **Incremental** re-run, see
    [Compute options](#9-compute-options)) still brings
    the map back on the timepoint you saved it on. If the plate no longer has
    that hour at all, the map opens on its default timepoint and the log says
    which hour was missing, rather than putting up a neighbouring one that would
    look exactly like the one you saved.

  Under **Display ▾**, since these set how the grid is coloured rather than what
  it shows:
  - **Color map** (e.g. *Greys*, *RdYlBu_r*, *Muted*).
  - **Scale** — *Auto*, or *Fixed* with explicit **vmin** / **vmax**.
  - **Log scale** toggle — colours the map across orders of magnitude (values at
    or below zero drop out), and the colour bar beside it is labelled on the same
    log scale. Needs at least two distinct positive values; when the data can't
    support it the status line says so and the linear scale stays.
  - **⇄ Transpose** — swap the rows and columns of the layout.
  - **Rep-set avg** — show the whole rep-set's average in a cell whose well
    belongs to one, instead of that single well.

  And elsewhere:
  - **Value format** (in the **Properties** panel, under *Cell values*) — a
    printf-style format for the number written in each cell: `%.2f` for two
    decimals, `%.1e` for scientific notation, `%d` for a whole number. Leave it
    blank to let the tab choose per metric — counts as whole numbers, fractions to
    two places, intensities scaled to their size.
  - The layout editor: drag wells into the grid and **Edit label** on any row /
    column header.
- **Selecting wells.** The sidebar plate map does double duty on this tab: a
  *click* on a well selects or deselects it exactly as on every other tab, while
  a *press-and-drag* off a well carries it into the layout grid below. The one
  gesture the drag source takes over is press-and-drag selection across several
  wells — use the row / column headers, the corner all/none toggle, or
  **Auto-populate from selection** for bulk work. You can also click a heatmap
  cell to select its wells; **Shift**-click adds to the selection and
  **Ctrl**-click (**Cmd** on macOS) toggles individual wells in and out.
- **Auto-populate from selection** fills the grid from whichever selection is
  live, and with Sample Definition groups defined the **Rep-set avg** toggle
  decides what a cell stands for:
  - *Rep-set avg on* — **one cell per visible replicate set**, holding all of
    that set's loaded wells, so the heat map shows one averaged value per set.
    A multi-well cell shows as `A01+3` in the layout editor (hover for the full
    membership) and moves as a unit when dragged.
  - *Rep-set avg off* — **one cell per well**, in plate order, across every
    visible set. The map is per-well, so the grid is too.

  With no groups defined it lays out one cell per selected well, in plate order.
  Either way the grid grows (up to 32×32) if the selection needs more cells than
  it currently has.
- **Optimal use** — build a layout once that matches your plate, pin the colour
  **Scale** to *Fixed* vmin/vmax so frames are comparable, then step through
  the **Timepoint** dropdown to watch the metric evolve.
- **A session that never opens this tab leaves its saved settings alone.** The
  colour map, the scale mode and its vmin/vmax, the log-scale and rep-set-average
  toggles and the timepoint are all stored with the dataset, and they are
  rewritten from the controls only once the controls exist. So opening a plate,
  working entirely on other tabs and closing it leaves them exactly as you left
  them last time. Resetting the view clears them, as it does the rest of the
  view.

### Statistics

**What it shows.** Pairwise significance tests across your saved selections /
replicate sets — **t-test**, **Mann-Whitney**, and **KS** — with a KS CDF plot.

- **Needs** — at least two groups. On first visit the tab auto-populates from
  the replicate sets defined on [Home → Groups](#groups).
- **Controls** — the group editor is in the sidebar; everything else is on the
  same one-row controls band every tab uses — **Channel**, **Property**,
  **Statistic**, **Timepoint** and **Test**, then an FOV-replicate indicator
  that mirrors the plot tabs' FOV mode, with **Run test** at the right.
- **Replicate unit.** What counts as one replicate is the single rule shared
  with the bar / line error bars, so the test's `n` and the plotted spread never
  disagree. With FOV mode **off**, each well in a group is one replicate. With
  FOV mode **on**, every field of view **across all of the group's wells** is an
  independent replicate (`n` = total FOVs), not just the well count.
  > **Note.** Pooling FOVs across wells treats each field as an independent
  > replicate — statistically this is pseudo-replication and tends to produce
  > smaller p-values than a well-level test. It's an explicitly-chosen mode;
  > leave FOV mode off for well-level replicates.
- **Optimal use** — define replicate sets first so the tests run on
  biologically meaningful groups rather than single wells.

Every page that needs a dataset — the plot tabs, Statistics, Multivariate,
Spatial, Review CSV, Batch Export and smFISH — shows the same centred placeholder
when it has nothing to draw, instead of an empty table or a blank panel that
looks like something failed. The placeholder says *which* nothing it is: before a
dataset is open it asks you to load a directory, and once one is open it names
what the page is still waiting for. On the plot tabs, Multivariate, Review CSV
and smFISH that is a well selection, so they read "No wells selected." — "No well
selected." on smFISH, which shows one well at a time — with a
line telling you to click wells on the plate at the left (or open Plot Groups
below it) until you pick some. Statistics asks for two comparison groups instead — it names the gap when you
have one as well as when you have none —
because it compares groups rather than plotting the plate directly, and the Heat
Map asks you to put wells on its grid — press **Auto-populate from selection**,
or drag them in from the plate.

Inside the Image Browser, **Image Table** and **Cell Browser** are not driven by
the plate: Image Table has its own well picker, and Cell Browser follows whatever
the plots are showing and its own cell filters. Both still show the load-a-directory
placeholder before a dataset is open — Image Table used to ask you to choose wells
and press Generate when there were no wells to choose, and Cell Browser showed a
blank panel with no explanation at all.

Messages about anything else stay on the plot itself: no timepoints chosen, a
channel the run never produced, gating that excluded every cell. So a centred
placeholder always means "this page is waiting for you to choose something", and
a line on the axes always means "the page ran and this is what it found".

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
Spatial, and use the controls row:

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
  smallest finite `timepoint_hours` in the well. Greyed out at **Summary**
  level, which walks every timepoint and draws them as the columns of its
  well × timepoint matrix — there is no single frame to choose there. Hover it
  for the same explanation.
- **Recompute** — forces a fresh compute. The per-frame levels (Cell, Colony,
  Intra-colony) recompute on every control change, so this is only strictly
  needed at Summary level, where changes are queued until you press it (one
  DBSCAN per well × timepoint is expensive to run on every keystroke).

The rest of the row controls **colony detection** — colonies are computed, not
manually annotated. These controls appear only at the levels that use them, so
the row stays short at Cell level and grows at Colony / Intra-colony / Summary:

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
  inclusion; **Delete Cell** turns the canvas into a delete tool, where a click
  drops one cell and a dragged rectangle drops every cell inside it (see
  *Deleting a region across the plate* below); **draw a crop rectangle** to keep
  only cells whose centroid is inside it (applied across every
  FOV/timepoint/channel/well by default, or only the frames **Crop scope ▾**
  names — see *Scoping the crop to particular frames* below),
  with **Reset** to restore every cell and **Clear crop** to remove just the
  box; click-drag to pan and scroll to zoom (zoom is kept when you switch
  wells). **Zoom controls** sit on the control row — **−** and **+** step
  through the zoom levels, **Fit** returns to the whole image, and the readout
  between them shows the current factor, where 100% is fit-to-window. It tracks
  every way the view can change, including the scroll wheel and the zoom that
  happens when you double-click a Review CSV row. Also on the row: a
  **Raw / Flat-field / Corrected** source toggle; LUT + colormap +
  binary + mask-overlay controls; the QC debris gate. **Hover** a nucleus to
  read its pixel value, cell id, and **mean fluorescence (MFI)** for the current
  channel (the same per-cell readout the Image Table shows).
- **The status line says when it is showing you less than it was asked for.**
  Two cases, each of which would otherwise look like an ordinary picture. If reading the
  well's archive stopped part way, the line ends with **INCOMPLETE** and the
  reason — the FOV and timepoint menus above it are then shorter than the dataset
  actually is, which is otherwise indistinguishable from a well that holds that
  few frames. And if you switch on the binary (≥ threshold) view for a field the
  measurements do not cover — no rows for the well, or none for this
  FOV/timepoint — the binary view is declined rather than drawn: you get the
  ordinary intensity image and a line saying the threshold could not be measured
  for this field. An all-black binary image means *measured, and no cell passed*,
  which is a real result; a field nothing measured is a different fact and no
  longer draws the same picture.
- **Definition.** *Included* is the per-cell keep/drop flag every plot and
  export honours. A cell counts only when it is manually **kept** *and* it
  passes QC. Overrides persist to `cell_overrides.json` next to the CSVs.
- **Optimal use** — exclude a well's edge artifacts in one gesture with the
  crop rectangle, narrowing **Crop scope ▾** to the wells that need it; click
  away obvious mis-segmentations.

##### Deleting a region across the plate

A dragged delete normally lands on the one frame you are looking at. Artifacts
rarely do: a dust speck at a fixed stage position, a bright corner, a scratch on
the plate bottom all sit in the same part of the image in well after well, field
after field, timepoint after timepoint. **Delete scope ▾**, next to the Delete
Cell button, widens the next drag to cover them in one gesture.

Open it and tick the wells, FOVs and timepoints you want. The button then reads
back what is armed — *Delete scope: this frame*, or *Delete scope: 3 wells · all
FOVs · 2 TPs* — so a wide scope is always visible on the control row rather than
hiding inside a menu. **Use plate selection** arms whatever wells you have
selected on the plate (it does not change that selection), and **Reset to this
frame** disarms everything. The scope stays armed across drags until you reset
it, and is cleared whenever you load a different dataset.

Two things are worth knowing about what "inside the box" means:

- On the frame you drew on, a cell is deleted if **any of its pixels** fall in
  the rectangle — its mask is loaded, so the box means exactly what it looks
  like.
- On every other frame in scope, a cell is deleted if its **centroid** falls in
  the rectangle. Those masks are not loaded, so the cell's recorded centre is
  what decides; this is the same rule the crop rectangle uses. A dataset
  processed without shape features has no centroids, so nothing can be deleted
  in those wells — a warning appears rather than a quietly smaller number. The
  same warning covers wells whose data will not load and wells the scope names
  that this dataset does not have.

The status line reports both counts after every drag, e.g. *Region delete: 412
cell(s) Included=0 — 38 in this frame (pixel overlap); 374 across 11 other
frame(s) in 3 well(s) (centroid inside).* The deletes are ordinary per-cell
edits, saved to `cell_overrides.json` like any other, and **Edit ▾ → Reset**
undoes them along with the rest of your curation.

A wide scope takes a few seconds: the app reads every scoped well's data and
re-applies your curation across it, with the cursor showing busy while it does.
A plate-wide drag over roughly 300,000 cells takes about two and a half seconds
on a warm dataset, longer if the wells have not been read yet.

##### Scoping the crop to particular frames

The crop rectangle filters **every frame in the dataset** unless you say
otherwise, which is right for an artifact that sits in the same place
everywhere — an objective vignette, a consistently bad image edge. It is wrong
when only some wells need clipping: cropping the whole plate to fix two of them
throws away good cells in all the others.

**Crop scope ▾**, next to the Crop cells button, is the same control as **Delete
scope ▾** and works the same way — tick the wells, FOVs and timepoints the crop
should apply to, use **Use plate selection** to arm the wells you have selected
on the plate, and read the armed scope straight off the button (*Crop scope: all
frames*, or *Crop scope: 2 wells · all FOVs · 1 TP*). A field you never touch
keeps meaning **all** of that field, so narrowing by well leaves every FOV and
timepoint of those wells cropped. **Reset to all frames** widens it back.

The one difference from the delete's scope is the default, and it follows from
what each control does. A drag-delete is one gesture on one frame, so its scope
starts at *this frame* and you widen it. A crop is a standing filter on the
whole analysis, so its scope starts at *all frames* and you narrow it.

Cells in frames outside the scope are left alone entirely — the crop neither
includes nor excludes them, and they keep whatever inclusion state your curation
and gating gave them. Changing the scope takes effect as soon as you stop ticking — a run of ticks
is applied together rather than one at a time, since each one re-filters every
loaded well. The status line then reports how many cells the crop excludes, so a
scope that turns out to match nothing shows up as *excluding 0* rather than
looking like a working crop.
On a frame the scope does not name, the crop outline is still drawn where the
box sits, but **dashed and dimmed**, so you can see the rectangle without
mistaking it for a filter that is running there.

The scope is saved with the crop, in `persistence.json`, and reloads with the
dataset — a two-well crop stays a two-well crop across sessions. Each dataset
carries its own: opening another one loads that one's saved crop and scope, or
none if it has never been cropped. **Edit ▾ → Clear crop** and **Edit ▾ → Reset
crop + curation** both clear the scope along with the box, so the next
rectangle you draw starts at all frames again.

#### Image Table

**What it shows.** A configurable grid of per-FOV image thumbnails for
side-by-side comparison — now across **any combination** of wells / channels /
timepoints / FOVs, including more than two at once (e.g. a time-course of every
channel for every well).

- **Needs** — image ZIPs present, and a well set (drive it from the plate
  picker, or save/pick a **Plot Group** in this sidebar's Plot Groups panel).
- **Opens as** — **Rows = Wells › Channels** (each well's channels nested under
  it), **Columns = Timepoint**, and FOV pinned under **Fixed**. Only the **last**
  timepoint is selected: timepoints are the column axis, so selecting the whole
  run would multiply the grid by its length before you have chosen anything. Add
  the rest from the Timepoint picker.
- **Build with the shelf** — the top **Build** shelf is a drag-and-drop tree.
  Drag a **channel** from the **Channels** tray onto the **Rows** or **Columns**
  zone; drag a **root** dimension (Well / Timepoint / FOV) onto a zone to make that
  axis vary by it, with its channels nested underneath (*root ↓ channels* for Rows,
  *root → channels* for Columns). Then press **Generate** to render. Editing the
  shelf only stages the layout — **Generate** draws the images. More than two
  dimensions can vary at once (e.g. Rows = *Well › Channels*, Columns = *Timepoint*).
- **Reorder the nested channels** — the order of the channel chips under a root is
  the order their rows or columns appear in, and you set it by dragging a chip
  onto another chip rather than onto the zone. An accent rule lights up on the
  top or bottom edge of the chip you are hovering, showing the side the dragged
  channel will land on; drop it there. Dropping onto the zone's empty background
  still sends the channel to the end of the list, and a channel dragged from the
  **Channels** tray can be dropped straight onto a chip to arrive in position.
- **Fixed panel** — every root dimension **not** on an axis is listed under
  **Fixed**, pinned to a single value you choose. Drag a root out of Fixed onto an
  axis to make it vary; click the **×** on a root to drop it back to Fixed. Wells
  come from the sidebar plate; changing that selection — including **Select all /
  none** — re-runs the layout in place.
- **Per-channel render options** — each channel chip has a **⚙** menu: **Raw /
  Corrected**, **Mask**, **≥ threshold** (renders that channel's
  ON-cutoff binary — ThreshFracOn, or ThreshCountOn when the selected Property
  is a spot count — positive cells white), **Colour cells**, a **boundary** outline
  overlay, and a **colormap + LUT** min/max. The options travel with the channel
  wherever it sits, so the same channel looks identical on either axis. Remove a
  chip with its **×**.
- **The render is part of the row** — drop the same channel twice and give each
  chip a different render to see them side by side: DAPI's corrected image on one
  row and its cell mask on the next. Each render gets its own row, labelled
  **DAPI**, **DAPI (mask)**, **DAPI (raw)** or **DAPI (binary)**, so a channel can
  appear as many times as you have renders for it.
- **Cells the analysis has dropped are not drawn.** A cell you deleted on the
  Segmentation tab, one a crop excluded, one that failed the QC debris gate, and
  one outside your gating all get no outline when the table renders, and do not
  appear in the **Mask** or **≥ threshold** views — the table shows the same
  population every plot, statistic and export counts. A grid drawn *before* such
  an edit does not repaint itself (re-rendering a plate is seconds of work): the
  header cues **edited — press Generate**, and the grid is current again after
  that. The one place that still shows every segmented cell is the pipeline's
  own **NUC+SEG** overlay channel, whose boundaries are drawn into the saved
  image before All-Well ever sees it.
- **Colour cells** — in the **Mask** and **≥ threshold** views, give every cell its
  own colour instead of painting them all white. Two nuclei that touch are
  otherwise a single white shape, which is exactly the mistake you are looking at a
  mask to catch. The colour is a fixed function of the cell's label id, so it does
  not change between redraws, and it is the same colour the Playground gives that
  cell. Off by default; it has no effect on a **Raw** or **Corrected** row, where
  there are no per-cell labels to colour, and the menu item is greyed out there.
- **Separate ↔ Overlay** — a switch on each root's channel group: **Separate** shows
  one cell per channel; **Overlay** additively blends that root's channels into one
  composite cell (LUT-coloured, clipped to white).
- **Wrap cols** folds the columns into vertically-stacked bands of *N* (handy for a
  long column axis), repeating the whole rows axis under each band. It works with
  any axis pairing, including one whose channels are nested under a root — with,
  say, **FOV › Channel** down the rows and **Wells** across the columns, wrapping
  at 3 gives three wells per band and repeats every FOV/channel row beneath it. The
  one place it is unavailable is **Plate A–H × 1–12** below, which pins both axes to
  the plate's own rows and columns and so has nothing left to band; the box is
  greyed out while Plate mode is on. One thing a wrapped table gives up: the column
  headers along the top disappear. There is a single header strip for the whole
  grid, and a column that holds A01 in the first band and A04 in the second cannot
  be described by one label — so those labels are dropped rather than shown stale.
  The row labels are unaffected and repeat under every band. **Clear builder**
  empties the shelf and starts fresh (it leaves the rendered images — use
  **Clear images** for those).
- **Plate A–H × 1–12** lays each selected well's image out in its true 96-well-plate
  position — rows labelled **A–H**, columns **1–12** — using the first channel in the
  builder as the image. Toggle it on in the shelf header; wells come from the plate
  selection (a partial selection renders a correctly-labelled partial plate), and it's
  mutually exclusive with **Wrap cols** — dragging any root or channel onto Rows /
  Columns switches Plate mode back off. Prefer this over well-on-columns + Wrap when
  you want a plate view: a wrapped grid loses its column headers entirely (see
  above), whereas Plate labels every row and column correctly. Because both axes are the plate, Plate mode shows **one
  channel** — the first in the builder. The LUT panel offers a row for that channel
  only and names the others as not shown, so you are never editing a brightness
  nothing on screen reads.
- **Cells marked `raw`** have no corrected image for that field, so the table is
  showing you the uncorrected frame instead. It still carries the camera offset and
  the background that correction removes, which puts it on a different brightness
  scale from its neighbours: one LUT window cannot suit both, and a window set for
  the corrected cells usually leaves the marked ones washed out and unresponsive to
  further LUT changes. They are also left out of **Auto**'s pooled brightness window,
  so they cannot darken the rest of the grid. The status line after **Generate**
  counts them; the badge says which. If a whole block of the plate is marked, the
  correction never ran for those wells — check the run's `errors/` folder. An
  **Overlay** cell and a **ratio** cell are marked when *any* of the channels they
  are built from was served raw — a blend or a ratio carrying one uncorrected
  input is an uncorrected image — and the status line counts marked *cells*, so
  the number matches what you can see rather than counting a three-channel
  overlay three times.
- **A well whose images could not all be listed says so.** If reading a well's
  archive stops part way — a damaged zip, a drive that drops out — the table
  still draws the frames it did get, because a partial answer beats none. But a
  short list of real images looks exactly like a well that genuinely holds that
  few, so the status line after **Generate** names the wells whose inventory was
  cut short and why, and a PNG saved from the table repeats it: that file
  outlives the status line and is short by however many frames were never
  reached.
- **Action strip** — **Generate** · **Table ▾** (*Reset table layout*, back to
  defaults; *Clear rendered images*, which keeps the layout so Generate re-renders
  in one click) · **Crop view ▾** (drag a region on any image — the button reads
  the current crop, `Crop view · (full FOV)` when there is none). This crops what
  the images *show*; it is not the Segmentation tab's **Crop cells**, which
  excludes cells from every plot, statistic and export. · an **ON threshold** value
  with a **channel** dropdown choosing which channel it applies to (the same
  cutoff shared with Cell Gating and the plot tabs; it follows the picked
  channel, and reads 0 for the nuclear channel until you set one). It also
  follows the selected **Property**: with **smFISH Count** selected the caption
  reads **ON spots**, the box shows a spot count, and it edits that channel's
  ThreshCountOn instead. Caption, value and write target move together, so the
  number you see is the number you are changing. A channel with no spot-count
  column has no count cutoff, so it keeps editing its ON threshold whatever the
  Property says · **Export ▾** (to file ·
  Copy PNG / SVG · Save to Saved Plots) · **zoom** (− · the current % · +; click the
  % to reset to 100) · a **Properties** button that slides out a
  right-side panel for the grouped view's **spacing gaps** and **label font sizes**,
  plus the **Scale bar** and **Cell labels** options below.
- **Scale bar** — **on by default**: enter the **pixel size (µm/px)** and a **bar
  length (µm)** in the Properties panel to burn a scale bar into the images (pixel
  size isn't stored by the pipeline, so you supply it). Choose which cell(s) show it
  (**every cell**, or a single grid corner), where in the image it sits, its
  **thickness**, and its colour (**Auto** picks black or white for contrast against
  the pixels underneath). The bar is drawn into the pixels, so the live grid and the
  exported figure match exactly.
- **Cell labels** — set **Mode → Identity** to burn each cell's *well · channel ·
  timepoint* label into the image (respecting custom Well Labels), with the same
  which-cell / in-image-corner / colour / background-box controls as the scale bar —
  handy for a single-panel figure where the axis strips don't apply.
- **Matched brightness** — a channel left on **Auto** LUT now shares **one** intensity
  window across every cell of that channel (pooled over the grid), so the same channel
  is directly comparable cell-to-cell; set an explicit LUT min/max on the channel to
  override.
- **Pixel readout** — hover a rendered cell to see that pixel's intensity (prefixed
  with the cell's combo identity), plus the segmented cell's id and **mean intensity
  (MFI)** when the cursor is over one; the readout now stays put when the cursor comes
  to rest instead of reverting to the plain cell tooltip.
- **Per-cell tweak** — right-click any rendered cell to override just that cell's
  well / channel / timepoint / FOV.
- **Exported layout** — every way out of the table (**Export to File…**, **Copy PNG**,
  **Copy SVG**, **Save to Saved Plots**) reproduces the grid as you see it: the row and
  column axis labels, the vertical group strips, the corner label, and the extra
  spacing between groups. The saved image also reads at the same brightness as the
  grid on screen. Because the axis labels already identify every cell, the per-cell
  titles are off — switch **Show titles** on in the Export settings dialog if you want
  them repeated above each image as well.
- **Save to Saved Plots quality** — a per-cell/whole-grid resolution preset (**Draft**,
  **Standard**, **High**), picked either from the Export dropdown's quick-pick submenu
  or the Export settings dialog. Save to Saved Plots pre-shrinks each cell and caps the
  assembled grid's longest side before saving (Export to File / Copy PNG / Copy SVG
  always render full resolution), so a large grid saves faster at the cost of per-cell
  detail; raise it to **High** when the saved image needs to hold up in a full-page PDF
  report. Save to Saved Plots also reuses whatever the last **Generate** already
  decoded, and warms anything it hasn't with the same parallel prefetch Generate uses —
  saving right after generating decodes nothing twice.
- **Cells that can't be drawn say so** — a cell never shows a different picture
  than the one you asked for. `(no image)` means nothing decoded for that well /
  channel / timepoint / FOV; `(no mask)` means the cell's **Mask** or
  **≥ threshold** view has no segmentation mask for that frame (the intensity
  image is *not* shown in its place, so a threshold view is never half
  silhouettes and half pictures — and such a cell draws from its mask alone, so a
  missing fluorescence frame does not stop it); `(render failed)` means that one cell errored —
  the rest of the table still draws, and the status line counts how many cells
  could not be drawn. A cell that failed because the source was briefly
  unavailable (an external drive spinning down, a folder still being written)
  comes back the next time you press **Generate** — nothing is written off for
  the session, so there is no need to reload the dataset.
- **Large grids** — the images a Generate decodes are held in one bounded cache
  sized to your machine's RAM, and nothing else keeps them: the grid on screen
  holds thumbnails, not full-resolution frames. A grid whose images exceed that
  budget therefore re-reads some of them on the next pass, so a plate-wide walk
  of large frames takes longer to Generate — but it stays within memory instead
  of failing to load images part-way through. If a big grid is slow, narrow the
  well or timepoint selection, or crop the view.
- **Well labels** — where a well would show its `A01` position (axis / group labels,
  cell tooltips, exported labels and titles), its custom label is shown instead when
  one is set under Home → Well Labels.
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
- **From scatter gate.** Ticking this sources the gallery from the gate(s) drawn
  on the Scatter Plot instead of the criteria — so the criteria rows and
  **+ Add criterion** grey out, with a line saying why. The setting sticks until
  you untick it, which is worth knowing if you come back later and find the
  criteria editor inert.
- **Optimal use** — pair it with the Scatter Plot in Split View: gate a
  population on the scatter and watch the matching cells populate the gallery.

#### Playground

**What it shows.** An interactive testbed for the pipeline's
background-flattening / subtraction methods **and its StarDist segmentation**,
on a **single frame**, without re-running the pipeline (it shares the pipeline's
exact correction and segmentation code).

- **Needs** — nothing. With a dataset loaded it works on that dataset's frames
  and can show the pipeline's saved mask alongside; with no dataset at all,
  **Open image…** loads any TIFF from disk. That second source is what makes
  this usable *before* a plate has been run, which is when segmentation settings
  most want choosing.
- **Choosing the frame** — either pick the **well on the ever-present left-rail
  plate** (single-well here — the tab has no plate of its own, it drives the
  sidebar picker) plus the **channel / timepoint / FOV** dropdowns, or press
  **Open image…** and choose a file. While a file is open the dropdowns grey out
  and **Use dataset** returns to the plate. A lone file has no reference well to
  sample, so the `empty_well` and `basic` correction modes cannot run on one —
  the status line says which correction was skipped rather than showing you an
  uncorrected frame labelled as corrected.
- **Correct up to** — the first control in the Correction section, and the one
  that decides what everything below it does: **Raw** shows the unprocessed
  frame and runs no correction at all, **Flat-field only** applies the
  flat-field and ignores the background method, **Corrected** applies both (what
  a run produces). Controls the chosen stage does not use are greyed out, so you
  can see at a glance why one is not responding.
- **Correction controls** — all in the **right-hand panel**, where every other
  per-tab inspector in the app sits: any flat-field + background method and its
  parameters. Correction re-runs as you change them; segmentation waits for the
  Segment button.
  - The `empty_well` and `basic` modes first estimate a field from every frame
    of the **Bg well** you choose, for the channel you are viewing. That takes a
    moment the first time you pick a well, a channel or one of those two modes;
    afterwards it is cached. If the chosen well has no readable frames for that
    channel, the read-out says the field could not be built and names it — and
    the Stage line then shows the correction that *actually* ran, which is
    `none` for that half. A correction that could not run never looks like one
    that did.
- **Display controls** — LUT / colormap / binary + outlines; pan / zoom;
  per-stage LUT memory; a frame-local binary threshold; a hover pixel-intensity
  readout; a QC size-cleanup preview.
- **What you're looking at** — the read-out at the bottom of the panel. None of
  what it says is visible in the picture, which is why it is written out:

  - **Frame** and **Stage** — the frame on screen and the correction *actually*
    applied to it (not what the controls say, if the stage ignores some of them).
  - **Image** — the displayed image's **range and mean**. Worth watching: the
    LUT auto-scales to whatever is on screen, so a background change can leave
    the picture looking identical while the data underneath it moved. If these
    numbers change, the correction ran.
  - **Mask** — where the outlines came from, how many labels it holds, how many
    the QC size filter removed, and a warning if the mask does not fit the frame.
    *pipeline mask* is the mask a run already produced: it does **not** respond
    to the Correction controls, because changing a correction does not
    re-segment. *live StarDist* is what this tab computed; press **Segment** to
    refresh it.
  - **Shown** — exactly what is drawn. This matters most in Binary mode, which
    can mean three different things that all look like white shapes on black:
    each label filled when its **mean** intensity clears the threshold (the real
    per-cell measurement); every **pixel** above the threshold, if there is no
    usable mask; or every labelled pixel with the threshold ignored. The
    read-out names which one you are seeing, and where the threshold came from —
    set on this tab, or the channel's app-wide ON cutoff (its ThreshFracOn, or
    its ThreshCountOn when the selected Property is a spot count).
  - **Colour cells** (on by default) gives every cell its own colour in Binary
    mode instead of painting them all white. Two nuclei that touch are one white
    shape otherwise, so a segmentation that merged them looks exactly like one
    that separated them. Each colour is fixed to the label, so it does not
    change as you redraw. The per-pixel fallback stays white — there are no
    cells there to colour.
- **Segmentation controls** — tick **Live StarDist**, set **Probability** and
  **Overlap (NMS)** (blank = the pretrained model's own values), and press
  **Segment**. The nuclei it finds replace the pipeline's saved mask in the
  overlay, so you can see what a threshold change actually does. The status line
  reports the nucleus count and the thresholds that produced it.
  - **Cells** — directly under the Segment button, the count the thresholds are
    being tuned against: how many objects the current picture actually draws,
    what the QC size filter dropped, and — in Binary mode with a usable mask —
    how many of them clear the threshold. It reads **—**, not `0`, whenever
    there is no count to give: no mask at all, or labels that no longer describe
    this frame. Zero cells and an unknown number of cells are different answers.
  - **The first Segment of a session is slow** — around 10–30 seconds, because
    it loads TensorFlow and the StarDist model into the application, and about
    1–2 GB stays in use until you quit. Every later Segment takes a few seconds.
    The status bar at the bottom of the window shows a progress bar while it
    runs and says which phase it is in, so the long first load is visibly a load
    rather than a hang. Building an `empty_well` or `basic` reference field
    shows the same bar, counting the frames it samples. Ordinary corrections do
    not: they run on the interface's own thread, take a second or two, and
    finish before a bar could tell you anything.
  - Change the frame or any correction setting and the labels no longer describe
    what is on screen, so they are **removed** and the status line says they are
    stale. Press **Segment** again.
  - Segmentation always runs on the **corrected** frame, whatever stage you are
    looking at — that is what the pipeline segments. If you press Segment while
    viewing Raw, the status says so rather than letting the count look like it
    came from the picture.
- **Optimal use** — two jobs, both here. *Before* a run: **Open image…** a
  representative frame, dial in a correction method and the two segmentation
  thresholds, then type those values into the Analyze tab's matching fields and
  run the plate. *After* a run: load the dataset and check a well that came out
  wrong, comparing live segmentation against the pipeline's own saved mask.

### smFISH

**What it shows.** Single-molecule FISH spot detection on channels you
configured as smFISH, with a parameter sweep and an **Apply to All** threshold.
Operates on **one well at a time** (spot detection is per-frame).

- **Needs** — at least one channel configured as smFISH at pipeline time (its
  CSV carries a `_smfish_count` column) and image ZIPs. Selecting several wells
  collapses the view to a single well.
- **Controls** — **Channel / FOV / Timepoint** pick the frame, **Threshold**
  sets the spot-detection cut-off (it writes the CSV's `smFISH_Thresh`), and
  **LUT Min / LUT Max** set the display brightness. On the right: **Apply Global
  Threshold** to push one threshold across the whole dataset, **Refresh**,
  **Hide Overlays**, and **This Frame CDF**.
- **Apply to All progress** — the run counts spots in every well, then writes
  the counts into the per-well CSVs. Both passes drive the progress bar in the
  status row at the bottom of the window — the same bar a dataset load uses —
  alongside a per-well message on the tab's own status line. The bar clears
  when the run finishes, is cancelled, or fails. The app stays usable while it
  runs; switching datasets mid-run cancels it rather than writing counts into
  the wrong folder.
- **Optimal use** — sweep the threshold on one representative well, confirm the
  spot overlay, then Apply to All.

### Review CSV

**What it shows.** A tabular view of the loaded per-well CSVs — the raw
per-nucleus rows behind every plot.

- **Controls** — the **Well** picker; column filtering; **double-click a row**
  to jump to that cell in the Segmentation tab. The jump switches wells for
  you: the row's well becomes the Segmentation sidebar's selected well, and the
  view opens on that cell's field of view and timepoint, zoomed to the nucleus.
- **Optimal use** — sanity-check outliers: filter to a suspicious metric range,
  then double-click into Segmentation to see the actual cell.

### Saved Plots

**What it is.** A per-dataset gallery of plots you have captured. Hover any plot
panel's top-right corner and click the **save** button to send that panel —
plus the exact parameters / thresholds that produced it — here. On a multi-panel
figure a **one-click whole-figure save** captures the entire figure at once
rather than a single panel.

- **Per-card controls** — **Restore settings** (re-apply the plot's saved
  parameters and take you to the tab it was captured from, so you arrive looking
  at the figure it recreated), **Export**, **Rename**, **Delete**, and a
  collapsible parameter summary.
  - A saved **Image Table** grid comes back as its settings: wells, timepoints,
    channels, LUTs, layout and scope, all filled back into the shelf. The
    pictures are not re-read from disk — that tab draws when you press
    **Generate** and at no other time, so a restore never spends minutes
    decoding a grid you may only have wanted the settings from. The shelf header
    says **edited — press Generate** so it is clear the images below it are not
    yet of what the shelf now describes.
  - A plot saved before its tab began recording its own settings can only
    restore the settings shared across tabs — channel, metric, wells, gating.
    The status line says so by name when it happens, because a silent partial
    restore looks exactly like a broken one. Re-save the plot from the tab and
    the new card carries everything.
  - A setting can also fail to come back for a reason particular to this
    dataset — a saved channel it does not have, a well that is not in it. Each
    setting is restored independently, so one failing never costs the others,
    and the status line names how many were missed and which ones. The same
    line goes to the log drawer, where it stays after the status line has moved
    on.
- **Clear all** asks first, and only clears if you say yes. If the
  confirmation cannot be shown at all, nothing is cleared — an answer that could
  not be obtained is not consent.
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

Pick the mode from the tab strip in the window's top band — *Line · Bar ·
Scatter (cells) · Scatter (aggregate)*, beside the Review / Analyze / Datasets
selector, where every other page's tabs are. The groups you build are **shared
by all four modes** — define them once and they are there whether you export
Line, Bar, Scatter (cells) or Scatter (aggregate). They describe your plate's conditions, not the shape of
the plot. (Datasets analysed before this carried a separate set per mode; those
are merged on first open, with a group appearing in more than one of them kept
just once.)

Groups are built on a **plate map**, the same plate control used everywhere else
in the app rather than a separate grid of squares. Left-drag across it to add
wells to the selected group, or drag across wells the group already has to
remove them — the direction is decided by the well you start on, so one sweep
adds or removes throughout instead of flipping each well under the cursor.
Wells belonging to a **sample definition** are added and removed as that whole
sample, not one well at a time — touch any well of it and the condition joins
the group; touch it again and the condition leaves. A well no sample claims
joins on its own. Each group has its own colour and a well takes the colour of
the group that owns it; the wells of the group you are **currently editing** also carry an accent
ring, so "which group is this well in" and "which group am I editing" stay
readable at the same time. Wells the dataset doesn't have are drawn as empty
sockets and ignore the drag.

Batch figures are drawn through the **same renderers as the on-screen plots**, so
a group's figure matches what the plotting tab shows for that selection: each
group is drawn in its own **distinct colour**, **solo wells are labelled by their
sample name** (their Well Label) rather than the raw well token, and line-graph
batch panels carry the **timepoint ticks and x-axis label on every time panel**.
Building a batch no longer writes to the dataset on every click — the panel edits
are held and saved once.

### Starting over

Two buttons at the right of the top band discard saved state, and they divide it
between them. Neither ever touches your image or CSV files.

**Reset view** (the circular arrow) returns the window to how a freshly-opened
dataset looks, in one step: selected wells and group visibility, fold-change
scopes, per-tab controls, plot and figure preferences, draw order, the heatmap
arrangement, the Multivariate configuration, and the Image Table grid. Nothing it
clears is a decision about your samples, so it asks once and clears the lot.

**Reset analysis** (the trash can) discards decisions, and asks which ones. It
opens a checklist — everything ticked by default — and clears only what you
leave ticked, then reloads the dataset:

| Item | What you lose |
|---|---|
| **Sample definitions** | Well labels, groups, notes, plot groups. |
| **Gating** | FluorGating floors, ThreshFracOn cutoffs, drawn scatter gates, and the QC debris gate — the last returning to the settings the pipeline run itself used, not to no gate at all. |
| **Curation** | The crop region and every manual include/exclude on individual cells. |
| **Ratio metrics** | Every ratio channel you defined. |
| **smFISH thresholds** | The saved per-channel thresholds. |
| **Batch Export groups** | The export groups on each Batch Export panel. |

Each item is cleared everywhere it is stored — some of this state lives in
`persistence.json`, some in `pipeline_info.json` beside your data, and manual
curation in its own `cell_overrides.json` — so a reload cannot bring back
something you asked to discard. If one item fails (an unreadable file, say), the
others still run and the status line names the one that did not.

Smaller resets stay next to the thing they affect: **Clear all groups** on
Home → Groups, and **Reset crop + curation** in the Segmentation
tab's Edit menu, which is the same action as the Curation checkbox above.

---

## Datasets mode — dataset-level tools

Switch to **Datasets** with the title-bar segmented control (the third segment,
beside Review and Analyze). Unlike Review there is no plate/well picker — the left
sidebar is just a list of dataset-level tools.

### Convert OME-TIFF

Turns a single packed OME-TIFF (one file holding every well, channel, Z-plane and
timepoint of a plate scan) into an ordinary dataset folder of per-well images —
the layout Analyze processes. Do this first, then open the result in Analyze.

1. **OME-TIFF source** — pick the `.ome.tif` file. All-Well reads its metadata
   immediately and suggests a **Destination folder** (`<name>_dataset` beside the
   file); change it if you like. The readout then shows the file's **target** —
   how many stage positions (or, for a single packed image, frames) it holds, and
   once you set FOVs per well, the well count that implies (positions ÷ FOVs). Use
   it to guide how many wells to place before you touch the grid or the plate.
2. **Which wells did you image?** — this group has one job: produce the order in
   which the stage visited the wells. **Well grid (rows × cols)** is the block you
   imaged (e.g. `2 × 3`), **Origin** is its top-left well (default `A01`),
   **Sweep** is the axis the stage swept along (by row or by column), and
   **Snake** is what it did at the end of each line. Ticked, alternate lines run
   backwards — the stage never travels back across the plate empty. Unticked,
   every line runs the same way (a raster), the stage flying back to the near
   edge between them. Get this wrong and every second line is labelled
   *backwards* while the well count stays right, so it is worth a glance at the
   plate below, which shows the resulting path live.
3. **How was each well sampled?** — **FOVs per well** is the fields-of-view per
   well; leave it blank to derive it, or enter a value and the converter refuses
   to run if it doesn't match the file. **Frame order** appears here for a
   single-image file (step 6).
4. **Acquisition path** — the plate mockup below the settings shows how stage
   positions map onto the plate: each visited well is numbered in visit order and
   a subtle arrow traces the sequence across the plate. Entering a grid seeds this
   with that path automatically, so for a normal rectangular scan you
   only need to glance at it to confirm the labelling. If your scan **isn't**
   rectangular — an L-shape, a plate with rows left out, a scattered subset — the
   converter reads it straight from the stage coordinates and fills the plate in
   for you, describing it as e.g. *5 wells in a 3×3 block*; the wells you never
   imaged stay empty and no archive is written for them. Skipping whole rows or columns is
   read correctly as long as some part of the scan visits two neighbouring wells
   — if *every* step skips (say rows A, C and E in one column, and nothing
   else), the file gives no way to tell that from three wells side by side, so
   check the plate and correct it by clicking if needed — unless the file
   records the *unit* of its stage coordinates, which most do. Then the spacing
   is known in millimetres rather than only relative to itself, any selection of
   wells maps exactly however scattered it is, and a plate-referenced file fills
   in the starting well as well as the shape. Rows and columns have
   to be on the same spacing, as they are on any standard plate; if they are
   not, the converter says so rather than guessing a grid. To define a visit order
   by hand instead, or to correct one, click
   the wells directly, in the order the stage visited them: the first click is
   position 1, the next is position 2, and so on. Clicking a numbered well removes
   it and the rest renumber.

   **The two are alternatives, not layers.** Your first click takes the path over
   from the grid — the wells the grid was showing are dropped, so position 1 is
   the well you clicked and nothing you did not choose is in the order. The grid,
   origin and serpentine boxes then grey out, with a note saying so, because a
   hand-drawn path is what will be used and those three no longer reach the
   conversion. **Generate path from grid** refills the plate with the serpentine
   of the current grid as an editable starting point; **Clear path** empties it,
   hands the three boxes back, and returns to the live grid preview.
5. **Workers** — beside the **Convert** button, since it changes only how fast the
   run goes and nothing about what comes out. How many wells to convert in
   parallel (blank = auto, capped at the number of wells). Lower it if converting
   to a single spinning disk feels slower rather than faster.
6. **Frame order** — *shown only for a single-image file that packs every
   position into its T axis*. Because an OME image has no position dimension, the
   positions live in the T axis, possibly interleaved with real timepoints. Say
   how the frames are ordered: **Single timepoint** (every frame is a distinct
   position), **Positions within timepoint** (all positions, then the set repeats
   next timepoint), or **Timepoints within position**. Positions come from your
   grid × FOVs; timepoints are then `frames ÷ positions`.
7. **Channel roles** — one row per channel read from the OME metadata, each with a
   role: **Nuclear / segmentation**, **Fluorescent**, **Phase / brightfield**, or
   **Skip**. The roles are seeded from the channel names: a `DAPI` / `Hoechst`-type
   stain is guessed as the nuclear/segmentation channel, or failing that a
   `NIR` / `RFP` channel (the usual far-red segmentation markers), with the rest
   fluorescent; change any of them. A channel whose name reads as brightfield
   (`DIC`, `BF`, `Phase`, `Trans`) is never guessed as the segmentation channel
   unless there is nothing else to segment. This is only a convenience: the channel names are always written into the
   output filenames regardless, but the roles you set here **pre-fill Analyze's
   channel-token fields** when you open the dataset there, so you don't retype them.

The readout shows how the file splits into wells × FOVs × timepoints and lists the
channel names (any Z-stack is **maximum-intensity projected**; labels stay within
the 96-well plate `A01`–`H12`). It names the **wells at both ends of the order**
and says which input produced them — `4 well(s) C03→D03 (clicked path)` — so you
can see at a glance that the run starts where you meant it to, which a count on
its own cannot tell you. A line below it in red says why **Convert** is unavailable:
a grid that doesn't divide into the file's positions, a box that isn't a whole
number (FOVs per well, Workers and the grid all say so rather than quietly
treating a typo as "auto"), or a destination folder that already holds a
converted plate. Press **Convert** — the acquisition plate above
becomes the progress view: each well fills clockwise as its images are written,
the same dial every other plate map in the app uses, and the plate locks against
clicks until the run finishes so the order being converted can't be edited
under it. A progress bar for the run as a whole appears below (Cancel stops it
cleanly), and when the run ends a line under that bar says how it went — a green
summary, or the reason in red if it failed or was cancelled. You never have to
open **Show raw log** to find out whether it worked. The output is one
per-well `<WELL>.zip` in `<destination>/in/`, the layout Analyze reads directly
(no regrouping step).

**If a conversion is interrupted, running it again finishes it.** Each well is
written in one piece, so whatever was completed before the interruption is
already correct on disk. Point the same source at the same destination with the
same settings and the readout says *"Resuming an interrupted run: 12 well(s) are
already converted and will be kept"*; only the missing wells are converted, and
the summary at the end says how many were kept. Change anything that alters
which stage position becomes which well — the grid, origin, serpentine
direction, FOVs per well, the clicked path, or the source file — and it is
refused instead, because the two runs would produce files with the same names
from different images. Empty the folder and start again in that case. Then **Open dataset in Analyze** — the Segmentation /
fluorescent channel tokens arrive pre-filled from the channel roles you set (step
7; adjust if needed), so you can run straight away. Because the wells now exist on
disk, background-well and control-well options work normally.

**The file is checked as soon as you pick it**, and a file that can't be
converted honestly is refused there rather than part-way through a run. The check
reads metadata only — no images — so it costs nothing however large the plate is.
What it catches, and what each one means:

- **"has no readable OME metadata"** — the file is named `.ome.tif` but its OME
  block can't be read. A copy or download that stopped part-way does this: what
  is left is still a valid TIFF, so it opens without complaint and reads as a
  small fraction of the plate. Check its size against the original and copy it
  again. (A plain `.tif` image stack is fine and is not caught by this.)
- **"carries OME metadata that does not describe its pages"** — the OME block is
  there but doesn't match the images, so which channel and timepoint each plane
  belongs to is a guess. Re-export from the acquisition software.
- **"declares SizeT=… but the image series it produced holds …"** — the header
  and the pages disagree about how many frames there are.
- **"is incomplete: … carry no image data"** — the metadata declares planes the
  file has nothing behind, which would land in the dataset as solid black images
  indistinguishable from real ones. The message names the likely cause, and the
  three need different fixes: **companion files** listed but not in the same
  folder (put them back beside the source), an **acquisition stopped early** (the
  planned dimensions are in the header — re-export with the dimensions it really
  holds), or a **broken index map**, where the message says "No image data is
  missing" — every image is in the file and only the metadata is wrong, so
  re-export or repair the metadata rather than re-running the plate.
- **"has 2 channels whose names all reduce to the filename token …"** — two
  channels (or two timepoints) whose names differ only by spacing or punctuation,
  such as `488 nm` and `488nm`, or `GFP-1` and `GFP_1`. Converted images are
  named from those labels with everything but letters and digits stripped, so
  both channels would be written to the same filename inside a well's archive and
  only one of them would be readable afterwards — while the conversion reported
  writing both. Rename the channels in the acquisition software so they differ by
  more than punctuation, and export again.

**Timepoints are named from the times the file records.** Where an OME-TIFF
carries a `DeltaT` for each frame, the converted filenames use it — `0h`, `1h`,
`24h` — whatever unit the file states them in (milliseconds, seconds, minutes,
hours or days). If the file states a unit All-Well does not recognise — or
records times that would give two timepoints the same name — the timepoints are
named by position instead (`t0`, `t1`, …): the order is still right and nothing
claims an elapsed time the converter could not work out.

All-Well looks for those coordinates in **both** places a file keeps metadata:
the OME block, and the original metadata the microscope wrote, which conversion
tools copy in alongside it. Plenty of files record positions only in the second.

**Not every OME-TIFF carries stage coordinates anywhere.** When yours does not, the page
says so where the suggestion would have been, **and names the metadata the file
does hold** — how large its OME block is, and which of the instrument's own
blocks it carries. Open **Show raw log** for a sample of that metadata's keys.
That sample is what to send if a file you believe records positions still comes
back with none: the keys say whether the coordinates are truly absent or merely
spelled in a way All-Well does not match yet.

The message reads — *"This file records no stage
coordinates, so the well grid cannot be read from it"* — and you enter the grid
and FOVs per well yourself. If what you enter doesn't fit, the refusal now names
the splits the file's frame count *can* take (`14 position(s) × 7 timepoint(s)`,
and so on): a frame count has only so many divisors, and seeing them is usually
enough to recognise your own experiment.

**Most of the form fills itself from the file.** Microscopes record where on the stage
each image was taken, and All-Well reads those coordinates: wells sit 9 mm apart
while the fields of view inside one well are a fraction of a millimetre apart, so
the grid falls out of the spacing. When the file carries them, a line under the
settings reads *"Read from the file: 2×3 wells × 4 FOV(s), snaking by row, 4
timepoint(s), starting at C04"* — and, on a form you have not answered yet, it is
already applied. Between the coordinates and the rest of the metadata that
covers the **well grid**, **FOVs per well**, **serpentine direction**,
**frame order** (how the packed frame sequence splits into positions and
timepoints — read from which coordinates repeat, and how), the **channel names
and roles**, and the **timepoint labels**. A scan that isn't a plain serpentine
is handed over as a clicked path instead, since the grid fields cannot express
one.

The **starting well** usually comes from the file too. Many plate scanners
report positions in the *plate's* own frame — A01 at the origin — so every well
centre is a whole number of well-pitches out and the well is arithmetic: a block
at 27 mm, 18 mm is C04. All-Well believes that only when every well in the scan
lands on its grid position within a third of a pitch and inside the plate;
otherwise the coordinates are in the microscope's own frame, with an arbitrary
zero, and say nothing about the plate. Failing that, acquisition software usually
writes the well into each position's name — `B02`,
`Well B2, Site 1`, `scan_B02_f0` — and when every position parses, All-Well uses
those names directly: the origin is filled in and the whole visit order comes
from the file rather than from a grid. Only when the names say nothing does the
**Origin** box need you.

A suggestion never overwrites an answer you have given. It applies itself while
the form still holds its defaults; edit anything and it stops, leaving
**Apply again** to re-apply on demand.

**And you can always correct the starting well.** When the file supplies the
order itself, the plate fills in but **Origin** stays live — type another well
and the whole order moves there, keeping its shape. That matters because the
starting well is the one thing the coordinates can get wrong in a way only you
can see. Clicking the plate still takes the order over completely, and *then*
the grid controls grey out, because a hand-drawn path replaces them.

**If it says the run does not fit a plate, check the field spacing.** The well
grid is read from the *spacing* of the coordinates — nothing about the shape of a
well is involved — by looking for gaps between wells that are wider than the gaps
between fields inside one. A dense montage leaves no such gap: sixteen fields
spaced a quarter of the well pitch apart tile the well exactly, every step in the
file is identical, and the scan reads as one well per field. When the file
records the unit of its coordinates the converter recovers from that by itself;
when it does not, enter the grid and FOVs per well and the plate map follows what
you typed.

**A batched remote run queues once, not once per batch.** With **Remote batch size** set, the plate is uploaded and processed in slices so the cluster never holds all of it at once. The compute node is now requested a single time and held for the whole run, so on a busy cluster you wait in the queue once instead of once per batch. Two consequences worth knowing: the `--time` in your host's launcher is scaled up automatically (it sized one batch and now covers the plate, plus one batch's worth again for the transfers that happen while the node is held, capped at 24 hours — the log says what it asked for), and a batch that fails no longer ends the run. The remaining batches still process, and the summary names which ones failed so you can re-run just those.

If you start the same plate with the same settings from a second window while the first is still running, the second one stops and says so rather than starting: both would use the same folder on the cluster, and the second would clear the first's input out from under it. Wait for the first to finish, or change something about the run.

**The stage path is drawn under the plate**, at the same scale, whenever the
file carries coordinates. **Scroll on it to zoom, drag to move around, and
double-click to fit the whole run again.** Each position numbers itself in
acquisition order as soon as there is room to print the number — so zoom into a
well and its fields of view are labelled 1, 2, 3… in the order the stage took
them, which is the detail a plate map can never show. A number is only printed
where it does not touch one already printed, so on a dense scan some are left
off rather than piling on top of each other; zoom in further and the rest
appear. Numbers that overlap are not numbers, and a run of several thousand
positions is labelled with four digits apiece. If the run turns out not to fit a 96-well plate at
the well spacing read from the file, the drawing is scaled down to show all of
it and the caption says so — a square is then no longer a well, and it usually
means the grid and FOVs per well need checking too. Otherwise: one dot per image — not per well — joined in the order
they were taken, with a larger green dot where the scan starts.
**If the map comes out mirrored, flip it.** Under the drawing are two
checkboxes — **left ↔ right** and **top ↔ bottom**. A microscope's stage axes are
not the plate's: depending on how the plate sits in the holder, a scan that swept
left to right can be recorded with X running the other way, and read straight the
whole block is mirrored. Every count still looks right, so the giveaway is the
picture: the path starts at the wrong end, or runs off the side of the plate.
Ticking a box reflects the coordinates and re-reads everything from them — the
suggested grid, the visit order, the map, and the wells the conversion will
actually write into — so the drawing and the run stay in step. If you have typed
your own grid, that is left alone and only the drawing moves, which is what a
grid that no longer matches the coordinates should look like.

**A timelapse is drawn once.** The stage walks the same route at every
timepoint, so drawing every coordinate would be one map laid over itself a
dozen times — each dot on top of the last, and the numbers running to a
dozen times the number of places actually visited. Only the first visit to
each place is drawn, and the caption says how many coordinates the file
holds so you can see that is what happened. The tight
clusters are the fields of view within a single well, which the plate map above
cannot show at all: every field in a well is the same square there. Use it to
check the shape of the run before converting — that the block sits where you
think, that the snake turns the way you think, and that the fields inside each
well were taken in the order you expect. It is anchored at whichever well
position 1 will land in: the **Origin** you typed, or the first well of a
hand-drawn path.

### Concatenate datasets

Use this when the *same plate* was imaged in two separate sessions and you want a
single continuous timelapse instead of two datasets. It appends **dataset 2**
onto **dataset 1**, shifting dataset 2's timepoints so they carry on after dataset
1's last one.

How it works:

1. Fill in the one-line **Dataset 1 + Dataset 2 = Destination** row: click each
   folder icon to pick a folder, and hover a filled-in field to see its full
   path (the box itself shows just the folder name). A field turns up its own
   hint below it if the folder isn't usable — **Dataset 1**/**Dataset 2** must
   each contain both an `in/` and an `out/` folder; **Destination** must be a
   new, empty folder, different from both datasets. Both originals are left
   untouched either way.
2. Once both folders are picked, a status panel appears below showing a
   "Ready — N well(s)" plate preview of what the merge will actually touch —
   before you've pressed anything. Press **Concatenate** and that same plate
   starts colouring per well as the merge progresses (grey → amber "merging"
   → green "merged" with a checkmark, or blue with a "!" if a well's images
   survived without a matching measurements file on one side), alongside a
   phase rail (Validate → Merge → Verify → Finalize) and an overall progress
   bar. The full text log is still there if you want it — tick **Show raw
   log** to reveal it below the plate.
3. On success, click **Open merged dataset in Review** to load the result
   straight into Review (now the page's highlighted default action, in place
   of Concatenate, until you change one of the source folders again).

If something goes wrong — even before any well is touched, like an
incompatible schema between the two datasets — the headline above the plate
turns red with the reason, so the failure is visible whether or not the raw
log is showing.

What it does to the data:

- **Timepoints.** Dataset 2's first timepoint is relabelled to *dataset 1's last
  timepoint + dataset 1's imaging interval* (the typical gap between its
  timepoints), and the rest of dataset 2 keeps its spacing. New labels are
  written in **dataset 1's own format** — so a compound-format dataset 1
  (`00d00h00m`, `00d08h00m`) gets compound-format continuations, not a mix of
  conventions in the same file. Every value a row's timepoint is stored in —
  the label, the numeric hours column, and the image filename it was measured
  from — is shifted together, so dataset 2's data plots and its images open
  exactly like dataset 1's.
- **Wells.** The union of both datasets' wells is written. A well in both is
  stitched (dataset 1's rows, then dataset 2's shifted rows); a well in only one
  is copied across.
- **Everything else** — measurement CSVs, per-well QC-image zips, raw input
  images, saved cell-inclusion overrides, and `pipeline_info.json` — is merged
  accordingly, with dataset 2's timepoints rewritten throughout. Gating
  thresholds and sample definitions carry over from dataset 1 and apply to
  dataset 2's rows too — worth reviewing after the merge, since they were set
  from dataset 1's imaging conditions.
- **Carried-over cell-inclusion overrides are re-stamped against the copy.**
  Each override records a fingerprint of the segmentation it was made on, so a
  later re-run of the pipeline drops overrides that would otherwise re-attach to
  different cells. Copying a dataset re-stamps that fingerprint from the new
  folder's own QC archive. If a well has no QC archive in the destination — a
  run made without QC images, say — the override is carried **without** a
  fingerprint rather than with the source's, which would have looked stale to
  the next check and been thrown away; the log line says how many, and what it
  costs: those overrides will no longer be dropped if that well really is
  re-segmented. Subset and Merge Plates do exactly the same.
- **Before finishing, the merge verifies itself** — that it actually wrote
  measurement data at all (rather than only copying images), that every row's
  timepoint label and its numeric hours agree, that dataset 2's timepoints
  actually land after dataset 1's with no overlap, and that every image a row
  references really is in the merged output. A verification failure aborts
  before anything is marked complete, so a broken merge can never masquerade
  as a finished dataset. A well whose images exist but has no measurements
  (it segmented zero cells) is called out by name in the status window rather
  than silently skipped.

It refuses (with an explanation on the dashboard headline) when the two datasets
weren't analysed the same way (different filename schema, channel positions, or
segmentation method), when a timepoint can't be read as a time, when dataset 1
has only a single timepoint (so there's no interval to continue), or when a
dataset stores its QC images as per-well folders rather than zip archives (a
layout Concatenate doesn't support). A column present in one dataset's
measurements but not the other's — a channel one dataset didn't collect,
say — gets called out too, since those rows will have a blank value for it.

**A dataset that spells one well two ways is refused before anything is
copied** — and the same is true of Subset and Merge Plates. If a folder holds
both `in/A1.zip` and `in/A01.zip` (or two `A01_out.zip`-style QC archives, or
two measurements CSVs for one well under the same prefix), those are the same
plate position written two different ways, which happens when a plate is
exported by one tool and later topped up by another. Everything that looks a
well up by name — the pipeline, Review, and all three copy tools — can only
see one of them, so a copy would silently be a whole well's images short and
the well count would never mention it. The refusal lists every well affected and
every file claiming it, so you can see which two spellings to reconcile. To see
the rest of the plate at the same time, run
[Validate & Repair](#validate--repair-dataset), which reports the same thing as
a finding rather than stopping at the first one.

**If a run is interrupted** (the app is closed, the machine loses power, the
disk fills up) partway through, just point Concatenate at the same three
folders and press Concatenate again. The app can tell an interrupted attempt
apart from a folder you've already finished into or one holding unrelated
files: only a destination it itself marked as in-progress is offered as
resumable, and you're asked to confirm before its partial contents are cleared
and rebuilt. A destination that already looks like a finished dataset is
refused outright rather than risked; a destination with files that were never
part of any concatenation attempt (a plate zipped but not yet analyzed, say) is
refused too, so nothing is deleted on a guess.

### Validate & Repair Dataset

Use this to check whether an Analyze run actually finished — a crash or a
partial run can leave `pipeline_info.json` advertising more timepoints than
`out/` actually holds, so Review would show gaps with no obvious cause and a
plain re-run would skip the very wells that need finishing.

Every run already records its own completeness when it ends (see
[What the pipeline writes](#what-the-pipeline-writes)), and the two answer
different questions. That record is a snapshot of the moment the run finished,
free, and always there. This tool is the on-demand sweep: it re-reads the
dataset as it stands *now*, checks things the end-of-run record does not —
sidecar metadata that has drifted out of sync, CSVs that look like a well's data
but aren't, stale cell-inclusion overrides — and it can repair what it finds.
Reach for it when a dataset has been moved, edited, merged or hand-repaired
since the run, or when the run's own record is missing. Both use the same
definition of a complete well, so they will never disagree about one.

**A completed check writes its own record.** When the scan finishes it stores
what it found into `pipeline_info.json`, in the same block a run writes at its
end — so a dataset produced before that check existed, or by a run with the
check turned off, gets a completeness record from here and Review's **Home**
page fills in. The record says which of the two produced it, and Home words
itself accordingly: *checked \<date\>* for this tool, *run finished \<date\>* for a
run. That matters because they describe different moments — a run's record is
about the run, this one is about the folder as it stood when you scanned it.
If the dataset is already open in Review, going back to Home shows the new
record without reloading. If the record cannot be written — a read-only volume,
say — the raw log says so and the findings are unaffected.

1. Pick the dataset folder. As soon as it's recognized, a status panel
   appears showing a "Ready — N well(s)" preview of the plate, before you've
   pressed anything. Press **Check** and that same plate stays on screen and
   fills in well by well as each one is read, so a scan over a full plate
   shows its progress rather than sitting still until it's done. Each well
   takes its actual state (green with a checkmark = complete, yellow with a
   "!" = missing some `(FOV, timepoint)` work, red with a "!" = no
   measurements recorded, grey = not read yet), and a findings list below names
   anything wrong — exactly which timepoints a well is missing, a sidecar
   that's out of sync with what's on disk, a CSV that looks like a well's
   data but isn't, cell-inclusion overrides that no longer match the
   current segmentation, or a plate position that more than one file claims
   under two different spellings (`in/A1.zip` beside `in/A01.zip`). That last
   one is listed first and counts as an error, because everything that looks a
   well up by name can only see one of the two files, and the Datasets copy
   tools refuse to run on such a dataset at all — the finding names every
   colliding position and the exact paths, which is what you need to reconcile
   them. Before a check there is no findings list on screen at
   all — it appears with the first thing it has to say, and disappears again
   when you pick a different dataset rather than leaving an earlier check's
   results up.
   Where the run left records of its own — the per-well completeness block in
   `pipeline_info.json` and any tracebacks under `out/errors/` — the findings
   use them rather than guessing. A well with images and no measurements CSV
   is reported three different ways depending on what the run recorded: as
   normal ("processed every field and segmented zero cells in all of them —
   nothing to recover"), as a real failure (naming the FOV-groups that failed
   and the traceback files to read), or as unknown when the run died before
   writing a record. Only the last two are worth re-running, and only those
   are put on the force list when you complete the run.
   Tick **Show raw log** for the line-by-line record: one line per well as
   it's read, and a final line stating what the check concluded.
2. If any findings are metadata-only (the sidecar's timepoint/FOV lists don't
   match what's on disk, or stale cell overrides), **Apply repairs** fixes
   just that metadata. It never touches measurement CSVs or images — gating
   thresholds, sample definitions, and every other saved setting are
   preserved untouched. If it's greyed out despite findings being listed,
   hover it — the tooltip explains when a finding genuinely can't be
   auto-repaired.
3. If a well is genuinely missing FOV/timepoint work, **Complete this run in
   Analyze** opens the dataset there set up to finish it: the Analyze form is
   filled in from the dataset's own recorded settings (schema, separator,
   channel roles, QC options, CSV prefix — everything the original run used),
   and **Incremental — process only missing FOVs** is ticked for you. You land
   with Compute Options already unfolded so you can see that tick, and a line
   across the top of the run panel naming the dataset and confirming what was
   set up, including any wells listed under **Force these wells** — those are
   wells whose images exist but whose measurements CSV is gone, which an
   incremental run would skip forever, so they are re-segmented in full. Press
   Run and only the missing `(FOV, timepoint)` pairs are
   processed, merged into the existing per-well CSVs and `<WELL>_out.zip`
   archives; wells that are already complete are left untouched. Without that
   tick a plain re-run would skip every well that already has any output at
   all — which is most of the wells you need finished. If the dataset's
   settings can't be read, the handoff still happens but that same line says
   the settings were **not** loaded — check the schema, channel tokens and CSV
   prefix by hand before running in that case.

If a check fails, the findings list says why instead of emptying, and the
**Complete this run in Analyze** button disappears — the offer belonged to the report that failed, and taking
it would hand Analyze a dataset whose settings had just proved unreadable.

### Subset / Split Dataset

Use this to copy part of a dataset — some wells, some timepoints, some
FOVs — into a new folder, without re-running the pipeline. Useful for
sharing one condition with a collaborator, dropping a contaminated well, or
trimming a drifted tail of timepoints.

1. Pick the **source dataset** folder. The well plate, timepoint checkboxes,
   and FOV checkboxes fill in from what's actually in that dataset, all
   selected by default (meaning "keep everything" until you narrow it down).
   Selected wells get a distinct highlight colour on the picker, separate
   from any colour a finished run leaves behind.
2. Uncheck the wells, timepoints, or FOVs you don't want kept, and pick a
   **destination folder** (must be new and empty). A status panel below
   shows "Ready — N well(s) selected" as you adjust the selection, before
   you've pressed anything.
3. Press **Subset**. The same well plate you picked wells with now shows
   each one being copied — locked against further clicks until the run
   finishes, so it can double as the progress view instead of a second
   plate map elsewhere on the page. A well being copied fills clockwise as a
   dial, the same way Analyze's run plate does, so you can see whether a big
   well is nearly through or has barely started; it ends as a full circle
   with a ✓, or as a plain `!` well if nothing in it matched your filter. A
   well you kept whole is the exception: it is copied as a single file, so it
   goes straight from empty to full with nothing to report in between.

Subsetting copies files rather than rebuilding them, so it runs at roughly
the speed of copying the archives yourself in Finder or Explorer. Narrowing
by well only is the fastest case — nothing inside those wells' archives has
to be touched at all — and narrowing by timepoint or FOV costs roughly in
proportion to how much you keep.

A dataset that spells one well two ways (`in/A1.zip` beside `in/A01.zip`, say)
is refused before anything is copied, for the reason given under
[Concatenate datasets](#concatenate-datasets).

If a well's measurements CSV was written by an older run that left the numeric
timepoint column blank, its rows are still matched against your timepoint
filter — the label column and the image filename are read as fallbacks, so the
rows and the images agree about what a timepoint is. When a subset genuinely
ends up with no measurement rows at all, the error says which of the three
reasons it was: the rows could not be read against the filter at all, the images
matched a filter the rows did not, or nothing in the source matched.

Timepoints keep their original hours — a subset spanning 24h–72h stays
labelled 24h–72h in the new dataset, it isn't relabelled to start at 0. Cell-
inclusion overrides carry over for cells that survive the filter (dropped for
ones that don't) and gating thresholds / sample definitions carry over from
the source, same as Concatenate. The source dataset is never modified.

### Zip Loose Folder

Use this to group a folder of loose, ungrouped TIFs (no `in/<WELL>.zip`
structure yet) into the layout Analyze reads directly.

1. Pick the folder of loose TIFs, then describe their filenames. The separator
   box and the position dropdowns are the same control as
   [Analyze → Filename schema](#1-filename-schema) — the same field vocabulary
   in the same order, so the schema you build here is the one Analyze will use
   on the result. A readout below it shows how the files split into
   wells × FOVs × timepoints × channels, updating on every keystroke, so a wrong
   separator or schema is obvious before you convert rather than partway
   through. A schema the run would reject — no **Well** field, or two of them,
   or no **Channel** — says so under the control and holds **Zip folder**
   disabled instead of scanning every file and grouping none of them.

   When nothing comes back, the readout says *which* of the three reasons it
   is, because two of them are not about the schema at all: the folder holds no
   images; its images are one level down, in per-condition subfolders (this tool
   reads only the top level); or the images are there and the schema does not
   describe them. **Zip folder** stays disabled while the answer is nothing, and
   the readout names a couple of the files that did not match — usually enough
   to spot a wrong separator at a glance.
2. By default the result is written to a **new destination folder** you pick,
   leaving the loose TIFs exactly where they are. It has to be a different
   folder from the source, and one that doesn't already hold a dataset — the
   field says so under itself either way. Check **Group in place** instead to
   group the files directly in the source folder and delete the loose TIFs once
   each well is safely zipped — the same thing Analyze itself does when it's
   pointed at a folder of loose TIFs, exposed here as its own step. Checking it
   shows an explicit warning under the checkbox spelling out that this deletes
   the originals and can't be undone, and the destination field (unused in this
   mode) stays visible but greyed out rather than disappearing. You're still
   asked to confirm before anything is deleted, and that confirmation names the
   folder, how many files and wells it is about to zip, and how many files it is
   leaving alone. Once an in-place run succeeds, **Zip folder** goes dark — the
   folder holds archives now, not loose images, and a second run would replace
   the result with a failure.

   **Group in place also converts well folders that were already there.** The
   scan above it looks at loose TIFs only, but the in-place run zips — and
   deletes the TIFs of — every `A01/`-style subfolder in the source, not just
   the wells it groups out of the loose files. That is deliberate: it leaves the
   folder in one uniform ready-to-run layout, and it is what lets a run that was
   interrupted be started again, since the wells it already grouped have no
   loose TIFs left to find. So the folders are named rather than skipped: as
   soon as you tick **Group in place**, the readout adds a line listing them, the
   confirmation dialog repeats it above the delete warning, and the run's own
   summary says how many of the wells it produced came from folders that were
   already in the source rather than from loose images.
3. As soon as the scan finds any wells, a status panel appears below showing
   a "Ready — N well(s) to zip" plate preview. Press **Zip folder** and
   that same plate fills in as each well is zipped, the same graphical
   progress view Analyze itself uses. On success, **Open dataset in Analyze**
   sends the resulting folder there.

Files that don't match the schema are left alone and reported rather than
guessed at; a broken symlink is skipped and counted rather than crashing the
conversion.

### Merge Plates

Use this when the *same plate* imaged in two separate sessions isn't what
you have — instead, you have **two different plates** you want to treat as
one 96-well dataset (e.g. two 24-well runs of different conditions). This is
the well-axis counterpart to Concatenate, which joins the same plate across
time.

1. Fill in **Dataset 1 + Dataset 2 = Destination**, the same way as
   Concatenate.
2. If the two plates used any of the same well positions, the merge refuses
   — a well in both datasets is treated as two different samples, not one to
   stitch together. Check **Place dataset 2 starting at well** and enter a
   well (e.g. `E01`) to shift all of dataset 2's wells as a block to start
   there, landing on free positions. If the two plates are already disjoint,
   leave it unchecked — nothing needs remapping.
3. Once both folders (and any remap) are valid, a status panel appears below
   showing a "Ready — N well(s)" plate preview, with dataset 2's wells shown
   at their remapped positions if you checked the block-placement option.
   Press **Merge** and that same plate colours dataset 1's wells and dataset
   2's wells differently as the merge progresses — dataset 2's wells also
   carry a small "2" marker so the two are distinguishable even without
   relying on colour.

When nothing needs remapping, the merge is a pure copy — nothing about a
well's images, measurements, or filenames is rewritten. When wells are
remapped, every value tied to that well moves together: the images' file
names, the measurements' `well` column, the archive names, and any saved
cell-inclusion overrides. Gating thresholds carry over from dataset 1 (same
"set under dataset 1's imaging conditions, worth reviewing" caveat as
Concatenate); sample definitions from both datasets are merged, with dataset
2's group/replicate-set names disambiguated rather than overwritten if they
happen to share a name with one of dataset 1's.

It refuses when the two datasets weren't analysed the same way (same checks
Concatenate applies), when a remap target isn't a valid 96-well position, when a
remap would push a well past the edge of the plate, or when either dataset
spells one well two ways (see
[Concatenate datasets](#concatenate-datasets)). Timepoints
differing between the two plates is only a warning, not a refusal — the
merged plate is legitimately ragged in that case, and plots will show gaps
for the combinations one plate doesn't have.

It also refuses when a remapped well holds an image whose filename does not
match the dataset's schema closely enough for the well token in it to be
rewritten — a name with the wrong number of separator-delimited fields, or one
naming a different well than the folder it sits in. Such a file would land in
the destination well's archive still labelled for the plate position it came
from, and nothing downstream could tell that apart from a correct merge, since
Review matches a measurement row to an image by filename. The message names the
file, both wells, and what is wrong with the name, so you can fix or remove it —
or merge that well without remapping it. Files that were never images —
`.DS_Store`, an editor's backup, a `readme.txt` you put in an archive on
purpose — carry no well token, are not this tool's business, and pass through
under their own names.

### Job Queue

Lines up Analyze runs and Datasets-tool runs to work through unattended,
instead of babysitting each one.

**Adding a job** — three routes, all equivalent:

- **+ Add job** on the Job Queue page lists all seven tools. Pick one and
  All-Well takes you to that tool's page with a strip across the top saying
  you are setting up a queued job; fill the page in as usual and press **Add
  to queue**.
- **+ Queue** beside any Datasets tool's own run button, which queues
  whatever that page currently holds.
- **+ Queue** beside Analyze's **Run Pipeline** button, which captures the
  whole Analyze form — every option, not just the input folder.

**Looking at a job** — click its row and All-Well takes you to that tool's
page. What you find there depends on the job:

- A **running** job's page is left exactly as it is and simply shown, so you
  land on that run's own live progress — its dashboard and its plate filling
  in. Nothing is written to the page, because those fields *are* the progress
  display.
- Any **waiting or finished** job has its settings loaded into the page first,
  so you arrive looking at what that job will run.
- If the tool is busy with a *different* run, the click is refused and says so
  — its page is describing the run in flight, and loading another job's
  settings over it would misdescribe both.

**Changing a queued job** — open the row's **⋯** menu and choose **Edit…**.
The job's tool page opens with all of its settings restored and the same
strip across the top; change what you came to change and press **Save
changes**, and the job is updated in place, keeping its position in the
queue. **Cancel** leaves it exactly as it was. Editing is refused while that
job is running, or while that tool is busy with a run of its own, and while
you have a job open for editing All-Well will not let you click another row
onto the same page underneath it.

If the page cannot show everything the job holds — most often because the
dataset it points at has moved, so Subset cannot rebuild its timepoint and
FOV lists — All-Well says which settings are missing before you save, since
saving would otherwise quietly widen the job to everything. Editing a job
that has already finished is allowed, and says so: it updates what the job
would do, but the job does not run again until you choose **Retry**.

The **⋯** menu also offers **Duplicate** (a copy directly below, ready to
point at another dataset — the quickest way to queue the same operation over
several plates), **Retry** on a job that has finished, **Run after** (below),
**Move up** / **Move down**, and **Remove from queue**.

The queue runs strictly one job at a time, in the order shown, and never
starts on its own — press **▶ Start queue** to begin working through it, and
**⏸ Pause queue** (the same button) to stop after the current job. Queued
jobs persist across app restarts, but a relaunch always comes up paused —
you always decide when unattended work resumes. If a restart interrupted a
job, the page says so above the list when it next opens.

Each row names what the job actually runs — "Subset — plateA → plateA_sub",
"Analyze — plate7" — with a status chip (pending / running / succeeded /
failed / cancelled / blocked / interrupted / suspended) and, underneath, the
folders it works on, the job it waits for, and its latest message. That
second line is shortened with "…" when it does not fit; hover it for the
full text. A pending job also shows its position in line ("1.", "2.", …);
the currently running job shows a moving progress strip instead.

Drag a row by its handle at the left to reorder it, or use **Move up** /
**Move down** from the menu. The whole row is a click target — hover it and
the pointer changes to say so — click it to open that job on its own tool's
page (without running it) so you can double-check what it will do.
Reordering a job ahead of one it depends on, or removing the job currently
running, is refused, and All-Well says why. The move actions disappear once
a job has reached a final outcome, since there is nothing left to reorder.

Above the list, a one-line summary counts what is in each state, and **Clear
completed** removes every finished job at once — succeeded, failed,
cancelled or blocked — leaving pending and running ones alone.

**Chaining** links one job's input to another's output. Open the **⋯** menu
on the job that should run second, choose **Run after**, and pick the job it
should follow; **Nothing (run on its own)** in the same submenu breaks the
link again. Only jobs above it in the queue are offered, since a job cannot
run before the one it waits for. The second job's input folder is filled in
only once the first one finishes, not when you queue it — so you can queue a
Subset and a Validate of its result together, without knowing yet where that
result will land.

If a job fails, everything chained after it is marked **blocked**, not
failed — those jobs never ran. **Retry** on the *failed* job puts it back in
the queue and returns everything it blocked to pending, so a whole chain
recovers in one step. Retrying a blocked job itself is refused: it never ran,
and the job that stopped it is still there to fix. Stopping the currently-running job pauses the queue
rather than skipping ahead to the next one, so a run you meant to cancel
cannot be silently followed by one you did not get to review.

A queued run proceeds without the confirmation dialog its own page would
normally show for a destructive step (resuming into a partially-written
destination, or grouping loose TIFs in place) — there's no one there to
answer it, so queuing a job is an implicit "yes, go ahead" for whatever that
step would have asked.

---

## Filename schema

The schema is a colon-separated list of field names applied left-to-right
to filename tokens (split on the chosen separator). See
[Analyze mode → Filename schema](#1-filename-schema) above for the
recognised field names and worked examples.

`pipeline_info.json` records the schema you ran the pipeline with, so the
viewer doesn't need you to re-enter it.

**A lower-case `f` followed by digits is read as a field, never as row F.** When
Review has to find a well's images in a folder whose filenames it cannot parse
positionally, it falls back to looking for a well token anywhere in the name —
and `A01_GFP_f1_t0.tif` contains `f1`. Reading that as well `F01` meant every
plate written in the standard `…_{well}_f{fov}_{tp}` layout answered "yes, this
is F01" for every well's first field, so selecting F01 in Review drew A01's
image, F02 drew B02's, and each well showed another well's picture with nothing
on screen to say so. Row letters are matched case-insensitively for every row
except **F**, where only the upper-case spelling counts. The cost is narrow and
deliberate: a plate whose filenames spell a row-F well in lower case (`f01_GFP_…`)
is no longer found by that free-text fallback — such names are genuinely
ambiguous — while a plate whose filenames match the schema you gave the run is
read by position, which is not ambiguous at all, and is unaffected.

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
feature-subset build are distinguishable at a glance:
`dist/AllWell-<version>.app` for the full app, or
`dist/AllWell-<version>-<profile>.app` for a subset (e.g.
`AllWell-<version>-thin.app`), with `<version>` taken from the repo-root
`version.py`. The internal executable stays `AllWell`, so
launching is unchanged. To distribute it:

```sh
cd dist && zip -r AllWell-mac.zip AllWell-<version>.app
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
| `⌘/` / `Ctrl+/` | Open the help drawer |
| `⌘L` / `Ctrl+L` | Open the application log drawer — including the lines background work writes while a run is going |
| `⌘B` / `Ctrl+B` | Collapse / expand the left sidebar (same as the top-bar panel button) |
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
