"""A heatmap replicate-set cell shows what Bar and Line show for the same set.

The heatmap used to pool the cells of every well in a set and take one mean over
the pool. Bar, Line and the Statistics test take the mean of each replicate's
mean. Pooling weights a well by how many cells it segmented, so the two agree
only when every well in the set has the same cell count — and diverge silently
whenever confluence differs, which is most real plates.

Both surfaces now reduce the one replicate list ``group_replicate_stats``
produces, through the same ``replicate_mean`` reducer, so they cannot describe
different quantities again.

The fixture gives the two wells deliberately different cell counts, because a
set whose wells have equal counts cannot tell the two conventions apart — a
test built on one would have passed against the unfixed code.
"""

from __future__ import annotations

import types

import pytest

from well_viewer.domain.stats_compute import group_replicate_stats
from well_viewer.features.heatmap import controller as HM

_VAL = "gfp_mean_intensity"
_TP = 0.0

# (timepoint, mean, median, frac, n_above, n_total, …) — the AggPoint layout the
# aggregator emits. A01 is sparse and bright, A02 is dense and dim: pooling
# lets A02 dominate, averaging the means does not.
_POINTS = {
    "A01": [(_TP, 10.0, 0.0, 0.5, 5.0, 10, 0.0, 0.0, 0.0, 0.0)],
    "A02": [(_TP, 2.0, 0.0, 0.1, 9.0, 90, 0.0, 0.0, 0.0, 0.0)],
}


@pytest.fixture
def ctx():
    """A ctx whose aggregator is the pre-computed map above."""
    def _well(label, **_kw):
        return _POINTS[label]

    def _group(wells, **_kw):
        # The pooled convention, which is what the heatmap used to use: one
        # cell-count-weighted mean across the set.
        num = sum(_POINTS[w][0][1] * _POINTS[w][0][5] for w in wells)
        den = sum(_POINTS[w][0][5] for w in wells)
        n_above = sum(_POINTS[w][0][4] for w in wells)
        return [(_TP, num / den, 0.0, n_above / den, n_above, den,
                 0.0, 0.0, 0.0, 0.0)]

    return types.SimpleNamespace(
        state=types.SimpleNamespace(
            dataset=types.SimpleNamespace(well_paths={"A01": "a", "A02": "b"}),
            channel=types.SimpleNamespace(ratio_index={}, active_val_col=_VAL),
            plot=types.SimpleNamespace(use_fov_replicates=False),
            heatmap=types.SimpleNamespace(repset_avg=True),
            cell_gating=types.SimpleNamespace(
                use_scatter_thresh=False, use_scatter_gates=False,
                selected_gate=None,
            ),
            repo=None,
        ),
        runtime=types.SimpleNamespace(
            aggregate=types.SimpleNamespace(well=_well, group=_group),
        ),
    )


def _bar_value(ctx, wells, field="mean"):
    """What Bar and Line draw for this set: the mean across replicates.

    Spelled out here rather than imported from the reducer the fix introduces,
    so this file imports nothing that only exists after the fix — otherwise
    running it against the unfixed code raises ImportError before measuring
    anything, and a red run that measured nothing proves nothing (CLAUDE.md
    section 7.4 #4). ``_reduce_reps`` skips non-finite replicates; every
    replicate here is finite.
    """
    reps = group_replicate_stats(
        ctx, wells, _TP, use_fov=False, val_col=_VAL, threshold=0.0,
        fluor_gates={},
    )
    vals = [float(getattr(r, field)) for r in reps]
    return sum(vals) / len(vals)


def test_the_mean_metric_agrees_with_the_bar(ctx, monkeypatch):
    monkeypatch.setattr(HM, "_expand_repset_wells", lambda _c, w: list(w))
    wells = ["A01", "A02"]

    cell = HM._cell_value(ctx, wells, _TP, HM.METRIC_MEAN, _VAL, 0.0, {})

    # (10 + 2) / 2 — each well counts once.
    assert cell == pytest.approx(6.0)
    assert cell == pytest.approx(_bar_value(ctx, wells))
    # And it is emphatically not the pooled mean, which A02's 90 cells drag
    # down to 2.8. That number is what shipped before.
    assert cell != pytest.approx(2.8)


def test_the_fraction_metric_agrees_with_the_bar(ctx, monkeypatch):
    monkeypatch.setattr(HM, "_expand_repset_wells", lambda _c, w: list(w))
    wells = ["A01", "A02"]

    cell = HM._cell_value(ctx, wells, _TP, HM.METRIC_FRACTION, _VAL, 0.0, {})

    assert cell == pytest.approx(0.3)                     # (0.5 + 0.1) / 2
    assert cell == pytest.approx(_bar_value(ctx, wells, "frac"))
    assert cell != pytest.approx(14.0 / 100.0)            # the pooled fraction


def test_a_single_well_cell_is_unchanged(ctx, monkeypatch):
    """One well is one replicate, so the mean of its mean is its mean."""
    monkeypatch.setattr(HM, "_expand_repset_wells", lambda _c, w: list(w))
    cell = HM._cell_value(ctx, ["A01"], _TP, HM.METRIC_MEAN, _VAL, 0.0, {})
    assert cell == pytest.approx(10.0)


def test_the_count_metric_is_still_the_set_total(ctx, monkeypatch):
    """COUNT is a total, not an average, and is deliberately not converted."""
    monkeypatch.setattr(HM, "_expand_repset_wells", lambda _c, w: list(w))
    cell = HM._cell_value(ctx, ["A01", "A02"], _TP, HM.METRIC_COUNT, _VAL, 0.0, {})
    assert cell == pytest.approx(100.0)
