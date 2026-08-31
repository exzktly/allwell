"""Named reset scopes — the one map from what a user names to the state it owns.

The app used to carry three overlapping "start over" commands (a title-bar
trash-can, a title-bar circular arrow, and an x on the definitions band),
each written against the *files* it happened to know about. That is the wrong
axis: a user reaches for a reset thinking "drop my gating", not "delete
persistence.json". Two of the three drifted from their own dialog text as a
result — one promised to clear manual cell curation that lives in a file it
never touched, and one promised to clear plot groups that a second copy in
``pipeline_info.json`` restored on the reload immediately afterwards.

So state is modelled in two tiers and every reset is expressed in them:

**View** — how the data currently looks: selected wells, fold-change scopes,
per-tab controls, plot preferences, draw order, heatmap arrangement, the Image
Table grid. Cheap to rebuild, carries no scientific decision.

**Analysis** — decisions the user made about the science: sample variables,
sample definitions, gating, curation, ratio metrics, smFISH thresholds, Batch
Export groups. Each is
one :data:`ANALYSIS_SCOPES` entry, individually selectable.

A scope names every storage *domain* it owns, and its ``clear`` is responsible
for all of them — live state and every file the value is written to, whether
that is ``persistence.json``, ``pipeline_info.json``, or a standalone sidecar.
``tests/test_reset_scopes.py`` asserts that the scopes partition the full domain
set exactly: a new persistence domain that belongs to nobody fails the suite, so
a reset command can no longer quietly stop covering something.

The dataset's own files are a third tier that no reset touches.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from well_viewer.core.safe import swallow as _swallow, swallowed
from well_viewer.core.ui_helpers import clear_notes_editors as _clear_notes_editors

from well_viewer.domain.sample_variables import VariableSet




def _refresh_variables(ctx) -> None:
    """Repaint the Variables tab and the band's Group by control, if built."""
    with _swallow("refresh the Variables tab after a reset"):
        ctx.features.call("variables.refresh", ctx)
    with _swallow("refresh the Group by control after a reset"):
        from well_viewer.core.group_by_controller import refresh_group_by_combo
        refresh_group_by_combo(ctx)

_logger = logging.getLogger("well_viewer")


@dataclass(frozen=True)
class ResetScope:
    """One user-facing reset unit.

    ``key``      stable identifier (used by the dialog and by tests).
    ``title``    checkbox label.
    ``detail``   what the user loses, in their words — shown under the title.
    ``domains``  every persistence domain this scope is responsible for.
    ``clear``    ``clear(ctx) -> None``; resets live state *and* writes the
                 cleared value to every store the domain lives in.
    """

    key: str
    title: str
    detail: str
    domains: tuple[str, ...]
    clear: Callable


# Domains that are not in the persistence registry because they live in the
# pipeline sidecar rather than persistence.json. They are still user state a
# reset has to account for, so they are named here and covered by the same
# partition guard.
SIDECAR_DOMAINS: tuple[str, ...] = ("cell_gating", "smfish")


def _save(ctx, domain: str) -> None:
    """Write *domain* to disk now (not debounced).

    A reset must land before anything reloads, so it cannot use the debounced
    ``schedule_save`` path: a reload racing a pending timer would read the
    pre-reset value back and the reset would appear to have been ignored.
    """
    pers = getattr(ctx, "persistence", None)
    if pers is None:
        return
    try:
        pers.save(domain)
    except Exception:
        _logger.exception("reset: saving cleared %r failed", domain)


def _refresh(ctx, attr: str) -> None:
    """Call a shell refresh hook by name if the shell exposes it."""
    _notify(getattr(ctx.window, attr, None), attr)


def _notify(cb, what: str) -> None:
    """Fire a feature-stamped refresh callback, if one was stamped.

    Features stamp these on ``ctx.runtime.<feature>`` when they build (the same
    idiom as ``qc.changed_cb`` / ``scatter.gates_changed_cb``), which is how a
    reset refreshes a feature's widgets without this module importing it — and
    why ``persistence/`` can own the scope table at all. An unstamped callback
    means the feature was never built, so there are no widgets to refresh; the
    state it would have shown was already cleared above.
    """
    if not callable(cb):
        return
    try:
        cb()
    except Exception:
        _logger.exception("reset: refreshing %s failed", what)


