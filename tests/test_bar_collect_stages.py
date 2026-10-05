"""Two promises of the bar collector that nothing pinned.

Row C3 of ``_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`` splits
``collect_bar_items`` into stages. Mutating the split against every test that
reaches the bar collector or fold-change left two changes green — and each
changed hundreds of configurations in a 3,840-configuration differential of
the collector's output:

* with *Pool replicates* off, a set's wells stopped taking the set's colour and
  took their own well colour (768 configurations differed);
* with both fold-change axes on, a single well's Events panel stopped stacking
  the control onto its baseline (216 configurations differed).

Each fixture is built so the right and the wrong answer differ: a set of two
wells (a set takes its lowest well's colour, so the second well's own colour is
different), and a control whose count changes between the baseline and the
drawn timepoint.
"""

from __future__ import annotations

from review.domain.selections_model import color_for_replicate_set, color_for_well
from review.features.barplot.collect import collect_bar_items
from tests.conftest import MockApp, make_agg


class _UnpooledApp(MockApp):
    """Replicate sets active, *Pool replicates* off: one bar per well."""

    @property
    def _app_state(self):
        st = MockApp._app_state.fget(self)
        st.plot.bar_pool_replicates = False
        return st


def test_an_unpooled_well_takes_its_set_s_colour():
    pts = {"A01": [make_agg(0.0, 10.0)], "C01": [make_agg(0.0, 20.0)]}
    # A set's colour is its lowest well's, so in a two-well set the second
    # well's own colour and its set's colour differ.
    sels = [{"name": "CTRL", "wells": ["A01", "C01"], "hidden": False}]
    app = _UnpooledApp(per_well_pts=pts, selections=sels)
    selections = app._ctx.state.selection.selections
    set_colour = {w: color_for_replicate_set(r, selections)
                  for r in app._rep_sets_active() for w in r.wells}
    assert set_colour["C01"] != color_for_well("C01"), "fixture cannot tell them apart"

    _, items, _ = collect_bar_items(app._ctx, 0.0)

    assert {it.key: it.color for it in items} == set_colour


def test_a_well_s_events_panel_stacks_both_fold_change_axes():
    """ctrl_first, ratio/ratio: (value / control@t) / (baseline / control@t0)."""
    pts = {"A01": [make_agg(0.0, 10.0, n_above=2), make_agg(2.0, 10.0, n_above=3)],
           "C01": [make_agg(0.0, 10.0, n_above=4), make_agg(2.0, 10.0, n_above=12)]}
    app = MockApp(per_well_pts=pts, fc_vs_control_on=True,
                  fc_control_label="A01", fc_vs_t0_on=True, fc_order="ctrl_first")

    _, items, _ = collect_bar_items(app._ctx, 2.0)

    events = {it.key: it.n_above for it in items}
    # Unstacked, the baseline would be the raw 4 and the bar (12 / 3) / 4 = 1.
    assert events["C01"] == (12 / 3) / (4 / 2)

