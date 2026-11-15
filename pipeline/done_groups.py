"""Which of a well's FOV-groups were processed to the end, recorded beside its output.

A well's measurements CSV says which ``(fov, timepoint)`` groups produced
cells. It cannot say which groups *finished*: a field that legitimately holds
no cells writes no row, so read from the CSV alone it is indistinguishable from
a field that was never processed. Counted that way a well with one empty field
covered fewer groups than its input holds, was reported INCOMPLETE, was never
sealed, and was run again by every plain re-run without ever changing.

The well's output images cannot say it either. They are written before a
group is quantified, so a group whose quantification raised leaves its images
in ``<well>_out.zip`` and no rows in the CSV. Counting image members as *done*
made a later ``--incremental`` run skip exactly that group, for good.

So the well task writes this record: one small JSON file per well,
``<output_dir>/.aw_done/<well>.json``, written atomically after the well's CSV
is on disk and never appended to (the shape of ``pipeline/settings_record.py``
and ``pipeline/run_report.py``, for their reason — the worker pool records
without a lock). It lists every group that returned from the per-group
pipeline, and which of those returned no cells::

    {"well": "A01", "format": 1, "digest": "<settings digest>",
     "groups":   [["f1", "t0"], ["f2", "t0"], ["f3", "t0"]],
     "no_cells": [["f3", "t0"]]}

**Two rules, and which applies is decided by whether this file is readable.**

* *With a record* — a well written by a build that records — a group is done
  when it is in the CSV **or** in the record's ``groups``. Output images count
  for nothing.
* *Without one* — every well written before the record existed, and a well
  whose record could not be written or brought back — the earlier rule stands
  unchanged: the CSV's keys, unioned by the incremental planner and Validate
  with the output's image members. Such a well is **not** read as having no
  finished groups; it is read exactly as it always was.

The record carries the settings digest it was written under, and a reader
making a *reuse* decision (the incremental done-set) passes its own settings
key: a record made under other settings is then no record. A reader that only
describes what is on disk (Validate, the end-of-run check) passes ``None``.

Stdlib-only: the remote client reads it without the imaging stack.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from util.aw_safe import swallow
from util.json_write import write_json_atomic
from util.settings_digest import primary_settings_key, settings_key_aliases

#: Beside ``.aw_settings/`` and ``.aw_complete/``, dot-prefixed for their reason: every dataset scan skips it.
DONE_DIRNAME = ".aw_done"

#: The payload's shape. A record of another format is no record: the well falls back to the earlier rule.
DONE_FORMAT = 1

Key = tuple[str, str]


@dataclass(frozen=True)
class DoneRecord:
    """One well's record: every group processed to the end, and those among them that held no cells."""

    groups: frozenset[Key]
    no_cells: frozenset[Key]
    digest: str = ""


def done_record_path(output_dir: Path | str, well_label: str) -> Path:
    """Where *well_label*'s record lives: ``<output_dir>/.aw_done/<well>.json``."""
    return Path(output_dir) / DONE_DIRNAME / f"{well_label}.json"


def _keys(value) -> frozenset[Key] | None:
    """A JSON list of ``[fov, timepoint]`` pairs as a set of keys, or ``None`` when it is anything else."""
    if not isinstance(value, list):
        return None
    out = set()
    for pair in value:
        if not (isinstance(pair, list) and len(pair) == 2):
            return None
        out.add((str(pair[0]), str(pair[1])))
    return frozenset(out)


def _made_under(digest: str, settings_key: str | None) -> bool:
    """Whether a record written under *digest* may answer a reader holding *settings_key*.

    ``None`` is a reader that reuses nothing — it describes the output on disk — and takes any record. Otherwise
    the record's digest must be one the key accepts (``util.settings_digest.settings_key_aliases``), and a record
    with no digest answers only a reader with no key. Unlike the per-well settings marker there is nothing to
    grandfather: no well written before this record existed carries one, so an unknown digest is never an old one.
    """
    if settings_key is None:
        return True
    accepted = settings_key_aliases(settings_key)
    return digest in accepted if (digest or accepted) else True


def parse_done_record(text: str, settings_key: str | None = None) -> DoneRecord | None:
    """The record in *text*, or ``None`` when it is not one a reader holding *settings_key* may use."""
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("format") != DONE_FORMAT:
        return None
    groups, no_cells = _keys(payload.get("groups")), _keys(payload.get("no_cells"))
    if groups is None or no_cells is None:
        return None
    digest = str(payload.get("digest") or "")
    if not _made_under(digest, settings_key):
        return None
    return DoneRecord(groups=groups | no_cells, no_cells=no_cells, digest=digest)


