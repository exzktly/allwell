"""Analyze's input folder: is it runnable, and which folders does a run use.

:func:`plan_input_layout` is the one decision, rendered under the input field
and acted on at Run by :func:`resolve_input_output`. The pipeline reads one
``<WELL>.zip`` per well; loose images and per-well folders are refused by name
with the remedy (Datasets → Zip loose folder), using the same check the
pipeline refuses on (``pipeline.input_layout.non_zip_input``).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple
from util.dir_scan import files_in
from util.well_token import canonical_well_label
from pipeline.input_layout import NonZipInput, ZIP_LOOSE_FOLDER_REMEDY, non_zip_input


def build_bootstrap_cmd(bootstrap: Path, alias: str) -> list[str]:
    """Build the argv for the remote-provisioning bootstrap subprocess.

    Frozen bundle: re-invoke the launcher with the ``--run-bootstrap`` sentinel,
    which routes argv into ``services/remote_bootstrap.main()`` (mirrors the
    ``--run-zipper`` / ``--run-pipeline`` paths). Passing the ``.py`` path there
    would make ``sys.executable`` (the .app binary) relaunch the GUI instead of
    running the bootstrap — the frozen-only failure this guards. From source,
    invoke the interpreter on the script path as before.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-bootstrap", alias]
    return [sys.executable, str(bootstrap), alias]


def _is_well_named(p: Path) -> bool:
    """Match `A1` / `A01` … `H12` against the well-token canonicaliser."""
    from util.well_token import is_valid_well_name
    return is_valid_well_name(p.stem)


def _well_zip_among(files: list[Path]) -> bool:
    """True iff *files* holds at least one well-named ``<WELL>.zip``.

    Takes an already-made listing so a caller that has one does not read the
    directory again to ask this. ``Path.match`` rather than a suffix
    comparison, because that is the rule ``glob("*.zip")`` applied and it is
    platform-dependent in a way a hand-written test is not: case-sensitive on
    POSIX, case-insensitive on Windows.
    """
    return any(p.match("*.zip") and _is_well_named(p) for p in files)


def has_well_zip(folder: Path) -> bool:
    """True iff *folder* contains at least one well-named ``<WELL>.zip``.

    A *file* named ``<WELL>.zip``: a directory of that name is not an archive,
    and opening it as one fails.
    """
    try:
        return _well_zip_among(files_in(folder))
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
#: The selected path is a Zeiss CZI, which Analyze cannot read either.
PACKED_CZI = "packed_czi"
#: The selected path is a Nikon ND2, which Analyze cannot read either.
PACKED_ND2 = "packed_nd2"
#: The selected path is not a directory at all.
NOT_A_DIR = "not_a_dir"
#: The input folder holds per-well zips and nothing a run would skip.
GROUPED = "grouped"
#: Loose images or per-well folders a zips-only run would not read. Datasets →
#: Zip loose folder converts them; Analyze does not.
NEEDS_ZIPPING = "needs_zipping"
#: An ``in/`` that holds something, none of which parses as a well.
UNRECOGNISED = "unrecognised"
#: An ``in/`` that exists and is empty.
EMPTY_IN = "empty_in"
#: The folder is there and is a directory, but listing it failed.
UNREADABLE = "unreadable"
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

#: The same for a Zeiss CZI. Two messages rather than one generic sentence,
#: because the remedy is a *different page* and naming the wrong one sends the
#: user to a converter that will refuse their file.
PACKED_CZI_MESSAGE = (
    "⚠ This is a Zeiss CZI (or a folder of them, as ZEN writes when it splits "
    "scenes). Convert it to a dataset folder first in the Datasets tab → "
    "“Convert CZI”, then open that folder here to process it."
)

#: And for a Nikon ND2. It names no folder case, unlike the CZI message:
#: NIS-Elements writes a plate as one file, so a folder holding several ND2s is
#: several plates and Analyze has no way to know which one was meant.
PACKED_ND2_MESSAGE = (
    "⚠ This is a Nikon ND2. Convert it to a dataset folder first in the "
    "Datasets tab → “Convert ND2”, then open that folder here to process it."
)


_OME_SUFFIXES = (".ome.tif", ".ome.tiff")
_CZI_SUFFIXES = (".czi",)
_ND2_SUFFIXES = (".nd2",)

