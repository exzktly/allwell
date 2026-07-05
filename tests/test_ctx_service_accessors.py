"""A8 Phase 3 (design "C") — freeze the ViewerContext named-service surface.

``ViewerContext`` exposes a *minimal, closed* set of named accessors for genuine
app/window-level services (status bar, Save-As dialog, …) so feature code names
them instead of reaching through ``ctx.app``. This test freezes that set: adding
an accessor requires editing ``_ALLOWED`` here — a deliberate, reviewed act. The
list is the enumerable spec of what features legitimately need from
``WellViewerApp``; if it grows past a handful, that is the signal to decompose
the god-object itself, not to keep adding accessors (which would let unbounded
coupling hide behind named methods and defeat the ``ctx.app`` burn-down).
"""
from __future__ import annotations

import inspect

from well_viewer.viewer_context import ViewerContext

# The frozen set of public, callable service accessors on ViewerContext.
# Keep this SMALL. Each entry is a genuine WellViewerApp service with no typed
# home yet — not a convenience wrapper for arbitrary god-object access.
_ALLOWED = {
    "set_status",
    # ``toast`` names the transient notification-popup service (import/export
    # outcome banners), distinct from the status bar; a genuine window-shell
    # service with no typed-state home, like ``set_status``.
    "toast",
    "save_figure_dialog",
    "open_export_style_panel",
    # ``window`` exposes the WellViewerApp *qua QMainWindow* for the operations
    # that genuinely need the widget (dialog parenting, isinstance/findChildren)
    # and can never be god-object-free. Named so those needs don't count against
    # the ctx.app burn-down while staying enumerable + frozen here.
    "window",
    "persistence",
    # ``features`` names the feature-descriptor registry — a genuine app-level
    # catalogue (which tabs exist + their metadata) controllers read to reason
    # about tab behaviour, not arbitrary god-object access.
    "features",
    # ``rebuild_all`` / ``refresh_sidebar_map`` name the two genuine app-level
    # *orchestration* services (master redraw fan-out; debounced sidebar-map
    # repaint) that the final ctx.app seams bottomed into. Each fans out into a
    # dozen god-object methods with no ctx form, so they are named here as the
    # teardown endgame — not convenience wrappers for arbitrary access.
    "rebuild_all",
    "refresh_sidebar_map",
}


def _public_methods() -> set[str]:
    # Count both plain methods and property accessors: a property is just as much
    # a named surface onto WellViewerApp, so it must be frozen here too (else a
    # property could smuggle in unreviewed coupling the isfunction check misses).
    return {
        name
        for name, member in inspect.getmembers(ViewerContext)
        if not name.startswith("_") and (inspect.isfunction(member)
                                         or isinstance(member, property))
    }


def test_service_accessor_set_is_frozen():
    actual = _public_methods()
    added = actual - _ALLOWED
    assert not added, (
        f"New public ViewerContext method(s) {sorted(added)} — a named service "
        f"accessor is a deliberate act: add it to _ALLOWED here with review, and "
        f"only for a genuine app/window service (not arbitrary god-object access)."
    )
    removed = _ALLOWED - actual
    assert not removed, (
        f"Expected accessor(s) {sorted(removed)} missing from ViewerContext — "
        f"if intentionally removed, drop them from _ALLOWED too."
    )


def test_no_getattr_passthrough_backdoor():
    # The anti-gaming guardrail: a __getattr__ forwarder would turn the context
    # back into an unrestricted god-object alias.
    assert "__getattr__" not in ViewerContext.__dict__, (
        "ViewerContext must not define __getattr__ — named accessors only."
    )
