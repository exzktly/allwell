"""The Nanowells page honours the plate's sample variables: *Group by*, *Colour by*, *Split by* and *Then by*.

The page is an Analysis page, so it never shows the plotting band where those controls live. It pooled through the
band's *Group by* with no control of its own, and nothing decorated its figures at all. These tests hold:

* the pure half (``decorate_compute`` and the Distribution model) — the order, the spans, the inks, and that no
  value moves;
* the renderer, read back from the figure's own artists (CLAUDE.md §7.4 #1): the dots' colours, the key, the bands;
* the page through its real builder and its real combos (§7.6, §7.8 rule 3): choosing a variable in the page's own
  *Group by* regroups the page, and choosing one in its *Colour by* recolours the dots on the canvas.

Fixtures are built so the right and the wrong answer differ (§7.8 rule 2): values are assigned **against** the
group order, so "ink the groups in order" and "band the groups in order" both produce a different figure from the
right one.
"""

from __future__ import annotations

import numpy as np
import pytest
from matplotlib.colors import to_hex

from review.domain import sample_variables as sv
from review.domain import variable_series as vs
from review.features.nanowell import distribution_compute as dc
from review.features.nanowell import decorate_compute as dec
from tests.test_nanowell_page_compute import _plate

WELLS = ("A01", "A02", "B01", "B02", "C01")

#: Cell line assigned against the well order: A01 is U2OS (the variable's *second* value) and A02 HeLa (its first).
LINE = sv.Variable(id="line", name="Cell line", kind=sv.TEXT, unit="",
                   values=(sv.VariableValue("h", "HeLa", None), sv.VariableValue("u", "U2OS", None)))
#: Dose against the well order too, and C01 left unfilled.
DOSE = sv.Variable(id="dose", name="Dose", kind=sv.NUMBER, unit="µM",
                   values=(sv.VariableValue("d0", "0", 0.0), sv.VariableValue("d1", "1", 1.0)))
ASSIGNMENTS = {"line": {"A01": "u", "A02": "h", "B01": "u", "B02": "h", "C01": "h"},
               "dose": {"A01": "d1", "A02": "d0", "B01": "d1", "B02": "d0"}}


def _vset(*variables):
    return sv.VariableSet(variables=variables or (LINE, DOSE),
                          assignments={v.id: dict(ASSIGNMENTS[v.id]) for v in (variables or (LINE, DOSE))})


class _Ctx:
    """The two slices ``decoration_for`` reads: the plate's variables and the plot's decoration fields."""

    def __init__(self, *, colour="", split="", sub="", vset=None):
        from types import SimpleNamespace

        self.state = SimpleNamespace(
            selection=SimpleNamespace(variables=vset or _vset()),
            plot=SimpleNamespace(colour_by_variable=colour, split_by_variable=split, sub_split_by_variable=sub))


def _groups():
    return {w: [w] for w in WELLS}


@pytest.fixture
def plate(tmp_path):
    return _plate(tmp_path, {w: {"t_c": [1, 2, 0, 3], "t_a": [i, 2, 3, 1]} for i, w in enumerate(WELLS)})


# ── the pure half ─────────────────────────────────────────────────────────────
def test_splitting_orders_the_groups_into_contiguous_bands_keeping_the_page_s_order_inside_each():
    deco = dec.decoration_for(_Ctx(split="dose"), _groups())
    # Page order A01 A02 B01 B02 C01; dose 1 µM, 0, 1 µM, 0, unfilled. Bands in the variable's order, the
    # non-value last, and the page's order kept inside each band.
    assert list(dec.order_groups(_groups(), deco)) == ["A02", "B02", "A01", "B01", "C01"]
    assert dec.band_spans(list(dec.order_groups(_groups(), deco)), deco.bands) == [
        (0, 1, "0 µM"), (2, 3, "1 µM"), (4, 4, vs.UNASSIGNED_LABEL)]


def test_a_sub_band_never_spans_across_two_bands():
    names = ["A02", "B02", "A01", "B01"]
    bands = {"A02": "0", "B02": "0", "A01": "1", "B01": "1"}
    subs = {"A02": "HeLa", "B02": "U2OS", "A01": "U2OS", "B01": "U2OS"}
    # B02 and A01 are neighbours holding the same sub-band, but in different bands: two spans, not one.
    assert dec.sub_band_spans(names, bands, subs) == [(0, 0, "HeLa"), (1, 1, "U2OS"), (2, 3, "U2OS")]


