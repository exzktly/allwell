from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent.parent
ZIPPER_SCRIPT = HERE / "WellPlateZipper.py"
TIF_GLOB = ("*.tif", "*.tiff", "*.TIF", "*.TIFF")
_WELL_NAME_RE = re.compile(r"^([A-Ha-h])(\d{1,2})$")


def find_zipper_script() -> Path | None:
    return ZIPPER_SCRIPT if ZIPPER_SCRIPT.exists() else None


def tif_files_in(folder: Path) -> list[Path]:
    files: list[Path] = []
    for pat in TIF_GLOB:
        files.extend(folder.glob(pat))
    return files


def _is_well_named(p: Path) -> bool:
    """Match `A1` / `A01` … `H12` against the well-token canonicaliser."""
    from well_token import is_valid_well_name
    return is_valid_well_name(p.stem)


def _has_well_content(folder: Path) -> bool:
    """Return True iff *folder* contains *well-named* zip files OR
    well-named subdirectories.

    A stray ``archive.zip`` used to be enough to short-circuit the zipper
    even though the pipeline would then warn on every non-well zip — the
    user saw a flood of skipped-file warnings and concluded the pipeline
    was broken. Match the well-token regex on the basename to avoid that.
    """
    try:
        for p in folder.glob("*.zip"):
            if _is_well_named(p):
                return True
        return any(_WELL_NAME_RE.match(p.name) for p in folder.iterdir() if p.is_dir())
    except OSError:
        return False


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
        raise RuntimeError(f"WellPlateZipper.py not found. Expected: {ZIPPER_SCRIPT}")

    out_folder.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(zipper),
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
    if log_fn:
        log_fn(f"[zipper] $ {' '.join(cmd)}\n")

    well_pat = re.compile(r"^([A-H]\d{2}):\s+\d+\s+files", re.IGNORECASE)
    popen_kwargs: dict = dict(
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
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
            pass
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
            raise RuntimeError(f"WellPlateZipper exited with code {proc.returncode}.")
    finally:
        if proc_hook is not None:
            try:
                proc_hook(None)
            except Exception:
                pass


def resolve_input_output(
    raw: Path,
    *,
    log_fn: Callable[[str], None] | None = None,
    progress_fn: Callable[[str], None] | None = None,
    filename_schema: str,
    filename_sep: str,
    proc_hook: Callable[[subprocess.Popen | None], None] | None = None,
    move_source_tifs: bool = False,
) -> tuple[Path, Path, Callable[[], None] | None]:
    """Resolve the raw input folder into ``(input_dir, output_dir,
    on_success)``.

    ``on_success`` is ``None`` for inputs that needed no zipping
    (already-zipped folders, an ``in/`` subfolder with well content),
    AND for the loose-TIF case when ``move_source_tifs=True`` (the
    zipper has already removed the sources, so there's nothing for
    the post-success hook to delete).

    For the default ``move_source_tifs=False`` loose-TIF case the
    returned callback deletes ``raw/*.tif`` so a successful pipeline
    run converts the dataset from "loose TIFs at top level" to
    "zips in ``raw/in/``" (the next run picks up the zips and skips
    the zipper entirely).

    The caller MUST invoke ``on_success`` only on a clean pipeline
    completion. On any failure (zipper error, pipeline non-zero
    exit, user cancel), skip it — the loose TIFs stay intact so
    the user can retry from scratch.
    """
    if not raw.is_dir():
        raise ValueError(f"Not a directory:\n{raw}")

    if raw.name.lower() == "in":
        return raw, raw.parent / "out", None

    in_sub = raw / "in"
    if in_sub.is_dir() and _has_well_content(in_sub):
        return in_sub, raw / "out", None

    tifs = tif_files_in(raw)
    if len(tifs) > 3:
        # Snapshot the loose TIFs BEFORE the zipper runs so the
        # post-success cleanup deletes exactly the inputs the user
        # had at start-of-run — not any incidental .tif files that
        # might land in raw/ later (the zipper's own output goes to
        # raw/in/, not raw/, so this is mostly defensive).
        source_tifs = tuple(tifs)
        if log_fn:
            log_fn(
                f"[zipper] Found {len(tifs)} TIF files — running WellPlateZipper to create per-well folders in in/\n"
            )
        run_zipper(
            raw,
            in_sub,
            log_fn=log_fn,
            progress_fn=progress_fn,
            filename_schema=filename_schema,
            filename_sep=filename_sep,
            proc_hook=proc_hook,
            move_source_tifs=move_source_tifs,
        )
        if not in_sub.is_dir() or not _has_well_content(in_sub):
            raise ValueError(
                "WellPlateZipper ran but produced no well folders or zip files.\n\n"
                "Common causes:\n"
                "  • The 'well' position in the Filename Schema does not match the actual well token in your filenames.\n"
                "  • The separator character does not match your filenames.\n"
                "  • The well token is not a valid 96-well plate position (A01–H12).\n\n"
                f"Schema used: {filename_schema}  |  Separator: '{filename_sep}'"
            )

        if move_source_tifs:
            # The zipper already moved the source TIFs; no post-success
            # cleanup needed and nothing safe to undo on failure either
            # (the source files are gone in their old location regardless
            # of pipeline outcome).
            return in_sub, raw / "out", None

        def _on_success() -> None:
            """Delete loose TIFs from raw/ once the pipeline completes
            cleanly. raw/in/{well}.zip already has the data; the
            loose copies in raw/ are now redundant."""
            for tif in source_tifs:
                try:
                    tif.unlink()
                except OSError:
                    pass

        return in_sub, raw / "out", _on_success

    raise ValueError(
        "Cannot determine input layout.\n\n"
        "Expected one of:\n"
        "  • Selected folder is named \"in\"\n"
        "  • Selected folder contains a sub-folder named \"in\"\n"
        "  • Selected folder contains more than 3 TIF files\n"
        "    (will be grouped by well using WellPlateZipper)\n\n"
        f"Folder selected: {raw}\n"
        f"TIF files found: {len(tifs)}"
    )
