"""The resume marker the packed-file converters share.

Three Datasets tools turn one packed acquisition file into a folder of
per-well archives: *Convert OME-TIFF*, *Convert CZI* and *Convert ND2*. Each
publishes each well's
``<dest>/in/<WELL>.zip`` atomically, so a run that is cancelled or killed leaves
whole, correct wells behind and only the rest to do — and finishing those is
worth hours on a plate.

Finishing them is only *safe* when the second run is making the same dataset as
the first, and that is the whole reason this module exists. Two different
mappings of one source, or the same mapping of two different sources, produce
**identical filenames**: nothing on disk distinguishes ``A01.zip`` written from
scene 0 from ``A01.zip`` written from scene 7. So the settings that decide the
mapping are written into the destination while the run is in flight, and a
later run resumes only against a marker that matches its own. Against a marker
that does not match, it refuses rather than stitching one folder out of two
acquisitions — the reuse decision CLAUDE.md §7.7 #3 is about, where the failure
mode is a complete-looking plate of half-wrong numbers.

The marker is per-tool (each converter passes its own filename) because a folder
carrying the *other* converter's leftovers is not a resume either.

Distinct from ``datasets.core.dataset_paths.make_dataset_api``, which the
copy-into-a-destination tools use: theirs answers "did I leave leftovers here?"
and is cleared by rebuilding from scratch. This one answers "may I carry on from
what is here?", which is a question about settings and cannot be answered by the
presence of a file alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from util.aw_safe import swallow
from util.json_write import write_json_atomic


def mapping_signature(src: Path | str, params: object, fields) -> dict:
    """What a resume has to match: the source, and the settings that map it.

    *fields* names the attributes of *params* that change which image lands
    under which filename. Every one of them belongs here and nothing else does:
    a field left out lets two mappings share a marker, and a field wrongly
    included (a worker count, say) refuses a resume that was perfectly safe.
    Tuples are written as lists so the JSON round-trip compares equal.
    """
    values: dict = {}
    for name in fields:
        value = getattr(params, name)
        values[name] = list(value) if isinstance(value, tuple) else value
    source = Path(src)
    values["source"] = str(source.resolve())
    # Size and mtime as well as the path: a file replaced at the same name — a
    # re-export, a restore, the next plate saved over the last — is a different
    # source, and resuming across that leaves half of one plate and half of
    # another under one set of filenames. Cheap: one stat, no hashing.
    try:
        stat = source.stat()
        values["source_size"] = stat.st_size
        values["source_mtime_ns"] = stat.st_mtime_ns
    except OSError:
        values["source_size"] = None
        values["source_mtime_ns"] = None
    return values


def write_run_marker(dest: Path | str, marker: str, signature: dict) -> None:
    """Record that a conversion is under way in *dest*, and with what settings."""
    root = Path(dest)
    root.mkdir(parents=True, exist_ok=True)
    # Atomically, because this marker is what decides whether a later run may
    # finish this one's work. A half-written marker reads as a *different*
    # mapping, and the wells already on disk were written by this one — so an
    # interrupted write turns a resumable folder into one the tool refuses,
    # or, if it happened to truncate to something parseable, into one it
    # resumes with the wrong settings.
    write_json_atomic(root / marker, signature)


#: What a *finished* run leaves behind, so a later one can add the wells it did
#: not convert. Named apart from the in-progress marker because the two answer
#: different questions: that one says "a run died here, finish it", this one
#: says "a completed run made this dataset, and these were its settings".
#:
#: It exists for subset conversions. Converting six wells today and the rest
#: next week is one dataset built in two sittings, and without a record of the
#: first sitting's mapping the second is indistinguishable from someone
#: pointing a different plate at the same folder — which is what the
#: "Already holds converted wells" refusal is there to stop.
DONE_MARKER = ".aw_converted"


def clear_run_marker(dest: Path | str, marker: str, label: str) -> None:
    """Drop the in-progress marker — the run finished.

    Does **not** leave the folder unmarked: :func:`write_done_marker` records
    the settings that built it, so a later run adding the remaining wells can
    tell this is its own dataset to extend rather than a stranger's plate.
    """
    with swallow(f"{label} marker"):
        (Path(dest) / marker).unlink(missing_ok=True)


def write_done_marker(dest: Path | str, signature: dict) -> None:
    """Record the mapping a completed conversion used, for a later top-up."""
    write_json_atomic(Path(dest) / DONE_MARKER, dict(signature))


def read_done_marker(dest: Path | str) -> dict | None:
    """The settings of the completed conversion in *dest*, or ``None``."""
    path = Path(dest) / DONE_MARKER
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def read_run_marker(dest: Path | str, marker: str) -> dict | None:
    """The settings of the interrupted run in *dest*, or ``None`` if there is none."""
    path = Path(dest) / marker
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def converted_wells(dest: Path | str) -> list[str]:
    """The well archives already sitting in ``<dest>/in``."""
    in_dir = Path(dest) / "in"
    if not in_dir.is_dir():
        return []
    return sorted(p.stem for p in in_dir.glob("*.zip"))


@dataclass(frozen=True)
class ResumeState:
    """Whether *dest* holds an interrupted run of this conversion to finish."""

    resumable: bool = False
    wells_done: int = 0
    # A previous run is there but was making a different dataset — different
    # source, grid, origin or ordering. Resuming onto it would leave a folder
    # stitched from two mappings, with the filenames of one and the images of
    # both, and nothing in it saying so.
    mismatched: bool = False

    def hint(self) -> str:
        if self.mismatched:
            return ("This folder holds an interrupted conversion made with "
                    "different settings. Empty it, or choose another folder.")
        if self.resumable:
            return (f"↳ Resuming an interrupted run: {self.wells_done} well(s) "
                    f"are already converted and will be kept.")
        return ""


def resume_state(dest: Path | str, marker: str, signature: dict) -> ResumeState:
    """Read *dest* as a place to continue the conversion *signature* describes."""
    found = read_run_marker(dest, marker)
    if found is None:
        return ResumeState()
    done = len(converted_wells(dest))
    if found != signature:
        # A marker with no wells behind it describes a run that wrote nothing —
        # one that failed before its first archive, or was cancelled and then
        # had its settings changed, which is the commonest reason to cancel.
        # There is nothing on disk for a second mapping to contaminate, and the
        # marker is a hidden file the "empty the folder" advice does not clear.
        return ResumeState() if done == 0 else ResumeState(mismatched=True,
                                                           wells_done=done)
    return ResumeState(resumable=True, wells_done=done)


def destination_problem(dest: Path | str, marker: str,
                        signature: dict | None = None) -> str:
    """``""`` if *dest* is usable, else a short reason, for the field's hint.

    A conversion writes ``<dest>/in/<WELL>.zip`` and overwrites each archive it
    produces, so a destination that already holds wells keeps whichever of them
    this run does not write — a folder holding two different plates, with
    nothing in it saying so. Every other Datasets tool validates its destination
    field this way (``datasets.core.dataset_fields.is_free_destination``); the
    wording differs here only because a converter writes ``in/`` and nothing else.

    Given a *signature*, a folder carrying this tool's own in-progress marker
    for the *same* settings is not a problem but a resume, and one carrying it
    for different settings is refused with its own wording — the filenames of
    two mappings are identical, so only the marker can tell them apart.
    """
    root = Path(dest)
    if not str(dest).strip():
        return ""
    if root.exists() and not root.is_dir():
        return "Not a folder — choose a folder."
    if signature is not None:
        state = resume_state(root, marker, signature)
        if state.mismatched:
            return state.hint()
        if state.resumable:
            return ""      # this tool's own interrupted run: finish it
    if (root / "out" / "pipeline_info.json").exists():
        return "Already holds a finished dataset — choose an empty folder."
    in_dir = root / "in"
    if in_dir.is_dir() and any(in_dir.glob("*.zip")):
        # A dataset this converter built with *these* settings is not a
        # stranger's plate: it is a subset conversion being topped up, which
        # is the workflow converting part of a plate implies. Compared on the
        # mapping alone — the well selection is deliberately not part of the
        # signature (see `mapping_signature`), because which *other* wells a
        # run wrote changes nothing about the one in front of you.
        if signature is not None and read_done_marker(root) == signature:
            return ""
        return "Already holds converted wells — choose an empty folder."
    return ""
