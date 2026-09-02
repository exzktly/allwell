"""Batch Export's fifth mode writes the Response tab's figure, per group.

The mode exists so a plate of dose-response conditions comes out as one figure
and one CSV per export group instead of one screenshot at a time. Everything
that decides *what* is on those figures is the Response tab's own — the
collector, the renderer, the fit, the title vocabulary — so what is worth
pinning here is the half that is this mode's:

* the CSV carries the group, the drawn points, **the members that could not be
  placed**, and the fits. A file listing five of eight members is the same
  silent substitution as a figure drawing five of eight, and a spreadsheet is
  where someone counts rows and believes the total;
* a group with no placeable points writes nothing at all rather than blank axes
  beside an empty CSV, reported as a success;
* the figure's title carries the group **and** the three clauses the tab's own
  title carries, assembled by the tab's own helper rather than spelled again;
* the panel offers no fold-change controls, because this collector normalises
  through the shared plotting state and a panel-local control would be a switch
  that configures nothing.
"""
from __future__ import annotations

import csv
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

import matplotlib  # noqa: E402
matplotlib.use("Agg")

from well_viewer.features.batch_export.response_panel import (  # noqa: E402
    ResponseBatchExportPanel,
    _parse_bound,
)
from well_viewer.features.response.collect import (  # noqa: E402
    ResponseCurve, ResponseData, ResponsePoint, Y_FRACTION, Y_MEAN,
)
from well_viewer.features.response.response_compute import (  # noqa: E402
    FIT_4PL, FIT_NONE,
)
from well_viewer.domain.batch_models import BarGroup  # noqa: E402


class _PanelStub:
    """The mode's own methods, off the real class, with no widgets."""

    _write_csv = ResponseBatchExportPanel._write_csv
    _figure_title = ResponseBatchExportPanel._figure_title
    _render_group_figure = ResponseBatchExportPanel._render_group_figure
    _discard_empty_csv = ResponseBatchExportPanel._discard_empty_csv
    _draws_fraction = ResponseBatchExportPanel._draws_fraction
    _y_metric = ResponseBatchExportPanel._y_metric
    _mode_key = ResponseBatchExportPanel._mode_key

    def __init__(self):
        self._ctx = SimpleNamespace(
            state=SimpleNamespace(plot=SimpleNamespace()))


def _data():
    """Two members placed at two doses, one omitted, one non-numeric.

    Deliberately not a clean plate: the omissions are the half of the file that
    is easiest to lose, and a fixture with none of them cannot tell a writer
    that drops them from one that keeps them.
    """
    return ResponseData(
        variable_name="Dose", unit="nM",
        curves=[ResponseCurve(
            timepoint=24.0, series_id="s1", series_label="HeLa",
            points=(
                ResponsePoint(member="rep 1", x=1.0, y=10.0, err=1.0, n=3,
                              label="1"),
                ResponsePoint(member="rep 2", x=10.0, y=40.0,
                              err=float("nan"), n=1, label="10"),
            ))],
        omitted=[("rep 3", "wells disagree")],
        non_numeric=["vehicle"],
        replicate_unit="replicate",
    )


def _rows(path):
    with open(path, newline="") as fh:
        return list(csv.reader(fh))


def test_the_csv_carries_the_points_the_omissions_and_the_fits(tmp_path):
    panel = _PanelStub()
    data = _data()
    fits = {}
    out = tmp_path / "response_drug_a.csv"
    panel._write_csv(out, BarGroup("Drug A"), data, fits)

    rows = _rows(out)
    assert rows[0] == ["group", "member", "series", "timepoint", "x", "x_label",
                       "y", "err", "n", "spread_unit", "reason"]
    body = rows[1:]
    assert [r[0] for r in body] == ["Drug A"] * len(body)
    # Two drawn points…
    assert [(r[1], r[4], r[6]) for r in body[:2]] == [
        ("rep 1", "1", "10"), ("rep 2", "10", "40")]
    assert body[0][2] == "HeLa" and body[0][3] == "24"
    # …a spread that was never defined is a **blank cell**, not a zero and not
    # the literal ``nan``: one replicate has no spread, and an error bar of zero
    # length reads as a measurement of perfect agreement.
    assert body[0][7] == "1" and body[1][7] == ""
    # …and the two kinds of member that were left out, each with its reason.
    assert [(r[1], r[10]) for r in body[2:]] == [
        ("rep 3", "wells disagree"), ("vehicle", "value is not a number")]


def test_a_fit_gets_a_row_in_the_columns_the_points_use(tmp_path):
    """Including a refused one, which carries the reason it was refused.

    Dropping it would leave a spreadsheet whose fits are all successful.
    """
    from well_viewer.features.response.collect import fit_curves

    panel = _PanelStub()
    data = _data()
    fits = fit_curves(data, FIT_4PL)
    assert fits, "the fixture must actually attempt a fit"
    out = tmp_path / "response_drug_a.csv"
    panel._write_csv(out, BarGroup("Drug A"), data, fits)

    fit_rows = [r for r in _rows(out)[1:] if r[1].startswith("fit (")]
    assert len(fit_rows) == 1, fit_rows
    row = fit_rows[0]
    assert row[0] == "Drug A"
    assert row[2] == "HeLa"           # which curve the fit belongs to
    assert row[3] == "24"             # its timepoint
    # Two doses cannot support a four-parameter fit, so this one is refused —
    # and says so rather than printing the optimiser's last iterate as an EC50.
    assert row[4] == "" and row[10]