#: The packed acquisition files Analyze cannot read, each with the plan action,
#: the message naming the Datasets page that converts it, and whether *several*
#: of them in one folder are one plate rather than an ambiguity. Ordered, and
#: OME-TIFF first, because a ``.ome.tif`` also ends in ``.tif`` and nothing
#: else here does — a table rather than a chain of ``if``s so adding a fourth
#: converter cannot add a fifth place to state the same fact.
#:
#: The last column is where the formats differ. Two OME-TIFFs in a folder are
#: two plates and picking either would convert one the user did not name, and
#: the same is true of two ND2s, because NIS-Elements writes a plate as one
#: file. Several ``.czi`` files in a folder are ZEN's *Split Scenes* export —
#: one file per scene, one plate — which the CZI converter reads as a set, so
#: a folder of them is a recognised input rather than an ambiguity.
_PACKED_KINDS: tuple[tuple[tuple[str, ...], str, str, bool], ...] = (
    (_OME_SUFFIXES, PACKED_OME, PACKED_OME_MESSAGE, False),
    (_CZI_SUFFIXES, PACKED_CZI, PACKED_CZI_MESSAGE, True),
    (_ND2_SUFFIXES, PACKED_ND2, PACKED_ND2_MESSAGE, False),
)

#: Every action the table above can produce. Derived from it rather than
#: written out, because the one place that lists these by hand is the one that
#: will be a converter short: a packed kind missing from a hand-written roster
#: does not fail, it silently falls through to the generic layout error and
#: tells the user their file has no images in it.
PACKED_ACTIONS = frozenset(action for _s, action, _m, _many in _PACKED_KINDS)


#: Actions that describe input the run can proceed with.
RUNNABLE_ACTIONS = frozenset({GROUPED})


@dataclass(frozen=True)
class InputPlan:
    """What Analyze will do with the folder the user selected.

    *input_dir* and *output_dir* are the pair the pipeline will be handed, and
    are ``None`` for the actions that cannot run. *loose_count* is the number of
    loose images the plan refuses (0 where it refuses none), and *message* is
    the one-line readout, phrased for the user.
    """

    action: str
    input_dir: Path | None
    output_dir: Path | None
    loose_count: int
    message: str

    @property
    def runnable(self) -> bool:
        return self.action in RUNNABLE_ACTIONS


def _unreadable_plan(folder: Path, exc: OSError) -> InputPlan:
    """The plan for a directory that exists and could not be listed.

    A directory can be traversable and not listable — a share whose ACL grants
    execute and not read, a mount that has gone stale — and the two are told
    apart only by trying. ``Path.is_dir()`` says yes and the listing then fails.

    This needs its own action because the alternatives are both wrong.
    ``Path.glob`` answers such a directory with an empty list, so the folder
    read as `EMPTY_IN`, *"nothing to process"* — a statement about the plate,
    made from a failure to look at it, and exactly the silent substitution the
    pipeline is not allowed to make. Letting the ``OSError`` out is no better
    here: this is reached from a ``textChanged`` slot, where an exception leaves
    the path field showing the new folder and every label beside it still
    describing the previous one.
    """
    return InputPlan(
        UNREADABLE, None, None, 0,
        f"Could not read {folder.name}/ — {exc.strerror or exc}. "
        f"The folder is there; its contents could not be listed.",
    )


def _needs_zipping_plan(found: NonZipInput, where: str) -> InputPlan:
    """The refusal for loose images / well folders, as one readout line."""
    parts = []
    if found.loose:
        parts.append(f"{len(found.loose)} loose image(s)")
    if found.stranded:
        parts.append(f"{len(found.stranded)} well folder(s) with no zip")
    return InputPlan(
        NEEDS_ZIPPING, None, None, len(found.loose),
        f"⚠ {where} holds {' and '.join(parts)}. Analyze reads per-well zips — "
        f"convert them first in Datasets → Zip loose folder.",
    )


def _plan_grouped_root(root: Path, output_dir: Path, *, named_in: bool) -> InputPlan:
    """Classify a folder that is being used as the pipeline's input directly.

    The verdict is :func:`pipeline.input_layout.non_zip_input`'s, the same
    function the pipeline refuses on, so a plate this readout calls runnable is
    one the pipeline will run.
    """
    try:
        entries = files_in(root)
    except OSError as exc:
        return _unreadable_plan(root, exc)
    well_zips = [(canonical_well_label(p.stem), p) for p in entries
                 if p.match("*.zip") and _is_well_named(p)]
    found = non_zip_input(root, well_zips)
    if found.refused:
        return _needs_zipping_plan(found, f"{root.name}/")
    if well_zips:
        return InputPlan(
            GROUPED, root, output_dir, 0,
            "✓ Using selected folder as input" if named_in
            else f"✓ Found {root.name}/ subfolder — using as input",
        )
    if dir_has_entries(root):
        return InputPlan(
            UNRECOGNISED, None, None, 0,
            f"No per-well zips (A01.zip …) in {root.name}/ — nothing to process.",
        )
    return InputPlan(
        EMPTY_IN, None, None, 0,
        f"The {root.name}/ folder is empty — nothing to process.",
    )


