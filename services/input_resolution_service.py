from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from util.aw_safe import swallowed
from util.retry_io import retry_io
from util.zip_write import atomic_zip

HERE = Path(__file__).resolve().parent.parent
ZIPPER_SCRIPT = HERE / "WellPlateZipper.py"
TIF_GLOB = ("*.tif", "*.tiff", "*.TIF", "*.TIFF")

# Consolidation reads and writes on whatever volume the user pointed at as the
# input folder, and it moves the whole plate: every loose TIF into a per-well
# archive. On a network mount that load is enough to drop the session, and the
# unguarded write turned the blip into a failed run before analysis started
# (``OSError: [Errno 9] Bad file descriptor``). ``atomic_zip`` stages and
# commits with ``os.replace``, so re-running an attempt is safe.
_CONSOLIDATION_ADVICE = (
    "A repeated failure here usually means the input volume is a network "
    "mount dropping its session under load. Copy the plate to local storage "
    "and point All-Well at that copy."
)


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
    """Build the argv for the WellPlateZipper subprocess.

    Frozen bundle: re-invoke the launcher with the ``--run-zipper`` sentinel,
    which routes argv into ``WellPlateZipper.main()`` (mirrors the pipeline's
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


def build_bootstrap_cmd(bootstrap: Path, alias: str) -> list[str]:
    """Build the argv for the remote-provisioning bootstrap subprocess.

    Frozen bundle: re-invoke the launcher with the ``--run-bootstrap`` sentinel,
    which routes argv into ``scripts/_bootstrap_lib.main()`` (mirrors the
    ``--run-zipper`` / ``--run-pipeline`` paths). Passing the ``.py`` path there
    would make ``sys.executable`` (the .app binary) relaunch the GUI instead of
    running the bootstrap — the frozen-only failure this guards. From source,
    invoke the interpreter on the script path as before.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-bootstrap", alias]
    return [sys.executable, str(bootstrap), alias]


def tif_files_in(folder: Path) -> list[Path]:
    files: list[Path] = []
    for pat in TIF_GLOB:
        files.extend(folder.glob(pat))
    return files


def _is_well_named(p: Path) -> bool:
    """Match `A1` / `A01` … `H12` against the well-token canonicaliser."""
    from util.well_token import is_valid_well_name
    return is_valid_well_name(p.stem)


def has_well_zip(folder: Path) -> bool:
    """True iff *folder* contains at least one well-named ``<WELL>.zip``."""
    try:
        return any(_is_well_named(p) for p in folder.glob("*.zip"))
    except OSError:
        return False


def _schema_field_index(schema: str, field: str) -> int:
    fields = [f.strip().lower() for f in str(schema or "").split(":")]
    return fields.index(field) if field in fields else -1


def _loose_timepoints_for_wells(
    folder: Path, wells: set[str], *, schema: str, sep: str,
) -> set[str]:
    """Raw timepoint tokens of loose TIFs in *folder* belonging to *wells*.

    Tokens are compared verbatim (a dataset names its timepoints consistently),
    which is enough to tell a brand-new timepoint from one already on disk.
    """
    from util.well_token import canonical_well_label
    wi = _schema_field_index(schema, "well")
    ti = _schema_field_index(schema, "timepoint")
    if wi < 0 or ti < 0:
        return set()
    targets = {canonical_well_label(w) for w in wells if w}
    out: set[str] = set()
    for p in tif_files_in(folder):
        parts = p.stem.split(sep)
        if wi < len(parts) and ti < len(parts) \
                and canonical_well_label(parts[wi]) in targets:
            tok = parts[ti].strip()
            if tok:
                out.add(tok)
    return out


def _zip_timepoints(zip_path: Path, *, schema: str, sep: str) -> set[str]:
    """Raw timepoint tokens already present in a ``<WELL>.zip``'s members."""
    ti = _schema_field_index(schema, "timepoint")
    if ti < 0 or not zip_path.exists():
        return set()
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
    except (OSError, zipfile.BadZipFile):
        return set()
    out: set[str] = set()
    for name in names:
        if name.endswith("/"):
            continue
        parts = Path(name).stem.split(sep)
        if ti < len(parts):
            tok = parts[ti].strip()
            if tok:
                out.add(tok)
    return out


def has_well_content(folder: Path) -> bool:
    """Return True iff *folder* contains *well-named* zip files OR
    well-named subdirectories.

    A stray ``archive.zip`` used to be enough to short-circuit the zipper
    even though the pipeline would then warn on every non-well zip — the
    user saw a flood of skipped-file warnings and concluded the pipeline
    was broken. Judge the basename with the shared canonicaliser to avoid
    that, so this module and the pipeline that then reads the folder agree
    on what counts as a well (ARCHITECTURE §15).
    """
    from util.well_token import is_valid_well_name
    try:
        for p in folder.glob("*.zip"):
            if _is_well_named(p):
                return True
        return any(is_valid_well_name(p.name) for p in folder.iterdir() if p.is_dir())
    except OSError:
        return False


