"""Scatter regressions: marker identity, fluor-gate lookup, gate axis labels.

Three unrelated defects on the Scatter tab and its Properties sidebar:

1. The aggregate scatter encodes well/group identity as the marker *shape* and
   draws a legend keyed on those shapes — but the export-style pass stamped the
   Marker-style pref's shape onto every data line afterwards, collapsing all
   points onto one shape (the pref default ``"o"``, which is also the first
   well's) while the legend went on advertising one shape per well.
2. The Cell Gating tab's fluorescence gate never reached the scatter: the tab
   keys its rows by normalised channel token (``"gfp"``) and the scatter looks
   the gate up by the upper-cased axis label (``"GFP"``), so the lookup missed
   and returned the "gate off" default.
3. A gate is a rectangle in one specific (x, y) metric pair, but the sidebar's
   gate list showed only the name.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("matplotlib")
pytest.importorskip("PySide6.QtWidgets")   # the cell-gating controller imports Qt


# ── 1. marker shape is data on the aggregate scatter ────────────────────────

class _RecordingLine:
    """Line2D stand-in that records the marker the style pass stamps on it."""

    def __init__(self, *, marker: str, marker_is_data: bool):
        self._marker = marker
        self.marker_set_calls: list[str] = []
        if marker_is_data:
            self._aw_marker_is_data = True

    # -- getters the style pass calls
    def get_linestyle(self):
        return "None"          # markers-only, like the aggregate's errorbar line

    def get_marker(self):
        return self._marker

    def get_color(self):
        return "blue"

    # -- setters
    def set_marker(self, marker):
        self.marker_set_calls.append(marker)
        self._marker = marker

    def set_linewidth(self, _w):
        pass

    def set_markersize(self, _s):
        self.markersize = _s

    def set_markeredgewidth(self, _w):
        self.markeredgewidth = _w

    def set_linestyle(self, _ls):
        pass

    def set_markerfacecolor(self, c):
        self.markerfacecolor = c

    def set_markeredgecolor(self, c):
        self.markeredgecolor = c


class _FakeText:
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
        self.label = _FakeText()


class _FakeAx:
    """Minimum Axes surface ``apply_export_style_prefs`` touches."""

    def __init__(self, lines):
        self.lines = lines
        self.collections: list = []
        self.containers: list = []
        self.xaxis = _FakeAxis()
        self.yaxis = _FakeAxis()
        self.title = _FakeText()
        self.patch = SimpleNamespace(set_alpha=lambda _a: None)
        self._categorical_xaxis = False
        self._categorical_yaxis = False

    def get_xticklabels(self):
        return []

    def get_yticklabels(self):
        return []

    def tick_params(self, **_kw):
        pass

    def grid(self, *_a, **_kw):
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

    def set_xlim(self, *_a, **_kw):
        pass

    def set_ylim(self, *_a, **_kw):
        pass

    def set_xscale(self, _s):
        pass

    def set_yscale(self, _s):
        pass

    def get_position(self):
        return SimpleNamespace(frozen=lambda: None)

    def set_position(self, _p):
        pass


def _fake_fig(lines):
    ax = _FakeAx(lines)
    fig = SimpleNamespace(
        axes=[ax],
        patch=SimpleNamespace(set_alpha=lambda _a: None),
        set_layout_engine=lambda *_a, **_kw: None,
        set_constrained_layout=lambda *_a: None,
    )
    return fig, ax


def test_style_pass_keeps_a_data_carrying_marker_shape():
    """The aggregate's points must survive the style pass with their own shape:
    the legend maps shape → well, so overwriting it mislabels every point."""
    from well_viewer.core.plot_style import apply_export_style_prefs

    square = _RecordingLine(marker="s", marker_is_data=True)
    fig, _ax = _fake_fig([square])

    apply_export_style_prefs(fig, {"marker_style": "o", "marker_size": 8.0})

    assert square.marker_set_calls == [], "the shape override must be skipped"
    assert square.get_marker() == "s"
    # Everything else about the marker still follows the prefs.
    assert square.markersize == 8.0


def test_style_pass_still_overrides_an_ordinary_marker():
    """Untagged lines keep honouring the Marker-style pref — the opt-out is
    per-artist, not a blanket disabling of the override."""
    from well_viewer.core.plot_style import apply_export_style_prefs

    plain = _RecordingLine(marker="s", marker_is_data=False)
    fig, _ax = _fake_fig([plain])

    apply_export_style_prefs(fig, {"marker_style": "o", "marker_size": 8.0})

    assert plain.marker_set_calls == ["o"]
    assert plain.get_marker() == "o"


def test_style_pass_applies_open_markers_to_a_data_carrying_shape():
    """Open-vs-filled is a rendering choice, not an identity one, so it applies
    even where the shape is pinned."""
    from well_viewer.core.plot_style import apply_export_style_prefs

    square = _RecordingLine(marker="s", marker_is_data=True)
    fig, _ax = _fake_fig([square])

    apply_export_style_prefs(fig, {"marker_style": "o_open", "marker_size": 8.0})

    assert square.get_marker() == "s"
    assert square.markerfacecolor == "none"
    assert square.markeredgecolor == "blue"


# ── 2. the Cell Gating fluor gate reaches callers using display labels ──────

def _gating_ctx(*, fluor_gates=None, thresh_frac_on=None):
    from well_viewer.core.cell_gating_state import CellGatingState

    cg = CellGatingState()
    cg.fluor_gates = dict(fluor_gates or {})
    cg.thresh_frac_on = dict(thresh_frac_on or {})
    return SimpleNamespace(state=SimpleNamespace(cell_gating=cg))


def test_fluor_gate_resolves_an_upper_case_channel_label():
    """The scatter passes ``"GFP"`` (its axis label); the gate is stored under
    ``"gfp"``. A missed lookup returned 0.0 — the gate silently off."""
    from well_viewer.features.grouping.cell_gating.controller import (
        cell_gating_get_fluor_gate,
    )

    ctx = _gating_ctx(fluor_gates={"gfp": 50.0}, thresh_frac_on={"gfp": 60.0})

    assert cell_gating_get_fluor_gate(ctx, "GFP") == 50.0
    assert cell_gating_get_fluor_gate(ctx, "gfp") == 50.0


def test_fluor_gate_resolves_when_only_the_gate_map_is_populated():
    """The resolver keys off the known rows; a gate edited before any
    threshold row exists must still be found."""
    from well_viewer.features.grouping.cell_gating.controller import (
        cell_gating_get_fluor_gate,
    )

    ctx = _gating_ctx(fluor_gates={"mcherry": 120.0})

    assert cell_gating_get_fluor_gate(ctx, "MCHERRY") == 120.0


def test_thresh_frac_on_resolves_an_upper_case_channel_label():
    from well_viewer.features.grouping.cell_gating.controller import (
        cell_gating_get_thresh_frac_on,
    )

    ctx = _gating_ctx(thresh_frac_on={"gfp": 75.0})

    assert cell_gating_get_thresh_frac_on(ctx, "GFP") == 75.0


def test_ratio_channels_still_resolve_through_the_ratio_prefix():
    from well_viewer.features.grouping.cell_gating.controller import (
        cell_gating_get_fluor_gate,
    )

    ctx = _gating_ctx(fluor_gates={"ratio:GFP/MCHERRY": 10.0},
                      thresh_frac_on={"ratio:GFP/MCHERRY": 20.0})

    assert cell_gating_get_fluor_gate(ctx, "GFP/MCHERRY") == 10.0
    assert cell_gating_get_fluor_gate(ctx, "ratio:GFP/MCHERRY") == 10.0


def test_an_unknown_channel_still_reports_the_gate_as_off():
    from well_viewer.features.grouping.cell_gating.controller import (
        cell_gating_get_fluor_gate,
    )

    ctx = _gating_ctx(fluor_gates={"gfp": 50.0}, thresh_frac_on={"gfp": 60.0})

    assert cell_gating_get_fluor_gate(ctx, "cy5") == 0.0


# ── 3. the sidebar gate list names the axes each gate was drawn on ──────────

def test_value_column_label_covers_all_three_column_kinds():
    from well_viewer.data.metric_labels import value_column_label

    assert value_column_label("gfp_mean_intensity") == "GFP Mean Intensity"
    assert value_column_label("cy5_smfish_count") == "CY5 (spots)"
    assert value_column_label("ratio:GFP/MCHERRY") == "GFP/MCHERRY"
    # Unrecognised columns pass through so a caller can always display them.
    assert value_column_label("something_else") == "something_else"
    assert value_column_label("") == ""


def test_gate_axes_label_names_both_axes():
    from well_viewer.data.metric_labels import gate_axes_label

    text = gate_axes_label("gfp_mean_intensity", "mcherry_total_intensity")

    assert "GFP Mean Intensity" in text
    assert "MCHERRY Total Intensity" in text
    assert text.startswith("x: ")


def test_gate_axes_label_is_empty_when_no_axes_were_recorded():
    """Gates drawn before the columns were stamped carry neither; the sidebar
    omits the caption rather than printing a pair of dashes."""
    from well_viewer.data.metric_labels import gate_axes_label

    assert gate_axes_label(None, None) == ""
    assert gate_axes_label("", "") == ""


def test_gate_axes_label_marks_a_half_recorded_gate():
    from well_viewer.data.metric_labels import gate_axes_label

    text = gate_axes_label("gfp_mean_intensity", None)

    assert "GFP Mean Intensity" in text
    assert "—" in text
