"""Every bound Properties control shows its stored pref and writes its own key.

The Properties panel (``ExportStyleSidebar._build_ui``) binds between 20 and
33 controls to pref keys through ``add_row`` / ``add_stacked``, depending on the
figure; the rest of its controls are wired by hand.
Before this file, the tests over it asserted which *sections* exist and drove a
handful of named controls; none asked the two questions every bound control
has to answer. Three deliberate mutations of the builder survived the whole
Properties suite (C0 of the implementation remediation plan):

* a toggle seeded with a constant instead of its stored pref (Show grid);
* a combo whose stored value was never applied (Legend location);
* a stepper bound under a neighbour's key (Grid opacity as ``grid_style``).

So, for every control the panel binds, on the figures whose panels differ:

0. **The roster of bound keys is the measured one.** A control can stay on
   screen while its binding is lost, and then rules 1–3 have nothing to check.

1. **No key is bound twice.** A second binding silently replaces the first in
   the getter map, so the first control stops being read and its own key is
   written by somebody else.
2. **A rebuilt panel shows the stored value.** The fixture stores a value that
   differs from what the control showed, so a control that ignores the store
   (and shows its default) cannot pass.
3. **Moving a control writes its key, and only its key.** Again with a value
   that differs from the current one, so "nothing was written" and "the right
   thing was written" cannot coincide.

The panel is decomposed by row C2 of that plan; this file is what says the
decomposition kept every binding.
"""

from __future__ import annotations

import pytest

from review.core.plot_style import figure_prefs
from tests.test_properties_hides_what_it_cannot_do import _sidebar

pytestmark = pytest.mark.qt

#: Figures whose panels are built differently (facets, sections, rows).
_KINDS = ["_line_fig", "_bar_fig", "_heatmap_fig", "_scatter_fig"]

#: The keys each figure's panel binds, as measured on the panel before it was
#: decomposed. A binding dropped on the way (the control still drawn, its key
#: no longer read or written) fails here; nothing else in the Properties suite
#: noticed one.
_BOUND_EVERYWHERE = frozenset({
    "axis_label_size", "axis_target", "export_profile", "grid_alpha",
    "grid_show", "grid_style", "layout_constrained", "layout_tight",
    "tick_direction", "tick_label_size", "tick_length", "tick_major",
    "tick_minor", "title_size", "x_lim_max", "x_lim_min", "x_tick_angle",
    "y_lim_max", "y_lim_min",
})
_BOUND_ROSTER: dict[str, frozenset[str]] = {
    "_line_fig": _BOUND_EVERYWHERE | {
        "group_label_size", "legend_font_size", "legend_loc", "legend_show",
        "line_style", "line_width", "marker_edge_width", "marker_size",
        "marker_style", "x_log", "x_log_base", "y_log", "y_log_base",
    },
    "_bar_fig": _BOUND_EVERYWHERE | {
        "group_label_size", "legend_font_size", "legend_loc", "legend_show",
        "line_style", "line_width", "marker_edge_width", "marker_size",
        "marker_style", "x_log", "x_log_base", "y_log", "y_log_base",
    },
    "_heatmap_fig": _BOUND_EVERYWHERE | {
        "heatmap_value_format",
    },
    "_scatter_fig": _BOUND_EVERYWHERE | {
        "group_label_size", "legend_font_size", "legend_loc", "legend_show",
        "line_style", "line_width", "marker_edge_width", "marker_size",
        "marker_style", "scatter_density", "scatter_x_log", "scatter_y_log",
        "x_log_base", "y_log_base",
    },
}

#: Keys whose control legitimately writes other keys too, with the reason.
_WRITES_OTHER_KEYS: dict[str, str] = {
    # A profile is a preset: choosing one applies its whole bundle of keys.
    "export_profile": "a preset of other keys",
}

#: Keys the panel binds but deliberately does not seed from the flat pref
#: store, each with its reason. Anything else that fails rule 2 is a defect.
_NOT_SEEDED_FROM_STORE: dict[str, str] = {
    # The axis picker's selection is UI state: a rebuilt panel starts on "All"
    # so an edit never lands on one panel by accident.
    "axis_target": "picker selection, reset to All on build",
}


def _alternatives(value):
    if isinstance(value, bool):
        return [not value]
    if isinstance(value, int):
        return [value + 1, value - 1, value + 5]
    if isinstance(value, float):
        return [value + 1.0, value - 1.0, value + 0.25, value - 0.25, value * 2 + 1]
    return None


def _different_value(sidebar, key):
    """A value the control accepts and that differs from what it shows now.

    Found through the control's own setter, so a stepper's range and a combo's
    items decide what is acceptable rather than this test.
    """
    from PySide6.QtWidgets import QComboBox

    getter, setter = sidebar._getters[key], sidebar._setters[key]
    current = getter()
    candidates = _alternatives(current)
    if candidates is None:
        combos = [w for w in sidebar.findChildren(QComboBox)
                  if sidebar._getters.get(key) == w.currentText]
        if combos:
            items = [combos[0].itemText(i) for i in range(combos[0].count())]
            candidates = [t for t in items if t != current]
        else:
            candidates = ["7", "3"] if current != "7" else ["3"]
    sidebar._updating = True
    try:
        for candidate in candidates:
            setter(candidate)
            got = getter()
            if got != current:
                setter(current)
                return got
    finally:
        sidebar._updating = False
    return None


