"""The pipeline's command line, declared in one place.

``process_microscopy.build_parser`` is a 370-line data declaration with no
behaviour: every option the pipeline takes, its default, and its help text.
It moved out of ``process_microscopy`` for the reason the upload manifest
moved out of ``remote_runner`` — that module carries a line budget whose
whole point is that a cohesive block leaves rather than the file growing,
and a parser that must gain an option cannot be in tension with it.

It is also read as a **roster** rather than run. ``util.settings_digest``
keys the reuse decision on this parser's destinations, and
``tests/test_reuse_is_keyed_on_the_settings.py`` requires every option here
to be classified as either changing the numbers or not. Having the
declaration in its own module makes that relationship visible: this file is
the list, and that roster is the list of what each entry means.

``process_microscopy`` re-exports ``build_parser``, so every call site and
test keeps importing it from where it always did.
"""

from __future__ import annotations

from pipeline.field_quality_record import FOCUS_COLUMN as _FOCUS_COLUMN

import argparse
import math
from pathlib import Path

from pipeline.background import BACKGROUND_METHODS
from pipeline.capacity import ALLOW_LOW_DISK_HELP
from pipeline.output_policy import (
    DEFAULT_PHASE_OUTPUT, DEFAULT_TOPHAT_DTYPE,
    PHASE_OUTPUT_CHOICES, TOPHAT_DTYPE_CHOICES,
)
from util.schema import DEFAULT_SCHEMA, DEFAULT_SEP, SCHEMA_FIELDS

#: Declaration-level defaults, here rather than in ``process_microscopy``
#: because that module imports this one and the other direction would cycle.
#: ``process_microscopy`` re-exports both.
DEFAULT_SEGMENTATION_METHOD = "stardist_nuclei"
FLATFIELD_MODES = ("none", "empty_well", "gaussian", "basic")