# ── analysis scopes ───────────────────────────────────────────────────────


def _clear_sample_definitions(ctx) -> None:
    """Well labels, groups, notes, and plot groups.

    Plot groups have two homes — the ``plot_groups`` section of
    ``persistence.json`` and a copy inside ``pipeline_info.json``'s
    ``sample_definitions`` block — and the loader treats an absent section as
    "nothing to say", deliberately, so a folder shared without the sidecar keeps
    its groups. That makes clearing only one of the two a no-op in practice, so
    both are written here.

    The sample-definitions block goes out through
    ``sample_definitions.autosave_forgetting_absent_labels`` rather than the
    registry's ordinary ``sample_definitions`` save: that one preserves the
    labels of wells the current load does not hold, by reading them back out of
    the file. Correct for an edit, wrong for a reset — the user asked for every
    label to go, and on a subset load the ordinary path would restore most of
    them.
    """
    sel = ctx.state.selection
    ctx.state.dataset.well_labels.clear()
    sel.notes_text = ""
    sel.selections = []
    sel.current_selection_id = None
    sel.plot_groups = []

    _clear_notes_editors(ctx)

    _save(ctx, "plot_groups")
    from well_viewer.persistence import sample_definitions as _sd
    try:
        _sd.autosave_forgetting_absent_labels(ctx)
    except Exception:
        _logger.exception("reset: clearing the sample_definitions block failed")

    _refresh(ctx, "_label_panel_refresh")
    _refresh(ctx, "_groups_centre_refresh")
    _refresh(ctx, "_plot_groups_refresh")


def _clear_gating(ctx) -> None:
    """FluorGating floors, ON cutoffs, drawn scatter gates, and the QC debris gate.

    QC resets to ``None`` rather than to blank thresholds: the accessor re-seeds
    it from the run's own ``pipeline_info`` settings on the next read, so the
    gate returns to what the pipeline actually used instead of to "no gate at
    all", which is not a state this dataset was ever in.
    """
    from well_viewer.persistence import _doc
    from well_viewer.persistence import cell_gating as _cg

    _cg.clear_from_pipeline_info(ctx)

    ctx.state.qc.params = None
    try:
        _doc.set_section(ctx, "qc_params", None)
    except Exception:
        _logger.exception("reset: dropping the qc_params section failed")
    # Dropping the row cache is what re-runs the QC verdict: the registered cache
    # projectors re-derive ``QC_Pass`` from the re-seeded params the next time a
    # frame is fetched (``data_loading.get_rows``). Going through the projector
    # registry rather than calling the QC feature keeps this module free of
    # feature imports, and re-projects Crop in the same pass.
    ctx.state.dataset.cache.clear()
    _notify(ctx.runtime.qc.changed_cb, "QC listeners")
    _notify(ctx.runtime.qc.refresh_widgets_cb, "the QC gate row")


def clear_curation(ctx) -> None:
    """The crop region and every manual include/exclude on individual cells.

    Manual curation lives in its own ``cell_overrides.json`` — not in
    ``persistence.json`` — which is why deleting that one file never cleared it.

    The Segmentation tab's own "Reset crop + curation" runs this same function
    (``crop.controller.reset_crop_and_overrides_now`` wraps it with the mode exit
    and redraw its menu needs), so the two entry points cannot drift into
    clearing different things.
    """
    ctx.runtime.review.included_overrides.clear()
    ctx.state.dataset.crop_region = None
    # The frames the crop was armed for go with it. Leaving them would silently
    # narrow the *next* crop the user draws to a scope they reset away.
    ctx.state.dataset.reset_crop_scope()
    # Clearing the override dict is not enough on its own: the cached rows still
    # carry the previously-projected Included=0, and apply_review_overrides_to_cache
    # early-returns on an empty dict (it never *un-writes* a 0). Drop the dataset
    # cache so frames reload their original Included from the source CSVs; lazy
    # _get_rows then re-applies the now-empty crop/QC layers.
    ctx.state.dataset.cache.clear()
    ctx.state.review_image.override_version += 1
    _save(ctx, "cell_overrides")
    _save(ctx, "crop_region")


