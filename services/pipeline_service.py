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
    # The label-free preset segments the designated phase channel; main() applies
    # the cyto3 brightfield preset, so the GUI sends only the phase token here.
    if segmentation_method == "cellpose_labelfree":
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
    try:
        args += ["--tophat_radius_nir", str(int(opts["tophat_radius_nir"]))]
    except (TypeError, ValueError, KeyError):
        pass
    try:
        args += ["--tophat_radius_fluor", str(int(opts["tophat_radius_fluor"]))]
    except (TypeError, ValueError, KeyError):
        pass

    for flag in (
        "no_tophat_nir",
        "no_tophat_fluor",
        "force",
        "cpu_only",
    ):
        if opts.get(flag):
            args.append(f"--{flag}")
    for flag in (
        "compress_input_well_folders",
        "compress_output_well_folders",
    ):
        args.append(f"--{flag}" if opts.get(flag) else f"--no-{flag}")
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
    # Cell tracking post-pass.
    if opts.get("track"):
        args.append("--track")
        args += ["--track_engine", str(opts.get("track_engine", "greedy") or "greedy")]
        try:
            args += ["--track_max_distance", str(float(opts.get("track_max_distance", 30.0) or 30.0))]
        except (TypeError, ValueError):
            pass
        try:
            args += ["--track_max_gap", str(int(opts.get("track_max_gap", 1) or 1))]
        except (TypeError, ValueError):
            pass
        if opts.get("track_allow_division"):
            args.append("--track_allow_division")

    try:
        tf = int(opts["tf_threads"])
        if tf != 0:
            args += ["--tf_threads", str(tf)]
    except ValueError:
        pass
    try:
        workers = int(opts.get("workers", 0))
        if workers > 0:
            args += ["--workers", str(workers)]
    except (TypeError, ValueError):
        pass

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