__all__ = [
    "DEFAULT_SEGMENTATION_METHOD", "FLATFIELD_MODES", "build_parser",
]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Parallel StarDist nuclei segmentation + GFP quantification. "
            "Accepts a flat directory of images, a directory of per-well "
            "zip files (A01.zip … H12.zip), or a directory of per-well "
            "subfolders (A01/ … H12/)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    p.add_argument("--input_dir",  type=Path, required=True,
                   help="Directory of images, per-well zip files, or per-well subfolders")
    p.add_argument("--output_dir", type=Path, default=Path("results"),
                   help="Destination for CSVs, masks, overlays, and output folders/zips")
    p.add_argument("--scratch_dir", type=Path, default=None,
                   help="Directory for per-well working files (zip extraction, "
                        "intermediate images, FOV checkpoints). Overrides "
                        "$ALLWELL_SCRATCH_DIR; both default to ~/.allwell/scratch. "
                        "Keep this on fast LOCAL storage — pointing it at a "
                        "network mount puts the run's bulk I/O on a volume whose "
                        "dropped sessions surface as 'Bad file descriptor'.")

    p.add_argument("--suspend_file", type=Path, default=None,
                   help="A file whose appearance asks this run to suspend: stop "
                        "taking on new FOV-groups, finish the ones in flight, "
                        "write each started well's CSV and out.zip, and exit 0 — "
                        "exactly what SIGTERM does. For hosts a signal cannot "
                        "reach reliably, such as a remote run behind ssh, "
                        "'conda run' or a scheduler. Its creator clears it "
                        "before the run starts; the pipeline only reads it.")

    p.add_argument("--nuclear_token", default="NIR",
                   help="Channel token identifying nuclear-channel files "
                        "(used for segmentation and quantified like other channels)")
    p.add_argument("--fluor_tokens", nargs="+", default=["GFP"],
                   metavar="TOKEN",
                   help="One or more channel tokens identifying fluorescent "
                        "channels to quantify (e.g. GFP mCherry DAPI). "
                        "Each token produces its own intensity columns in the CSV.")
    p.add_argument(
        "--segmentation_method",
        default=DEFAULT_SEGMENTATION_METHOD,
        choices=("stardist_nuclei", "stardist_seeded_watershed_cell",
                 "cellpose", "cellpose_labelfree", "classical_threshold"),
        help=(
            "Segmentation routine, run on the --nuclear_token channel (any "
            "fluorescent or brightfield channel) unless noted. 'stardist_nuclei' "
            "(default) and 'stardist_seeded_watershed_cell' (StarDist seeds + "
            "cytoplasm watershed) need a fluorescent stain. 'cellpose' is general "
            "Cellpose (tune --cellpose_model/diameter/flow/cellprob, optional "
            "--cellpose_nuclei_channel). 'classical_threshold' is Otsu + "
            "watershed. 'cellpose_labelfree' is a Cellpose cyto3 preset that "
            "segments the --phase_token (brightfield) channel."
        ),
    )
    p.add_argument(
        "--phase_token",
        default="",
        help=(
            "Channel token of the phase / brightfield image. Under every method "
            "this channel skips flat-field and background correction and is "
            "resaved from the original; the 'cellpose_labelfree' preset also "
            "segments it. It may not equal --nuclear_token (or --cytoplasm_token) "
            "under any other method."
        ),
    )
    p.add_argument(
        "--cellpose_model",
        default="cyto3",
        choices=("cyto3", "cyto2", "cyto", "nuclei"),
        help="Cellpose model for --segmentation_method=cellpose.",
    )
    p.add_argument(
        "--cellpose_diameter",
        type=float,
        default=0.0,
        help=("Cellpose cell diameter in px. Each image is resized by 30/diameter "
              "before segmenting, so small values are slower. 0 = no resize "
              "(the model's own 30 px); the size is not estimated."),
    )
    p.add_argument(
        "--cellpose_flow_threshold",
        type=float,
        default=0.4,
        help="Cellpose flow_threshold (mask recovery tolerance).",
    )
    p.add_argument(
        "--cellpose_cellprob_threshold",
        type=float,
        default=0.0,
        help="Cellpose cellprob_threshold (cell probability cut).",
    )
    p.add_argument(
        "--cellpose_nuclei_channel",
        default="",
        help="Optional fluor token used as Cellpose's nucleus channel "
             "(channels=[seg, nucleus]) for cyto models; empty = grayscale.",
    )
    p.add_argument(
        "--cytoplasm_token",
        default="",
        help=(
            "Channel token used as cytoplasm image for watershed mode. "
            "Required when --segmentation_method=stardist_seeded_watershed_cell. "
            "In watershed mode this channel is also quantified."
        ),
    )
    p.add_argument(
        "--min_nucleus_area_px",
        type=int,
        default=50,
        help="Minimum StarDist nucleus area (pixels) kept as watershed seeds.",
    )

    p.add_argument("--filename_schema", default=DEFAULT_SCHEMA,
                   help=(
                       "Colon-separated ordered field names describing the "
                       "filename structure.  Each token must be one of: "
                       + ", ".join(SCHEMA_FIELDS) + ".  "
                       "'channel' and 'well' must each appear exactly once.  "
                       "Slots whose content is not needed should be labelled "
                       "'ignore'.  "
                       f"Default: '{DEFAULT_SCHEMA}' matches the convention "
                       "Exp01_NIR_B03_F001_02d04h30m.tif"
                   ))
    p.add_argument("--filename_sep", default=DEFAULT_SEP,
                   help=(
                       "Single character that separates fields in image "
                       f"filenames.  Default: '{DEFAULT_SEP}'"
                   ))

    p.add_argument("--tophat_radius_nir", type=int, default=100,
                   help="Top-hat structuring element radius for the nuclear "
                        "channel (pixels)")
    p.add_argument("--tophat_radius_fluor", type=int, default=100,
                   help="Top-hat structuring element radius applied to every "
                        "fluorescent channel (pixels)")
    p.add_argument("--fft_small_nir", type=int, default=0,
                   help="For --background=fft_bandpass: small-structure (high-"
                        "frequency) cutoff for the nuclear channel (pixels). 0 "
                        "= high-pass only (no denoise); >1 attenuates features "
                        "smaller than this for a true band-pass.")
    p.add_argument("--fft_small_fluor", type=int, default=0,
                   help="For --background=fft_bandpass: small-structure (high-"
                        "frequency) cutoff applied to every fluorescent channel "
                        "(pixels). 0 = high-pass only.")
    p.add_argument("--mesh_ratio", type=float, default=3.0,
                   help="For --background=robust_mesh: tile size as a fraction of "
                        "the radius (tile = radius / mesh_ratio). Larger → "
                        "coarser, smoother background. Default 3.0.")
    p.add_argument("--mesh_sigma", type=float, default=3.0,
                   help="For --background=robust_mesh: per-tile sigma-clip "
                        "threshold. Smaller rejects bright object pixels more "
                        "aggressively (crowded fields). Default 3.0.")
    p.add_argument("--no_tophat_nir", action="store_true",
                   help="Disable white top-hat on the nuclear channel")
    p.add_argument("--no_tophat_fluor", action="store_true",
                   help="Disable white top-hat on all fluorescent channels")
    p.add_argument("--smfish_tokens", nargs="*", default=[],
                   help="Fluorescent channel tokens containing smFISH data. "
                        "A LoG spot-enhancement kernel is applied after tophat.")
    p.add_argument("--shape_features", nargs="*", default=[],
                   help="Per-cell shape features to emit as shape_<feature> columns "
                        "(e.g. eccentricity solidity perimeter major_axis_length). "
                        "Cell centroids (shape_centroid_row/col) are always emitted.")

    # ── Quality control ──────────────────────────────────────────────────────
    p.add_argument("--flatfield", choices=FLATFIELD_MODES, default="none",
                   help="Flat-field illumination correction applied before top-hat. "
                        "'gaussian' self-estimates a field per image; 'basic' estimates "
                        "one field per channel from a sample of the dataset (pure-numpy "
                        "BaSiC); 'empty_well' uses --background_well as the reference.")
    p.add_argument("--background_dir", default="",
                   help="Directory to read the --background_well blank(s) from, "
                        "when they do not live in --input_dir. A batched remote "
                        "run stages one batch at a time, so the blank is not in "
                        "most batches' input; it is uploaded once to its own "
                        "directory and named here, which is what lets every "
                        "batch build the same correction from it. Defaults to "
                        "--input_dir.")
    # `default=None`, not `""`, and that is load-bearing rather than a style
    # choice. `settings_digest` skips a `None` value and includes a `""` one, so
    # a `""` default on an option in KEYED_ON would change the recorded digest
    # of every plate that already exists on every user's disk and force a
    # plate-wide recompute for all of them. `None` means "this run predates the
    # option / did not use it", which is what a plate analysed before pairing
    # existed should say.
    p.add_argument("--background_well_map", default=None,
                   help="Pair each blank with the wells it corrects, instead of "
                        "pooling every --background_well into one plate-wide "
                        "reference: 'H12=A01:D12; G12=E01:H12'. A blank's wells "
                        "are a comma-separated list of wells (A01) and "
                        "rectangular blocks (A01:D12). Use this when the blanks "
                        "differ because of the treatment — a compound that "
                        "fluoresces puts real signal into its own blank, and "
                        "pooling spreads it over the whole plate. With a map in "
                        "force every well the run processes must be named by "
                        "exactly one blank, or the run refuses to start. Without "
                        "one, the blanks pool, which is the less noisy estimate "
                        "when they really are interchangeable.")
    p.add_argument("--background_well", default="",
                   help="Empty/background well(s) used as the reference for "
                        "--flatfield=empty_well and/or --background=empty_well. One "
                        "well (e.g. H12) or several comma-separated (e.g. 'H12,G12') "
                        "to pool for a cleaner estimate. The field is built per "
                        "timepoint (background drifts over time), with a pooled "
                        "all-timepoints fallback for timepoints the blank lacks.")
    p.add_argument("--background", choices=BACKGROUND_METHODS, default="sliding_disk",
                   help="Background-subtraction method (applied per channel where "
                        "top-hat is enabled). 'top_hat' = fast square morphological "
                        "(leaves square halos on uneven background); "
                        "'sliding_disk' = smooth isotropic disk baseline (default; recommended "
                        "for structured/uneven background, no square halos); "
                        "'fov_median' = subtract a single scalar, the median "
                        "intensity of the whole field of view (uniform offset, "
                        "no spatial flattening; no radius/reference needed); "
                        "'empty_well' = subtract a "
                        "measured per-channel background from --background_well; "
                        "'robust_mesh' = sigma-clipped local background on a "
                        "coarse grid (SExtractor/Background2D style), robust to "
                        "bright/confluent objects on uneven background (uses the "
                        "radius as the background scale); 'fft_bandpass' = 2-D FFT "
                        "Butterworth high-pass (removes low-frequency background "
                        "regardless of object size; radius = large-structure "
                        "cutoff, --fft_small_* = optional high-frequency denoise).")
    p.add_argument("--qc_remove_debris", action="store_true",
                   help="Remove segmented regions whose size/solidity fall outside the "
                        "QC bounds below (debris, fragments, merged blobs).")
    p.add_argument("--qc_min_area", type=float, default=0.0,
                   help="Debris removal: minimum region area in px (0 = no lower bound).")
    p.add_argument("--qc_max_area", type=float, default=0.0,
                   help="Debris removal: maximum region area in px (0 = no upper bound).")
    p.add_argument("--qc_min_solidity", type=float, default=0.0,
                   help="Debris removal: minimum region solidity 0-1 (0 = no bound).")

    # Per-cell focus. On by default, hence a --no_ flag: the column costs a few
    # percent of quantification and is the only evidence a later reader has that
    # part of a field was soft, so a run without it cannot be asked the question
    # afterwards without being re-run.
    p.add_argument("--no_focus_metric", action="store_true",
                   help="Do not measure per-cell focus (the boundary-sharpness "
                        f"column {_FOCUS_COLUMN}). Measured by default.")
    # Recorded, NOT applied. See process_microscopy's sidecar note and
    # _Docs/Markdowns/INFERENCE_SUBSYSTEMS_TRACKER.md §D.4: the cut is the
    # number a user gets wrong first time, and a pipeline that dropped the
    # cells would make it cost a re-run to revisit.
    p.add_argument("--focus_min", type=float, default=0.0,
                   help="The per-cell focus value below which a cell is out of "
                        "focus, recorded in pipeline_info.json as this "
                        "dataset's own cut (0 = none stated). The run RECORDS "
                        "it and does not apply it; Review's Cell Gating tab "
                        "applies it, reversibly.")

    p.add_argument("--no_save_tophat",   action="store_true",
                   help="Do not save top-hat/smFISH intermediate TIFFs")
    # How the saved images are stored (pipeline.output_images). Both defaults
    # shrink <well>_out.zip without changing a measured number.
    p.add_argument("--phase_output", choices=PHASE_OUTPUT_CHOICES,
                   default=DEFAULT_PHASE_OUTPUT,
                   help="How the --phase_token channel's image is archived. "
                        "'reference' (default) writes no _tophat for it: the "
                        "phase frame is uncorrected, so Review reads it from the "
                        "input archive in/<well>.zip, which must stay with the "
                        "dataset. 'copy' resaves it into <well>_out.zip.")
    p.add_argument("--tophat_dtype", choices=TOPHAT_DTYPE_CHOICES,
                   default=DEFAULT_TOPHAT_DTYPE,
                   help="Storage type of the background-corrected _tophat TIFFs. "
                        "'source' (default) rounds each to its input's uint8/"
                        "uint16 type when every value fits (otherwise that frame "
                        "stays float32); 'float32' keeps the fractional values. "
                        "Measurements are computed before saving and do not "
                        "change. _smfish and _cytoplasm_tophat are always float32.")
    # Input/output well compression is mandatory: folder-mode runs always
    # compress <input_dir>/<well>/ → <input_dir>/<well>.zip and each output well
    # folder → <output_dir>/<well>_out.zip. The pipeline only ever emits per-well
    # .zip files, so there is no flag to disable this.

    p.add_argument("--tf_threads", type=int, default=0,
                   help=(
                       "CPU threads per worker for TensorFlow/BLAS inference. "
                       "0 (default) = 4 (non-Mac) or 2 (macOS/Metal); smaller yields "
                       "more workers (cpu_count // tf_threads). Common values: "
                       "4  = default, suits most x86 CPUs (≤32 cores); "
                       "8  = for CPUs with large L3 caches (e.g. EPYC, Xeon Scalable) "
                       "     where inference scales beyond 4 threads; "
                       "16 = for very wide-cache server CPUs where per-image "
                       "     throughput still improves at 16 threads "
                       "(workers = cpu_count // tf_threads in all cases). "
                       "Ignored when running on GPU (--cpu_only not set)."
                   ))
    p.add_argument(
        "--workers",
        type=int,
        default=0,
        help=(
            "Number of parallel worker processes. "
            "0 (default) = auto-select from CPU availability and tf_threads."
        ),
    )

    p.add_argument(
        "--stardist_tile_px",
        type=int,
        default=0,
        help=(
            "Max pixels per tile SIDE for StarDist prediction (NOT the number of "
            "tiles): the frame is split into as many tiles as needed so no block "
            "exceeds this size, capping per-worker peak memory on large frames. "
            "0 (default) = OFF (predict the whole frame at once). On ~4K frames "
            "an untiled predict can use several GB per worker, so a few parallel "
            "workers exhaust RAM; e.g. 2048 splits a 4096-px frame into a 2x2 "
            "grid, 1024 into 4x4 (less RAM, a bit slower). Results are stitched."
        ),
    )

    p.add_argument(
        "--stardist_prob_thresh",
        type=float,
        default=-1.0,
        help=(
            "StarDist object-probability threshold (0-1). Candidate nuclei whose "
            "predicted objectness falls below it are dropped before polygon NMS. "
            "LOWER detects more nuclei, including dim / marginal ones; HIGHER "
            "keeps only confident detections. Negative (the default) = use the "
            "threshold the pretrained model ships with (~0.479 for "
            "2D_versatile_fluo)."
        ),
    )
    p.add_argument(
        "--stardist_nms_thresh",
        type=float,
        default=-1.0,
        help=(
            "StarDist polygon non-maximum-suppression overlap threshold (0-1). "
            "HIGHER lets more overlapping nuclei survive, which recovers touching "
            "nuclei in a confluent field but can double-count one; LOWER merges "
            "more aggressively, removing duplicates at the risk of fusing "
            "neighbours. Negative (the default) = the pretrained model's own "
            "value (0.3 for 2D_versatile_fluo)."
        ),
    )

    p.add_argument("--csv_prefix", default="measurements",
                   help="Filename prefix for per-well CSVs (<prefix>_<well>.csv)")

    p.add_argument("--force", action="store_true",
                   help=(
                       "Reprocess all wells even if output already exists. "
                       "By default, any well whose <well>_out.zip and "
                       "<csv_prefix>_<well>.csv are both present is skipped."
                   ))

    p.add_argument("--incremental", action="store_true",
                   help=(
                       "FOV-level incremental update. Instead of skipping a "
                       "well that already has output, process only the "
                       "FOV-groups not already present in its CSV and merge the "
                       "new rows / images into the existing <csv>_<well>.csv and "
                       "<well>_out.zip. New wells process fully; unchanged wells "
                       "are left as-is. Ignored when --force is given."
                   ))

    p.add_argument("--force_wells", nargs="*", default=None, metavar="WELL",
                   help=(
                       "Reprocess these wells in full, whatever else the run "
                       "decides — the per-well counterpart to --force. Given "
                       "with --incremental, the named wells ignore the "
                       "already-done set and re-segment from scratch, which is "
                       "the only way to regenerate a well whose measurements "
                       "CSV is missing while its <well>_out.zip survives: the "
                       "archive's own members make that well look complete to "
                       "an incremental run, so it would otherwise be skipped "
                       "forever. Well labels are matched canonically, so C8 "
                       "and C08 name the same well."
                   ))

    p.add_argument("--only_wells", nargs="+", default=None, metavar="WELL",
                   help=(
                       "Process only these wells and leave every other well's "
                       "output, and its records in pipeline_info.json, as they "
                       "are. The run refuses to start if a named well is not in "
                       "the input. Combine with --incremental to finish partial "
                       "wells, and with --force_wells to regenerate named ones. "
                       "Well labels are matched canonically (C8 == C08)."
                   ))

    p.add_argument("--incremental_prior_dir", default=None,
                   help=(
                       "Directory holding the prior per-well CSVs used to build "
                       "the already-done (fov, timepoint) set for --incremental. "
                       "Defaults to the output directory (local runs). Remote "
                       "runs point this at the uploaded prior-CSV sidecar, since "
                       "the remote output dir starts empty."
                   ))

    p.add_argument("--cpu_only", action="store_true",
                   help=(
                       "Force StarDist to run on CPU even if a GPU is available: "
                       "TensorFlow's GPUs are hidden in every worker before it "
                       "initialises. Cellpose is not affected; it uses a GPU "
                       "whenever Torch can see one. "
                       "Useful for debugging, reproducibility, or when sharing "
                       "a GPU with other jobs."
                   ))

    p.add_argument("--allow_low_disk", action="store_true",
                   help=ALLOW_LOW_DISK_HELP.replace("\n\n", " "))

    p.add_argument("--no_auto_threshold", action="store_true",
                   help=(
                       "Skip the post-pipeline auto-threshold estimation. "
                       "Used by AllWell's batched remote-execution mode (v5.4.7+) "
                       "to suppress the per-batch threshold pass, which would "
                       "otherwise overwrite the previous batch's defaults using "
                       "incomplete data. The runner re-runs the estimation once "
                       "locally after all batches finish, against the full "
                       "downloaded output dir."
                   ))

    p.add_argument("--no_run_check", action="store_true",
                   help=(
                       "Skip the end-of-run completeness check. The check reads "
                       "back what the run wrote — how many (fov, timepoint) "
                       "groups each well's input held against how many its CSV "
                       "and output archive cover — and records the answer as the "
                       "'run_check' block in pipeline_info.json, which is what "
                       "the Review Home page reports. Skipping it costs seconds "
                       "and leaves the dataset with no record of whether it is "
                       "complete; a run skipped this way says so in the sidecar "
                       "rather than looking like a dataset that predates the "
                       "check. Remote runs suppress it internally and run it "
                       "client-side after the fetch instead, since the remote "
                       "output dir holds only the slice that was uploaded."
                   ))

    p.add_argument("--pos_control_well", default="",
                   help=(
                       "Well label of the positive control (e.g. B02). With "
                       "--neg_control_well, the end-of-run threshold pass picks "
                       "each non-segmentation channel's default by scanning for "
                       "the cut whose separation between these two wells best "
                       "survives its own 95%% confidence interval, instead of "
                       "Otsu-splitting cell signal from background. The order "
                       "matters: a channel whose two wells do not differ at the "
                       "95%% level, including a pair given the wrong way round, "
                       "is skipped with a reason in the log rather than "
                       "thresholded. Both or neither."
                   ))
    p.add_argument("--neg_control_well", default="",
                   help=(
                       "Well label of the negative control (e.g. B11). See "
                       "--pos_control_well."
                   ))

    p.add_argument("--nanowell_format", choices=("100um", "50um", "custom"), default=None,
                   help=(
                       "Run in nanowell mode: the input holds tiled phase images of an array of nanowells (100 um "
                       "or 50 um format, or custom with --nanowell_diameter_um and --nanowell_pitch_um) and an "
                       "in/acquisition.json record that says where each tile sits (or, without one, the grid "
                       "given by --nanowell_grid and its companions). Each "
                       "well at each timepoint is processed as one unit (every tile together); nanowells are "
                       "addressed on the lattice and measured label-free, with fluorescence where present. "
                       "Needs --phase_token (the tile channel). Writes <output_dir>/nanowells/. Zip mode only."
                   ))
    p.add_argument("--nanowell_diameter_um", type=float, default=None,
                   help="With --nanowell_format custom: the nanowell diameter in micrometres.")
    p.add_argument("--nanowell_pitch_um", type=float, default=None,
                   help=("With --nanowell_format custom: the lattice pitch in micrometres, centre to centre. A "
                         "starting value: the run measures the pitch on the tiles, from half to twice this."))
    p.add_argument("--nanowell_grid", default=None,
                   help=("For a dataset with no in/acquisition.json: the tile grid as ROWSxCOLUMNS (e.g. 5x5). "
                         "Needs --nanowell_overlap_percent and --nanowell_pixel_size_um; fields are taken in the "
                         "order of their names, numerically, along --nanowell_scan."))
    p.add_argument("--nanowell_scan", default=None,
                   help=("With --nanowell_grid: the order the stage visited the tiles, <corner>-<row|column> with an "
                         "optional -serpentine (default top-left-row)."))
    p.add_argument("--nanowell_overlap_percent", type=float, default=None,
                   help="With --nanowell_grid: the nominal tile overlap set in the acquisition software, percent.")
    p.add_argument("--nanowell_pixel_size_um", type=float, default=None,
                   help="With --nanowell_grid: the image pixel size in micrometres.")
    p.add_argument("--nanowell_cell_diameter_um", type=float, default=15.0,
                   help="Typical cell diameter in micrometres, the scale the label-free segmenter is given.")
    p.add_argument("--nanowell_margin_um", type=float, default=None,
                   help=("Margin around a nanowell's disk that must lie inside one tile for that tile to own it. "
                         "Default: a tenth of the nanowell diameter."))
    p.add_argument("--nanowell_square_edge_deg", type=float, default=0.0,
                   help=("Angle of the central square nanowell's edges to a lattice row, degrees (assumed 0: the "
                         "square is cut parallel to a row). Used to refuse a lattice fitted a sixth of a turn off."))
    p.add_argument("--nanowell_occluded_below", type=float, default=0.5,
                   help=("A nanowell visible in less than this share (0 to 1) is occluded and carries no cell count "
                         "or confluence, only its state."))
    p.add_argument("--nanowell_clipped_below", type=float, default=0.5,
                   help=("A nanowell imaged in less than this share (0 to 1) of its disk, at the edge of the "
                         "tiles, the array or the well, is clipped and carries no cell count or confluence."))

    return p


