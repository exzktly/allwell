"""Named Analyze presets: the form's settings, saved under a name and loaded back.

A preset answers "how is a plate of this kind analysed?" — the filename schema,
the channels, the segmentation method and its parameters, the corrections, the
QC gates, the measurements, the compute settings. It is the run options
``AnalyzeTabFormMixin._collect_run_options`` gathers, with the options that
describe *one particular run* taken out (:data:`PER_RUN_KEYS`), so loading a
preset onto a form that already names a folder never repoints it, never narrows
the next run to an old subset of wells, and never re-arms a safety override.

The presets live in one file in the app's folder, ``analyze_presets.json``
(``util.state_dir.ANALYZE_PRESETS_NAME``)::

    {"version": 1,
     "presets": {"<name>": {"saved_at": "<ISO time>", "opts": {...}}}}

``opts`` is stored in the form's own spelling — the same raw text the form
collects — so it is applied through the same hydrator the Job Queue's
*Edit…* uses (``analyze.domain.job_edit.AnalyzeJobEditMixin.hydrate_options``),
which ``tests/test_analyze_job_edit.py`` holds to be an exact inverse of the
collector. A key a preset does not carry (one saved by an older version, before
an option existed) leaves that field as it is; a key the form no longer reads is
ignored.

**An unreadable file is reported, never treated as empty.** :func:`load_presets`
raises :class:`PresetFileError` for a file that exists and cannot be parsed, and
the writers refuse to write over one: treating it as "no presets" and saving the
next one would silently replace every preset the user had with that single one.

Qt-free: the Qt half is ``analyze/presets.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from util.json_write import write_json_atomic
from util.state_dir import ANALYZE_PRESETS_NAME, app_file

#: The file format's version, written into every file this module saves.
FORMAT_VERSION = 1

#: The options a preset does not carry, each because it is a decision about one
#: run of one dataset rather than about how a kind of plate is analysed. Loading
#: a preset leaves every one of these fields exactly as it was.
PER_RUN_KEYS: dict[str, str] = {
    "raw": "the input folder: the dataset a run is about, never part of how it is analysed",
    "scratch_dir": "a path on this machine's disks, chosen for the space a run needs",
    "only_wells": "a subset chosen for one run; restoring it would quietly narrow the next run",
    "skip_wells": "the same, for the wells left out",
    "force_wells": "the wells to redo in one dataset that already has output",
    "force": "what to do with this dataset's existing output (one choice of three, with incremental)",
    "incremental": "the same choice as force",
    "allow_low_disk": "disarms the free-space check for one run on one disk; never re-armed from a record",
    "nanowell_registration_from": "another dataset's output folder, to reuse its registration",
}


class PresetFileError(Exception):
    """The presets file exists and cannot be read as presets."""


def presets_path() -> Path:
    """Where the presets are kept: ``analyze_presets.json`` in the app's folder."""
    return app_file(ANALYZE_PRESETS_NAME)


def clean_name(name: str) -> str:
    """*name* with surrounding whitespace removed and runs of whitespace collapsed."""
    return " ".join(str(name or "").split())


def preset_options(opts: dict[str, Any]) -> dict[str, Any]:
    """The part of a collected run-options dict a preset keeps, made JSON-safe.

    Drops :data:`PER_RUN_KEYS`; turns paths into strings and tuples into lists,
    the two non-JSON types the collector can produce.
    """
    out: dict[str, Any] = {}
    for key, value in opts.items():
        if key in PER_RUN_KEYS:
            continue
        if isinstance(value, Path):
            value = str(value)
        elif isinstance(value, tuple):
            value = list(value)
        out[key] = value
    return out


def load_presets(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Every saved preset, by name. An absent file is no presets.

    Raises :class:`PresetFileError` when the file exists and is not a presets
    file: unreadable, not JSON, or not shaped as one. A single malformed entry
    (no ``opts`` object) is skipped rather than failing the whole file.
    """
    path = path or presets_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise PresetFileError(f"{path} could not be read: {exc}") from exc
    presets = data.get("presets") if isinstance(data, dict) else None
    if not isinstance(presets, dict):
        raise PresetFileError(f"{path} is not an Analyze presets file.")
    return {str(name): entry for name, entry in presets.items()
            if isinstance(entry, dict) and isinstance(entry.get("opts"), dict)}


def sorted_names(presets: dict[str, Any]) -> list[str]:
    """The preset names in the order a menu lists them: alphabetical, ignoring case."""
    return sorted(presets, key=lambda n: (n.casefold(), n))


def _write(presets: dict[str, dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, {"version": FORMAT_VERSION, "presets": presets})


def save_preset(name: str, opts: dict[str, Any], path: Path | None = None) -> str:
    """Save *opts* (a collected run-options dict) under *name*, replacing any preset of that name.

    Returns the name as stored (:func:`clean_name`). Raises ``ValueError`` for a
    blank name, :class:`PresetFileError` when the existing file cannot be read
    (it is left untouched), and ``OSError`` when the file cannot be written.
    """
    name = clean_name(name)
    if not name:
        raise ValueError("A preset needs a name.")
    path = path or presets_path()
    presets = load_presets(path)
    presets[name] = {"saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "opts": preset_options(opts)}
    _write(presets, path)
    return name


def delete_preset(name: str, path: Path | None = None) -> bool:
    """Remove the preset called *name*. Returns whether there was one to remove."""
    path = path or presets_path()
    presets = load_presets(path)
    if name not in presets:
        return False
    del presets[name]
    _write(presets, path)
    return True