def dir_has_entries(folder: Path) -> bool:
    """True iff *folder* holds at least one entry of any kind.

    The weakest useful statement about a directory: it exists and is not empty.
    Used to tell an ``in/`` whose contents this module does not recognise as
    wells (which is still usable input — the pipeline reads it) apart from an
    ``in/`` that is genuinely empty (which is not, and must say so).
    """
    try:
        return any(folder.iterdir())
    except OSError:
        return False


# ── the one layout decision ──────────────────────────────────────────────────
# Analyze asks this question twice: once under the input field, to tell the user
# what pressing Run will do, and once at Run, to actually do it. Those were two
# separate implementations, and they disagreed — the readout accepted any zip in
# ``in/`` while the resolver required a well-named one, so a folder holding
# ``Plate1_A1.zip`` displayed "using as input" and then failed the run with
# "Cannot determine input layout". One planner, two consumers, no drift.

#: The selected path is a packed OME-TIFF, which Analyze cannot read.
PACKED_OME = "packed_ome"
#: The selected path is not a directory at all.
NOT_A_DIR = "not_a_dir"
#: The grouped root already holds per-well zips / folders; nothing to prepare.
GROUPED = "grouped"
#: The grouped root holds loose TIFs and no well zips — group them in place.
GROUP_LOOSE = "group_loose"
#: The grouped root holds loose TIFs *alongside* well zips — fold them in.
FOLD_LOOSE = "fold_loose"
#: The selected root holds enough loose TIFs to run WellPlateZipper into ``in/``.
ZIP_ROOT = "zip_root"
#: An ``in/`` that holds something, none of which parses as a well.
UNRECOGNISED = "unrecognised"
#: An ``in/`` that exists and is empty.
EMPTY_IN = "empty_in"
#: Nothing usable anywhere.
NO_INPUT = "no_input"

#: Shown when the user points Analyze at a packed OME-TIFF. Analyze processes
#: folders of per-well images; the Datasets converter is what de-interleaves a
#: packed file into one.
PACKED_OME_MESSAGE = (
    "⚠ This is a packed OME-TIFF. Convert it to a dataset folder first in the "
    "Datasets tab → “Convert OME-TIFF”, then open that folder here "
    "to process it."
)


#: Actions that describe input the run can proceed with.
RUNNABLE_ACTIONS = frozenset(
    {GROUPED, GROUP_LOOSE, FOLD_LOOSE, ZIP_ROOT, UNRECOGNISED}
)


@dataclass(frozen=True)
class InputPlan:
    """What Analyze will do with the folder the user selected.

    *input_dir* and *output_dir* are the pair the pipeline will be handed, and
    are ``None`` for the actions that cannot run. *loose_count* is the number of
    loose TIFs the action is about (0 where the action is not about loose TIFs),
    and *message* is the one-line readout, phrased for the user.
    """

    action: str
    input_dir: Path | None
    output_dir: Path | None
    loose_count: int
    message: str

    @property
    def runnable(self) -> bool:
        return self.action in RUNNABLE_ACTIONS


def _plan_grouped_root(root: Path, output_dir: Path, *, named_in: bool) -> InputPlan:
    """Classify a folder that is being used as the pipeline's input directly."""
    loose = len(tif_files_in(root))
    if loose > 0:
        if has_well_zip(root):
            return InputPlan(
                FOLD_LOOSE, root, output_dir, loose,
                f"✓ Will fold {loose} loose TIF(s) into the existing well "
                f"zips in {root.name}/",
            )
        return InputPlan(
            GROUP_LOOSE, root, output_dir, loose,
            f"✓ Will group {loose} loose TIF(s) into per-well folders "
            f"in {root.name}/",
        )
    if has_well_content(root):
        return InputPlan(
            GROUPED, root, output_dir, 0,
            "✓ Using selected folder as input" if named_in
            else f"✓ Found {root.name}/ subfolder — using as input",
        )
    if dir_has_entries(root):
        # Usable, but say what was not recognised: a run that then processes
        # nothing has at least told the user why before it started.
        return InputPlan(
            UNRECOGNISED, root, output_dir, 0,
            f"✓ Using {root.name}/ as input — no well-named zips or "
            f"folders recognised in it",
        )
    return InputPlan(
        EMPTY_IN, None, None, 0,
        f"The {root.name}/ folder is empty — nothing to process.",
    )


