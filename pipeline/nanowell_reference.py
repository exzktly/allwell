"""The empty reference well that calibrates every nanowell well's confluence.

Confluence is texture against an empty nanowell (``nanowell_measure``, C3): a template of what an empty nanowell
looks like and a threshold for how much texture its bare floor shows by itself. Read from the well being measured,
both are the cells' own texture once every nanowell holds cells — and a plate is loaded before it is first imaged,
so that is every well at every timepoint. A nanowell half covered by cells read 1-3 % confluent.

So the run is told which plate well holds no cells (``--nanowell_empty_well``, ``NanowellSettings.empty_well``).
That well is measured before any other (``process_microscopy.process_well_zips`` puts it first,
:func:`reference_well_first`); at each of its timepoints its own template, carrying the threshold measured on it
(``Template.texture_cut``), is saved here with a verdict on whether the well really was empty then
(:func:`verdict`). Every other well reads its confluence at a timepoint against the reference's template and
threshold at that same timepoint (``nanowell_run._confluence_basis``), so a change of exposure, focus or medium
between timepoints is calibrated by the reference imaged under it.

A timepoint has no confluence — ``None``, never 0 — in every well when the reference is not ``empty`` there, when
the reference has no such timepoint, when no reference well was named, or when this output folder holds no
reference measured under the run's settings. Each says why in the timepoint's notes.

Stored under ``<output>/nanowells/_confluence_reference/``: ``reference.json`` (the well, the measurement version,
the settings key and each timepoint's verdict) and one ``<timepoint>.npz`` per empty timepoint
(``nanowell_measure.save_template``). ``nanowells/`` is kept on the remote between batches, so a batch after the
one that measured the reference reads it there. Qt-free, like every ``pipeline`` module.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

#: The reference well is empty at a timepoint when at most this share of its counted nanowells holds a cell: a few
#: nanowells with debris the segmenter took for a cell do not disqualify it, and a loaded well is far above it.
MAX_LOADED_SHARE = 0.05
DIR_NAME = "_confluence_reference"
EMPTY, LOADED, UNVERIFIED = "empty", "loaded", "unverified"
NOTE_PREFIX = "No confluence at this timepoint"


@dataclass(frozen=True)
class Reference:
    """What a well's confluence is read against, timepoint by timepoint.

    ``own``: this well is the reference and reads itself. Otherwise ``templates`` maps each timepoint at which the
    reference was empty to its template (carrying its threshold), and ``reasons`` says why any other timepoint has
    none; ``default`` is the reason for a timepoint the reference does not have."""
    own: bool = False
    templates: dict = field(default_factory=dict)
    reasons: dict = field(default_factory=dict)
    default: str = ""
    #: timepoint -> cells the segmenter counted per nanowell in the reference well there (:func:`verdict`): its rate
    #: of counting a cell where there is none, under the same imaging and settings as every other well.
    false_cells: dict = field(default_factory=dict)

    def at(self, timepoint) -> tuple:
        """``(template or None, why there is none)`` at *timepoint*."""
        template = self.templates.get(timepoint)
        if template is not None:
            return template, ""
        return None, self.reasons.get(timepoint, self.default)


OWN = Reference(own=True)


def none_named() -> Reference:
    return Reference(default="no empty reference well was named (--nanowell_empty_well), and confluence is "
                             "calibrated against one")


def reference_well(settings) -> str | None:
    """The canonical label of the run's empty reference well, or ``None``."""
    from util.well_token import canonical_well_label

    name = getattr(settings, "empty_well", None)
    return (canonical_well_label(name) or str(name).strip().upper()) if name else None


def same_well(a, b) -> bool:
    from util.well_token import canonical_well_label

    return bool(a and b) and (canonical_well_label(a) or str(a).upper()) == (canonical_well_label(b) or str(b).upper())


def reference_well_first(settings, items: list) -> tuple[list, list]:
    """``(the reference well's item, every other item)`` from *items* (``(well label, path)`` pairs), so it is
    measured before the wells read against it; ``([], items)`` when none is named or it is not among them."""
    well = reference_well(settings)
    first = [item for item in items if same_well(item[0], well)]
    return first, [item for item in items if item not in first]


def verdict(rows: list) -> dict:
    """Whether the reference well was empty at one timepoint, from its *rows* there (``nanowells/<well>.csv``)."""
    counted = [r for r in rows if r.get("state") in ("empty", "measured") and r.get("n_cells") is not None]
    loaded = sum(1 for r in counted if r["n_cells"] > 0) / len(counted) if counted else None
    status = UNVERIFIED if loaded is None else LOADED if loaded > MAX_LOADED_SHARE else EMPTY
    # Every cell counted in a well that holds none was invented by the segmenter (or is debris it took for a cell):
    # per nanowell, that is the false-count baseline every other well's counts carry at this timepoint. Undefined
    # (None, not 0) when nothing was counted.
    false_cells = sum(r["n_cells"] for r in counted) / len(counted) if counted else None
    return {"status": status, "loaded_share": loaded, "nanowells_counted": len(counted),
            "max_loaded_share": MAX_LOADED_SHARE, "false_cells_per_nanowell": false_cells}


