"""Sample-variables persistence — the ``variables`` block in ``pipeline_info.json``.

The model and every parsing rule live in
:mod:`well_viewer.domain.sample_variables`, which is Qt-free and takes no
dataset; this module is the thin layer that decides *where* the block goes and
holds the app state either side of it.

It sits beside ``sample_definitions`` in the same file rather than in a sidecar
of its own, because a variable is the same kind of fact as a well label or a
replicate set — something the user declared about *this* plate — and a second
file is a second thing to copy, a second thing to lose, and a second thing whose
absence has to mean something.

The write goes through ``persistence/_io.update_json_atomic`` rather than
``domain.sample_definitions.save_to_pipeline_info``: that function's name reads
general and it is not — it replaces the ``sample_definitions`` key with whatever
it is handed, so calling it here nested the variables block inside that key and
deleted the user's selections. What is needed is the primitive underneath it,
which re-reads the file and compares it byte-for-byte immediately before the
replace. ``pipeline_info.json`` has several writers and the pipeline itself owns
``fluor_tokens``, ``schema`` and ``available_timepoints``, rewriting them at the
end of a run — often from another process on another host, and Analyze and
Review share one window, so that run can finish between this module's read and
its write. A plain read-modify-write here reverts the pipeline's keys, and does
it silently: the symptom is a viewer that stops being able to see a channel the
run produced.
"""

from __future__ import annotations

import json
import logging

from well_viewer.core.safe import swallow
from well_viewer.domain.sample_variables import (
    VariableSet, from_block, to_block,
)

_logger = logging.getLogger("well_viewer")

#: The key under ``pipeline_info.json``. Not nested inside
#: ``sample_definitions``: that block carries its own schema version and its own
#: v1→v2 migration, and adding an unrelated shape under it would put this block
#: behind that migration's backup-and-abort path for no reason.
BLOCK_KEY = "variables"


def save_to_data_dir(ctx) -> None:
    """Write the live variable set. Silent no-op without a dataset.

    Silent, and not a dialog: this is registered as an autosaving domain, so it
    runs on every edit. ``sample_definitions``' own save warns because it is
    also reachable from an explicit Save button, which this has none of — a
    modal on a keystroke would be worse than the missed write it reports.
    """
    dataset = ctx.state.dataset
    if not dataset.data_dir:
        return
    from pathlib import Path

    from well_viewer.domain.viewer_state import resolve_pipeline_info_path
    from well_viewer.persistence._io import update_json_atomic

    vset = getattr(ctx.state.selection, "variables", None) or VariableSet()
    block = to_block(vset)

    def _mutate(existing: dict) -> dict:
        # Runs against the bytes this attempt actually read, and may run again
        # if another writer lands in between — so it must build its result from
        # *existing* rather than from anything captured earlier.
        merged = dict(existing)
        merged[BLOCK_KEY] = block
        return merged

    path = resolve_pipeline_info_path(Path(dataset.data_dir))
    with swallow("write the variables block to pipeline_info.json"):
        # ``create_missing`` and a non-durable write, matching
        # ``smfish.save_threshold``. A dataset folder the pipeline never wrote a
        # sidecar into is ordinary — an imported plate, a hand-assembled one —
        # and without this a whole filled 96-well grid was never written at all,
        # with only an INFO line recording it. ``durable=False`` because this is
        # a debounced autosave on the GUI thread, which is exactly the case
        # ``update_json_atomic``'s own docstring names for it.
        if not update_json_atomic(path, _mutate, create_missing=True,
                                  durable=False):
            # WARNING, not INFO. A user who fills a 96-well grid into a
            # read-only or network-stale folder gets no other signal, and the
            # first they learn of it is the grid being gone next session — the
            # loss is silent, unbounded and unrecoverable, which is exactly the
            # shape §7 says must reach the user rather than only a debug log.
            _logger.warning(
                "variables were NOT saved to %s — the file is missing or could "
                "not be updated. They stay in memory for this session and will "
                "be lost when it ends.", path.name)


def read_block(data_dir) -> dict | None:
    """The ``variables`` block as the sidecar on disk actually holds it.

    **Read from the file, not from ``dataset.pipeline_info``.** That attribute
    is not the sidecar's contents: ``viewer_state.read_pipeline_info`` builds a
    *whitelist* of the keys the pipeline itself owns — ``schema``,
    ``fluor_tokens``, ``available_timepoints`` and the index fields — each
    through a ``.get`` with a default, so a block it has never heard of cannot
    appear in it. ``variables`` is not in that whitelist and structurally never
    will be, so hydrating from it returned ``None`` on every load and handed
    back an empty set: every variable and every plate fill the user had made
    was silently gone the next time the dataset was opened, while the block sat
    correctly written in the file the whole time.

    This mirrors ``domain.sample_definitions.read_sample_definitions``, which
    re-reads the sidecar for exactly this reason — the sibling block in the same
    file, which is why one of the two survived a reopen and the other did not.

    ``None`` for an absent file, unreadable JSON, or no block; the caller turns
    that into an empty set rather than into "keep what is loaded".
    """
    from pathlib import Path

    from well_viewer.domain.viewer_state import resolve_pipeline_info_path

    info_path = resolve_pipeline_info_path(Path(data_dir))
    if not info_path.exists():
        return None
    try:
        data = json.loads(info_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("Could not read %s for the variables block: %s",
                        info_path, exc)
        return None
    if not isinstance(data, dict):
        return None
    block = data.get(BLOCK_KEY)
    return block if isinstance(block, dict) else None


def load_from_data_dir(ctx) -> None:
    """Hydrate the variable set from the open dataset.

    **A dataset with no block gets an empty set, not the previous plate's.**
    Returning early on a missing block is the bug ``persistence/ratios.py``
    records: the last dataset's ratios stayed live as virtual channels across a
    swap, so every plot described work done on a plate that was no longer open.
    Variables would fail the same way and worse — a plot grouped by `Dose` would
    keep drawing the old plate's doses over the new plate's numbers.
    """
    dataset = ctx.state.dataset
    if not dataset.data_dir:
        return
    ctx.state.selection.variables = from_block(read_block(dataset.data_dir))
