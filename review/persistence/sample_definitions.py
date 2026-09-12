"""Sample-definitions persistence (in ``pipeline_info.json``).

Reads and writes the ``sample_definitions`` block — well labels, the unified
selections model, notes, and a copy of the plot groups. Clearing them is a reset
scope rather than a function here; see ``persistence/reset_scopes.py``.
"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import QMessageBox

from review.core.ui_helpers import (
    commit_focused_notes as _commit_focused_notes,
)

_logger = logging.getLogger("review")


def save_notes(ctx) -> None:
    app = ctx.window  # transitional bridge (B4)
    """Debounced-autosave entry for the Notes sub-tab.

    Registered as the ``notes`` persistence domain. Tolerates a missing
    ``pipeline_info.json`` — the Notes text stays in memory
    (``app._app_state.selection.notes_text``) until a dataset that has one is loaded.
    """
    try:
        save_to_pipeline_info(app)
    except FileNotFoundError:
        pass
    except Exception:
        _logger.exception("Notes auto-save failed")


def _labels_to_write(ds, *, keep_absent: bool) -> dict[str, str]:
    """The ``well_labels`` map to persist: live labels, plus — when
    *keep_absent* — the saved labels of wells this load does not hold.

    ``load_from_pipeline_info`` hydrates a label only for a token in
    ``dataset.well_paths``, which is right (a label for a well that is not
    here has nothing to attach to), but the block written back was rebuilt
    from that pruned map alone. Opening one plate's ``out/`` folder, or any
    other load that sees a subset of the wells, and then touching a single
    label rewrote the file without every other well's — the labels were gone
    from the only place they existed. ``view_state`` solved the same problem
    for selected wells with its ``absent_wells`` stash; here the file itself
    is the stash, so the merge is a read of what is already on disk rather
    than session state that has to survive a dataset swap.

    The rule the merge encodes: a token this dataset **holds** takes the live
    value, including its absence — clearing a label has to be able to delete
    it. A token it does not hold keeps whatever the file says.

    *keep_absent* is ``False`` for exactly one caller, the "Sample definitions"
    reset scope, which is the user asking for every label to go and would
    otherwise get back every label the current load cannot see.
    """
    live = {
        str(tok): str(lab)
        for tok, lab in (ds.well_labels or {}).items()
        if str(lab).strip()
    }
    if not keep_absent or not ds.data_dir:
        return live
    from review.domain.sample_definitions import read_sample_definitions
    stored = (read_sample_definitions(ds.data_dir) or {}).get("well_labels")
    if not isinstance(stored, dict):
        return live
    loaded = set(ds.well_paths or {})
    merged: dict[str, str] = {}
    for tok, lab in stored.items():
        token = str(tok)
        if token in loaded:
            continue  # the live map is authoritative for a well that is here
        text = str(lab).strip()
        if text:
            merged[token] = text
    if merged:
        _logger.debug(
            "sample_definitions: preserving %d label(s) for well(s) outside "
            "this load: %s", len(merged), ", ".join(sorted(merged)),
        )
    merged.update(live)
    return merged


def autosave_to_pipeline_info(ctx, *, keep_absent_labels: bool = True) -> None:
    app = ctx.window  # transitional bridge (B4)
    """Debounced-autosave variant of :func:`save_to_pipeline_info`.

    Writes the sample-definitions block silently — no dialogs, no status toast —
    and no-ops when there is no data directory or v2 writes are disabled, so it
    is safe to fire on every edit through the persistence registry (matching how
    notes / ratios / view-state already autosave). The explicit Save button
    keeps using the loud :func:`save_to_pipeline_info`.

    *keep_absent_labels* merges the saved labels of wells this load does not
    hold back into the block (see :func:`_labels_to_write`). Only the reset
    scope passes ``False``, through :func:`autosave_forgetting_absent_labels`.
    """
    from review.domain.sample_definitions import (
        build_sample_definitions_v2,
        save_to_pipeline_info as _write_block,
    )
    ds = app._app_state.dataset
    if not ds.data_dir or app._app_state.selection.v2_writes_disabled:
        return
    # Fold an in-progress edit in first, then read the slice — the slice is the
    # single truth, and with two notes editors on screen a direct widget read
    # here has no tiebreak. See ui_helpers.commit_focused_notes.
    _commit_focused_notes(ctx)
    notes_text = app._app_state.selection.notes_text or ""
    selections = list(app._app_state.selection.selections or [])
    current_id = app._app_state.selection.current_selection_id
    plot_groups = list(app._app_state.selection.plot_groups or [])
    try:
        block = build_sample_definitions_v2(
            _labels_to_write(ds, keep_absent=keep_absent_labels),
            selections, current_id, notes=notes_text,
            plot_groups=plot_groups,
        )
        # ``deferrable``: the Sample Definitions tab autosaves on every
        # keystroke pause, through the same seven-round-trip write that froze
        # the Variables tab. This arm catches and logs every failure anyway, so
        # deferring loses no report — the background writer makes the same one,
        # and also puts it on the status line.
        _write_block(ds.data_dir, block, deferrable=True)
    except Exception:
        # Autosave is best-effort and runs off a debounce timer with no user
        # dialog, so any failure (disk error *or* a malformed-selection build
        # error) must be logged rather than propagated or swallowed — else the
        # user's sample definitions silently fail to persist (audit E16).
        _logger.exception("sample_definitions autosave failed")


def autosave_forgetting_absent_labels(ctx) -> None:
    """Write the sample-definitions block with **no** merge of absent labels.

    The "Sample definitions" reset scope's promise is that every well label
    goes. Going through the ordinary autosave would read the labels back out of
    the file it is about to overwrite and restore each one belonging to a well
    the current load does not contain — a reset that reports success and leaves
    most of the plate labelled.
    """
    autosave_to_pipeline_info(ctx, keep_absent_labels=False)


def save_to_pipeline_info(app) -> None:
    """Merge well labels + the unified ``selections`` model into pipeline_info.json."""
    from review.domain.sample_definitions import (
        build_sample_definitions_v2,
        save_to_pipeline_info as _write_block,
    )
    if not app._app_state.dataset.data_dir:
        QMessageBox.warning(
            app, "No data loaded",
            "Open a data folder before saving sample definitions.",
        )
        return
    if app._app_state.selection.v2_writes_disabled:
        QMessageBox.warning(
            app, "Save unavailable",
            "Saved sample definitions couldn't be upgraded earlier this session, "
            "so writing them is disabled to avoid corrupting the file. See the log.",
        )
        return
    _commit_focused_notes(app._ctx)
    notes_text = app._app_state.selection.notes_text or ""
    selections = list(app._app_state.selection.selections or [])
    current_id = app._app_state.selection.current_selection_id
    plot_groups = list(app._app_state.selection.plot_groups or [])
    block = build_sample_definitions_v2(
        _labels_to_write(app._app_state.dataset, keep_absent=True),
        selections, current_id, notes=notes_text, plot_groups=plot_groups,
    )
    try:
        info_path = _write_block(app._app_state.dataset.data_dir, block)
    except FileNotFoundError as exc:
        QMessageBox.warning(app, "pipeline_info.json missing", str(exc))
        return
    except OSError as exc:
        QMessageBox.critical(app, "Save failed", str(exc))
        return
    n_hidden = sum(1 for s in selections if s.get("hidden"))
    app._set_status(
        f"Sample definitions saved to {info_path.name}: "
        f"{len(block['well_labels'])} label(s), "
        f"{len(selections)} selection(s), "
        f"{len(plot_groups)} saved selection(s)"
        + (f" ({n_hidden} hidden)" if n_hidden else "")
        + " (schema v2)."
    )


def _apply_unified_state(ctx, block, *, set_notes: bool = True) -> tuple[bool, dict]:
    """Parse a sample_definitions/bar_groups block → set ``ctx.state.selection.selections``
    (the in-memory source of truth). v1 blocks are migrated by ``from_block``.

    ctx-native: reads the typed ``ctx.state`` slices, reports through the
    ``ctx.set_status`` service accessor, and pushes the loaded Notes into every
    registered editor through ``mirror_notes`` — it names no god-object, and no
    single widget key, so an editor that has not been built yet (or has been
    profiled away) costs nothing.

    Returns ``(applied, well_labels_dict)``. On a malformed block / migration
    failure: logs, leaves the existing state alone, disables v2 writes for the
    session, and returns ``(False, {})``.
    """
    from domain import selections_format as _fmt
    from review.domain import selections_model as _sel
    sel_state = ctx.state.selection
    tok_to_label = ctx.state.dataset.tok_to_label
    # Mixed v1+v2 payload (both rep_sets/groups *and* a selections list
    # with content) — refuse to migrate. Either path would silently drop
    # data, so we keep the file untouched and disable v2 writes for the
    # session.
    if _sel.block_is_mixed(block):
        _logger.warning(
            "sample_definitions block has both v1 (rep_sets/groups) and v2 "
            "(selections) keys populated; refusing to migrate to avoid data "
            "loss. v2 writes disabled for this session."
        )
        sel_state.v2_writes_disabled = True
        ctx.notify.set_status(
            "Saved sample definitions are in a mixed v1/v2 shape — using "
            "compatibility mode. Edit and Save to clean up (see log)."
        )
        return False, {}
    # Detect a legacy v1 block so we can surface a one-time migration notice
    # after the upgrade succeeds. v2 blocks carry schema_version ≥ 2.
    was_v1 = False
    try:
        if isinstance(block, dict):
            was_v1 = int(block.get("schema_version", 1)) < 2
    except Exception:
        was_v1 = False
    try:
        # Don't honour line order here — line_order.json loads *after* this; its
        # loader re-applies the reorder.
        selections, current_id, well_labels, notes, plot_groups = _fmt.from_block(
            block, tok_to_label=tok_to_label)
    except Exception:  # pragma: no cover - defensive: never crash on bad data
        _logger.exception("Couldn't upgrade saved sample definitions — using "
                          "compatibility mode; the file was left untouched.")
        sel_state.v2_writes_disabled = True
        ctx.notify.set_status("Couldn't upgrade saved sample definitions — using "
                       "them in compatibility mode (see log).")
        return False, {}

    sel_state.selections = selections
    sel_state.current_selection_id = current_id
    sel_state.plot_groups = plot_groups
    if was_v1:
        # Surface the migration so users know an upgrade happened; the new
        # state will be persisted with a backup the next time they Save. Routed
        # through the status-bar service accessor (was a transient toast popup,
        # which needed the god-object handle — the status line already served as
        # the code's own fallback, so this one-time migration notice keeps
        # surfacing without naming the app).
        ctx.notify.set_status(
            f"Migrated legacy v1 sample-definitions to v2 "
            f"({len(selections)} selection(s)). Save to persist."
        )
    if set_notes:
        sel_state.notes_text = notes
        # No source editor — the text came off disk, so every box on screen is
        # a destination.
        from review.core.ui_helpers import mirror_notes
        mirror_notes(ctx, source_key="")
    return True, well_labels


def load_from_pipeline_info(ctx) -> bool:
    """Apply any saved sample_definitions block in pipeline_info.json.

    v1 blocks are migrated to the unified ``selections`` model on the fly (the
    migrated state is persisted, with a one-time backup, on the next Save).
    Returns True when a block was found and applied.
    """
    from review.domain.sample_definitions import read_sample_definitions
    if not ctx.state.dataset.data_dir:
        return False
    block = read_sample_definitions(ctx.state.dataset.data_dir)
    if not block:
        return False
    applied, well_labels = _apply_unified_state(ctx, block)
    if not applied:
        return True  # a block existed; we just couldn't upgrade it
    valid = set(ctx.state.dataset.well_paths.keys())
    for tok, lab in (well_labels or {}).items():
        if tok in valid and str(lab).strip():
            ctx.state.dataset.well_labels[str(tok)] = str(lab).strip()
    from review.core.selection_controller import invalidate_stats_cache
    invalidate_stats_cache(ctx)
    return True