def plan_input_layout(raw: Path) -> InputPlan:
    """Decide how *raw* — the folder the user selected — becomes pipeline input.

    Pure: reads the filesystem, changes nothing. :func:`resolve_input_output`
    executes the plan and ``AnalyzeTab._refresh_output`` displays it, so the
    readout under the input field cannot disagree with what Run then does.

    A folder *named* ``in`` is the input; a folder that *contains* ``in/`` feeds
    the pipeline from that subfolder. The two are classified by the same
    function, which is the point — the contains-``in/`` path used to demand
    recognisable well content that the named-``in`` path never asked for, so an
    ``in/`` full of, say, ``Plate1_A1.zip`` worked when selected directly and
    failed when its parent was selected.

    Loose TIFs sitting at the selected root still win over an ``in/`` that has
    nothing recognisable in it: that is a fresh drop next to a folder a previous
    interrupted run created, and it must still reach WellPlateZipper.
    """
    if detect_ome_input(raw) is not None:
        return InputPlan(PACKED_OME, None, None, 0, PACKED_OME_MESSAGE)
    if not raw.is_dir():
        return InputPlan(NOT_A_DIR, None, None, 0, "Not a directory.")

    if raw.name.lower() == "in":
        return _plan_grouped_root(raw, raw.parent / "out", named_in=True)

    in_sub = raw / "in"
    out_dir = raw / "out"
    if in_sub.is_dir():
        nested = _plan_grouped_root(in_sub, out_dir, named_in=False)
        if nested.action not in (UNRECOGNISED, EMPTY_IN):
            return nested
    else:
        nested = None

    tifs = len(tif_files_in(raw))
    if tifs > 3:
        return InputPlan(
            ZIP_ROOT, in_sub, out_dir, tifs,
            f"✓ Will run WellPlateZipper on {tifs} TIF file(s) → in/",
        )
    if nested is not None:
        return nested
    return InputPlan(
        NO_INPUT, None, None, tifs,
        "No TIF files or in/ folder found in selected directory.",
    )


def _layout_error(plan: InputPlan, raw: Path) -> str:
    """The long-form version of an unrunnable plan, for the run-time exception.

    The readout has room for one line; a failed run has room to explain. Both
    describe the same verdict, so they are built from the same plan.
    """
    if plan.action == PACKED_OME:
        return plan.message
    if plan.action == NOT_A_DIR:
        return f"Not a directory:\n{raw}"
    if plan.action == EMPTY_IN:
        return (
            f"{plan.message}\n\n"
            "The folder that holds the images is there, but there is nothing "
            "in it — no per-well zips, no per-well folders, no TIF files.\n\n"
            f"Folder selected: {raw}"
        )
    return (
        "Cannot determine input layout.\n\n"
        "Expected one of:\n"
        '  \u2022 Selected folder is named "in"\n'
        '  \u2022 Selected folder contains a sub-folder named "in"\n'
        "  \u2022 Selected folder contains more than 3 TIF files\n"
        "    (will be grouped by well using WellPlateZipper)\n\n"
        f"Folder selected: {raw}\n"
        f"TIF files found: {plan.loose_count}"
    )


def _well_in_grouped_root(root: Path, target: str) -> bool:
    """True iff *root* holds *target* as a per-well ``<WELL>.zip`` or ``<WELL>/``
    folder (non-recursive, mirroring the pipeline's grouped-input lookup)."""
    from util.well_token import canonical_well_label
    try:
        # per-well zip:  <root>/<WELL>.zip
        for p in root.glob("*.zip"):
            if canonical_well_label(p.stem) == target:
                return True
        # per-well folder:  <root>/<WELL>/
        for p in root.iterdir():
            if p.is_dir() and canonical_well_label(p.name) == target:
                return True
    except OSError:
        return False
    return False


def well_present_in_input(input_dir: Path, well: str, *,
                          schema: list[str] | None = None,
                          sep: str = "_") -> bool:
    """True iff *well* (any 96-well form, e.g. ``'c11'`` / ``'C11'``) is present
    in *input_dir* in any layout the pipeline reads: a per-well ``<WELL>.zip``,
    a per-well folder, or loose TIFFs whose schema ``well`` field matches.

    Mirrors the empty-well sampling in :func:`process_microscopy.sample_empty_well_images`
    (non-recursive zip / folder lookup, recursive loose-TIFF parse) so the
    Analyze pre-run check agrees with what the pipeline will actually find. Used
    to block a run when ``--flatfield empty_well`` / ``--background empty_well``
    name a background well that doesn't exist.

    The Analyze form validates the *selected* folder, but a folder that holds an
    ``in/`` subfolder (the common ``<dataset>/in`` + ``<dataset>/out`` layout)
    feeds the pipeline from that ``in/``, not the parent — see
    :func:`resolve_input_output`. The grouped-layout lookup therefore also
    searches ``<input_dir>/in`` so a well packaged as ``in/<WELL>.zip`` /
    ``in/<WELL>/`` isn't reported missing when it's really one level down.
    """
    from util.well_token import canonical_well_label
    target = canonical_well_label(well or "")
    if target is None:
        return False
    # 1 + 2) grouped layouts, in the selected dir and (when present) its ``in/``.
    roots = [input_dir]
    in_sub = input_dir / "in"
    if input_dir.name.lower() != "in" and in_sub.is_dir():
        roots.append(in_sub)
    for root in roots:
        if _well_in_grouped_root(root, target):
            return True
    # 3) loose TIFFs — parse the 'well' field from the filename schema. The
    # recursive walk from the selected dir already descends into ``in/``.
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


