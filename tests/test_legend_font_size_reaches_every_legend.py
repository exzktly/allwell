"""Properties → Legend → Font size, applied to every legend on the figure.

**The defect.** The style pass reached its legend through ``ax.get_legend()``,
which returns only the legend ``ax.legend()`` built and stored as
``ax.legend_``. A ``Legend`` a caller constructs itself and hands to
``ax.add_artist`` is not that object, so the pass walked past it — and the
aggregate scatter draws *both* of its keys that way on purpose, to stop this
same pass repositioning the two of them onto one corner. The consequence
nobody wrote down beside that choice is that the rest of the pass skipped them
too: on that tab, changing the legend font size did nothing whatsoever, and
turning the legend off left both keys on screen.

A legend **title** was never sized on any tab, so a key headed ``Timepoint``
kept the size it was created at while its own entries resized underneath it.

`plot_style.legends_on` is now the single enumeration, read off
``ax.get_children()`` so a legend arriving by some third route cannot be
missed by a roster that lists two.

The assertions are on the size the text objects actually carry, against a
value that is neither the size the legend was created at nor the app default
(§7.8 #2) — a fixture at either of those cannot tell a pass that applied the
pref from one that applied nothing.
"""

from __future__ import annotations

from types import SimpleNamespace

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pytest  # noqa: E402
from matplotlib.legend import Legend  # noqa: E402

from well_viewer.core.plot_style import (  # noqa: E402
    DEFAULT_EXPORT_STYLE_PREFS,
    apply_export_style_prefs,
    legends_on,
)
from well_viewer.features.scatter._annotations import (  # noqa: E402
    draw_scatter_agg_legends,
)

#: Neither the 8 the aggregate keys are built at, nor the 12 the app defaults
#: to, nor the 9 the style pass falls back to when the key is missing.
WANTED = 21.0

#: Two wells over two timepoints — the shape that makes the aggregate draw
#: both of its keys. One of either and the key is skipped as redundant, so a
#: single-well fixture would have measured a figure with nothing on it.
AGG_DATA = {
    "a": {"group_label": "A01", "marker": "o", "timepoint": 0.0,
          "color": (1, 0, 0, 1)},
    "b": {"group_label": "B02", "marker": "s", "timepoint": 24.0,
          "color": (0, 0, 1, 1)},
}


@pytest.fixture
def fig():
    f = plt.figure()
    yield f
    plt.close(f)


def _prefs(**over) -> dict:
    p = dict(DEFAULT_EXPORT_STYLE_PREFS)
    p["legend_font_size"] = WANTED
    p.update(over)
    return p


def _agg_axes(fig):
    """An axes shaped like the aggregate scatter: a plotted trace with the
    ordinary legend on it, and the two standalone keys beside it."""
    ax = fig.add_subplot(111)
    ax.plot([0, 1], [0, 1], label="A01")
    ax.legend(fontsize=8)
    draw_scatter_agg_legends(ax, AGG_DATA, "#333333")
    return ax


# ── the enumeration ─────────────────────────────────────────────────────────

def test_every_legend_on_the_axes_is_found(fig):
    ax = _agg_axes(fig)
    found = legends_on(ax)
    assert len(found) == 3, [type(x).__name__ for x in found]
    assert ax.get_legend() in found, "the ordinary legend is not in the list"
    standalone = [leg for leg in found if leg is not ax.get_legend()]
    assert sorted(leg.get_title().get_text() for leg in standalone) == [
        "Timepoint", "Well / group"]


def test_an_axes_with_no_legend_lists_none(fig):
    ax = fig.add_subplot(111)
    ax.plot([0, 1], [0, 1])
    assert legends_on(ax) == []


# ── the font size, on all three ─────────────────────────────────────────────

def test_the_pref_reaches_the_standalone_keys(fig):
    """The reported bug. Before the fix these two stayed at 8 for ever."""
    ax = _agg_axes(fig)
    apply_export_style_prefs(fig, _prefs())
    for leg in legends_on(ax):
        assert [t.get_fontsize() for t in leg.get_texts()] == [WANTED] * len(
            leg.get_texts()), leg.get_title().get_text()


def test_the_pref_reaches_a_legend_title(fig):
    ax = _agg_axes(fig)
    apply_export_style_prefs(fig, _prefs())
    titled = [leg for leg in legends_on(ax) if leg.get_title().get_text()]
    assert len(titled) == 2
    for leg in titled:
        assert leg.get_title().get_fontsize() == WANTED


def test_an_untitled_legend_keeps_its_empty_title_untouched(fig):
    """A blank title is not text to size; giving it one would place an empty
    Text of 21pt in the legend's box and pad every ordinary legend."""
    ax = fig.add_subplot(111)
    ax.plot([0, 1], [0, 1], label="A01")
    ax.legend(fontsize=8)
    before = ax.get_legend().get_title().get_fontsize()
    apply_export_style_prefs(fig, _prefs())
    assert ax.get_legend().get_title().get_text() == ""
    assert ax.get_legend().get_title().get_fontsize() == before