def nanowell_options_from_args(args, error) -> dict | None:
    """The nanowell settings the run records and the worker receives, or ``None`` when the mode is off.

    Validated here, in the self-sufficient entry point, because a remote run arrives as argv. *error*
    is ``parser.error``. In nanowell mode the phase channel is the tile channel, so it takes the
    nuclear slot the grouping machinery reads.
    """
    if args.nanowell_format is None:
        return None
    if not str(args.phase_token or "").strip():
        error("--nanowell_format needs --phase_token: the phase channel the tiles are imaged in.")
    if not (args.nanowell_cell_diameter_um > 0):
        error("--nanowell_cell_diameter_um must be a positive number of micrometres.")
    if args.nanowell_margin_um is not None and not (args.nanowell_margin_um >= 0):
        error("--nanowell_margin_um must be zero or more.")
    for flag, value in (("--nanowell_occluded_below", args.nanowell_occluded_below),
                        ("--nanowell_clipped_below", args.nanowell_clipped_below)):
        if not (0.0 < value <= 1.0):
            error(f"{flag} must be a share above 0 and at most 1.")
    if not (-45.0 <= args.nanowell_square_edge_deg <= 45.0):
        error("--nanowell_square_edge_deg must be within -45 and 45 degrees (a square repeats every 90).")
    args.nuclear_token = str(args.phase_token).strip()
    args.segmentation_method = "cellpose_labelfree"      # no StarDist model is built for a run that never calls it
    options = {"plate_format": args.nanowell_format, "cell_diameter_um": float(args.nanowell_cell_diameter_um),
               "margin_um": None if args.nanowell_margin_um is None else float(args.nanowell_margin_um),
               "square_edge_deg": float(args.nanowell_square_edge_deg),
               "occluded_below": float(args.nanowell_occluded_below),
               "clipped_below": float(args.nanowell_clipped_below)}
    # Added only when given, so a run without them keeps the settings (and reuse key) it always had.
    options.update(_custom_format(args, error))
    pattern = _grid_pattern(args, error)
    if pattern:
        options["pattern"] = pattern
    return options


