"""v5.2.31: beeswarm marker properties must respond to export-style prefs.

Bug: ``apply_export_style_prefs`` walked ``ax.lines`` only, but the
beeswarm dots are produced by ``ax.scatter`` → ``PathCollection`` in
``ax.collections``. The user's Marker-size / Open-vs-Filled prefs
were silently dropped for beeswarm and for any other scatter-based
artists.

These tests pin the new ``ax.collections`` pass and the
``_aw_marker_collection`` opt-in tag so renderers can mark only the
PathCollections that should be styled (not error-bar caps, contour
lines, etc.).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("matplotlib")


class _FakeCollection:
    """Minimal PathCollection-compatible double. Records every setter
    call so the test can assert what the style pass did."""

    def __init__(self, *, tagged: bool, face=(0.5, 0.5, 0.5, 1.0)):
        if tagged:
            self._aw_marker_collection = True
        self._sizes: list[float] | None = None
        self._linewidths: list[float] | None = None
        self._face = face
        self._edge: tuple | None = None
        self._paths: list | None = None

    def set_sizes(self, sizes):
        self._sizes = list(sizes)

    def set_linewidths(self, widths):
        self._linewidths = list(widths)

    def get_facecolors(self):
        # Match the matplotlib return shape: a 2D RGBA array.
        return [self._face]

    def get_edgecolors(self):
        return []

    def set_facecolor(self, c):
        self._face = c

    def set_edgecolor(self, c):
        self._edge = c

    def set_paths(self, paths):
        self._paths = list(paths)


class _FakeLine:
    """Stand-in for matplotlib Line2D so the existing line loop in
    ``apply_export_style_prefs`` doesn't blow up. Returns sensible
    defaults for the getters the loop calls."""

    def __init__(self):
        self._color = "blue"

    def get_linestyle(self):
        return "-"

    def get_marker(self):
        return "o"

    def get_color(self):
        return self._color

    def set_linewidth(self, _w):
        pass

    def set_markersize(self, _s):
        pass

    def set_markeredgewidth(self, _w):
        pass

    def set_linestyle(self, _ls):
        pass

    def set_marker(self, _m):
        pass

    def set_markerfacecolor(self, _c):
        pass

    def set_markeredgecolor(self, _c):
        pass


class _FakeXY:
    def set_color(self, _c):
        pass

    def set_fontsize(self, _s):
        pass

    def set_fontfamily(self, _f):
        pass

    def set_rotation(self, _r):
        pass


class _FakeAxis:
    def __init__(self):
        self.label = _FakeXY()


class _FakeAx:
    """Minimum Axes surface the style pass touches."""

    def __init__(self, lines, collections):
        self.lines = lines
        self.collections = collections
        self.containers: list = []
        self.xaxis = _FakeAxis()
        self.yaxis = _FakeAxis()
        self.title = _FakeXY()
        self.patch = SimpleNamespace(set_alpha=lambda _a: None)
        self._categorical_xaxis = False
        self._categorical_yaxis = False

    def get_xticklabels(self):
        return []

    def get_yticklabels(self):
        return []

    def tick_params(self, **kw):
        pass

    def grid(self, *a, **kw):
        pass

    def minorticks_on(self):
        pass

    def minorticks_off(self):
        pass

    def get_legend(self):
        return None

    def get_xlim(self):
        return (0, 1)

    def get_ylim(self):
        return (0, 1)

    def set_xlim(self, *a, **kw):
        pass

    def set_ylim(self, *a, **kw):
        pass

    def set_xscale(self, _s):
        pass

    def set_yscale(self, _s):
        pass

    def get_position(self):
        return SimpleNamespace(frozen=lambda: None)

    def set_position(self, _p):
        pass


def _fake_fig(lines, collections):
    ax = _FakeAx(lines, collections)
    fig = SimpleNamespace(
        axes=[ax],
        patch=SimpleNamespace(set_alpha=lambda _a: None),
        set_layout_engine=lambda *_a, **_kw: None,
        set_constrained_layout=lambda *_a: None,
    )
    return fig, ax


def test_apply_export_style_resizes_tagged_path_collection():
    """v5.2.31: a PathCollection tagged ``_aw_marker_collection`` must
    receive ``set_sizes([marker_size**2])`` so the beeswarm dot size
    actually responds to the user's Marker-size pref."""
    from well_viewer.features.export.style import apply_export_style_prefs

    coll = _FakeCollection(tagged=True)
    fig, _ax = _fake_fig([_FakeLine()], [coll])
    apply_export_style_prefs(fig, {"marker_size": 8.0})
    # PathCollection sizes are points^2; the style pass converts the
    # diameter-style pref (points) to area (points^2).
    assert coll._sizes == [64.0]


def test_apply_export_style_skips_untagged_path_collection():
    """Untagged collections (errorbar caps, contour lines, etc.) must
    NOT be modified by the marker-style pass."""
    from well_viewer.features.export.style import apply_export_style_prefs

    untagged = _FakeCollection(tagged=False)
    fig, _ax = _fake_fig([_FakeLine()], [untagged])
    apply_export_style_prefs(fig, {"marker_size": 8.0})
    assert untagged._sizes is None  # untouched
    assert untagged._linewidths is None


def test_apply_export_style_applies_open_marker_to_collection():
    """When the user picks an ``_open`` marker, tagged PathCollections
    get a transparent face + edge in the original colour."""
    from well_viewer.features.export.style import apply_export_style_prefs

    coll = _FakeCollection(tagged=True, face=(0.2, 0.4, 0.8, 1.0))
    fig, _ax = _fake_fig([_FakeLine()], [coll])
    apply_export_style_prefs(fig, {
        "marker_size": 5.0,
        "marker_style": "o_open",
    })
    assert coll._face == "none"
    # Edge colour preserved from the renderer's per-well pick.
    assert coll._edge == (0.2, 0.4, 0.8, 1.0)


def test_apply_export_style_swaps_marker_shape_on_collection():
    """A non-default marker_style swaps the PathCollection's path so
    the dots take the requested shape (square, triangle, etc.)."""
    from well_viewer.features.export.style import apply_export_style_prefs

    coll = _FakeCollection(tagged=True)
    fig, _ax = _fake_fig([_FakeLine()], [coll])
    apply_export_style_prefs(fig, {
        "marker_size": 5.0,
        "marker_style": "s",  # square
    })
    assert coll._paths is not None and len(coll._paths) == 1


def test_apply_export_style_sets_collection_linewidth():
    """``marker_edge_width`` should reach the PathCollection too."""
    from well_viewer.features.export.style import apply_export_style_prefs

    coll = _FakeCollection(tagged=True)
    fig, _ax = _fake_fig([_FakeLine()], [coll])
    apply_export_style_prefs(fig, {
        "marker_size": 5.0,
        "marker_edge_width": 2.5,
    })
    assert coll._linewidths == [2.5]
