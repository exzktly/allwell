"""Guardrail: a tab that has adopted the shared control band never goes back.

The Review pane's defining problem was that every tab invented its own control
layout — Scatter stacked four rows including one of permanently-greyed controls,
Heat Map used three plus a separator, Statistics replaced the row with a
five-row form. Tabs are being moved onto ``widgets/control_band.ControlBand``
one at a time, and the failure mode is not a bad band; it is a later change
quietly reintroducing a bespoke row beside it.

This is an AST scan, deliberately, so it runs in the **fast lane**. Anything
that builds a real tab needs the ``viewer_app`` fixture, which ``conftest.py``
auto-marks ``slow``, and ``scripts/test.sh`` runs ``-m "not slow"`` — a test
that constructs a tab to check its layout would never run in CI at all.

``ADOPTED`` is a ratchet: modules are added as they adopt the band and never
removed. ``test_adoption_list_only_grows`` fails if an entry goes stale, which
is what would happen if a tab were reverted to its own row.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Modules that build their tab's controls through the shared band. Add a module
#: here in the same commit that adopts the band in it; never remove one.
#: The three ways a module gets its control row. A page that owns the row builds
#: it — directly as a ``ControlBand``, or through ``build_page_body``, which lays
#: the page out as band-then-content and returns the band it made; a leaf sharing
#: a page's row claims a slot in it. Each is adoption — building two of them, in
#: any combination, is the stacking of control rows the band exists to remove.
_BAND_CALLS = ("ControlBand", "build_page_body", "plotting_band_slot")

ADOPTED = (
    "analyze_tab_log.py",                       # Analyze — the output pane
    "datasets_tab.py",                          # Datasets — the tool pages
    "well_viewer/core/centre_view.py",          # Plotting — all five sub-tabs
    "well_viewer/features/barplot/tab_view.py",
    "well_viewer/features/distribution/tab_view.py",
    "well_viewer/features/heatmap/tab_view.py",
    "well_viewer/features/lineplot/tab_view.py",
    "well_viewer/features/multivariate/tab_view.py",
    "well_viewer/features/scatter/agg_tab_view.py",
    "well_viewer/features/scatter/cells_tab_view.py",
    "well_viewer/features/batch_export/tab_view.py",
    "well_viewer/features/grouping/replicate_panel_view.py",  # Sample Definitions
    "well_viewer/features/image_browser/cell_browser/tab_view.py",
    "well_viewer/features/image_browser/playground/tab_view.py",
    "well_viewer/features/preview/panel_view.py",          # Segmentation
    "well_viewer/features/review_csv/tab_view.py",
    "well_viewer/features/saved_plots/tab_view.py",
    "well_viewer/features/smfish/tab_view.py",
    "well_viewer/features/spatial/tab_view.py",
    "well_viewer/features/stats/tab_view.py",
)


def _tree(rel: str) -> ast.AST:
    return ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))


def _calls(tree: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def _call_names(tree: ast.AST) -> list[str]:
    """Every call's callee spelled as written — ``Foo`` or ``mod.Foo``."""
    out = []
    for call in _calls(tree):
        fn = call.func
        if isinstance(fn, ast.Name):
            out.append(fn.id)
        elif isinstance(fn, ast.Attribute):
            out.append(fn.attr)
    return out


def test_adopted_modules_build_exactly_one_band():
    """One control row per module — a second one is a bespoke row wearing the name."""
    for rel in ADOPTED:
        names = _call_names(_tree(rel))
        count = sum(names.count(fn) for fn in _BAND_CALLS)
        assert count == 1, (
            f"{rel} builds a control row {count} times "
            f"({' / '.join(_BAND_CALLS)}); a tab has one control band, and a "
            "second row of controls beside it is the layout drift the band "
            "exists to stop."
        )


def test_adopted_modules_have_no_bespoke_control_row():
    """No hand-rolled flow row left over beside the band.

    ``FlowLayout`` is how the pre-band control rows wrapped, and it is what the
    band uses internally. A direct construction in an adopted module means a
    second control row was built by hand rather than through ``add_scope``.
    """
    for rel in ADOPTED:
        names = _call_names(_tree(rel))
        assert "FlowLayout" not in names, (
            f"{rel} builds a FlowLayout directly. The band owns the wrapping "
            "control row — add controls with ControlBand.add_scope() instead."
        )