def test_colour_by_inks_each_group_with_its_value_s_colour_from_the_shared_palette():
    vset = _vset()
    deco = dec.decoration_for(_Ctx(colour="line", vset=vset), _groups())
    palette = vs.colors_for(vset, "line")
    assert deco.inks == {"A01": palette["u"], "A02": palette["h"], "B01": palette["u"], "B02": palette["h"],
                         "C01": palette["h"]}
    assert deco.legend == (("HeLa", palette["h"]), ("U2OS", palette["u"]))
    assert deco.colour_name == "Cell line"


def test_a_group_whose_wells_disagree_and_one_holding_an_unfilled_well_take_their_own_named_inks():
    deco = dec.decoration_for(_Ctx(colour="dose"), {"mixed": ["A01", "A02"], "C01": ["C01"], "B01": ["B01"]})
    assert deco.inks["mixed"] == vs.MIXED_COLOR
    assert deco.inks["C01"] == vs.UNASSIGNED_COLOR
    assert [label for label, _c in deco.legend] == ["1 µM", vs.MIXED_LABEL, vs.UNASSIGNED_LABEL]


def test_then_by_is_only_drawn_inside_a_split_and_never_by_the_split_s_own_variable():
    assert dec.decoration_for(_Ctx(sub="line"), _groups()) is dec.NONE
    assert dec.decoration_for(_Ctx(split="dose", sub="dose"), _groups()).sub_bands == {}
    assert dec.decoration_for(_Ctx(split="dose", sub="line"), _groups()).sub_bands["A01"] == "U2OS"


def test_a_variable_that_no_longer_exists_decorates_nothing():
    deco = dec.decoration_for(_Ctx(colour="gone", split="gone"), _groups())
    assert not deco.colours and not deco.splits


@pytest.mark.parametrize("axis", [dc.WELLS, dc.TIME])
def test_decorating_moves_no_value(plate, axis):
    metrics, data = plate
    plain = dc.distribution_model(metrics, "n", channels=data.channels, axis=axis, groups=_groups())
    deco = dec.decoration_for(_Ctx(colour="line", split="dose", sub="line"), _groups())
    decorated = dc.distribution_model(metrics, "n", channels=data.channels, axis=axis, groups=_groups(),
                                      decoration=deco)

    def by_group(model):
        return {(s.group, s.well, s.label): sorted(s.values.tolist()) for s in model.swarms()}

    assert by_group(decorated) == by_group(plain)
    assert decorated.n_used == plain.n_used and decorated.excluded() == plain.excluded()


def test_along_wells_each_band_is_a_labelled_span_of_its_groups(plate):
    metrics, data = plate
    deco = dec.decoration_for(_Ctx(split="dose", sub="line"), _groups())
    model = dc.distribution_model(metrics, "n", channels=data.channels, axis=dc.WELLS, groups=_groups(),
                                  decoration=deco)
    panel = model.panels[0]
    assert [t[1] for t in panel.ticks] == ["A02", "B02", "A01", "B01", "C01"]
    assert panel.bands == [(0.0, 1.0, "0 µM"), (2.0, 3.0, "1 µM"), (4.0, 4.0, vs.UNASSIGNED_LABEL)]
    assert panel.sub_bands == [(0.0, 1.0, "HeLa"), (2.0, 3.0, "U2OS"), (4.0, 4.0, "HeLa")]
    # Each swarm sits over its own group's tick: the order moved the groups, not the swarms away from them.
    assert {s.group: s.x for s in panel.swarms} == {"A02": 0.0, "B02": 1.0, "A01": 2.0, "B01": 3.0, "C01": 4.0}


def test_over_time_each_group_s_panel_names_its_band(plate):
    metrics, data = plate
    deco = dec.decoration_for(_Ctx(split="dose"), _groups())
    model = dc.distribution_model(metrics, "n", channels=data.channels, axis=dc.TIME, groups=_groups(),
                                  decoration=deco)
    assert [p.title for p in model.panels] == ["0 µM · A02", "0 µM · B02", "1 µM · A01", "1 µM · B01",
                                              f"{vs.UNASSIGNED_LABEL} · C01"]


