"""Raw datasets for Concatenate: a converted plate nobody has analysed yet.

A dataset is normally ``in/`` plus ``out/`` with ``out/pipeline_info.json``,
which says how the filenames decompose into fields. A freshly converted plate
has only ``in/`` (and the acquisition record beside its archives), so the
filename schema has to come from the archives' own member names. Merging raw
datasets is the point at which the timepoint field gets shifted, so the schema
must be certain: a guess that placed the timepoint in the wrong slot would
rename every image under a wrong time. This module therefore refuses rather
than guesses — a schema is accepted only when :mod:`util.schema_infer` is
confident and finds a timepoint slot, and two raw datasets must agree on it.

Qt-free; takes the caller's error class so it can sit under any tool.
"""

from __future__ import annotations

from pathlib import Path

from datasets.core.dataset_paths import (
    DatasetPaths,
    collect_input_image_stems,
    schema_values_from_stems,
)
from datasets.core.member_names import is_os_junk_member
from util.image_exts import is_run_input_member
from util.schema_infer import infer_from_dir
from util.timepoint import sort_key

#: The sidecar keys two raw datasets must agree on. A subset of
#: ``COMPAT_KEYS``: the rest (nuclear token, segmentation method) are
#: properties of an analysis, and nothing has been analysed.
RAW_COMPAT_KEYS = ("separator", "schema_fields")


def _out_contents(out: Path) -> list[str] | None:
    """The names in *out* that are not operating-system debris, or ``None``
    when *out* is not a readable folder.

    Finder writes a ``.DS_Store`` into any folder a user has opened, so an
    ``out/`` that was created empty and then looked at is not empty on disk.
    Counting that file as analysis output turned a raw plate into a "broken
    analysed dataset" and the merge was refused for a missing
    ``pipeline_info.json``. Only the specific files an OS creates are skipped
    (:func:`datasets.core.member_names.is_os_junk_member`); a stray CSV still
    counts, because it may be real output.
    """
    try:
        if not out.is_dir():
            return None
        return sorted(entry.name for entry in out.iterdir() if not is_os_junk_member(entry.name))
    except OSError:
        return None


def is_raw_dataset(root: Path | str) -> bool:
    """``in/`` present, and no analysis output beside it.

    A missing ``out/``, or one holding nothing but OS debris such as
    ``.DS_Store``, is raw; an ``out/`` holding anything else is an analysed
    dataset (or a broken one, which the analysed path reports in its own words
    rather than this one quietly accepting it as raw).
    """
    root = Path(root)
    if not (root / "in").is_dir():
        return False
    out = root / "out"
    if not out.exists():
        return True
    return _out_contents(out) == []


def _holds_loose_images(root: Path) -> bool:
    """Whether *root*, or a folder directly inside it, holds image files.

    That is the shape of an image set straight off the microscope that has not
    been converted into a dataset yet. One level deep is enough to recognise it
    (a folder per well, or per timepoint) without walking a whole drive.
    """
    try:
        for entry in root.iterdir():
            if entry.is_file() and is_run_input_member(entry.name):
                return True
            if entry.is_dir() and not entry.name.startswith("."):
                if any(child.is_file() and is_run_input_member(child.name)
                       for child in entry.iterdir()):
                    return True
    except OSError:
        return False
    return False


#: What every "not usable" reason ends with: the two shapes this tool takes.
_SHAPES = ("Concatenate takes an analysed dataset (an 'in' and an 'out' folder) or a raw one "
           "(just an 'in' folder of per-well zips, as Import Loose or the OME/CZI import writes).")


def unusable_reason(root: Path | str) -> str:
    """``""`` when *root* is shaped like an analysed or a raw dataset, else why not.

    Structural only, so the page can call it on every edit of a path field. It
    exists because the shared rule (``in`` *and* ``out``) answered every
    rejection with "needs both an 'in' and an 'out' folder", which told the
    user raw image sets were not accepted at all — they are, once converted.
    Each branch names the one thing to do about it.
    """
    root = Path(root)
    if not root.is_dir():
        return "The folder doesn't exist."
    if not (root / "in").is_dir():
        if _holds_loose_images(root):
            return ("It holds images but no 'in' folder, so it hasn't been converted into a "
                    "dataset yet. Run Datasets → Import Loose (or the OME/CZI import) on it "
                    "first, then pick the folder that writes.")
        return "It has no 'in' folder. " + _SHAPES
    out = root / "out"
    if not out.exists():
        return ""
    contents = _out_contents(out)
    if contents is None:
        return "Its 'out' is not a readable folder. " + _SHAPES
    if contents and not (out / "pipeline_info.json").is_file():
        shown = ", ".join(contents[:3]) + (", …" if len(contents) > 3 else "")
        return ("Its 'out' folder holds files but no pipeline_info.json, so it is neither an "
                f"analysed dataset nor a raw one ({shown}). For a raw dataset, delete or empty "
                "the 'out' folder; for an analysed one, the analysis didn't finish.")
    return ""


def raw_paths(root: Path | str) -> DatasetPaths:
    root = Path(root)
    return DatasetPaths(root=root, in_dir=root / "in", out_dir=root / "out")


def raw_info(paths: DatasetPaths, *, label: str, error_cls: type[Exception]) -> dict:
    """A sidecar-shaped dict for a raw dataset, inferred from its archives.

    Carries what the planner reads from a real sidecar: the schema, the field
    indices, the channel tokens and ``available_timepoints``. Raises
    *error_cls* when the names do not determine a schema with a timepoint slot.
    """
    guess = infer_from_dir(paths.in_dir)
    if guess is None:
        raise error_cls(f"{label}: no images were found under its 'in' folder to read "
                        "a filename schema from.")
    if not guess.confident or "timepoint" not in guess.fields:
        raise error_cls(
            f"{label}: the image filenames don't determine a schema with a "
            f"timepoint field (best reading: {guess.schema_str or 'none'}). "
            "Concatenate can't tell which part of each name to shift.")
    fields = list(guess.fields)

    def index(name: str) -> int:
        return fields.index(name) if name in fields else -1

    stems = collect_input_image_stems(paths.in_dir)
    timepoints = schema_values_from_stems(
        stems, schema_fields=fields, sep=guess.separator,
        field_name="timepoint", sort_key=sort_key)
    return {
        "schema": guess.schema_str,
        "separator": guess.separator,
        "schema_fields": fields,
        "well_index": index("well"),
        "channel_index": index("channel"),
        "fov_index": index("fov"),
        "tp_index": index("timepoint"),
        "fluor_tokens": [c.lower() for c in guess.channels],
        "available_timepoints": timepoints,
    }


def check_raw_compatible(info1: dict, info2: dict, *, error_cls: type[Exception]) -> None:
    """Raise *error_cls* unless both raw datasets name their files the same way."""
    diffs = [
        f"  • {key}: dataset 1 = {info1.get(key)!r}, dataset 2 = {info2.get(key)!r}"
        for key in RAW_COMPAT_KEYS
        if info1.get(key) != info2.get(key)
    ]
    if diffs:
        raise error_cls(
            "The two datasets' image filenames are not laid out the same way, "
            "so their timepoints can't be joined:\n" + "\n".join(diffs))
