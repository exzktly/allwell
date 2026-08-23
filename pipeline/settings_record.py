"""Which configuration produced each well's output, recorded beside it.

`pipeline/run_report.py`'s shape and for its reason — one small file per well,
written atomically and never appended to, so the worker pool records without a
lock. Unlike that module's directory, this one is **not** cleared at the end of
a run: it is read by the *next* run, which is the whole point.

`util.settings_digest` decides what goes into the string; this decides where it
lives and answers "may this well be reused". The two are separate because the
pipeline and the Analyze pane both need the first, and only the pipeline writes
the second.
"""
from __future__ import annotations

import json
from pathlib import Path

from util.aw_safe import swallow
from util.json_write import write_json_atomic
from util.settings_digest import settings_match

#: Beside `.aw_run_report/` and `.aw_journal/`, dot-prefixed for the same
#: reason: every dataset scan in the app skips a name starting with a dot, so
#: this cannot be mistaken for a well.
SETTINGS_DIRNAME = ".aw_settings"


def _dir(output_dir: Path | str) -> Path:
    return Path(output_dir) / SETTINGS_DIRNAME


def record_well_settings(output_dir: Path | str, well_label: str,
                         digest: str) -> Path | None:
    """Record that *well_label*'s current output was produced under *digest*.

    Best-effort: a well whose marker cannot be written is still a well whose
    output is on disk, and the next run simply treats it as unknown — which
    grandfathers it, exactly as a well written before this existed.
    """
    if not digest:
        return None
    path = None
    with swallow(f"recording the settings digest for {well_label}"):
        d = _dir(output_dir)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{well_label}.json"
        write_json_atomic(
            path, {"well": str(well_label), "digest": str(digest)})
    return path


def recorded_well_settings(output_dir: Path | str, well_label: str) -> str:
    """The digest *well_label*'s output was produced under, or ``""``.

    ``""`` means unknown — no marker, an unreadable one, or one from before
    this record existed — and every caller must read it as "cannot tell",
    never as "matches".
    """
    out = ""
    with swallow(f"reading the settings digest for {well_label}"):
        path = _dir(output_dir) / f"{well_label}.json"
        if path.is_file():
            out = str(json.loads(path.read_text()).get("digest") or "")
    return out


def well_settings_allow_reuse(output_dir: Path | str, well_label: str,
                              current: str) -> bool:
    """Whether *well_label*'s existing output may stand for a *current* run.

    The grandfathering rule lives in :func:`util.settings_digest.settings_match`
    so that this module and any other caller cannot come to disagree about what
    an unknown digest means.
    """
    return settings_match(recorded_well_settings(output_dir, well_label),
                          current)