# ── what the standalone keys are off ``ax.legend_`` for ─────────────────────

def test_only_the_primary_legend_is_repositioned(fig):
    """The two keys pin their own corners precisely so this pass cannot move
    them; relocating both to ``legend_loc`` drops one on top of the other.
    Asserting only that the sizes changed would pass a version that moved
    them, which is the bug the ``add_artist`` construct was chosen to avoid."""
    ax = _agg_axes(fig)
    standalone = [leg for leg in legends_on(ax) if leg is not ax.get_legend()]
    locs = [leg._loc for leg in standalone]
    apply_export_style_prefs(fig, _prefs(legend_loc="lower left"))
    assert [leg._loc for leg in standalone] == locs
    assert ax.get_legend()._loc == Legend.codes["lower left"]


# ── Show legend reaches them too ────────────────────────────────────────────

def test_hiding_the_legend_hides_every_key(fig):
    """Same control, same omission: *Show legend* off used to leave both
    aggregate keys drawn over a figure the user had asked to clear."""
    ax = _agg_axes(fig)
    apply_export_style_prefs(fig, _prefs(legend_show=False))
    assert [leg.get_visible() for leg in legends_on(ax)] == [False] * 3
    apply_export_style_prefs(fig, _prefs(legend_show=True))
    assert [leg.get_visible() for leg in legends_on(ax)] == [True] * 3


# ── through the real control (§7.6, §7.8 #3) ────────────────────────────────

def _stub_app():
    """The smallest app the Properties sidebar will build against — the same
    shape ``tests/test_properties_colour_control.py`` uses."""
    from well_viewer.core.app_runtime import AppRuntime
    from well_viewer.core.cell_gating_state import CellGatingState
    from well_viewer.core.export_prefs_state import ExportPrefsState
    from well_viewer.core.plot_state import PlotState
    from well_viewer.features.scatter.state import ScatterState

    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(data_dir=None),
        plot=PlotState(),
        selection=SimpleNamespace(selected_wells=["A01"]),
        scatter=ScatterState(),
        export_prefs=ExportPrefsState(),
        cell_gating=CellGatingState(),
    )
    app._app_state.cell_gating.scatter_gates = []
    app._ctx = SimpleNamespace(
        app=app, state=app._app_state, runtime=AppRuntime(app),
        widgets=SimpleNamespace(
            get_or_none=lambda n: getattr(app, n, None),
            register=lambda n, w: (setattr(app, n, w), w)[1]),
        notify=SimpleNamespace(set_status=lambda _m: None),
    )
    app._rep_sets_active = lambda: []
    app._parse_rc = lambda lbl: (lbl, 0, 0)
    app._well_display_label = lambda w: w
    app._replicate_display_label = lambda rs: getattr(rs, "name", "")
    app._use_fov_spread_active = lambda: False
    app._toggle_sem = lambda: None
    app._toggle_fov_replicates = lambda: None
    app._scatter_gate_draw_mode = False
    app._scatter_gate_selector = None
    app._set_status = lambda _m: None
    return app


def _legend_stepper(sidebar):
    """The Font size control inside the Legend section, found by the words the
    user reads — so a rename that moves it out from under this lookup is a
    rename the test notices."""
    from widgets.collapsible_section import CollapsibleSection
    from widgets.stepper import Stepper

    for sec in sidebar.findChildren(CollapsibleSection):
        if sec.titleText() == "Legend":
            steppers = sec.findChildren(Stepper)
            if steppers:
                return steppers[0]
    return None


@pytest.mark.qt
def test_typing_a_size_into_the_panel_resizes_the_aggregate_keys(qtbot, monkeypatch):
    """Driven through the control a user touches, on the figure that had the
    bug. Asserting ``apply_export_style_prefs`` alone leaves the whole path
    from the stepper to the pref dict unmeasured — and that path is where a
    Properties control usually breaks."""
    from matplotlib.figure import Figure

    from well_viewer.features.export.sidebar_view import ExportStyleSidebar

    figure = Figure(figsize=(4, 3))
    ax = _agg_axes(figure)
    app = _stub_app()
    app._scatter_agg_fig = figure
    monkeypatch.setattr(ExportStyleSidebar, "_redraw_bound_figure",
                        lambda self: True, raising=False)
    sidebar = ExportStyleSidebar(app, None, figure, None,
                                 default_name="scatter_agg")
    qtbot.addWidget(sidebar)

    stepper = _legend_stepper(sidebar)
    assert stepper is not None, "no Font size control in the Legend section"
    stepper.setValue(WANTED)

    sizes = sorted({t.get_fontsize()
                    for leg in legends_on(ax) for t in leg.get_texts()})
    assert sizes == [WANTED], sizes
    assert app._app_state.export_prefs.style_prefs["legend_font_size"] == WANTED