def iter_well_tokens(input_dir: Path) -> list[str]:
    """Canonical well tokens (``A01``) present in a *resolved* input dir.

    Reads the same already-grouped layouts the pipeline does — per-well
    ``<WELL>.zip`` files and per-well ``<WELL>/`` folders — and returns the
    canonical tokens in sorted order. Used to surface grouping progress when the
    inputs are already grouped/zipped: the zipper is skipped in that case and so
    emits no per-well progress of its own, leaving the Group stage stuck at 0 and
    the wells jumping straight from empty to processing.
    """
    from util.well_token import canonical_well_label
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


def count_expected_wells(raw: Path, *, filename_schema: str, filename_sep: str) -> int:
    """Best-effort count of distinct wells the run will produce, for the Analyze
    progress denominator.

    Takes the layout from :func:`plan_input_layout`, so the dashboard's
    "x / N wells" counts the folder the run will actually read. Counts the UNION
    of already-grouped wells (per-well zips / folders) and distinct well tokens
    parsed from loose TIFs — so a partially-grouped ``in/`` (some well folders
    plus leftover loose TIFs) reports the full plate, not just the grouped few.

    Deliberately best-effort: a layout nothing can be counted in falls back to
    the loose-TIF count and then to 1, because this is a progress denominator
    and a zero would leave the dashboard dividing by nothing. When there is
    genuinely no input, :func:`resolve_input_output` is what says so.
    """
    from util.well_token import canonical_well_label

    fields = [f.strip().lower() for f in str(filename_schema or "").split(":")]
    well_idx = fields.index("well") if "well" in fields else -1

    def _loose_toks(folder: Path) -> set[str]:
        if well_idx < 0:
            return set()
        toks: set[str] = set()
        for p in tif_files_in(folder):
            parts = p.stem.split(filename_sep)
            tok = canonical_well_label(parts[well_idx]) if well_idx < len(parts) else None
            if tok:
                toks.add(tok)
        return toks

    toks: set[str] = set()
    grouped = plan_input_layout(raw).input_dir
    if grouped is not None:
        toks |= set(iter_well_tokens(grouped)) | _loose_toks(grouped)
        if grouped != raw:
            toks |= _loose_toks(raw)  # loose TIFs at the selected root
    if toks:
        return len(toks)
    return len(tif_files_in(raw)) or 1


