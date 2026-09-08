"""A figure that picks its timepoint from a menu still names the corrected hour.

Properties → **Time** states the correction ``filename hours × interval +
offset`` — the plate whose images are numbered 1, 2, 3 but were taken at 0, 24
and 48 hours. Every other figure that prints a timepoint reads it: the Line
panels put the marker on their axis label, the Bar panels through
``plot_style.mark_time_rescale``, the batch-export scatter through
``time_rescale.figure_timepoint_label``.

The Heat Map and the Distribution tab printed the raw filename hour. With a
correction in force each named a different hour from every other figure of the
same plate, and no figure said which of the two it meant — which also made the
Time section, on those tabs, two controls that did nothing.

Having no time axis is not a reason to skip the correction; it is the reason the
correction has nowhere else to go. The batch-export scatter figures already
worked this way for exactly that reason, so the two tabs here were the odd ones
out rather than a considered exception.

``figure_timepoint_label`` is the helper for exactly this shape: a figure with
no time axis to hang a marker on, whose title is the only place the timepoint
appears. It carries the marker itself, so a corrected title cannot be read as
real hours.

The assertions are on ``ax.get_title()`` — the words drawn on the figure — not
on the helper, which has its own tests and passes them just as happily against
a renderer that never calls it.
"""

from __future__ import annotations

import types

import pytest

from well_viewer.features.heatmap import controller as HM
from well_viewer.features.heatmap.models import HeatmapLayout

_TP = 6.0
_WELLS = {"A01": 5.0, "A02": 120.0, "B01": 300.0, "B02": 500.0}


def _layout() -> HeatmapLayout:
    layout = HeatmapLayout(name="Block", rows=2, cols=2)
    layout.assign(0, 0, ["A01"])
    layout.assign(0, 1, ["A02"])
    layout.assign(1, 0, ["B01"])
    layout.assign(1, 1, ["B02"])
    return layout


@pytest.fixture
def title(monkeypatch):
    """Draw the heat map under a time correction and hand back its title."""
    from matplotlib.figure import Figure

    def _draw(*, scale=1.0, offset=0.0):
        fig = Figure()
        ax = fig.add_subplot(1, 1, 1)
        layout = _layout()
        widgets = {
            "_heatmap_fig": fig, "_heatmap_ax": ax,
            "_heatmap_canvas": types.SimpleNamespace(draw_idle=lambda: None),
            "_heatmap_cax": None,
            "_heatmap_metric_cb": types.SimpleNamespace(
                currentText=lambda: HM.METRIC_MEAN),
            "_heatmap_tp_cb": types.SimpleNamespace(currentIndex=lambda: 0),
        }
        ctx = types.SimpleNamespace(
            window=None,
            state=types.SimpleNamespace(
                dataset=types.SimpleNamespace(
                    well_paths=dict.fromkeys(_WELLS, "p")),
                channel=types.SimpleNamespace(
                    active_val_col="v", active_channel="GFP",
                    active_metric="mean_intensity", ratio_index={}),
                plot=types.SimpleNamespace(
                    use_fov_replicates=False, use_sem=True,
                    time_scale=scale, time_offset=offset),
                selection=types.SimpleNamespace(
                    selected_wells=set(), variables=None),
                cell_gating=types.SimpleNamespace(
                    use_scatter_thresh=False, use_scatter_gates=False,
                    scatter_gates=[], thresh_choices=None, selected_gate=None),
                heatmap=types.SimpleNamespace(
                    tp_values=[_TP], scale_mode="Auto", vmin=None, vmax=None,
                    repset_avg=False, log_scale=False, show_error=False,
                    label_from_sample_map=False, active_layout_name="Block"),
            ),
            runtime=types.SimpleNamespace(heatmap=types.SimpleNamespace(
                layouts=[layout], global_range_cache=None, label_drag=None,
                colorbar=None, global_vmin=None, global_vmax=None)),
            widgets=types.SimpleNamespace(get_or_none=widgets.get),
            notify=types.SimpleNamespace(set_status=lambda _m: None),
        )

        def _value(_ctx, wells, *_a, **_k):
            vals = [_WELLS[w] for w in (wells or []) if w in _WELLS]
            return sum(vals) / len(vals) if vals else float("nan")

        monkeypatch.setattr(HM, "current_focus_tp", lambda _c: None)
        monkeypatch.setattr(HM, "on_cutoff_for_column", lambda *_a, **_k: 0.0)
        monkeypatch.setattr(HM, "get_all_fluor_gates", lambda _c: {})
        monkeypatch.setattr(HM, "active_channel_label", lambda _c: "GFP")
        monkeypatch.setattr(HM, "apply_export_style_to_current",
                            lambda *_a, **_k: None)
        monkeypatch.setattr(HM, "_cell_value", _value)
        monkeypatch.setattr(
            HM, "_cell_stat",
            lambda c, w, *_a, **_k: (_value(c, w), float("nan")))
        HM.redraw_heatmap(ctx)
        return ax.get_title()

    return _draw