def _clear_ratios(ctx) -> None:
    """User-defined ratio metrics."""
    from well_viewer.core.channel_state_controller import set_ratio_metrics
    set_ratio_metrics(ctx, [])


def _clear_smfish(ctx) -> None:
    """Saved smFISH per-channel thresholds (pipeline_info.json)."""
    from well_viewer.persistence import smfish
    smfish.clear_thresholds(ctx)


def _clear_export_groups(ctx) -> None:
    """Batch Export's per-panel export groups."""
    ctx.runtime.batch_export.export_groups_doc = {}
    _save(ctx, "export_groups")
    state = ctx.runtime.batch_export.inline_state or {}
    for panel in (state.get("panels") or {}).values():
        reload_groups = getattr(panel, "_reload_persisted_groups", None)
        if callable(reload_groups):
            try:
                reload_groups()
            except Exception:
                _logger.exception("reset: Batch Export panel refresh failed")


def _clear_sample_variables(ctx) -> None:
    """The plate's annotation axes, and every well's value for them.

    Its own scope rather than a line inside "Sample definitions", because the
    two are separately expensive to rebuild and separately likely to be wrong.
    A user who painted their replicate groups badly and wants to start those
    again must not lose a 96-well dose grid in the same click — filling that
    grid is the longer job of the two, and nothing about redoing groups implies
    the doses were wrong.

    Writes the emptied block out, rather than only clearing memory: the block
    lives in ``pipeline_info.json`` and a reset that left it there would come
    back on the next load, which is the shape of "the reset did not take".
    """
    ctx.state.selection.variables = VariableSet()
    # And the one-step undo behind them. Left in place it would offer to put
    # back exactly what the user asked to be rid of, one click after the reset
    # and under a button that says nothing about which plate it is restoring.
    ctx.state.selection.variables_undo = None
    # The grouping goes with them. Left set, the band still offers a variable
    # that no longer exists and ``plot_replicate_sets`` has silently fallen back
    # to the user's groups — the combo-and-plot disagreement
    # ``refresh_group_by_combo``'s own docstring says cannot happen.
    # The grouping and both decorations go with them. Cleared through the
    # controller's own roster rather than by naming the two fields here: a
    # fourth control added there and not here would survive a reset pointing at
    # a variable that no longer exists, which is the same disagreement in a
    # place nobody would think to look.
    with _swallow("clear the Group by variable"):
        from well_viewer.core.group_by_controller import DECORATION_CONTROLS
        ctx.state.plot.group_by_variable = ""
        for field, _widget, _label in DECORATION_CONTROLS:
            setattr(ctx.state.plot, field, "")
    _save(ctx, "sample_variables")
    # Every sibling scope notifies its own surface; without this the Variables
    # tab stays painted with the plate it just cleared.
    _refresh_variables(ctx)


ANALYSIS_SCOPES: tuple[ResetScope, ...] = (
    ResetScope(
        key="sample_variables",
        title="Sample variables",
        detail="The variables declared for this plate — cell line, dose — and "
               "every well's value for them.",
        domains=("sample_variables",),
        clear=_clear_sample_variables,
    ),
    ResetScope(
        key="sample_definitions",
        title="Sample definitions",
        detail="Well labels, groups, notes, and saved selections.",
        domains=("sample_definitions", "notes", "plot_groups"),
        clear=_clear_sample_definitions,
    ),
    ResetScope(
        key="gating",
        title="Gating",
        detail="FluorGating floors, ThreshFracOn cutoffs, drawn scatter gates, "
               "and the QC debris gate (back to the run's own settings).",
        domains=("cell_gating", "qc_params"),
        clear=_clear_gating,
    ),
    ResetScope(
        key="curation",
        title="Curation",
        detail="The crop region and every manual include/exclude on individual cells.",
        domains=("crop_region", "cell_overrides"),
        clear=clear_curation,
    ),
    ResetScope(
        key="ratios",
        title="Ratio metrics",
        detail="Every user-defined channel ratio.",
        domains=("ratios",),
        clear=_clear_ratios,
    ),
    ResetScope(
        key="smfish",
        title="smFISH thresholds",
        detail="The saved per-channel smFISH thresholds.",
        domains=("smfish",),
        clear=_clear_smfish,
    ),
    ResetScope(
        key="export_groups",
        title="Batch Export groups",
        detail="The export groups defined on each Batch Export panel.",
        domains=("export_groups",),
        clear=_clear_export_groups,
    ),
)


