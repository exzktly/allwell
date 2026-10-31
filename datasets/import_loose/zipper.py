"""Running ``pipeline/well_plate_zipper.py``, the loose-image grouper.

Datasets → Zip loose folder is the only caller: its in-place mode groups loose
images into per-well folders through this subprocess and then zips each folder.
Analyze reads per-well zips and never groups anything itself.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from services.subprocess_kill import signal_subprocess
from services.subprocess_text import TEXT_UTF8
from util.aw_safe import swallow, swallowed

HERE = Path(__file__).resolve().parents[2]
ZIPPER_SCRIPT = HERE / "pipeline" / "well_plate_zipper.py"
#: Kept for the well_plate_zipper subprocess, which takes glob patterns on its
#: command line and answers a different question — packing a well *folder*,
#: where `.tif`/`.tiff` is the deliberate rule. Deciding which loose files are
#: input is `util.image_exts.is_run_input_member`'s job.
TIF_GLOB = ("*.tif", "*.tiff", "*.TIF", "*.TIFF")  # aw-image-exts-ok: zipper argv


def find_zipper_script() -> Path | None:
    return ZIPPER_SCRIPT if ZIPPER_SCRIPT.exists() else None


def build_zipper_cmd(
    zipper: Path,
    tif_folder: Path,
    out_folder: Path,
    filename_schema: str,
    filename_sep: str,
    *,
    move_source_tifs: bool = False,
) -> list[str]:
    """Build the argv for the well_plate_zipper subprocess.

    Frozen bundle: re-invoke the launcher with the ``--run-zipper`` sentinel,
    which routes argv into ``well_plate_zipper.main()`` (mirrors the pipeline's
    ``--run-pipeline`` path in :mod:`services.pipeline_service`). Passing the
    ``.py`` path there would make ``sys.executable`` (the .app binary) relaunch
    the GUI instead of running the zipper — the frozen-only failure this guards.
    From source, invoke the interpreter on the script path as before.
    """
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--run-zipper"]
    else:
        cmd = [sys.executable, str(zipper)]
    cmd += [
        "--search-dir",
        str(tif_folder),
        "--output-dir",
        str(out_folder),
        "--filename_schema",
        filename_schema,
        "--filename_sep",
        filename_sep,
    ]
    if move_source_tifs:
        cmd.append("--move-source-tifs")
    return cmd


def run_zipper(
    tif_folder: Path,
    out_folder: Path,
    *,
    log_fn: Callable[[str], None] | None = None,
    progress_fn: Callable[[str], None] | None = None,
    filename_schema: str,
    filename_sep: str,
    proc_hook: Callable[[subprocess.Popen | None], None] | None = None,
    move_source_tifs: bool = False,
) -> None:
    zipper = find_zipper_script()
    if zipper is None:
        raise RuntimeError(
            f"pipeline/well_plate_zipper.py not found. Expected: {ZIPPER_SCRIPT}")

    out_folder.mkdir(parents=True, exist_ok=True)
    cmd = build_zipper_cmd(
        zipper, tif_folder, out_folder, filename_schema, filename_sep,
        move_source_tifs=move_source_tifs,
    )
    if log_fn:
        log_fn(f"[zipper] $ {' '.join(cmd)}\n")

    well_pat = re.compile(r"^([A-H]\d{2}):\s+\d+\s+files", re.IGNORECASE)
    popen_kwargs: dict = dict(
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        # A filename can carry any byte the filesystem allows, and the zipper
        # echoes every one it groups. Decoding strictly means one such byte
        # raises out of the reader below — see the `finally` there for why
        # that was worse than a lost log line.
        **TEXT_UTF8,
        bufsize=1,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    # Put the zipper in its own session / process group so the runner's
    # Stop button can signal it the same way it signals the pipeline.
    # Without this, the zipper inherited the GUI's session and Stop
    # could never reach it.
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    else:
        popen_kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    proc = subprocess.Popen(cmd, **popen_kwargs)
    if proc_hook is not None:
        try:
            proc_hook(proc)
        except Exception:
            swallowed()  # was: pass (silent except)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            if log_fn:
                log_fn(f"[zipper] {line}\n")
            m = well_pat.match(line.strip())
            if m and progress_fn:
                progress_fn(m.group(1).upper())
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(
                f"Grouping step exited with code {proc.returncode}.")
    finally:
        # Reap it whatever ended the read. The zipper is spawned into its own
        # process group so Stop can reach it, which also means nothing else
        # will reap it: an exception escaping the loop above left it running,
        # rewriting the user's input directory, while the caller reported the
        # grouping had failed.
        if proc.poll() is None:
            with swallow("stopping the zipper after its output stopped"):
                signal_subprocess(proc)
        with swallow("waiting for the zipper subprocess"):
            proc.wait(timeout=30)
        if proc_hook is not None:
            with swallow("clearing the zipper process hook"):
                proc_hook(None)