def test_adopted_modules_set_no_plot_card_headers():
    """The plot-card header is created and then hidden in eight tabs. A module
    on the band has no reason to build one."""
    for rel in ADOPTED:
        for call in _calls(_tree(rel)):
            fn = call.func
            if isinstance(fn, ast.Attribute) and fn.attr == "setHeaderVisible":
                arg = call.args[0] if call.args else None
                is_false = isinstance(arg, ast.Constant) and arg.value is False
                assert not is_false, (
                    f"{rel}:{call.lineno} builds a plot-card header only to hide "
                    "it; drop the header instead."
                )


def test_adoption_list_only_grows():
    """Every ADOPTED entry still exists and still uses the band.

    Without this the ratchet is decorative: a tab could be reverted to its own
    control row and the tests above would pass vacuously on a module that no
    longer mentions ControlBand.
    """
    for rel in ADOPTED:
        path = REPO_ROOT / rel
        assert path.is_file(), (
            f"{rel} is in ADOPTED but does not exist. If the module moved, "
            "update the entry; do not delete it."
        )
        names = _call_names(_tree(rel))
        assert any(fn in names for fn in _BAND_CALLS), (
            f"{rel} is listed as having adopted the shared control band but "
            f"calls none of {' / '.join(_BAND_CALLS)}."
        )


#: The Plotting page's five sub-tabs share the page's one band. Each claims its
#: own slot in it; a leaf that constructed a band instead would put a second
#: control row directly under the page's.
PLOTTING_LEAVES = (
    "well_viewer/features/barplot/tab_view.py",
    "well_viewer/features/distribution/tab_view.py",
    "well_viewer/features/heatmap/tab_view.py",
    "well_viewer/features/lineplot/tab_view.py",
    "well_viewer/features/scatter/agg_tab_view.py",
    "well_viewer/features/scatter/cells_tab_view.py",
)


def test_plotting_leaves_share_the_page_band():
    """No Plotting sub-tab builds a band of its own.

    This is the specific regression: the page band carries what every sub-tab
    scopes by (channel, property, threshold) and each sub-tab band carried the
    rest, so Bar Plots showed two full-width control rows stacked above the
    plot instead of one.
    """
    for rel in PLOTTING_LEAVES:
        names = _call_names(_tree(rel))
        assert "ControlBand" not in names, (
            f"{rel} constructs its own ControlBand. Plotting's sub-tabs share "
            "the page's band — claim a slot with plotting_band_slot(ctx, …) so "
            "the controls land in the one row."
        )
        assert "plotting_band_slot" in names, (
            f"{rel} is a Plotting sub-tab but never claims a slot in the page "
            "band, so its controls have nowhere to go."
        )


# ── the page tab bars live in the shell band, not in a tab body ─────────────
#: Modules that build a top-level page's tab strip. Each must hand it to the
#: shell band rather than adding it to its own body.
PAGE_TAB_OWNERS = (
    "well_viewer/core/centre_view.py",                       # Plotting
    "well_viewer/features/image_browser/tab_view.py",        # Image Browser
    "well_viewer/features/grouping/replicate_panel_view.py",  # Sample Definitions
    "well_viewer/features/batch_export/tab_view.py",          # Batch Export
)

_HOIST_CALLS = ("register_page_tabs", "mirror_tab_widget")


def test_page_tab_bars_are_mounted_in_the_shell_band():
    """The mode selector and the page tabs share one row across the top.

    A tab strip added to its own tab's body instead puts a second strip of
    chrome directly under the first, which is the stacking this replaced. Both
    hoist calls take the bar to ``app_shell_view``'s band; a module that builds
    a page tab strip must use one of them.
    """
    for rel in PAGE_TAB_OWNERS:
        names = _call_names(_tree(rel))
        assert any(fn in names for fn in _HOIST_CALLS), (
            f"{rel} builds a page tab strip but calls neither "
            f"{' nor '.join(_HOIST_CALLS)}. The strip belongs in the shell's "
            "mode band beside the Review / Analyze / Datasets selector, not in "
            "this tab's body."
        )