def plan_input_layout(raw: Path) -> InputPlan:
    """Decide whether *raw* — the folder the user selected — is runnable input.

    Pure: reads the filesystem, changes nothing. :func:`resolve_input_output`
    acts on the plan and ``AnalyzeTab._refresh_output`` displays it, so the
    readout under the input field cannot disagree with what Run then does.

    The pipeline reads one ``<WELL>.zip`` per well. A folder *named* ``in`` is
    the input; a folder that *contains* ``in/`` feeds the pipeline from that
    subfolder, and writes to the ``out/`` beside it. Loose images and per-well
    folders are refused by name (:data:`NEEDS_ZIPPING`) — at the selected root
    as well as inside ``in/``, because a run would silently skip either — and
    the message sends the user to Datasets → Zip loose folder.
    """
    packed = detect_packed_input(raw)
    if packed is not None:
        return InputPlan(packed.action, None, None, 0, packed.message)
    if not raw.is_dir():
        return InputPlan(NOT_A_DIR, None, None, 0, "Not a directory.")

    if raw.name.lower() == "in":
        return _plan_grouped_root(raw, raw.parent / "out", named_in=True)

    in_sub = raw / "in"
    try:
        has_in = in_sub.is_dir()
        # Well folders at the selected root count only when there is no
        # ``in/``: beside one, the pipeline never looked at the root's folders.
        found = non_zip_input(raw, [])
    except OSError as exc:
        return _unreadable_plan(raw, exc)
    if found.loose or (found.stranded and not has_in):
        return _needs_zipping_plan(found if not has_in else found._replace(stranded=[]),
                                   f"{raw.name}/")
    if has_in:
        return _plan_grouped_root(in_sub, raw / "out", named_in=False)
    return InputPlan(
        NO_INPUT, None, None, 0,
        "No in/ folder of per-well zips found in the selected directory.",
    )


def _layout_error(plan: InputPlan, raw: Path) -> str:
    """The long-form version of an unrunnable plan, for the run-time exception.

    The readout has room for one line; a failed run has room to explain. Both
    describe the same verdict, so they are built from the same plan.
    """
    if plan.action in PACKED_ACTIONS:
        # Each already carries the message naming its own converter — derived
        # from the file's suffix in `detect_packed_input`, which is the only
        # place that decides which page converts what.
        return plan.message
    if plan.action == NOT_A_DIR:
        return f"Not a directory:\n{raw}"
    if plan.action == UNREADABLE:
        return (
            f"{plan.message}\n\n"
            "The folder exists, but listing what is in it failed. On a network "
            "share or a mounted volume this is usually a permissions problem "
            "or a dropped connection — the run is not started, because what is "
            "in the folder is unknown rather than known to be nothing.\n\n"
            f"Folder selected: {raw}"
        )
    if plan.action == NEEDS_ZIPPING:
        return f"{plan.message}\n\n{ZIP_LOOSE_FOLDER_REMEDY}\n\nFolder selected: {raw}"
    if plan.action == EMPTY_IN:
        return (
            f"{plan.message}\n\n"
            "The folder that holds the images is there, but there is nothing "
            "in it.\n\n"
            f"Folder selected: {raw}"
        )
    return (
        f"{plan.message}\n\n"
        "Expected one of:\n"
        '  • Selected folder is named "in" and holds <WELL>.zip archives\n'
        '  • Selected folder contains a sub-folder "in" holding them\n\n'
        f"{ZIP_LOOSE_FOLDER_REMEDY}\n\n"
        f"Folder selected: {raw}"
    )