def append_loose_images_to_zips(
    grouped_dir: Path,
    *,
    filename_schema: str,
    filename_sep: str,
    log_fn: Callable[[str], None] | None = None,
) -> tuple[int, set[str]]:
    """Fold loose image files sitting next to grouped well zips into those zips.

    When a directory already holds per-well ``<WELL>.zip`` archives and the user
    drops additional loose TIFFs alongside them, those files would otherwise be
    ignored (``has_well_content`` short-circuits the zipper). This parses each
    loose file's well token, appends it into the matching ``<WELL>.zip`` (or
    creates a new ``<WELL>.zip`` for a well that doesn't have one yet), and
    deletes the loose source once it is durably in the zip.

    Rules:
      * Only acts when *grouped_dir* already contains ≥1 well-named zip (i.e. a
        zip-grouped layout). Folder-only / loose-only layouts are left to the
        normal zipper path.
      * Duplicate member name (the loose file's basename already inside the zip)
        → keep the existing member, skip the loose file (left in place).
      * Each appended loose file is unlinked right after the verified atomic
        rewrite; skipped duplicates are not deleted.

    Returns ``(files_appended, wells_touched)`` where *wells_touched* are the
    canonical well tokens whose zip gained at least one member (used by the
    caller to drive incremental reprocessing).
    """
    from util.well_token import canonical_well_label

    appended = 0
    wells_touched: set[str] = set()

    # Gate: only a zip-grouped layout qualifies.
    if not has_well_zip(grouped_dir):
        return 0, wells_touched

    fields = [str(f).strip().lower() for f in str(filename_schema or "").split(":")]
    if "well" not in fields:
        return 0, wells_touched
    well_idx = fields.index("well")

    # Collect loose top-level image files (not zips, not dirs) grouped by well.
    by_well: dict[str, list[Path]] = {}
    try:
        entries = sorted(grouped_dir.iterdir(), key=lambda p: p.name)
    except OSError:
        return 0, wells_touched
    for p in entries:
        if not p.is_file() or p.suffix.lower() not in (".tif", ".tiff"):
            continue
        parts = p.stem.split(filename_sep)
        token = canonical_well_label(parts[well_idx]) if well_idx < len(parts) else None
        if token is None:
            if log_fn:
                log_fn(f"[append] Skipping loose file (no valid well token): {p.name}\n")
            continue
        by_well.setdefault(token, []).append(p)

    if not by_well:
        return 0, wells_touched

    for well, files in sorted(by_well.items()):
        target = grouped_dir / f"{well}.zip"
        existing: set[str] = set()
        if target.exists():
            try:
                with zipfile.ZipFile(target, "r") as zf:
                    existing = set(zf.namelist())
            except (OSError, zipfile.BadZipFile):
                existing = set()
        new_files = [f for f in files if f.name not in existing]
        skipped = len(files) - len(new_files)
        if skipped and log_fn:
            log_fn(
                f"[append] {well}: skipped {skipped} loose file(s) already in "
                f"{target.name} (kept existing).\n"
            )
        if not new_files:
            continue

        def _append(target=target, new_files=new_files) -> None:
            with atomic_zip(target) as zf:
                if target.exists():
                    with zipfile.ZipFile(target, "r") as src:
                        for name in src.namelist():
                            zf.writestr(src.getinfo(name), src.read(name))
                for f in new_files:
                    zf.write(f, arcname=f.name)

        retry_io(
            _append, what=target.name, advice=_CONSOLIDATION_ADVICE,
            log_fn=log_fn,
        )

        created = "created" if not existing else "updated"
        # Durable in the zip now — remove the loose sources.
        for f in new_files:
            try:
                f.unlink()
            except OSError:
                pass
        appended += len(new_files)
        wells_touched.add(well)
        if log_fn:
            log_fn(
                f"[append] {well}: appended {len(new_files)} loose image(s) into "
                f"{target.name} ({created}).\n"
            )

    return appended, wells_touched


