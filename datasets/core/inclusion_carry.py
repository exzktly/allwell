"""Carry Review's inclusion settings (``persistence.json``) into a dataset a tool writes.

Which cells an analysis counts is decided by layers Review keeps beside the CSVs: manual curation in
``cell_overrides.json`` (carried by each tool's own override carry) and three sections of ``persistence.json`` —

* ``qc_params``: the QC size and shape gate, the flagged-field and soft-cell exclusions (``QC_Pass`` and friends);
* ``crop_region``: the crop rectangle and the frames it is scoped to (``Crop_Pass``);
* ``field_exclusions``: fields excluded by hand from QC triage (``Excluded_Field_Pass``).

A tool that dropped the file would hand back a dataset in which the debris the user gated out, the cells outside
the crop and the fields excluded by hand all count again, with every n, mean and fraction changed and nothing to
say so. So Subset, Merge Plates and Concatenate carry these sections, under the names each gives the wells and
timepoints it carries.

Two sources can disagree: the QC limits and the crop are one setting per dataset, so the destination takes the
first source's and says, by name, which other source's differed — a merged plate cannot hold both, and silently
picking one would be the very substitution this module exists to prevent. Field exclusions are per field, so
every source's are kept, renamed. Anything left behind is counted in the emitted line. Qt-free.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from util.json_write import write_json_atomic

PERSISTENCE_FILENAME = "persistence.json"
#: A standalone QC file older datasets carry; Review absorbs it into ``persistence.json`` on first load.
LEGACY_QC_FILENAME = "qc_params.json"
#: The sections that are one setting per dataset, and how a line names each.
SINGLE_SECTIONS = (("qc_params", "QC limits"), ("crop_region", "crop region"))
FIELD_SECTION = "field_exclusions"


@dataclass(frozen=True)
class InclusionSource:
    """One source dataset's output folder, what a message calls it, and how its wells and timepoints are named in
    the destination (*well* / *timepoint* return ``None`` for one the tool did not carry)."""
    out_dir: Path
    label: str = "the source dataset"
    well: Callable[[str], str | None] = lambda w: w
    timepoint: Callable[[str], str | None] = lambda t: t


def _read_doc(out_dir: Path, emit: Callable[[str], None], label: str) -> dict:
    """The source's ``persistence.json`` as a dict (``{}`` when it has none), with a legacy ``qc_params.json``
    standing in for an absent ``qc_params`` section. An unreadable file is said out loud, not skipped quietly."""
    doc: dict = {}
    path = out_dir / PERSISTENCE_FILENAME
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            emit(f"Warning: {label}'s {PERSISTENCE_FILENAME} could not be read ({exc}); its QC limits, crop "
                 "region and fields excluded by hand are not carried.")
            return {}
        doc = loaded if isinstance(loaded, dict) else {}
    legacy = out_dir / LEGACY_QC_FILENAME
    if "qc_params" not in doc and legacy.is_file():
        try:
            doc["qc_params"] = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            emit(f"Warning: {label}'s {LEGACY_QC_FILENAME} could not be read ({exc}); its QC limits are not "
                 "carried.")
    return doc


def carry_inclusion(sources, dest_out_dir: Path, emit: Callable[[str], None]) -> dict:
    """Write the inclusion sections of every source into *dest_out_dir*'s ``persistence.json``; returns what was
    carried (``{section: value}``). Sections none of the sources holds are left as the destination has them, so an
    in-place Concatenate whose second dataset set nothing keeps the first's untouched; every other section of an
    existing destination file is kept."""
    docs = [(source, _read_doc(Path(source.out_dir), emit, source.label)) for source in sources]
    carried: dict = {}
    said: list[str] = []
    for section, name in SINGLE_SECTIONS:
        held = [(source.label, doc[section]) for source, doc in docs if doc.get(section) is not None]
        if not held:
            continue
        carried[section] = held[0][1]
        said.append(f"the {name}")
        differing = [label for label, value in held[1:] if value != held[0][1]]
        if differing:
            emit(f"Warning: {', '.join(differing)} had different {name} from {held[0][0]}; the new dataset uses "
                 f"{held[0][0]}'s. Check them on Home ▸ Cell Gating before comparing the plates.")
    records, dropped = _renamed_fields(docs)
    if records or any(FIELD_SECTION in doc for _source, doc in docs):
        carried[FIELD_SECTION] = records
        said.append(f"{len(records)} field(s) excluded by hand")
    if dropped:
        emit(f"{dropped} field exclusion(s) were of wells or timepoints this dataset does not keep, and were left "
             "behind.")
    if not carried:
        return carried
    dest = Path(dest_out_dir) / PERSISTENCE_FILENAME
    existing: dict = {}
    if dest.is_file():
        try:
            loaded = json.loads(dest.read_text(encoding="utf-8"))
            existing = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError) as exc:
            emit(f"Warning: the destination's {PERSISTENCE_FILENAME} could not be read ({exc}); it is replaced.")
    write_json_atomic(dest, {**existing, **carried})
    emit("Carried " + ", ".join(said) + f" ({PERSISTENCE_FILENAME}), so the same cells count in the new dataset.")
    return carried


def _renamed_fields(docs) -> tuple[list[dict], int]:
    """Every source's field exclusions under the destination's names, and how many could not be carried."""
    out: dict[tuple[str, str, str], dict] = {}
    dropped = 0
    for source, doc in docs:
        for record in doc.get(FIELD_SECTION) or []:
            if not isinstance(record, dict) or not record.get("well"):
                continue
            well = source.well(str(record.get("well") or ""))
            timepoint = source.timepoint(str(record.get("timepoint") or ""))
            if well is None or timepoint is None:
                dropped += 1
                continue
            renamed = {**record, "well": well, "timepoint": timepoint}
            out[(well, str(record.get("fov") or ""), timepoint)] = renamed
    return list(out.values()), dropped
