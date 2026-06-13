from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable
from core.aw_safe import swallowed

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
    from core.well_token import is_valid_well_name
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


def well_present_in_input(input_dir: Path, well: str, *,
                          schema: "list[str] | None" = None,
                          sep: str = "_") -> bool:
    """True iff *well* (any 96-well form, e.g. ``'c11'`` / ``'C11'``) is present
    in *input_dir* in any layout the pipeline reads: a per-well ``<WELL>.zip``,
    a per-well folder, or loose TIFFs whose schema ``well`` field matches.

    Mirrors the empty-well sampling in :func:`process_microscopy.sample_empty_well_images`
    (non-recursive zip / folder lookup, recursive loose-TIFF parse) so the
    Analyze pre-run check agrees with what the pipeline will actually find. Used
    to block a run when ``--flatfield empty_well`` / ``--background empty_well``
    name a background well that doesn't exist.
    """
    from core.well_token import canonical_well_label
    target = canonical_well_label(well or "")
    if target is None:
        return False
    try:
        # 1) per-well zip:  <input_dir>/<WELL>.zip
        for p in input_dir.glob("*.zip"):
            if canonical_well_label(p.stem) == target:
                return True
        # 2) per-well folder:  <input_dir>/<WELL>/
        for p in input_dir.iterdir():
            if p.is_dir() and canonical_well_label(p.name) == target:
                return True
    except OSError:
        return False
    # 3) loose TIFFs — parse the 'well' field from the filename schema.
    fields = [str(f).strip().lower() for f in (schema or [])]
    if "well" not in fields:
        return False
    well_idx = fields.index("well")
    try:
        for p in input_dir.rglob("*.tif*"):
            parts = p.stem.split(sep)
            if well_idx < len(parts) and canonical_well_label(parts[well_idx]) == target:
                return True
    except OSError:
        return False
    return False


def iter_well_tokens(input_dir: Path) -> "list[str]":
    """Canonical well tokens (``A01``) present in a *resolved* input dir.

    Reads the same already-grouped layouts the pipeline does — per-well
    ``<WELL>.zip`` files and per-well ``<WELL>/`` folders — and returns the
    canonical tokens in sorted order. Used to surface grouping progress when the
    inputs are already grouped/zipped: the zipper is skipped in that case and so
    emits no per-well progress of its own, leaving the Group stage stuck at 0 and
    the wells jumping straight from empty to processing.
    """
    from core.well_token import canonical_well_label
    seen: set[str] = set()
    out: list[str] = []
    try:
        entries = sorted(input_dir.iterdir(), key=lambda p: p.name)
    except OSError:
        return out
    for p in entries:
        tok = None
        if p.is_file() and p.suffix.lower() == ".zip":
            tok = canonical_well_label(p.stem)
        elif p.is_dir():
            tok = canonical_well_label(p.name)
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


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
            raise RuntimeError(f"WellPlateZipper exited with code {proc.returncode}.")
    finally:
        if proc_hook is not None:
            try:
                proc_hook(None)
            except Exception:
                swallowed()  # was: pass (silent except)


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
        # v5.x.x resume support: ``raw/in/`` already has well content,
        # but a prior ``--move-source-tifs`` run may have crashed
        # mid-way and left some TIFs at ``raw/`` (only some were
        # moved). Detect that state and re-run the zipper to consume
        # ONLY the truly-unconsumed ones — for copy-mode reruns where
        # every loose TIF already has a same-named copy under
        # ``raw/in/<well>/``, the zipper is a no-op and skipping it
        # avoids re-copying the same files (which would show up as
        # noisy "zipping" log lines against files already in place
        # from a prior successful copy).
        loose_at_top = tif_files_in(raw)
        if loose_at_top:
            existing_basenames: set[str] = set()
            try:
                for well_dir in in_sub.iterdir():
                    if not well_dir.is_dir():
                        continue
                    for child in well_dir.iterdir():
                        if child.is_file() and child.suffix.lower() in (".tif", ".tiff"):
                            existing_basenames.add(child.name)
            except OSError:
                pass
            leftover_tifs = [
                p for p in loose_at_top if p.name not in existing_basenames
            ]
        else:
            leftover_tifs = []

        if leftover_tifs:
            if log_fn:
                log_fn(
                    f"[zipper] Resume: found {len(leftover_tifs)} unconsumed "
                    f"loose TIF(s) at top level after partial run; consuming "
                    f"into existing raw/in/ well folders.\n"
                )
            source_tifs = tuple(leftover_tifs)
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
            if move_source_tifs:
                return in_sub, raw / "out", None

            def _on_success_resume() -> None:
                """Delete the leftover loose TIFs from raw/ once the
                pipeline completes cleanly. Mirror the fresh-run
                callback."""
                for tif in source_tifs:
                    try:
                        tif.unlink()
                    except OSError:
                        pass

            return in_sub, raw / "out", _on_success_resume

        # All loose TIFs at top level (if any) are already represented
        # under raw/in/. If the prior run was copy-mode they're
        # redundant and the on_success cleanup will tidy them up;
        # otherwise (move-mode prior run, or no loose TIFs at all) the
        # state is already fully consumed and there's nothing to do.
        if loose_at_top and log_fn:
            log_fn(
                f"[zipper] Resume: all {len(loose_at_top)} loose TIF(s) at "
                f"top level already have a copy under raw/in/. Skipping "
                f"zipper re-run; will clean up the loose copies on "
                f"successful pipeline completion.\n"
            )
        if loose_at_top:
            already_copied = tuple(loose_at_top)

            def _on_success_post_copy() -> None:
                for tif in already_copied:
                    try:
                        tif.unlink()
                    except OSError:
                        pass

            return in_sub, raw / "out", _on_success_post_copy
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