def _bound_keys(qtbot, monkeypatch, kind):
    from review.features.export.sidebar_view import ExportStyleSidebar

    seen: list[str] = []
    original = ExportStyleSidebar._bind_getter_setter

    def recording(self, key, widget):
        seen.append(key)
        return original(self, key, widget)

    monkeypatch.setattr(ExportStyleSidebar, "_bind_getter_setter", recording)
    _sidebar(qtbot, monkeypatch, kind=kind)
    return seen


@pytest.mark.parametrize("kind", _KINDS)
def test_no_pref_key_is_bound_to_two_controls(qtbot, monkeypatch, kind):
    seen = _bound_keys(qtbot, monkeypatch, kind)
    assert seen, "the panel bound no controls at all"
    twice = sorted({k for k in seen if seen.count(k) > 1})
    assert not twice, f"bound more than once on {kind}: {twice}"


@pytest.mark.parametrize("kind", _KINDS)
def test_a_rebuilt_panel_shows_every_stored_value(qtbot, monkeypatch, kind):
    from review.features.export.sidebar_view import ExportStyleSidebar

    app, first = _sidebar(qtbot, monkeypatch, kind=kind)
    fig = getattr(app, kind)
    prefs = figure_prefs(app._ctx, fig)
    stored = {}
    for key in sorted(first._getters):
        if key in _NOT_SEEDED_FROM_STORE:
            continue
        alt = _different_value(first, key)
        if alt is None:
            continue
        stored[key] = alt
    assert len(stored) > 15, f"too few controls exercised on {kind}: {sorted(stored)}"
    prefs.update(stored)

    second = ExportStyleSidebar(app, None, fig, None, default_name=kind)
    qtbot.addWidget(second)
    wrong = {k: (v, second._getters[k]()) for k, v in stored.items()
             if k in second._getters and second._getters[k]() != v}
    assert not wrong, "\n".join(
        f"{k}: stored {v!r}, panel shows {got!r}" for k, (v, got) in sorted(wrong.items()))


@pytest.mark.parametrize("kind", _KINDS)
def test_moving_a_control_writes_its_own_key_only(qtbot, monkeypatch, kind):
    import review.features.export.sidebar_view as SV

    monkeypatch.setattr(SV, "apply_export_style_to_current", lambda *a, **k: None)
    app, sb = _sidebar(qtbot, monkeypatch, kind=kind)
    prefs = figure_prefs(app._ctx, sb._fig)
    # On a multi-panel figure the picker starts on one panel, and the per-axis
    # Axes-section keys then go to that panel's bucket rather than the flat
    # store (by design). "All" is the target whose writes land in the store.
    if "axis_target" in sb._setters:
        sb._updating = True
        sb._setters["axis_target"]("All")
        sb._updating = False
    wrong: list[str] = []
    exercised = 0
    for key in sorted(sb._getters):
        if key == "axis_target":
            continue
        alt = _different_value(sb, key)
        if alt is None:
            continue
        exercised += 1
        before = {k: v for k, v in prefs.items() if k != "axis_buckets"}
        sb._setters[key](alt)
        sb._on_fields_changed()
        after = {k: v for k, v in prefs.items() if k != "axis_buckets"}
        changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        if after.get(key) != alt:
            wrong.append(f"{key}: set {alt!r}, store holds {after.get(key)!r}")
        elif changed - {key} and key not in _WRITES_OTHER_KEYS:
            wrong.append(f"{key}: also changed {sorted(changed - {key})}")
    assert exercised > 15, f"too few controls exercised on {kind}"
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("kind", _KINDS)
def test_each_panel_binds_its_measured_roster(qtbot, monkeypatch, kind):
    _app, sb = _sidebar(qtbot, monkeypatch, kind=kind)
    bound = set(sb._getters)
    expected = set(_BOUND_ROSTER[kind])
    assert bound == expected, (
        f"{kind}: lost {sorted(expected - bound)}, gained {sorted(bound - expected)}")


def _segmented_with(sidebar, value):
    from widgets.segmented_control import SegmentedControl

    for seg in sidebar.findChildren(SegmentedControl):
        datas = list(seg._data)
        if value in datas:
            return seg, datas
    raise AssertionError(f"no segmented control offers {value!r}")


def test_choosing_an_error_bar_kind_reaches_the_plot_state(qtbot, monkeypatch):
    """The Statistics section's Error bars control is wired by hand, not bound
    through ``add_row``, so the roster above cannot see it. Measured: with its
    ``currentChanged`` connection deleted, all 303 tests that touch the panel
    stayed green (C0 of the implementation remediation plan).

    Choosing SD from SEM must store the kind *and* turn ``use_sem`` off through
    the app's own toggle; both differ from the starting state, so a control
    connected to nothing cannot pass.
    """
    from review.features.export.sidebar_view import ExportStyleSidebar

    monkeypatch.setattr(ExportStyleSidebar, "_redraw_active_plot",
                        lambda self: None, raising=False)
    app, sb = _sidebar(qtbot, monkeypatch, kind="_bar_fig")
    plot = app._app_state.plot
    plot.bar_err_kind = "sem"
    plot.use_sem = True
    toggles: list[bool] = []

    def toggle():
        toggles.append(True)
        plot.use_sem = not plot.use_sem

    app._toggle_sem = toggle

    seg, datas = _segmented_with(sb, "ci95")
    # Click the segment, as a user does: the button group's idClicked path.
    seg._buttons[datas.index("sd")].click()

    assert plot.bar_err_kind == "sd"
    assert toggles == [True]
    assert plot.use_sem is False
