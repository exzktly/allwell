"""Review's entries in the dataset's provenance log: analysis state that changed on disk.

The log itself is :mod:`util.provenance_log`. This module decides when Review
writes to it, and the answer is **at the write, from what was written** — not at
the edit, and not from the registry.

*Not at the edit*, because ``schedule_save`` debounces a burst of edits into one
write (``registry.py``): a gate slider dragged across two hundred values is one
change of state on disk, and the history should say one thing.

*Not from the registry*, because the registry is not the only writer. The
cell-gating block and the smFISH thresholds are written inline
(``domain/gating_state.py``, ``persistence/smfish.py``), a reset writes through
``persistence.save`` directly, and a legacy migration writes the consolidated
document on load. What every one of those has in common is
:func:`review.persistence._io._replace_with_text`, the one place a JSON file
under the dataset is actually replaced. That function calls :func:`before_write`
immediately before the replace and :func:`after_write` immediately after it, on
whichever thread performs the write — the background writer for a debounced
autosave, the caller otherwise.

*From what was written*: ``before_write`` reads the file as it stands a moment
before the replace, and ``after_write`` parses the text that replaced it. The
difference between the two, per analysis block, is what is recorded. So a write
that failed records nothing, a write that left a block byte-for-byte as it was
records nothing, and a block the pipeline changed between Review's load and
Review's next save is not attributed to Review — the "before" is read at the
write, not remembered from the load.

What is recorded per block is a digest of its before and after content
(:func:`util.provenance_log.block_digest`), never the content. The history says
*that* the gates moved and when; ``pipeline_info.json`` and
``persistence.json`` say where they are now.

Recording never fails a write. A failure here is logged at WARNING and the save
it rode on proceeds exactly as it would have without it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.safe import swallow
from util.provenance_log import append_event, block_digest

_logger = logging.getLogger("review")

#: Where each piece of analysis state is stored, as ``(file name, key)`` →
#: provenance domain. A key of ``None`` means the whole file is the block.
#: The domain names are ``reset_scopes.ANALYSIS_SCOPES``' storage domains, so a
#: reader can map an event to the reset scope that would clear it, and
#: ``tests/test_review_provenance.py`` holds every analysis domain to a row here
#: or to :data:`RECORDED_UNDER`.
STORED_AT: dict[tuple[str, str | None], str] = {
    ("pipeline_info.json", "sample_definitions"): "sample_definitions",
    ("pipeline_info.json", "variables"): "sample_variables",
    ("pipeline_info.json", "cell_gating"): "cell_gating",
    ("pipeline_info.json", "smfish_thresholds"): "smfish",
    ("persistence.json", "plot_groups"): "plot_groups",
    ("persistence.json", "qc_params"): "qc_params",
    ("persistence.json", "crop_region"): "crop_region",
    ("persistence.json", "ratios"): "ratios",
    ("persistence.json", "export_groups"): "export_groups",
    ("persistence.json", "field_exclusions"): "field_exclusions",
    ("persistence.json", "triage_decisions"): "triage_decisions",
    ("cell_overrides.json", None): "cell_overrides",
}

#: Analysis domains with no block of their own, and the domain whose block
#: carries them. A change to one is recorded under the other.
RECORDED_UNDER: dict[str, str] = {
    # ``sample_definitions.save_notes`` writes the notes text inside the
    # ``sample_definitions`` block of pipeline_info.json.
    "notes": "sample_definitions",
}

_WATCHED_FILES = frozenset(name for name, _key in STORED_AT)


def _blocks_of(name: str, doc) -> dict[str, object]:
    """Every watched block in *doc* (a parsed file named *name*), by domain.

    A block absent from the document maps to ``None``, so absent-before and
    present-after is a recorded change.
    """
    out: dict[str, object] = {}
    for (file_name, key), domain in STORED_AT.items():
        if file_name != name:
            continue
        if key is None:
            out[domain] = doc
        else:
            out[domain] = doc.get(key) if isinstance(doc, dict) else None
    return out


def log_directory(path: Path) -> Path:
    """The provenance log's directory for a dataset file at *path*.

    ``pipeline_info.json`` names it directly. For a file in the data directory
    the sidecar may sit one level up (the Analyze layout loads ``out/`` as the
    data dir), so the same resolution the loader uses decides it.
    """
    if path.name == "pipeline_info.json":
        return path.parent
    from review.domain.viewer_state import resolve_pipeline_info_path
    return resolve_pipeline_info_path(path.parent).parent


def before_write(path: Path) -> dict[str, object] | None:
    """The watched blocks of *path* as they stand now, or ``None`` if unwatched.

    A file that does not exist yet has every block absent. A file that cannot
    be read or parsed returns ``None`` — with nothing to compare against, a
    change cannot be stated, and stating one would invent its "before".
    """
    path = Path(path)
    if path.name not in _WATCHED_FILES:
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _blocks_of(path.name, None)
    except OSError as exc:
        _logger.warning("provenance: could not read %s before writing it (%s); "
                        "this write's changes will not be recorded", path, exc)
        return None
    try:
        return _blocks_of(path.name, json.loads(raw))
    except ValueError as exc:
        _logger.warning("provenance: %s was not readable JSON before this write "
                        "(%s); its changes will not be recorded", path, exc)
        return None


def after_write(path: Path, before: dict[str, object] | None, text: str, *,
                durable: bool = True) -> int:
    """Record every watched block that *text* changed. Returns how many.

    *durable* is the write's own: a non-durable autosave records its history
    line without an fsync too (:func:`util.provenance_log.append_event`).
    """
    if before is None:
        return 0
    path = Path(path)
    recorded = 0
    with swallow(f"record the provenance of a write to {path.name}",
                 level=logging.WARNING):
        after = _blocks_of(path.name, json.loads(text))
        directory = log_directory(path)
        for domain in sorted(after):
            old = block_digest(before.get(domain))
            new = block_digest(after[domain])
            if old == new:
                continue
            append_event(directory, "analysis_changed", source="review", durable=durable,
                         detail={"domain": domain, "file": path.name,
                                 "before": old, "after": new})
            recorded += 1
    return recorded


def record_event(ctx, event: str, *, source: str, detail: dict) -> bool:
    """Append *event* to the open dataset's log. ``False`` with no dataset open.

    The log sits beside the dataset's sidecar, which for the Analyze layout is
    one level above Review's data directory — the loader's own resolution
    decides it, so every writer on the Review side lands in the same file.
    """
    data_dir = getattr(ctx.state.dataset, "data_dir", None)
    if not data_dir:
        return False
    from review.domain.viewer_state import resolve_pipeline_info_path
    directory = resolve_pipeline_info_path(Path(data_dir)).parent
    return append_event(directory, event, source=source, detail=detail)


def record_reset(ctx, scopes) -> bool:
    """Record that the named reset scopes were cleared. ``True`` when written."""
    if not scopes:
        return False
    return record_event(ctx, "reset", source="review", detail={"scopes": list(scopes)})