def test_no_correction_names_the_filename_hour(title):
    """The whole title, so this is a pin rather than a substring that six
    different titles would satisfy."""
    assert title() == "GFP — Mean above threshold — t = 6 h"


def test_an_interval_multiplies_the_hour_on_the_title(title):
    """Six images four hours apart, numbered 1-6 by the microscope."""
    assert "t = 24 h" in title(scale=4.0), title(scale=4.0)


def test_an_offset_shifts_the_hour_on_the_title(title):
    assert "t = 18 h" in title(offset=12.0), title(offset=12.0)


def test_the_interval_and_the_offset_compose(title):
    """``filename × interval + offset``, in that order — 6 × 4 + 12 = 36."""
    assert "t = 36 h" in title(scale=4.0, offset=12.0), title(scale=4.0, offset=12.0)


def test_a_corrected_title_says_it_was_corrected(title):
    """The marker is the whole reason the shared helper exists.

    Printing ``t = 24 h`` with nothing beside it is worse than printing the raw
    6: a corrected number that reads as a measured one cannot be told from a
    plate that really was imaged at 24 hours.
    """
    assert "filename value" in title(scale=4.0), title(scale=4.0)


def test_an_uncorrected_title_carries_no_marker(title):
    """The other half: no correction, no sentence about one."""
    assert "filename value" not in title(), title()


# ── the Distribution tab, which names its timepoint the same way ────────────

def _distribution_title(*, scale=1.0, offset=0.0, tp=6.0):
    """The Distribution title for one timepoint under a time correction."""
    from well_viewer.features.distribution.controller import _title_for

    ctx = types.SimpleNamespace(
        state=types.SimpleNamespace(
            plot=types.SimpleNamespace(time_scale=scale, time_offset=offset),
            channel=types.SimpleNamespace(
                active_val_col="gfp_mean_intensity", active_channel="GFP",
                active_metric="mean_intensity", ratio_index={}),
        ),
    )
    from well_viewer.features.distribution import controller as DC
    _saved = DC.active_channel_label
    DC.active_channel_label = lambda _c: "GFP"
    try:
        return _title_for(ctx, tp, 120, 3)
    finally:
        DC.active_channel_label = _saved


def test_the_distribution_title_carries_the_correction():
    assert "t = 24 h" in _distribution_title(scale=4.0)
    assert "filename value" in _distribution_title(scale=4.0)


def test_the_distribution_title_is_the_filename_hour_by_default():
    title = _distribution_title()
    assert "t = 6 h" in title and "filename value" not in title, title


def test_all_timepoints_is_still_named_that(monkeypatch):
    """The no-single-timepoint case has no hour to correct and must not gain one."""
    assert "all timepoints" in _distribution_title(scale=4.0, tp=float("nan"))


def test_every_distribution_renderer_draws_the_title_it_builds():
    """``_title_for`` is tested directly above, which cannot see whether the
    renderers use it. This is the half that can.

    Both draw modes build a title — the overlay through ``ax.set_title`` and the
    faceted grid through ``fig.suptitle`` — and either one that stopped passing
    ``_title_for``'s result to its setter would leave every assertion above
    about a string nobody draws (CLAUDE.md §7.8, face two). A third mode added
    without a title is caught here too, by the count.
    """
    import ast
    import inspect

    from well_viewer.features.distribution import controller as DC

    tree = ast.parse(inspect.getsource(DC))
    drawn = 0
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or fn.name == "_title_for":
            continue
        titled = {
            t.id
            for node in ast.walk(fn)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and getattr(node.value.func, "id", None) == "_title_for"
            for t in node.targets if isinstance(t, ast.Name)
        }
        if not titled:
            continue
        setters = {
            node.args[0].id
            for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and getattr(node.func, "attr", None) in ("set_title", "suptitle")
            and node.args and isinstance(node.args[0], ast.Name)
        }
        assert titled & setters, (
            f"{fn.name} builds a title with _title_for ({sorted(titled)}) and "
            f"draws something else ({sorted(setters)})")
        drawn += 1
    assert drawn == 2, (
        f"expected the overlay and faceted renderers to title themselves; "
        f"found {drawn}")