def test_the_figure_title_names_the_group_and_what_is_plotted():
    panel = _PanelStub()
    title = panel._figure_title(
        BarGroup("Drug A"), _data(), channel="gfp",
        metric_key="mean_intensity", metric_label="Mean Intensity",
        draws_fraction=False, use_sem=True, variable_name="Dose",
        timepoints=[24.0])
    assert title == ("Drug A — GFP Mean Intensity (above threshold) ± SEM "
                     "— t = 24 h — vs Dose")


def test_the_fraction_title_is_the_fraction_not_the_property():
    """An axis naming the Property over a curve of fractions is the one thing a
    figure must never do, and the title follows the same rule."""
    panel = _PanelStub()
    title = panel._figure_title(
        BarGroup("Drug A"), _data(), channel="gfp",
        metric_key="fraction_above_threshold", metric_label="Mean Intensity",
        draws_fraction=True, use_sem=False, variable_name="Dose",
        timepoints=[24.0, 48.0])
    assert title == (
        "Drug A — GFP Fraction above threshold ± SD — t = [24, 48] h — vs Dose")


def test_the_rendered_figure_carries_that_title_and_its_y_label():
    """Asserted on the axes, not on the strings handed to the renderer.

    A title computed and never applied is exactly the shape of defect a test of
    the helper alone cannot see.
    """
    panel = _PanelStub()
    fig = panel._render_group_figure(
        BarGroup("Drug A"), _data(), {},
        channel="gfp", metric_key="mean_intensity",
        metric_label="Mean Intensity", draws_fraction=False, use_sem=True,
        variable_name="Dose", timepoints=[24.0], log_x=False)
    ax = fig.axes[0]
    assert ax.get_title() == (
        "Drug A — GFP Mean Intensity (above threshold) ± SEM "
        "— t = 24 h — vs Dose")
    assert ax.get_ylabel() == "GFP mean intensity"
    assert ax.get_xlabel() == "Dose (nM)"
    # The points actually reached the axes — a renderer handed the right
    # labels and no data would pass every assertion above.
    assert ax.containers or ax.lines


def test_the_y_label_says_when_the_curves_are_normalised():
    """The fold-change suffix travels on the data, so an export normalised by
    the shared setting cannot be labelled with the raw measurement."""
    panel = _PanelStub()
    data = _data()
    data.normalised = "÷ control"
    fig = panel._render_group_figure(
        BarGroup("Drug A"), data, {}, channel="gfp",
        metric_key="mean_intensity", metric_label="Mean Intensity",
        draws_fraction=False, use_sem=True, variable_name="Dose",
        timepoints=[24.0], log_x=False)
    assert fig.axes[0].get_ylabel() == "GFP mean intensity ÷ control"


def test_the_fraction_y_label_is_not_the_channel_s_property():
    panel = _PanelStub()
    data = _data()
    data.y_metric = Y_FRACTION
    fig = panel._render_group_figure(
        BarGroup("Drug A"), data, {}, channel="gfp",
        metric_key="fraction_above_threshold", metric_label="Mean Intensity",
        draws_fraction=True, use_sem=True, variable_name="Dose",
        timepoints=[24.0], log_x=False)
    assert fig.axes[0].get_ylabel() == "GFP fraction above threshold"


def test_an_empty_csv_is_removed_rather_than_left_beside_no_figure(tmp_path):
    """The skip note says the group produced nothing; the folder must agree.

    A CSV left behind is a file for the group, so a reader counting files finds
    the run complete.
    """
    path = tmp_path / "response_drug_a.csv"
    path.write_text("group,member\n")
    _PanelStub._discard_empty_csv(path)
    assert not path.exists()


def test_the_metric_decides_what_the_curves_plot():
    panel = _PanelStub()
    assert panel._draws_fraction("fraction_above_threshold") is True
    assert panel._draws_fraction("mean_intensity") is False
    assert panel._y_metric(True) == Y_FRACTION
    assert panel._y_metric(False) == Y_MEAN


def test_an_unreadable_fit_bound_frees_the_parameter():
    """Rather than keeping a previous value, which would fit a curve to a number
    the user did not type."""
    assert _parse_bound("100") == 100.0
    assert _parse_bound("  ") is None
    assert _parse_bound("1OO") is None


def test_the_mode_offers_no_fold_change_controls_of_its_own():
    """Because this collector reads the shared fold-change state.

    A panel-local control here would be offered, moved, and silently ignored by
    the export it appears to configure — the export wrong and the panel saying
    it is right.
    """
    from well_viewer.features.batch_export.base_panel import BatchExportPanel

    assert BatchExportPanel._INCLUDES_FOLD_CHANGE is True
    assert ResponseBatchExportPanel._INCLUDES_FOLD_CHANGE is False
    # And the info text tells the user where the setting does live, so the
    # absence reads as a decision rather than as a missing control.
    import inspect
    body = inspect.getsource(ResponseBatchExportPanel._build_output_panel)
    assert "Fold change follows the shared plotting setting" in body


def test_the_channel_roster_includes_this_mode_s_own_combo():
    """The repopulate-on-show loop and the read-back share one roster.

    A mode whose combo joined neither is populated once, at build time, and then
    silently exports against the channel list from before the dataset was
    opened.
    """
    assert "_response_channel_cb" in ResponseBatchExportPanel._CHANNEL_COMBO_ATTRS


def test_the_property_row_offers_the_fraction():
    """It is often the readout a dose response is actually about, and an EC50
    fitted to it is well defined — which is why the tab offers it."""
    import inspect

    body = inspect.getsource(ResponseBatchExportPanel._build_output_panel)
    assert "include_fraction=True" in body


def test_the_fit_model_offer_matches_the_tab_s():
    from well_viewer.features.response.controller import fit_model_labels

    models = [model for _label, model in fit_model_labels()]
    assert models[0] == FIT_NONE, "off by default — a fit is an assertion"
    assert FIT_4PL in models
