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
from pathlib import Path

from pipeline.background import BACKGROUND_METHODS
from pipeline.capacity import ALLOW_LOW_DISK_HELP
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
        help="Cellpose cell diameter in px (0 = auto-estimate).",
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
                       "Force StarDist to run on CPU even if a GPU is available. "
                       "Sets CUDA_VISIBLE_DEVICES='' in every worker process "
                       "before TensorFlow is initialised, preventing GPU use. "
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

    return p
