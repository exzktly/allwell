"""The empty reference well that calibrates every nanowell well's confluence.

Confluence is texture against an empty nanowell (``nanowell_measure``, C3): a template of what an empty nanowell
looks like and a threshold for how much texture its bare floor shows by itself. Read from the well being measured,
both are the cells' own texture once every nanowell holds cells — and a plate is loaded before it is first imaged,
so that is every well at every timepoint. A nanowell half covered by cells read 1-3 % confluent.

So the run is told which plate well holds no cells (``--nanowell_empty_well``, ``NanowellSettings.empty_well``).
That well is measured before any other (``process_microscopy.process_well_zips`` puts it first,
:func:`reference_well_first`); at each of its timepoints its own template, carrying the threshold measured on it
(``Template.texture_cut``), is saved here with what the segmenter counted in it then (:func:`verdict`). Every other
well reads its confluence at a timepoint against the reference's template and threshold at that same timepoint
(``nanowell_run._confluence_basis``), so a change of exposure, focus or medium between timepoints is calibrated by
the reference imaged under it.

**The well the user names as empty is believed.** Its cell count is not evidence against that: in a well that holds
no cells, every object the segmenter counts is debris or floor texture it took for a cell, and on real plates that
reached 70 % or more of the empty well's nanowells — so a rule that refused a reference whose nanowells held counted
objects refused exactly the wells it was meant to accept, and left the whole plate with no confluence. The count is
kept instead as what it is, the segmenter's false-count baseline (``false_cells_per_nanowell``), and when more than
:data:`MAX_LOADED_SHARE` of the nanowells hold a counted object the reference's own timepoint says so in its notes
(:data:`LOOKS_LOADED_PREFIX`) and the run log warns, so a mistyped well stays visible without costing the plate its
confluence. Debris is filtered after the run, in QC.

A timepoint has no confluence — ``None``, never 0 — in every well when the reference has no template there, when
the reference has no such timepoint, when no reference well was named, or when this output folder holds no
reference measured under the run's settings. Each says why in the timepoint's notes. A reference saved by the
earlier count-based rule, with a timepoint marked ``loaded`` or ``unverified`` and so no template, is not measured
again by a later run: those timepoints keep no confluence, and say why, until the plate is re-run with Force.

Stored under ``<output>/nanowells/_confluence_reference/``: ``reference.json`` (the well, the measurement version,
the settings key and each timepoint's verdict) and one ``<timepoint>.npz`` per empty timepoint
(``nanowell_measure.save_template``). ``nanowells/`` is kept on the remote between batches, so a batch after the
one that measured the reference reads it there. Qt-free, like every ``pipeline`` module.

**What a well's confluence was read against is part of what makes it reusable.** The settings key names the
reference *well* only, so a well measured while the reference was missing, failed or loaded, or before the reference
was re-exported, would otherwise be kept as up to date beside a reference that now says something else (review
tracker M7). Each measured timepoint records :meth:`Reference.key_at` in its grid block (:data:`KEY_FIELD`): a digest
of the template's *content* where there was one, never of the ``.npz`` file (whose zip entries carry the time it was
written, and an incremental run of the reference rewrites every kept template), else why there was none, as a code.
A run keeps that timepoint, or skips the well, only when the key is the one the reference saved now gives.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

#: Above this share of its counted nanowells holding a counted object, the named empty well's timepoint carries a
#: note and the run log a warning (:data:`LOOKS_LOADED_PREFIX`). It is never refused for it: the user named it
#: empty, and an empty well's counted objects are debris (see the module docstring).
MAX_LOADED_SHARE = 0.05
DIR_NAME = "_confluence_reference"
#: ``empty`` is the only status :func:`verdict` writes. ``loaded`` and ``unverified`` are read from references saved
#: by the earlier count-based rule, which refused the timepoint and saved no template for it.
EMPTY, LOADED, UNVERIFIED = "empty", "loaded", "unverified"
NOTE_PREFIX = "No confluence at this timepoint"
#: The note on the reference well's own timepoint when many of its nanowells hold a counted object.
LOOKS_LOADED_PREFIX = "Named empty reference well holds counted objects"
#: The note a timepoint carries for a channel with no empty-well fluorescence background (``fluor_background``).
FLUOR_NOTE_PREFIX = "No fluorescence background from the empty well at this timepoint for"
#: The grid block field a measured timepoint records :meth:`Reference.key_at` in.
KEY_FIELD = "confluence_reference_key"
OWN_KEY = "own"


@dataclass(frozen=True)
class Reference:
    """What a well's confluence is read against, timepoint by timepoint.

    ``own``: this well is the reference and reads itself. Otherwise ``templates`` maps each timepoint at which the reference was empty to its template (carrying its
    threshold), and ``reasons`` says why any other timepoint has none; ``default`` is the reason for a timepoint the
    reference does not have. ``codes`` and ``default_code`` say the same as short words, for :meth:`key_at`."""
    own: bool = False
    templates: dict = field(default_factory=dict)
    reasons: dict = field(default_factory=dict)
    default: str = ""
    codes: dict = field(default_factory=dict)
    default_code: str = "absent"
    #: timepoint -> cells the segmenter counted per nanowell in the reference well there (:func:`verdict`): its rate
    #: of counting a cell where there is none, under the same imaging and settings as every other well.
    false_cells: dict = field(default_factory=dict)
    #: timepoint -> ``{channel: floor level}`` of the reference at each timepoint it was empty, loaded only for a run
    #: whose fluorescence is measured above it (``NanowellSettings.fluor_background == "empty_well"``).
    fluor_levels: dict = field(default_factory=dict)

    def fluor_at(self, timepoint) -> tuple:
        """``({channel: level}, why there is none)`` at *timepoint*: the empty reference's floor level per channel,
        and, when it has none at all there, the same reason confluence gives."""
        levels = self.fluor_levels.get(timepoint)
        if levels:
            return dict(levels), ""
        return {}, self.at(timepoint)[1] if self.templates.get(timepoint) is None else ""

    def at(self, timepoint) -> tuple:
        """``(template or None, why there is none)`` at *timepoint*."""
        template = self.templates.get(timepoint)
        if template is not None:
            return template, ""
        return None, self.reasons.get(timepoint, self.default)

    def key_at(self, timepoint) -> str:
        """What a well's confluence at *timepoint* is read against, as a short string a later run compares with the
        one the well recorded: :data:`OWN_KEY` for the reference itself, the template's content digest
        (:func:`template_key`), or ``none:<why>`` when there is no template there (``unnamed``, ``unmeasured``,
        ``other_settings``, ``absent``, or the timepoint's verdict). Codes rather than the reasons' sentences, so
        rewording a reason re-runs nothing."""
        if self.own:
            return OWN_KEY
        template = self.templates.get(timepoint)
        if template is None:
            return f"none:{self.codes.get(timepoint, self.default_code)}"
        levels = self.fluor_levels.get(timepoint)
        # A well's fluorescence measured above the reference's floor is stale when that floor moves, even if the
        # template did not: a re-exported reference with a new fluorescence channel exposure, say.
        return template_key(template) + (f"|fluor:{sorted(levels.items())!r}" if levels else "")


def template_key(template) -> str:
    """A digest of what a template *is* — its pixels, its threshold and how it was taken — so the same template read
    back from a file written at another time, or by another run, gives the same key."""
    import hashlib

    import numpy as np

    h = hashlib.blake2b(digest_size=8)
    h.update(np.ascontiguousarray(template.array, dtype=np.float64).tobytes())
    h.update(repr((int(template.half), str(template.source), int(template.n_sites), int(template.candidates),
                   None if template.texture_cut is None else float(template.texture_cut),
                   tuple(int(v) for v in template.used))).encode())
    return f"template:{h.hexdigest()}"


OWN = Reference(own=True)


def none_named() -> Reference:
    return Reference(default="no empty reference well was named (--nanowell_empty_well), and confluence is "
                             "calibrated against one", default_code="unnamed")


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


def _cell_count(value) -> float | None:
    """A site row's ``n_cells`` as a number, or ``None`` when it was not counted. A row the run just made holds an
    int or ``None``; a kept row read back from ``nanowells/<well>.csv`` holds text, where not counted is ``''``."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        count = float(value)
    except (TypeError, ValueError):
        return None
    return None if count != count else count          # NaN: not counted


def verdict(rows: list) -> dict:
    """What the segmenter counted in the reference well at one timepoint, from its *rows* there
    (``nanowells/<well>.csv``). The status is always :data:`EMPTY`: the user named the well empty, and that is
    believed whatever was counted (module docstring). ``looks_loaded`` says when more than :data:`MAX_LOADED_SHARE`
    of its counted nanowells hold a counted object, for the note and the warning. The rows may be the run's own or
    kept ones read back from the CSV, so each count is read as a number."""
    counts = [_cell_count(r.get("n_cells")) for r in rows if r.get("state") in ("empty", "measured")]
    counted = [n for n in counts if n is not None]
    loaded = sum(1 for n in counted if n > 0) / len(counted) if counted else None
    # Every cell counted in a well that holds none was invented by the segmenter (or is debris it took for a cell):
    # per nanowell, that is the false-count baseline every other well's counts carry at this timepoint. Undefined
    # (None, not 0) when nothing was counted.
    false_cells = sum(counted) / len(counted) if counted else None
    return {"status": EMPTY, "loaded_share": loaded, "nanowells_counted": len(counted),
            "max_loaded_share": MAX_LOADED_SHARE, "looks_loaded": loaded is not None and loaded > MAX_LOADED_SHARE,
            "false_cells_per_nanowell": false_cells}


def looks_loaded_note(found: dict) -> str:
    """The note on the reference well's own timepoint when :func:`verdict` found it ``looks_loaded``."""
    per = found.get("false_cells_per_nanowell")
    return (f"{LOOKS_LOADED_PREFIX}: {found['loaded_share']:.0%} of its nanowells hold an object the segmenter "
            f"counted ({'?' if per is None else f'{per:.2f}'} per nanowell). It is used as the empty reference "
            "anyway, as named, so these are the segmenter's false counts (debris); if the well does hold cells, "
            "name another, or every confluence is read against cells")


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
    """Write the reference: *entries* maps each timepoint to ``(template or None, verdict)`` or ``(template or None,
    verdict, {channel: floor level})``; a template, and the floor levels, are saved only for an ``empty`` timepoint. Replaces whatever was there, the index last, so a reader never pairs a new
    index with old templates it does not name."""
    from pipeline import nanowell_measure as nm

    root = directory(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    timepoints = {}
    for k, (tp, (template, found, *rest)) in enumerate(entries.items()):
        entry = dict(found)
        if template is not None and found["status"] == EMPTY and template.texture_cut is not None:
            path = _template_path(root, k)
            nm.save_template(path, template, str(tp))
            entry["template"] = path.name
            levels = {str(c): float(v) for c, v in (rest[0] if rest else {}).items() if v is not None}
            if levels:
                entry["fluorescence_floor"] = levels
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
        return Reference(default=f"the empty reference well {well} has not been measured in this output folder",
                         default_code="unmeasured")
    if index.get("measure_version") != nm.MEASURE_VERSION or index.get("settings") != json.loads(
            json.dumps(settings.key(), default=str)):
        return Reference(default=f"the empty reference well {well} was measured under other settings; run it "
                                 "again with these", default_code="other_settings")
    templates, reasons, codes, false_cells, fluor = {}, {}, {}, {}, {}
    wants_fluor = getattr(settings, "fluor_background", None) == "empty_well"
    for tp, found in index.get("timepoints", {}).items():
        loaded = nm.load_template(directory(output_dir) / found["template"]) if found.get("template") else None
        if loaded is not None and found["status"] == EMPTY:
            templates[tp] = loaded[0]
            if found.get("false_cells_per_nanowell") is not None:
                false_cells[tp] = float(found["false_cells_per_nanowell"])
            if wants_fluor and found.get("fluorescence_floor"):
                fluor[tp] = {str(c): float(v) for c, v in found["fluorescence_floor"].items()}
        else:
            reasons[tp] = reason(well, tp, found)
            codes[tp] = found["status"] if found.get("status") != EMPTY else "untemplated"
    return Reference(templates=templates, reasons=reasons, codes=codes, false_cells=false_cells, fluor_levels=fluor,
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
    (``confluence_reference``), a note where many of its nanowells hold counted objects (:func:`looks_loaded_note`),
    and at a timepoint whose verdict is not ``empty`` (only a kept one the earlier rule judged), its own confluence
    undefined with the reason in the block's notes. Idempotent, so kept rows and blocks may pass through it again."""
    for tp, block in blocks.items():
        if not isinstance(block, dict):
            continue
        found = verdicts.get(tp)
        block["confluence_reference"] = {"well": well, **(found or {"status": None})}
        if "notes" in block:
            block["notes"] = [n for n in block["notes"]
                              if not str(n).startswith((NOTE_PREFIX, LOOKS_LOADED_PREFIX))]
        if found is not None and found.get("looks_loaded"):
            block.setdefault("notes", []).append(looks_loaded_note(found))
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