def select_incremental_scope(
    opts: dict,
    input_dir: Path,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> None:
    """Append loose images next to grouped well zips, then choose the run scope.

    Mutates *opts* in place: if loose files were folded into existing/new well
    zips, switch the run to FOV-level **incremental** mode (process only the new
    FOVs, merge into the existing per-well CSV + out-zip). Falls back to a full
    ``--force`` reprocess when the run uses cross-well models the incremental
    path can't keep consistent — a ``basic`` flat-field / background (sampled
    across all wells) or an ``empty_well`` model whose designated background well
    just received new files.

    Qt-free so it stays testable and reusable; the GUI passes a ``log_fn`` that
    forwards to its run-log queue.
    """
    import re

    from util.well_token import canonical_well_label

    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    flatfield = str(opts.get("flatfield", "none") or "none")
    background = str(opts.get("background", "") or "")
    bg_tokens = {
        canonical_well_label(w)
        for w in re.split(r"[,\s]+", str(opts.get("background_well", "") or ""))
        if w
    }
    empty_well_in_use = flatfield == "empty_well" or background == "empty_well"

    # Snapshot the background well's timepoints BEFORE the append consumes the
    # loose files. The empty-well field is per-timepoint, so newly-added blanks
    # only make ALREADY-PROCESSED wells stale when they land on a timepoint that
    # already had a blank (that timepoint's field changes). Blanks for a
    # brand-new timepoint only affect that timepoint's own wells, which the
    # incremental run processes fresh — no full reprocess needed. (A pre-existing
    # timepoint that had no blank of its own used the pooled fallback; that rare
    # case isn't tracked here.)
    existing_bg_tps: set[str] = set()
    new_bg_tps: set[str] = set()
    if empty_well_in_use and bg_tokens:
        new_bg_tps = _loose_timepoints_for_wells(
            input_dir, bg_tokens,
            schema=opts["filename_schema"], sep=opts["filename_sep"])
        for w in bg_tokens:
            if w:
                existing_bg_tps |= _zip_timepoints(
                    input_dir / f"{w}.zip",
                    schema=opts["filename_schema"], sep=opts["filename_sep"])

    try:
        appended, wells_touched = append_loose_images_to_zips(
            input_dir,
            filename_schema=opts["filename_schema"],
            filename_sep=opts["filename_sep"],
            log_fn=log_fn,
        )
    except OSError as exc:
        _log(f"[append] Skipped loose-file append ({exc}).\n")
        return
    if not appended:
        return

    bg_touched = bool(bg_tokens & wells_touched)
    stale_existing_tp = bool(new_bg_tps & existing_bg_tps)
    needs_full = (
        flatfield == "basic"
        or background == "basic"
        or (empty_well_in_use and bg_touched and stale_existing_tp)
    )
    if needs_full:
        opts["force"] = True
        opts["incremental"] = False
        reason = ("basic flat-field/background is sampled across all wells"
                  if (flatfield == "basic" or background == "basic")
                  else "the empty-well background well gained blanks at an "
                       "already-processed timepoint")
        _log(
            f"[append] Appended {appended} loose image(s) into "
            f"{len(wells_touched)} well(s); forcing a full reprocess ({reason}).\n"
        )
    else:
        opts["incremental"] = True
        _log(
            f"[append] Appended {appended} loose image(s) into "
            f"{len(wells_touched)} well(s); processing only new FOVs (incremental).\n"
        )


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


def _consolidate_mixed_to_zips(
    folder: Path,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> bool:
    """Normalise a *mixed* grouped dir (some ``<well>.zip`` + some ``<well>/``
    folders) to all-zips, so the pipeline reads every well.

    A hard-killed run can leave ``in/`` with zips for the wells it finished
    compressing and bare folders for the rest. ``process_microscopy`` prefers
    zips — ``well_folders`` is ignored whenever ANY zip is present — so the
    folder-only wells would be silently dropped. This zips each well folder that
    has no matching zip yet (atomic tmp→rename) and removes the now-redundant
    folder, mirroring the remote ``_stage_mixed_mode`` behaviour. Folders for
    wells that already have a zip are left untouched (the zip wins), as are
    folders holding no TIFs at all — packing one would publish a member-less
    ``<WELL>.zip``. No-op unless the dir is genuinely mixed. Returns True iff it
    zipped at least one folder.
    """
    from util.well_token import canonical_well_label

    try:
        entries = list(folder.iterdir())
    except OSError:
        return False
    zip_wells = {
        canonical_well_label(p.stem)
        for p in entries
        if p.is_file() and p.suffix.lower() == ".zip" and canonical_well_label(p.stem)
    }
    if not zip_wells:
        return False  # pure folders / pure loose — folder mode handles it
    leftover: dict[str, Path] = {}
    for p in entries:
        if p.is_dir():
            tok = canonical_well_label(p.name)
            if tok and tok not in zip_wells:
                leftover[tok] = p
    if not leftover:
        return False

    if log_fn:
        log_fn(
            f"[zipper] Mixed input ({len(zip_wells)} zip(s) + "
            f"{len(leftover)} bare well folder(s)) — zipping the leftover "
            f"folder(s) so every well is processed.\n"
        )
    zipped_any = False
    for well, well_dir in sorted(leftover.items()):
        target = folder / f"{well}.zip"
        tifs = sorted(
            p for p in well_dir.iterdir()
            if p.is_file() and p.suffix.lower() in (".tif", ".tiff")
        )
        if not tifs:
            # Nothing to pack. Writing the archive anyway would publish a
            # member-less <WELL>.zip — which macOS refuses to expand and the
            # pipeline reads as a well whose images vanished — and deleting the
            # folder would destroy whatever non-TIF content it does hold.
            if log_fn:
                log_fn(
                    f"[zipper]   {well}: folder holds no TIFs — left as-is "
                    "(no zip written)\n"
                )
            continue
        def _pack(target=target, tifs=tifs) -> None:
            with atomic_zip(target) as zf:
                for t in tifs:
                    zf.write(t, arcname=t.name)

        retry_io(
            _pack, what=target.name, advice=_CONSOLIDATION_ADVICE, log_fn=log_fn,
        )
        shutil.rmtree(well_dir, ignore_errors=True)
        zipped_any = True
        if log_fn:
            log_fn(f"[zipper]   {well}: folder → {well}.zip ({len(tifs)} file(s))\n")
    return zipped_any


def _group_loose_tifs_in_place(
    folder: Path,
    output_dir: Path,
    *,
    log_fn: Callable[[str], None] | None,
    progress_fn: Callable[[str], None] | None,
    filename_schema: str,
    filename_sep: str,
    proc_hook: Callable[[subprocess.Popen | None], None] | None,
    move_source_tifs: bool,
) -> tuple[Path, Path, Callable[[], None] | None]:
    """Group loose TIFs that live *directly inside* ``folder`` into per-well
    subfolders within ``folder`` itself.

    This covers the case where a user puts ungrouped TIFs inside an ``in/``
    folder (or selects an ``in/`` folder full of loose TIFs) — including a
    *partially-grouped* ``in/`` left by an interrupted prior run (some
    ``in/<well>/`` folders already exist, the rest of the wells still loose).
    Such a folder used to be mistaken for already-grouped input, so the grouper
    never ran on the leftovers. Here we run WellPlateZipper with
    ``search == output == folder`` so the loose TIFs become ``folder/<well>/``
    folders the pipeline reads in folder mode; existing well folders are left
    untouched (the walk is non-recursive) and added to where wells overlap.

    Returns ``(folder, output_dir, on_success)``.
    """
    source_tifs = tuple(tif_files_in(folder))
    if log_fn:
        log_fn(
            f"[zipper] Found {len(source_tifs)} loose TIF(s) inside "
            f"{folder.name}/ — grouping into per-well folders in place.\n"
        )
    run_zipper(
        folder,
        folder,
        log_fn=log_fn,
        progress_fn=progress_fn,
        filename_schema=filename_schema,
        filename_sep=filename_sep,
        proc_hook=proc_hook,
        move_source_tifs=move_source_tifs,
    )
    if not has_well_content(folder):
        raise ValueError(
            "WellPlateZipper ran but produced no well folders.\n\n"
            "Common causes:\n"
            "  • The 'well' position in the Filename Schema does not match the actual well token in your filenames.\n"
            "  • The separator character does not match your filenames.\n"
            "  • The well token is not a valid 96-well plate position (A01–H12).\n\n"
            f"Schema used: {filename_schema}  |  Separator: '{filename_sep}'"
        )

    # If grouping produced folders alongside pre-existing well zips, normalise to
    # all-zips so the pipeline (which prefers zips) doesn't drop the new wells.
    _consolidate_mixed_to_zips(folder, log_fn=log_fn)

    if move_source_tifs:
        # Sources were moved into the well folders; nothing left to clean up.
        return folder, output_dir, None

    def _on_success() -> None:
        """Delete only the loose TIFs whose well actually got grouped — i.e. the
        well now exists as a ``folder/<well>/`` directory OR a ``folder/<well>.zip``
        (the pipeline may compress the input folders into zips during the run).
        Keying on the well token (not on a file living inside a folder) keeps the
        cleanup correct across that compression, and a name that doesn't parse to
        a grouped well is kept — so a schema mismatch never silently deletes data."""
        from util.well_token import canonical_well_label

        grouped_wells = set(iter_well_tokens(folder))
        fields = [f.strip().lower() for f in str(filename_schema or "").split(":")]
        well_idx = fields.index("well") if "well" in fields else -1
        if well_idx < 0:
            return
        for tif in source_tifs:
            parts = tif.stem.split(filename_sep)
            tok = canonical_well_label(parts[well_idx]) if well_idx < len(parts) else None
            if tok and tok in grouped_wells:
                try:
                    tif.unlink()
                except OSError:
                    pass

    return folder, output_dir, _on_success


_OME_SUFFIXES = (".ome.tif", ".ome.tiff")


def detect_ome_input(raw: Path) -> Path | None:
    """Return a packed OME-TIFF at *raw*, or ``None`` if it isn't one.

    Accepts either the ``.ome.tif`` / ``.ome.tiff`` file selected directly, or a
    directory containing exactly one such file at its top level. Analyze uses this
    to spot a raw OME-TIFF and redirect the user to the Datasets converter; the
    converter itself does the de-interleave (``datasets.ome_import``).
    """
    if raw.is_file() and raw.name.lower().endswith(_OME_SUFFIXES):
        return raw
    if raw.is_dir():
        try:
            hits = [
                p for p in sorted(raw.glob("*"))
                if p.is_file() and p.name.lower().endswith(_OME_SUFFIXES)
            ]
        except OSError:
            return None
        if len(hits) == 1:
            return hits[0]
    return None


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
    plan = plan_input_layout(raw)
    if not plan.runnable:
        raise ValueError(_layout_error(plan, raw))

    grouped = plan.input_dir
    out_dir = plan.output_dir
    assert grouped is not None and out_dir is not None  # guaranteed by `runnable`

    if plan.action == FOLD_LOOSE:
        # Loose TIFs dropped next to existing well zips — e.g. a new
        # timepoint/FOV added to an already-grouped dataset. Do NOT group these
        # into per-well folders: a folder for a well that already has a
        # <WELL>.zip is silently ignored by the zip-preferring pipeline (see
        # _consolidate_mixed_to_zips), so the new images would never be
        # processed. Leave them for append_loose_images_to_zips (run by
        # select_incremental_scope) to fold into the matching zips / create new
        # ones and drive FOV-level incremental reprocessing.
        if log_fn:
            log_fn(
                f"[zipper] Found {plan.loose_count} loose TIF(s) alongside "
                f"existing well zip(s) in {grouped.name}/ — folding them into "
                f"the matching zips for incremental reprocessing.\n"
            )
        _consolidate_mixed_to_zips(grouped, log_fn=log_fn)
        return grouped, out_dir, None

    if plan.action == GROUP_LOOSE:
        # A pre-existing grouped root that still holds loose, ungrouped TIFs —
        # whether a brand-new drop or the leftovers of a partial prior run (some
        # <well>/ folders already present) — is grouped in place so every well
        # gets consumed, not just the ones a prior run reached.
        return _group_loose_tifs_in_place(
            grouped,
            out_dir,
            log_fn=log_fn,
            progress_fn=progress_fn,
            filename_schema=filename_schema,
            filename_sep=filename_sep,
            proc_hook=proc_hook,
            move_source_tifs=move_source_tifs,
        )

    if plan.action == UNRECOGNISED:
        # Nothing in here parses as a well, but the pipeline is still the thing
        # that reads it — the same acceptance a folder named "in" has always
        # had. Say what was not recognised rather than failing the run with a
        # message listing a condition the user has already met.
        if log_fn:
            log_fn(
                f"[zipper] Using {grouped}/ as input — no well-named zips or "
                f"folders were recognised in it, so the pipeline will report "
                f"what it can read.\n"
            )
        _consolidate_mixed_to_zips(grouped, log_fn=log_fn)
        return grouped, out_dir, None

    if plan.action == ZIP_ROOT:
        # Snapshot the loose TIFs BEFORE the zipper runs so the
        # post-success cleanup deletes exactly the inputs the user
        # had at start-of-run — not any incidental .tif files that
        # might land in raw/ later (the zipper's own output goes to
        # raw/in/, not raw/, so this is mostly defensive).
        source_tifs = tuple(tif_files_in(raw))
        if log_fn:
            log_fn(
                f"[zipper] Found {len(source_tifs)} TIF files — running WellPlateZipper to create per-well folders in in/\n"
            )
        run_zipper(
            raw,
            grouped,
            log_fn=log_fn,
            progress_fn=progress_fn,
            filename_schema=filename_schema,
            filename_sep=filename_sep,
            proc_hook=proc_hook,
            move_source_tifs=move_source_tifs,
        )
        if not grouped.is_dir() or not has_well_content(grouped):
            raise ValueError(
                "WellPlateZipper ran but produced no well folders or zip files.\n\n"
                "Common causes:\n"
                "  \u2022 The 'well' position in the Filename Schema does not match the actual well token in your filenames.\n"
                "  \u2022 The separator character does not match your filenames.\n"
                "  \u2022 The well token is not a valid 96-well plate position (A01\u2013H12).\n\n"
                f"Schema used: {filename_schema}  |  Separator: '{filename_sep}'"
            )

        if move_source_tifs:
            # The zipper already moved the source TIFs; no post-success
            # cleanup needed and nothing safe to undo on failure either
            # (the source files are gone in their old location regardless
            # of pipeline outcome).
            return grouped, out_dir, None

        def _on_success() -> None:
            """Delete loose TIFs from raw/ once the pipeline completes
            cleanly. raw/in/{well}.zip already has the data; the
            loose copies in raw/ are now redundant."""
            for tif in source_tifs:
                try:
                    tif.unlink()
                except OSError:
                    pass

        return grouped, out_dir, _on_success

    # GROUPED. Normalise a mixed (zips + bare folders) input dir left by a
    # hard-killed run to all-zips, so no folder-only well is dropped by the
    # zip-preferring pipeline.
    _consolidate_mixed_to_zips(grouped, log_fn=log_fn)
    if grouped == raw:
        # The user selected the grouped folder itself; there is no enclosing
        # dataset folder to hold leftover loose TIFs from a partial run.
        return grouped, out_dir, None

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
            for well_dir in grouped.iterdir():
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
            grouped,
            log_fn=log_fn,
            progress_fn=progress_fn,
            filename_schema=filename_schema,
            filename_sep=filename_sep,
            proc_hook=proc_hook,
            move_source_tifs=move_source_tifs,
        )
        if move_source_tifs:
            return grouped, out_dir, None

        def _on_success_resume() -> None:
            """Delete the leftover loose TIFs from raw/ once the
            pipeline completes cleanly. Mirror the fresh-run
            callback."""
            for tif in source_tifs:
                try:
                    tif.unlink()
                except OSError:
                    pass

        return grouped, out_dir, _on_success_resume

    # All loose TIFs at top level (if any) are already represented
    # under raw/in/. If the prior run was copy-mode they're
    # redundant and the on_success cleanup will tidy them up;
    # otherwise (move-mode prior run, or no loose TIFs at all) the
    # state is already fully consumed and there's nothing to do.
    if loose_at_top:
        if log_fn:
            log_fn(
                f"[zipper] Resume: all {len(loose_at_top)} loose TIF(s) at "
                f"top level already have a copy under raw/in/. Skipping "
                f"zipper re-run; will clean up the loose copies on "
                f"successful pipeline completion.\n"
            )
        already_copied = tuple(loose_at_top)

        def _on_success_post_copy() -> None:
            for tif in already_copied:
                try:
                    tif.unlink()
                except OSError:
                    pass

        return grouped, out_dir, _on_success_post_copy
    return grouped, out_dir, None