def reason(well: str, timepoint, found: dict | None) -> str:
    """Why *timepoint* has no confluence, given the reference's verdict *found* there (``None``: it has none)."""
    if found is None:
        return f"the empty reference well {well} has no timepoint {timepoint}"
    if found["status"] == LOADED:
        return (f"the empty reference well {well} is not empty there: {found['loaded_share']:.0%} of its nanowells "
                "hold a counted cell")
    if found["status"] == UNVERIFIED:
        return f"the empty reference well {well} had no cells counted there, so it cannot be shown to be empty"
    return f"the empty reference well {well} has no template there"


def directory(output_dir) -> Path:
    from pipeline.nanowell_task import NANOWELL_DIR

    return Path(output_dir) / NANOWELL_DIR / DIR_NAME


def _template_path(root: Path, index: int) -> Path:
    return root / f"tp{index:04d}.npz"


def save(output_dir, well: str, settings, entries: dict) -> None:
    """Write the reference: *entries* maps each timepoint to ``(template or None, verdict)``; a template is saved
    only for an ``empty`` timepoint. Replaces whatever was there, the index last, so a reader never pairs a new
    index with old templates it does not name."""
    from pipeline import nanowell_measure as nm

    root = directory(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    timepoints = {}
    for k, (tp, (template, found)) in enumerate(entries.items()):
        entry = dict(found)
        if template is not None and found["status"] == EMPTY and template.texture_cut is not None:
            path = _template_path(root, k)
            nm.save_template(path, template, str(tp))
            entry["template"] = path.name
        timepoints[str(tp)] = entry
    index = {"well": well, "measure_version": nm.MEASURE_VERSION, "settings": settings.key(),
             "timepoints": timepoints}
    tmp = root / "reference.json.tmp"
    tmp.write_text(json.dumps(index, indent=1, default=str))
    os.replace(tmp, root / "reference.json")


def read_index(output_dir) -> dict | None:
    path = directory(output_dir) / "reference.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def load(output_dir, settings) -> Reference:
    """The reference a well other than the reference itself reads its confluence against, from *output_dir*.

    Only a reference measured under these settings (``NanowellSettings.key``, which names the reference well) and
    this measurement version counts: one from another configuration describes another measurement."""
    from pipeline import nanowell_measure as nm

    well = reference_well(settings)
    if well is None:
        return none_named()
    index = read_index(output_dir)
    if index is None or not same_well(index.get("well"), well):
        return Reference(default=f"the empty reference well {well} has not been measured in this output folder")
    if index.get("measure_version") != nm.MEASURE_VERSION or index.get("settings") != json.loads(
            json.dumps(settings.key(), default=str)):
        return Reference(default=f"the empty reference well {well} was measured under other settings; run it "
                                 "again with these")
    templates, reasons, false_cells = {}, {}, {}
    for tp, found in index.get("timepoints", {}).items():
        loaded = nm.load_template(directory(output_dir) / found["template"]) if found.get("template") else None
        if loaded is not None and found["status"] == EMPTY:
            templates[tp] = loaded[0]
            if found.get("false_cells_per_nanowell") is not None:
                false_cells[tp] = float(found["false_cells_per_nanowell"])
        else:
            reasons[tp] = reason(well, tp, found)
    return Reference(templates=templates, reasons=reasons, false_cells=false_cells,
                     default=f"the empty reference well {well} has no such timepoint")


def kept_templates(output_dir, well: str) -> dict:
    """The reference's saved templates by timepoint, for an incremental run of the reference well itself, whose kept
    timepoints are not measured again and so bring no template of their own."""
    from pipeline import nanowell_measure as nm

    index = read_index(output_dir)
    if index is None or not same_well(index.get("well"), well):
        return {}
    out = {}
    for tp, found in index.get("timepoints", {}).items():
        loaded = nm.load_template(directory(output_dir) / found["template"]) if found.get("template") else None
        if loaded is not None:
            out[tp] = loaded[0]
    return out


def apply(sites: list, blocks: dict, well: str, verdicts: dict) -> None:
    """For the reference well itself, once its counts are known: each timepoint's verdict on its block
    (``confluence_reference``), and at a timepoint where it was not empty, its own confluence undefined with the
    reason in the block's notes. Idempotent, so kept rows and blocks may pass through it again."""
    for tp, block in blocks.items():
        if not isinstance(block, dict):
            continue
        found = verdicts.get(tp)
        block["confluence_reference"] = {"well": well, **(found or {"status": None})}
        if "notes" in block:
            block["notes"] = [n for n in block["notes"] if not str(n).startswith(NOTE_PREFIX)]
        if found is not None and found["status"] != EMPTY:
            block.setdefault("notes", []).append(f"{NOTE_PREFIX}: {reason(well, tp, found)}")
            for row in sites:
                if row.get("timepoint") == tp:
                    row["confluence"] = None


def note(well_reference: Reference, timepoint) -> str | None:
    """The note a measured timepoint carries when it has no confluence reference, or ``None``."""
    if well_reference.own:
        return None
    template, why = well_reference.at(timepoint)
    return None if template is not None else f"{NOTE_PREFIX}: {why}"