# ── the renderer, read back from the figure ───────────────────────────────────
def _render(model):
    from matplotlib.figure import Figure

    from review.features.nanowell.renderer import render_distribution

    fig = Figure(figsize=(11, 6.5))
    render_distribution(fig, model)
    return fig


def _texts(fig, gid):
    found = list(fig.texts) + [t for ax in fig.axes for t in ax.texts]
    return [t.get_text() for t in found if t.get_gid() == gid]


def test_coloured_by_a_variable_each_dot_takes_its_group_s_value_ink_and_the_key_names_the_values(plate):
    metrics, data = plate
    vset = _vset()
    deco = dec.decoration_for(_Ctx(colour="line", vset=vset), _groups())
    fig = _render(dc.distribution_model(metrics, "n", channels=data.channels, axis=dc.WELLS, groups=_groups(),
                                        decoration=deco))
    palette = vs.colors_for(vset, "line")
    ax = fig.axes[0]
    dots = [c for c in ax.collections if c.get_gid() == "nw-dots"]
    ticks = [t.get_text() for t in ax.get_xticklabels()]
    drawn = {ticks[int(round(c.get_offsets()[:, 0].mean()))]: {to_hex(f) for f in c.get_facecolors()} for c in dots}
    assert drawn == {"A01": {to_hex(palette["u"])}, "A02": {to_hex(palette["h"])}, "B01": {to_hex(palette["u"])},
                     "B02": {to_hex(palette["h"])}, "C01": {to_hex(palette["h"])}}
    legend = next(lg for lg in fig.legends if lg.get_gid() == "nw-value-legend")
    assert [t.get_text() for t in legend.get_texts()] == ["HeLa", "U2OS"]
    assert legend.get_title().get_text() == "Cell line:"
    # The occupancy key would decode colours the figure no longer has.
    assert not any(t.startswith("Cells at baseline") for t in _texts(fig, "nw-legend"))
    assert any(t.startswith("Dots coloured by Cell line") for t in _texts(fig, "nw-footer"))


def test_undecorated_the_dots_keep_their_occupancy_colours_and_key(plate):
    metrics, data = plate
    fig = _render(dc.distribution_model(metrics, "n", channels=data.channels, axis=dc.WELLS, groups=_groups()))
    assert not fig.legends
    assert any(t.startswith("Cells at baseline") for t in _texts(fig, "nw-legend"))


def test_split_bands_are_headed_under_their_groups_with_a_divider_between_them(plate):
    metrics, data = plate
    deco = dec.decoration_for(_Ctx(split="dose", sub="line"), _groups())
    fig = _render(dc.distribution_model(metrics, "n", channels=data.channels, axis=dc.WELLS, groups=_groups(),
                                        decoration=deco))
    ax = fig.axes[0]
    assert _texts(fig, "nw-band") == ["0 µM", "1 µM", vs.UNASSIGNED_LABEL]
    assert _texts(fig, "nw-subband") == ["HeLa", "U2OS", "HeLa"]
    bands = {t.get_text(): t.get_position()[0] for t in ax.texts if t.get_gid() == "nw-band"}
    assert bands == {"0 µM": 0.5, "1 µM": 2.5, vs.UNASSIGNED_LABEL: 4.0}     # centred under its groups
    dividers = sorted(line.get_xdata()[0] for line in ax.lines if line.get_gid() == "nw-band-divider")
    assert dividers == [1.5, 3.5]


# ── the page, through its real builder and combos ─────────────────────────────
pytest.importorskip("PySide6")


@pytest.fixture
def page(qtbot, tmp_path):
    from tests.test_nanowell_page import _loaded, _page, _write_nanowell_dataset

    counts = {w: {"t_c": [1, 2, 0, 1], "t_a": [2, 3, 1, i]} for i, w in enumerate(WELLS)}
    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path, counts=counts))
    ctx.state.selection.selected_wells = set(WELLS)
    ctx.state.selection.variables = _vset()
    _loaded(qtbot, ctx)
    return ctx