# ── the view tier ─────────────────────────────────────────────────────────

VIEW_DOMAINS: tuple[str, ...] = (
    "view_state", "line_order", "heatmap_layouts", "multivariate",
)


def reset_view(ctx) -> None:
    """Return the whole view tier to how a freshly-opened dataset looks.

    Covers everything ``view_state`` persists (see its ``reset``), plus the
    heatmap arrangement, the Multivariate tab's configuration, and the Image
    Table grid. Nothing here is a scientific decision, so no scope selection is
    offered — it is all or nothing.
    """
    from well_viewer.persistence import view_state
    view_state.reset(ctx)

    # ``view_state.reset`` clears the live draw order (it is two ``PlotState``
    # fields, so ``restore_defaults`` empties them), but the order has a second
    # home: its own ``line_order`` section of persistence.json. Leaving that
    # section behind made the reset look complete and then undo itself — the
    # next load re-read it and ``line_order._reorder_selections_by_line_order``
    # re-applied the order to ``selections``, permanently reordering them. This
    # module's promise is that what resets live is what is dropped from disk, so
    # the emptied order is written in the same pass, after ``reset`` has cleared
    # it rather than before.
    _save(ctx, "line_order")

    ctx.runtime.heatmap.layouts = []
    hm = ctx.state.feature_slice("heatmap")
    if hm is not None:
        restore_slice_defaults(hm, "heatmap")
    _save(ctx, "heatmap_layouts")

    mv = ctx.state.feature_slice("multivariate")
    if mv is not None:
        restore_slice_defaults(mv, "multivariate")
        _save(ctx, "multivariate")

    _notify(ctx.runtime.image_table.reset_cb, "the Image Table grid")


def restore_slice_defaults(slice_obj, name: str) -> None:
    """Best-effort ``restore_defaults`` for a feature slice.

    A slice that cannot be default-constructed is logged and left alone rather
    than half-reset — a partially-reset slice is the silent-degradation failure
    this whole module exists to remove.

    Public because the same question — "put this slice back the way a fresh
    dataset finds it" — is asked by the hydrators as well as by a reset: a
    plate with no saved section for a feature must land on that feature's
    defaults, not on the previous plate's configuration.
    """
    from well_viewer.domain.state_defaults import restore_defaults
    try:
        restore_defaults(slice_obj)
    except Exception:
        _logger.exception("reset: could not restore %s defaults", name)


# ── running a reset ───────────────────────────────────────────────────────


def scope_by_key(key: str) -> ResetScope | None:
    """The analysis scope named *key*, or ``None``."""
    for scope in ANALYSIS_SCOPES:
        if scope.key == key:
            return scope
    return None


def clear_analysis(ctx, keys) -> list[str]:
    """Run the analysis scopes named in *keys*; returns the keys that ran.

    Each scope is isolated: one that raises is logged and the rest still run, so
    a failure in (say) the smFISH sidecar cannot silently abandon the gating
    reset the user also asked for. The returned list is what actually completed,
    which is what the caller should report — not what was requested.
    """
    done: list[str] = []
    for key in keys:
        scope = scope_by_key(key)
        if scope is None:
            _logger.warning("reset: unknown scope %r ignored", key)
            continue
        try:
            scope.clear(ctx)
        except Exception:
            _logger.exception("reset: clearing %r failed", key)
            continue
        done.append(key)
    if done:
        try:
            from well_viewer.core.selection_controller import invalidate_stats_cache
            invalidate_stats_cache(ctx)
        except Exception:
            swallowed()  # cache invalidation is best-effort; the reload re-reads
    return done


def all_domains() -> frozenset[str]:
    """Every storage domain claimed by a scope (analysis + view)."""
    claimed: set[str] = set(VIEW_DOMAINS)
    for scope in ANALYSIS_SCOPES:
        claimed.update(scope.domains)
    return frozenset(claimed)