def well_present_in_input(input_dir: Path, well: str) -> bool:
    """True iff *well* (any 96-well form, e.g. ``'c11'`` / ``'C11'``) has a
    ``<WELL>.zip`` in *input_dir*, or in its ``in/`` when it has one.

    Used to block a run when ``--flatfield empty_well`` / ``--background
    empty_well`` name a background well that doesn't exist. The Analyze form
    validates the *selected* folder, but a folder that holds an ``in/``
    subfolder feeds the pipeline from that ``in/`` — see
    :func:`plan_input_layout` — so that is searched too.

    The archive has to be a *file*: a directory named ``A01.zip`` answering yes
    here is a run allowed to start against a background well the pipeline will
    not find.
    """
    target = canonical_well_label(well or "")
    if target is None:
        return False
    roots = [input_dir]
    in_sub = input_dir / "in"
    if input_dir.name.lower() != "in" and in_sub.is_dir():
        roots.append(in_sub)
    for root in roots:
        try:
            files = files_in(root)
        except OSError:
            continue
        if any(p.match("*.zip") and canonical_well_label(p.stem) == target for p in files):
            return True
    return False


def iter_well_tokens(input_dir: Path) -> list[str]:
    """Canonical well tokens (``A01``) of the ``<WELL>.zip`` archives in a
    *resolved* input dir, sorted by file name."""
    try:
        files = files_in(input_dir)
    except OSError:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for p in sorted(files, key=lambda q: q.name):
        tok = canonical_well_label(p.stem) if p.suffix.lower() == ".zip" else None
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def iter_all_well_tokens(raw: Path) -> list[str]:
    """Every distinct well the run will read from *raw*, canonical and sorted.

    Reads the folder :func:`plan_input_layout` resolves *raw* to, so it reads
    the folder the run will actually read. Best-effort by design: a layout
    nothing can be read from answers empty rather than raising, and
    :func:`resolve_input_output` is what says there is no input at all.
    """
    grouped = plan_input_layout(raw).input_dir
    return sorted(iter_well_tokens(grouped)) if grouped is not None else []


def count_expected_wells(raw: Path) -> int:
    """Best-effort count of distinct wells the run will produce, for the Analyze
    progress denominator — never zero, because the dashboard divides by it.

    A folder that cannot be *listed* answers 1 rather than raising: the caller
    asks for this *before* ``resolve_input_output``, and raising here would
    pre-empt the refusal that names the folder and the errno.
    """
    try:
        return len(iter_all_well_tokens(raw)) or 1
    except OSError:
        return 1


class PackedInput(NamedTuple):
    """A packed acquisition file found at the selected path.

    Carries the plan *action* and the *message* as well as the path, because
    which converter to send the user to is decided here — by the suffix — and a
    caller re-deriving it would be a second place that has to learn about the
    next file format.
    """

    path: Path
    action: str
    message: str


def detect_packed_input(raw: Path) -> PackedInput | None:
    """Return the packed acquisition file at *raw*, or ``None`` if there is none.

    Accepts either the file selected directly, or a directory holding them.
    A directory with exactly one is that file. A directory with *several* is
    ambiguous for an OME-TIFF or an ND2 — picking either would convert a plate
    the user did not name — and is not for a CZI, where a folder of them is
    ZEN's *Split Scenes* export of one plate; see :data:`_PACKED_KINDS`.
    Analyze uses this to spot a packed input and redirect the user to the
    Datasets converter for it; the converters themselves do the work
    (``datasets.ome_import``, ``datasets.czi_import``, ``datasets.nd2_import``).
    """
    if raw.is_file():
        name = raw.name.lower()
        for suffixes, action, message, _many in _PACKED_KINDS:
            if name.endswith(suffixes):
                return PackedInput(raw, action, message)
        return None
    if raw.is_dir():
        try:
            entries = list(files_in(raw))
        except OSError:
            return None
        for suffixes, action, message, many_is_one_plate in _PACKED_KINDS:
            hits = [p for p in entries if p.name.lower().endswith(suffixes)]
            if len(hits) == 1:
                return PackedInput(hits[0], action, message)
            if hits and many_is_one_plate:
                # The folder, not a member: several of these *are* the plate,
                # and naming one of them would describe a scene rather than
                # the thing Analyze was pointed at.
                return PackedInput(raw, action, message)
    return None


def resolve_input_output(raw: Path) -> tuple[Path, Path]:
    """Resolve the folder the user selected into ``(input_dir, output_dir)``.

    Acts on :func:`plan_input_layout`'s verdict and nothing else, so the run
    does what the readout said. An unrunnable plan raises ``ValueError`` with
    the long-form explanation (:func:`_layout_error`). Nothing is converted or
    moved: loose images and per-well folders are refused, and Datasets → Zip
    loose folder is where they become zips.
    """
    plan = plan_input_layout(raw)
    if not plan.runnable:
        raise ValueError(_layout_error(plan, raw))
    assert plan.input_dir is not None and plan.output_dir is not None  # guaranteed by `runnable`
    return plan.input_dir, plan.output_dir
