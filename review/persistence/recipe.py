"""Read a dataset's analysis blocks from disk, and copy chosen ones into the open dataset.

The I/O half of a recipe (:mod:`review.domain.recipe_compute` is the pure half).
Qt-free, so the headless report path can read a source the same way the dialog
does.

**Reading** (:func:`read_dataset_blocks`) takes a dataset folder as a user
picks it — the folder holding ``in/`` and ``out/``, or the results folder
itself — and reads ``pipeline_info.json`` and ``persistence.json`` once each,
without loading the dataset. A file that exists and cannot be read, or a
``persistence.json`` written under a schema this build does not know, is
*refused* by name rather than read as empty: an empty source block would be
copied as "nothing", and a refused one is reported as "could not be read",
which is what is true.

**Writing** (:func:`apply_recipe`) goes into the dataset open in Review, through
the same primitives every other analysis write uses, so each copied block is
recorded in the provenance log by the route every analysis change takes:
``pipeline_info.json`` blocks through ``_io.update_json_atomic`` (it never
creates a sidecar the pipeline did not write), ``persistence.json`` sections
through ``_doc.set_section`` (which refuses a foreign or unreadable document).
Every pending debounced save is flushed **first** — otherwise a timer armed by
an edit just before the copy would fire after it and write the old value back
over the copied one. The caller reloads the dataset afterwards, so live state is
re-read from what is now on disk rather than patched in memory.

A source block that is absent is not copied: it says nothing, and copying it
would erase the target's own. Saved plots are **added** beside the target's,
never in place of them, each under a fresh id, with no image — a copied record's
picture would be the source plate's data under the target's name — and marked
``copied_from`` so every surface can say it has not been drawn here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from review.domain.recipe_compute import (
    PERSISTENCE, PIPELINE_INFO, DatasetBlocks, RECIPE_SCOPES, scope_by_key,
)

_logger = logging.getLogger("review")

#: The sidecar keys whose values name the dataset's channels.
CHANNEL_KEYS = ("nuclear_token", "fluor_tokens", "smfish_tokens", "phase_token",
                "cytoplasm_token")


def data_dir_of(folder: Path | str) -> Path:
    """The results folder of a dataset as a user picks it: ``out/`` when the
    folder holds one, else the folder itself — the loader's own rule."""
    folder = Path(folder)
    out = folder / "out"
    return out if out.is_dir() else folder


def _read_json(path: Path) -> tuple[dict | None, str]:
    """``(document, refusal)``; ``(None, "")`` for a file that does not exist."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, ""
    except OSError as exc:
        return None, str(exc)
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        return None, f"not valid JSON: {exc}"
    if not isinstance(doc, dict):
        return None, "not a JSON object"
    return doc, ""


def _channels(info: dict) -> frozenset[str]:
    out = set()
    for key in CHANNEL_KEYS:
        value = info.get(key)
        if isinstance(value, str) and value:
            out.add(value)
        elif isinstance(value, (list, tuple)):
            out.update(str(v) for v in value if v)
    return frozenset(out)


def read_dataset_blocks(folder: Path | str, *, label: str = "",
                        wells=()) -> DatasetBlocks:
    """Every recipe block the dataset at *folder* holds, read from disk."""
    from review.domain.viewer_state import resolve_pipeline_info_path
    from review.persistence._doc import PERSISTENCE_FILENAME, SCHEMA_VERSION

    data_dir = data_dir_of(folder)
    info, info_refusal = _read_json(resolve_pipeline_info_path(data_dir))
    doc, doc_refusal = _read_json(data_dir / PERSISTENCE_FILENAME)
    if doc is not None and doc.get("schema_version") not in (None, SCHEMA_VERSION):
        doc_refusal = (f"written under schema_version {doc.get('schema_version')!r}, "
                       f"which this build does not read")
        doc = None
    refused = {}
    if info_refusal:
        refused[PIPELINE_INFO] = info_refusal
    if doc_refusal:
        refused[PERSISTENCE] = doc_refusal
    blocks = {}
    for scope in RECIPE_SCOPES:
        for file_name, key in scope.blocks:
            source = info if file_name == PIPELINE_INFO else doc
            blocks[(file_name, key)] = (source or {}).get(key)
    from review.domain.dataset_name import dataset_display_name
    return DatasetBlocks(label=label or dataset_display_name(data_dir),
                         root=str(folder), blocks=blocks,
                         channels=_channels(info or {}), wells=frozenset(wells),
                         refused=refused)


def read_open_dataset(ctx) -> DatasetBlocks:
    """The open dataset's blocks as they stand on disk, after a flush."""
    data_dir = Path(ctx.state.dataset.data_dir)
    return read_dataset_blocks(data_dir,
                               wells=ctx.state.dataset.well_paths.keys())


