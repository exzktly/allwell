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
from util.schema_infer import infer_from_dir
from util.timepoint import sort_key

#: The sidecar keys two raw datasets must agree on. A subset of
#: ``COMPAT_KEYS``: the rest (nuclear token, segmentation method) are
#: properties of an analysis, and nothing has been analysed.
RAW_COMPAT_KEYS = ("separator", "schema_fields")


def is_raw_dataset(root: Path | str) -> bool:
    """``in/`` present, and no analysis output beside it.

    An empty or missing ``out/`` is raw; an ``out/`` holding anything is an
    analysed dataset (or a broken one, which the analysed path reports in its
    own words rather than this one quietly accepting it as raw).
    """
    root = Path(root)
    if not (root / "in").is_dir():
        return False
    out = root / "out"
    if not out.exists():
        return True
    try:
        return out.is_dir() and not any(out.iterdir())
    except OSError:
        return False


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