def _choose(combo, data):
    index = combo.findData(data)
    assert index >= 0, f"{data!r} is not offered: {[combo.itemData(i) for i in range(combo.count())]}"
    combo.setCurrentIndex(index)


def test_without_variables_the_page_offers_neither_control(qtbot, tmp_path):
    from review.features.nanowell import controller as nwc
    from tests.test_nanowell_page import _loaded, _page, _write_nanowell_dataset

    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path))
    ctx.state.selection.selected_wells = {"A01"}
    _loaded(qtbot, ctx)
    nwc.redraw(ctx)
    assert not ctx.widgets.get("_nw_groupby_grp").isVisibleTo(ctx.window)
    assert not ctx.widgets.get("_nw_decorate_btn").isVisibleTo(ctx.window)


def test_group_by_on_the_page_regroups_the_page_by_the_variable_s_values(page, qtbot):
    from review.features.nanowell import controller as nwc

    nwc.redraw(page)
    group = page.widgets.get("_nw_groupby_cb")
    assert page.widgets.get("_nw_groupby_grp").isVisibleTo(page.window)
    assert [group.itemText(i) for i in range(group.count())] == ["Wells / groups", "Cell line", "Dose"]
    _choose(group, "dose")
    assert page.state.plot.group_by_variable == "dose"
    # One group per value, in the variable's order; C01 holds no dose and is not drawn.
    assert nwc.page_groups(page) == {"0 µM": ["A02", "B02"], "1 µM": ["A01", "B01"]}
    nwc.set_axis(page, dc.WELLS)
    ticks = [t.get_text() for t in page.widgets.get("_nw_fig").axes[0].get_xticklabels()]
    assert ticks == ["0 µM", "1 µM"]


def test_a_group_by_chosen_on_the_plotting_band_shows_on_the_page_s_combo(page):
    from review.features.nanowell import controller as nwc

    page.state.plot.group_by_variable = "line"
    nwc.redraw(page)
    assert page.widgets.get("_nw_groupby_cb").currentData() == "line"


def test_decorate_is_offered_on_the_distribution_view_only(page):
    from review.features.nanowell import controller as nwc

    button = page.widgets.get("_nw_decorate_btn")
    nwc.set_view(page, "distribution")
    assert button.isVisibleTo(page.window)
    nwc.set_view(page, "trajectories")
    assert not button.isVisibleTo(page.window)


def test_colour_by_on_the_page_recolours_the_dots_on_the_canvas(page):
    from review.features.nanowell import controller as nwc

    nwc.set_view(page, "distribution")
    nwc.set_axis(page, dc.WELLS)
    colour = page.widgets.get("_nw_colourby_cb")
    # The way back is named for what the page draws without it, not for the band's plate colours.
    assert colour.itemText(0) == "Occupancy" and colour.itemData(0) == ""
    _choose(colour, "line")
    assert page.state.plot.colour_by_variable == "line"
    ax = page.widgets.get("_nw_fig").axes[0]
    ticks = [t.get_text() for t in ax.get_xticklabels()]
    drawn = {ticks[int(round(c.get_offsets()[:, 0].mean()))]: {to_hex(f) for f in c.get_facecolors()}
             for c in ax.collections if c.get_gid() == "nw-dots"}
    palette = vs.colors_for(page.state.selection.variables, "line")
    assert drawn["A01"] == {to_hex(palette["u"])} and drawn["A02"] == {to_hex(palette["h"])}


def test_split_by_on_the_page_bands_the_groups_and_enables_then_by(page):
    from review.features.nanowell import controller as nwc

    nwc.set_view(page, "distribution")
    nwc.set_axis(page, dc.WELLS)
    then_by = page.widgets.get("_nw_subsplitby_cb")
    assert not then_by.isEnabled()                      # nothing to nest inside yet
    _choose(page.widgets.get("_nw_splitby_cb"), "dose")
    assert then_by.isEnabled()
    assert then_by.findData("dose") < 0                 # never the split's own variable
    fig = page.widgets.get("_nw_fig")
    assert _texts(fig, "nw-band") == ["0 µM", "1 µM", vs.UNASSIGNED_LABEL]
    assert np.isfinite([t.get_position()[0] for t in fig.axes[0].texts if t.get_gid() == "nw-band"]).all()