def read_done_record(output_dir: Path | str, well_label: str,
                     settings_key: str | None = None) -> DoneRecord | None:
    """*well_label*'s record in *output_dir*, or ``None``: absent, unreadable, of another format, or — for a reader
    that passes its *settings_key* — made under other settings. ``None`` always means "decide by the earlier rule",
    never "nothing is done"."""
    from util.well_token import canonical_well_label

    label = str(well_label)
    for spelling in dict.fromkeys((label, canonical_well_label(label) or label)):
        try:
            text = done_record_path(output_dir, spelling).read_text(encoding="utf-8")
        except OSError:
            continue
        return parse_done_record(text, settings_key)
    return None


def recorded_done_keys(output_dir: Path | str, well_label: str,
                       settings_key: str | None = None) -> frozenset[Key] | None:
    """The groups *well_label*'s record says were processed to the end, or ``None`` when there is no usable record."""
    record = read_done_record(output_dir, well_label, settings_key)
    return None if record is None else record.groups


def withdraw_done_record(output_dir: Path | str, well_label: str) -> None:
    """Remove *well_label*'s record before its output is rewritten from scratch.

    A whole-well run replaces the CSV and the archive, so the earlier record describes output that is about to
    stop existing; left in place past a run that died mid-rewrite it would vouch for groups the new output may not
    hold. Raises ``OSError`` when a record is there and cannot be removed: the well then fails before anything is
    rewritten, as it does when its completion seal cannot be withdrawn.
    """
    done_record_path(output_dir, well_label).unlink(missing_ok=True)


def _write(path: Path, well_label: str, groups, no_cells, digest: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, {
        "well": str(well_label), "format": DONE_FORMAT, "digest": digest,
        "groups": sorted(list(k) for k in groups),
        "no_cells": sorted(list(k) for k in no_cells),
    })


def record_processed_groups(output_dir: Path | str, well_label: str, processed, *, schema, sep: str,
                            settings_key: str = "", incremental: bool = False,
                            csv_keys=frozenset()) -> DoneRecord | None:
    """Write *well_label*'s record after a run and return it; ``None`` when it could not be written.

    *processed* is ``(nuclear file name, cell count)`` for each group this run took to the end — processed now or
    taken back from a resume checkpoint, never one that failed, was skipped by an abort, or was not reached before
    a suspend. Its keys are parsed with the positional rule every other key reader uses
    (:func:`pipeline.incremental_keys.fov_tp_from_name`).

    An *incremental* run adds to the record already here, when that one was made under these settings; a
    whole-well run, or an incremental one under other settings (which recomputes every group), starts a new one.
    A group counts as holding no cells only while *csv_keys* — the keys the well's CSV carries now — does not
    hold it.

    Best-effort, like the other bookkeeping: a record that cannot be written leaves none (a whole-well run
    withdrew the old one first), and the well is then read by the earlier rule.
    """
    from pipeline.incremental_keys import fov_tp_from_name

    digest = primary_settings_key(settings_key)
    prior = read_done_record(output_dir, well_label, settings_key) if incremental else None
    groups = set(prior.groups) if prior else set()
    no_cells = set(prior.no_cells) if prior else set()
    for name, n_cells in processed:
        key = fov_tp_from_name(str(name), schema=schema, sep=sep)
        groups.add(key)
        (no_cells.discard if n_cells else no_cells.add)(key)
    no_cells -= set(csv_keys)
    record = None
    with swallow(f"recording the finished FOV-groups of {well_label}"):
        _write(done_record_path(output_dir, well_label), well_label, groups, no_cells, digest)
        record = DoneRecord(groups=frozenset(groups), no_cells=frozenset(no_cells), digest=digest)
    if record is None:
        with swallow(f"removing {well_label}'s earlier record of finished FOV-groups"):
            withdraw_done_record(output_dir, well_label)
    return record


def merge_fetched_done_record(fetched: Path, dest: Path, *, incremental: bool) -> bool:
    """Install the record a remote run brought back (*fetched*, a staged file) as *dest*; whether one is there after.

    A remote incremental run is sent only the fields this machine does not hold, so the record it writes names
    only those. Put over the local one it would drop every earlier field that held no cells, and each such field
    would be sent and computed again by the next run — which would then drop the others. So an *incremental* fetch
    unions the two when both were made under the same settings, and otherwise the fetched record stands alone. A
    fetched file that is not a record is discarded and *dest* left as it was.
    """
    try:
        new = parse_done_record(fetched.read_text(encoding="utf-8"))
    except OSError:
        new = None
    if new is None:
        return dest.exists()
    groups, no_cells = set(new.groups), set(new.no_cells)
    if incremental:
        try:
            old = parse_done_record(dest.read_text(encoding="utf-8"), new.digest)
        except OSError:
            old = None
        if old is not None:
            no_cells = (no_cells | old.no_cells) - (new.groups - new.no_cells)
            groups |= old.groups
    _write(dest, dest.stem, groups, no_cells, new.digest)
    return True
