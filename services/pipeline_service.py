from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PIPELINE_SCRIPT = HERE / "process_microscopy.py"


def find_pipeline_script() -> Path | None:
    # When running from a PyInstaller bundle ``sys.executable`` is the
    # bundle launcher (not a Python interpreter), so there's no .py to
    # locate — the GUI dispatches into ``process_microscopy.main``
    # via a ``--run-pipeline`` re-exec of the launcher (see
    # ``build_pipeline_args`` + ``all_well_launcher._dispatch``).
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return PIPELINE_SCRIPT if PIPELINE_SCRIPT.exists() else None


def _emit_num_flag(args: list[str], opts: dict, key: str, flag: str, cast) -> None:
    """Append ``[flag, str(cast(opts[key]))]`` when *key* carries a usable value.

    A missing or blank value means "use the pipeline default" and is skipped
    silently. A value that is *present but cannot be cast* is a user
    misconfiguration — silently dropping it would run the pipeline with
    different parameters than the operator configured (see audit E4), so it
    raises a clear ``ValueError`` instead.
    """
    if key not in opts:
        return
    raw = opts[key]
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return
    try:
        value = cast(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"Invalid value for {key!r}: {raw!r} (expected {cast.__name__})"
        ) from None
    args += [flag, str(value)]


def _emit_execution_flags(args: list[str], opts: dict) -> None:
    """Append the optional compute/execution flags (tf_threads, workers,
    stardist_tile_px).

    Extracted from :func:`build_pipeline_args` to keep that function's
    cyclomatic complexity under the ratchet ceiling. Each flag is emitted only
    when present and non-zero (``0`` = use the pipeline default → no flag);
    ``tf_threads`` allows any non-zero value, the others only positive. A
    malformed value is skipped rather than raising, matching the long-standing
    behaviour for these performance knobs.
    """
    for key, flag, positive_only in (
        ("tf_threads", "--tf_threads", False),
        ("workers", "--workers", True),
        ("stardist_tile_px", "--stardist_tile_px", True),
    ):
        try:
            v = int(opts.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue
        if (v > 0) if positive_only else (v != 0):
            args += [flag, str(v)]


def _emit_stardist_threshold_flags(args: list[str], opts: dict) -> None:
    """Append the StarDist detection thresholds when the form supplied one.

    These are probabilities, not counts, so they get their own loop rather than
    being forced into the integer one above: a blank field, the word ``auto``,
    or anything unparseable means "use the threshold the pretrained model ships
    with" and emits nothing. A value outside [0, 1] also emits nothing — the
    pipeline would reject it outright, and the Analyze form validates before the
    run, so this path only guards the CLI-shaped callers.
    """
    for key, flag in (("stardist_prob_thresh", "--stardist_prob_thresh"),
                      ("stardist_nms_thresh", "--stardist_nms_thresh")):
        raw = opts.get(key)
        if raw is None or (isinstance(raw, str) and raw.strip().lower() in ("", "auto")):
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if 0.0 <= v <= 1.0:
            args += [flag, str(v)]


def _emit_auto_threshold_flags(args: list[str], opts: dict) -> None:
    """Append the end-of-run threshold flags.

    Otsu is the pipeline's default and needs no flag — it runs whenever no
    control wells are named. That is also why wells left dormant by a switch back
    to Otsu must not be emitted: naming both *is* the control method on the
    pipeline side, so passing them would override the user's choice.
    """
    if not opts.get("auto_threshold", True):
        args.append("--no_auto_threshold")
        return
    if str(opts.get("auto_threshold_method", "otsu") or "otsu") != "controls":
        return
    pos_well = str(opts.get("pos_control_well", "") or "").strip()
    neg_well = str(opts.get("neg_control_well", "") or "").strip()
    # Both or neither: one control well on its own carries no separation to
    # optimise, so a half-filled pair falls back to the Otsu pass rather than
    # reaching the pipeline and being rejected there. The form blocks this case;
    # the guard is for the CLI-shaped callers.
    if pos_well and neg_well:
        args += ["--pos_control_well", pos_well, "--neg_control_well", neg_well]


def build_pipeline_args(
    pipeline: Path,
    input_dir: Path,
    output_dir: Path,
    opts: dict,
) -> list[str]:
    segmentation_method = opts.get("segmentation_method", "stardist_nuclei")
    if getattr(sys, "frozen", False):
        # Frozen bundle: re-invoke the launcher with the sentinel; the
        # launcher routes argv past the sentinel into
        # ``process_microscopy.main``. Passing the .py path here
        # would make the launcher treat it as a CLI argument and
        # argparse would bail with exit code 2.
        args = [sys.executable, "--run-pipeline"]
    else:
        args = [sys.executable, str(pipeline)]
    args += [
        "--input_dir",
        str(input_dir),
        "--output_dir",
        str(output_dir),
        "--nuclear_token",
        opts["nuclear_token"],
        "--fluor_tokens",
        *opts["fluor_tokens"],
        "--csv_prefix",
        opts["csv_prefix"],
        "--filename_schema",
        opts["filename_schema"],
        "--filename_sep",
        opts["filename_sep"],
        "--segmentation_method",
        segmentation_method,
    ]
    try:
        min_area = int(opts.get("min_nucleus_area_px", 50))
    except (TypeError, ValueError):
        min_area = 50
    args += ["--min_nucleus_area_px", str(max(1, min_area))]
    cytoplasm_token = (opts.get("cytoplasm_token") or "").strip()
    if segmentation_method == "stardist_seeded_watershed_cell" and cytoplasm_token:
        args += ["--cytoplasm_token", cytoplasm_token]
    # The phase token has two uses, so it is sent for ANY method when set:
    #   * cellpose_labelfree — main() remaps it onto the nuclear slot (cyto3
    #     brightfield preset) as the segmentation input;
    #   * every method — the phase channel skips background correction and is
    #     resaved from the original (see _correct_image_group phase passthrough).
    phase_token = (opts.get("phase_token") or "").strip()
    if phase_token:
        args += ["--phase_token", phase_token]
    # General Cellpose: expose model + diameter + thresholds + optional nucleus
    # channel (works on a fluorescent or brightfield channel; no phase remap).
    if segmentation_method == "cellpose":
        args += ["--cellpose_model", str(opts.get("cellpose_model", "cyto3") or "cyto3")]
        for flag, key, default in (
            ("--cellpose_diameter", "cellpose_diameter", 0.0),
            ("--cellpose_flow_threshold", "cellpose_flow_threshold", 0.4),
            ("--cellpose_cellprob_threshold", "cellpose_cellprob_threshold", 0.0),
        ):
            try:
                val = float(opts.get(key, default))
            except (TypeError, ValueError):
                val = default
            # diameter is only meaningful when > 0; thresholds always emitted
            if key == "cellpose_diameter" and val <= 0:
                continue
            args += [flag, str(val)]
        nuc = (opts.get("cellpose_nuclei_channel") or "").strip()
        if nuc:
            args += ["--cellpose_nuclei_channel", nuc]
    _emit_num_flag(args, opts, "tophat_radius_nir", "--tophat_radius_nir", int)
    _emit_num_flag(args, opts, "tophat_radius_fluor", "--tophat_radius_fluor", int)
    _emit_num_flag(args, opts, "fft_small_nir", "--fft_small_nir", int)
    _emit_num_flag(args, opts, "fft_small_fluor", "--fft_small_fluor", int)
    _emit_num_flag(args, opts, "mesh_ratio", "--mesh_ratio", float)
    _emit_num_flag(args, opts, "mesh_sigma", "--mesh_sigma", float)

    for flag in (
        "no_tophat_nir",
        "no_tophat_fluor",
        "force",
        "incremental",
        "cpu_only",
    ):
        if opts.get(flag):
            args.append(f"--{flag}")
    # Incremental reads the prior per-well CSVs to skip already-done FOVs. Local
    # runs read them straight from the output dir; the remote runner rewrites
    # this to the uploaded sidecar (the remote output dir starts empty).
    if opts.get("incremental"):
        args += ["--incremental_prior_dir", str(output_dir)]
    # Per-well force. Emitted independently of --force/--incremental: it is the
    # only way to regenerate a well whose measurements CSV is gone but whose
    # <well>_out.zip survives, because that archive's own members make the well
    # look complete to an incremental run's done-set.
    force_wells = [str(w).strip() for w in (opts.get("force_wells") or []) if str(w).strip()]
    if force_wells:
        args += ["--force_wells", *force_wells]
    # Input/output well compression is always on (no longer a flag) — the
    # pipeline always produces per-well .zip files.
    smfish = opts.get("smfish_tokens", [])
    if smfish:
        args += ["--smfish_tokens"] + list(smfish)

    shape_features = opts.get("shape_features", [])
    if shape_features:
        args += ["--shape_features"] + list(shape_features)

    # Quality control: flat-field correction + background method + debris removal.
    flatfield = str(opts.get("flatfield", "none") or "none")
    bg_well = str(opts.get("background_well", "") or "").strip()
    if flatfield and flatfield != "none":
        args += ["--flatfield", flatfield]
    # Always pass the selected method explicitly so the run can never silently
    # fall back to the CLI default — the GUI dropdown is the source of truth.
    background = str(opts.get("background", "sliding_disk") or "sliding_disk")
    if background:
        args += ["--background", background]
    # The background well feeds both empty_well flat-field and empty_well bg.
    if bg_well and (flatfield == "empty_well" or background == "empty_well"):
        args += ["--background_well", bg_well]
    # Auto-threshold. The form's checkbox is the positive statement ("calculate
    # thresholds"); the pipeline flag is the negative, so only the off case emits
    # anything. Absent from a legacy opts dict ⇒ on, matching prior behaviour.
    _emit_auto_threshold_flags(args, opts)
    if opts.get("qc_remove_debris"):
        args.append("--qc_remove_debris")
        for flag, key in (("--qc_min_area", "qc_min_area"),
                          ("--qc_max_area", "qc_max_area"),
                          ("--qc_min_solidity", "qc_min_solidity")):
            try:
                v = float(opts.get(key, 0) or 0)
            except (TypeError, ValueError):
                v = 0.0
            if v > 0:
                args += [flag, str(v)]

    _emit_execution_flags(args, opts)
    _emit_stardist_threshold_flags(args, opts)

    return args


def spawn_pipeline(args: list[str]) -> subprocess.Popen:
    # Run the pipeline in its own process group / session so the runner can
    # signal the entire tree (the script itself plus any multiprocessing
    # workers it spawns) when the user clicks Stop.
    kwargs: dict = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        **kwargs,
    )