@dataclass
class ApplyResult:
    applied: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    plots_added: int = 0


def _copied_plots(records, existing_ids, label: str) -> list[dict]:
    taken = set(existing_ids)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out = []
    for n, rec in enumerate(records or []):
        if not isinstance(rec, dict):
            continue
        new_id = f"{stamp}-c{n}"
        while new_id in taken:
            new_id += "x"
        taken.add(new_id)
        copy = dict(rec)
        copy.update(id=new_id, png_filename="", svg_filename="",
                    frames_filename="", copied_from=label)
        out.append(copy)
    return out


def _apply_scope(ctx, scope, source: DatasetBlocks, written: list[str]) -> int:
    """Write one scope's present blocks. Returns plots added. Raises on refusal.

    Each block that lands is appended to *written* as it does.
    """
    from review.domain.viewer_state import resolve_pipeline_info_path
    from review.persistence import _doc
    from review.persistence._io import update_json_atomic

    data_dir = Path(ctx.state.dataset.data_dir)
    writes_doc = any(f == PERSISTENCE and source.blocks.get((f, k)) is not None
                     for f, k in scope.blocks)
    if writes_doc:
        # Read first: it is the read that discovers a foreign document, and
        # ``_doc.write`` declines one silently — a copy reported as done that
        # never landed is the outcome this check exists to prevent.
        _doc.read(ctx.state)
        if getattr(ctx.state.dataset, "persistence_doc_foreign", False):
            raise OSError("this dataset's persistence.json was written by a "
                          "different build, and this one will not write over it")
    added = 0
    for file_name, key in scope.blocks:
        value = source.blocks.get((file_name, key))
        if value is None:
            continue
        if file_name == PIPELINE_INFO:
            info_path = resolve_pipeline_info_path(data_dir)

            def _set(existing, _k=key, _v=value):
                existing[_k] = _v
                return existing

            if not update_json_atomic(info_path, _set):
                raise OSError(f"{info_path.name} is missing or could not be updated")
            written.append(key)
            continue
        elif key == "saved_plots":
            current = _doc.get_section(ctx, key) or []
            copies = _copied_plots(value, [r.get("id") for r in current
                                           if isinstance(r, dict)], source.label)
            _landed(_doc.set_section(ctx, key, list(current) + copies), key)
            added += len(copies)
        else:
            _landed(_doc.set_section(ctx, key, value), key)
        written.append(key)
    return added


def _landed(ok: bool, key: str) -> None:
    """Raise when a ``persistence.json`` write did not land.

    ``_doc.write`` logs a failed write and carries on — right for an autosave,
    wrong here: a copy whose write failed must not be reported as copied, in
    the status line or in the provenance log.
    """
    if not ok:
        raise OSError(f"persistence.json could not be written ({key}); see the log")


def apply_recipe(ctx, source: DatasetBlocks, keys) -> ApplyResult:
    """Copy the scopes named in *keys* from *source* into the open dataset.

    Each scope is isolated: one that fails is reported and the rest still run,
    the same rule the reset runner follows. The caller reloads the dataset.
    """
    result = ApplyResult()
    pers = getattr(ctx, "persistence", None)
    if pers is not None:
        pers.flush_all()
    for key in keys:
        scope = scope_by_key(key)
        if scope is None:
            result.failed[key] = "not a recipe scope"
            continue
        written: list[str] = []
        try:
            result.plots_added += _apply_scope(ctx, scope, source, written)
        except (OSError, ValueError, TypeError) as exc:
            _logger.warning("recipe: copying %s failed: %s", key, exc)
            # A scope of two blocks can fail on its second: say which landed,
            # rather than let "failed" imply this dataset was left untouched.
            result.failed[key] = (f"{exc} (already copied: {', '.join(written)})"
                                  if written else str(exc))
            continue
        result.applied.append(key)
    if result.applied or result.failed:
        from review.domain.viewer_state import resolve_pipeline_info_path
        from util.provenance_log import append_event
        directory = resolve_pipeline_info_path(Path(ctx.state.dataset.data_dir)).parent
        append_event(directory, "recipe_applied", source="review", detail={
            "source": source.label,
            "source_path": source.root,
            "scopes": list(result.applied),
            "failed": dict(result.failed),
            "plots_added": result.plots_added,
        })
    return result