def _custom_format(args, error) -> dict:
    """``{"diameter_um", "pitch_um"}`` for ``--nanowell_format custom``; nothing for a known format."""
    given = args.nanowell_diameter_um is not None or args.nanowell_pitch_um is not None
    if args.nanowell_format != "custom":
        if given:
            error("--nanowell_diameter_um and --nanowell_pitch_um go with --nanowell_format custom; the 100um and "
                  "50um formats have their own.")
        return {}
    diameter, pitch = args.nanowell_diameter_um, args.nanowell_pitch_um
    if diameter is None or pitch is None or not all(math.isfinite(v) and v > 0 for v in (diameter, pitch)):
        error("--nanowell_format custom needs --nanowell_diameter_um and --nanowell_pitch_um, both positive.")
    if diameter >= 0.95 * pitch:
        error(f"A {diameter:g} um nanowell on a {pitch:g} um pitch would touch its neighbours: the pitch is the "
              "centre-to-centre distance and must exceed the diameter.")
    return {"diameter_um": float(diameter), "pitch_um": float(pitch)}


def _grid_pattern(args, error) -> dict | None:
    """The record-less placement (tracker P15): grid, scan, overlap and pixel size, all or none of the grid's."""
    from pipeline.nanowell_pattern_record import parse_grid, parse_scan

    companions = (args.nanowell_scan, args.nanowell_overlap_percent, args.nanowell_pixel_size_um)
    if args.nanowell_grid is None:
        if any(v is not None for v in companions):
            error("--nanowell_scan, --nanowell_overlap_percent and --nanowell_pixel_size_um describe the tile grid "
                  "for a dataset without an acquisition record, and go with --nanowell_grid.")
        return None
    try:
        rows, cols = parse_grid(args.nanowell_grid)
        scan = args.nanowell_scan or "top-left-row"
        parse_scan(scan)
    except ValueError as exc:
        error(str(exc))
    overlap, pixel = args.nanowell_overlap_percent, args.nanowell_pixel_size_um
    if overlap is None or not (0 <= overlap < 100):
        error("--nanowell_grid needs --nanowell_overlap_percent, from 0 to under 100.")
    if pixel is None or not (math.isfinite(pixel) and pixel > 0):
        error("--nanowell_grid needs --nanowell_pixel_size_um, a positive number of micrometres.")
    return {"grid": [rows, cols], "scan": scan, "overlap_percent": float(overlap), "pixel_size_um": float(pixel)}


def qc_options_from_args(args) -> dict:
    """The ``qc_options`` block ``pipeline_info.json`` records for a run.

    The correction and QC choices a reader needs in order to know how a plate's
    numbers were produced, taken from the parsed arguments with the defaults the
    pipeline applies stated explicitly, so an absent flag and its default record
    the same value.
    """
    return {
        "flatfield": args.flatfield,
        "background": args.background,
        "background_well": args.background_well or "",
        "background_well_map": args.background_well_map or "",
        "fft_small_nir": int(args.fft_small_nir or 0),
        "fft_small_fluor": int(args.fft_small_fluor or 0),
        "mesh_ratio": float(args.mesh_ratio or 3.0),
        "mesh_sigma": float(args.mesh_sigma or 3.0),
        "remove_debris": bool(args.qc_remove_debris),
        "min_area": float(args.qc_min_area or 0.0),
        "max_area": float(args.qc_max_area or 0.0),
        "min_solidity": float(args.qc_min_solidity or 0.0),
        "measure_focus": not args.no_focus_metric,
        # RECORDED, not applied. The run measures the column and states
        # the dataset's own cut; Review's Cell Gating tab is what acts
        # on it, so a cut chosen wrongly costs a tick rather than a
        # re-run. 0.0 means the run stated none -- which is the default,
        # because the number depends mostly on the microscope and
        # nothing in a run can guess it (Review's Cell Gating tab draws
        # a well's own distribution, which is where a user finds
        # theirs).
        "focus_min": float(args.focus_min or 0.0),
    }
