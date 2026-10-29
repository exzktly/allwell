"""Review honours the identity check's verdicts (NANOWELL_IDENTITY_TRACKER C1-C3).

A timepoint the check contradicted names other nanowells than the reference, so none of its rows may be joined to an
address over time: its rows read ``identity_contradicted`` with the check's reason, its values are no fold change's
numerator or baseline, and an address only it holds is not followed at all. A well whose timepoints are not all
confirmed says so in every figure's footer; a well with no current sidecar reads ``not_checked``.
"""
from __future__ import annotations

import math

import pytest

from pipeline import nanowell_identity as ni
from review.features.nanowell import distribution_compute as dc
from review.features.nanowell.collect import IDENTITY_CONTRADICTED, load_nanowell_data
from review.features.nanowell.identity_compute import identity_note
from review.features.nanowell.series_compute import baseline_at, exclusion_reason, site_metrics
from tests.test_nanowell_review_data import _dataset, _row

STARTS = {"t_c": "2026-03-01T09:00:00", "t_a": "2026-03-01T14:00:00", "t_d": "2026-03-01T19:00:00"}
WHY = "40 of 40 addresses do not land on themselves: every address is one lattice step (+1, +0) off"


def _well():
    """Nanowell (0, 0): 2, 4, 8 cells. (1, 0): 1, 1, 1. At t_a the rows carry a ghost (5, 0) only t_a names."""
    rows = []
    for tp, n0, n1 in (("t_c", 2, 1), ("t_a", 4, 1), ("t_d", 8, 1)):
        rows += [_row(tp, 0, 0, n=n0, c=0.1), _row(tp, 1, 0, n=n1, c=0.1)]
    rows.append(_row("t_a", 5, 0, n=9, c=0.9))
    return rows


def _out(tmp_path, verdicts: dict | None):
    out = _dataset(tmp_path, {"A01": _well()}, starts=STARTS)
    if verdicts is not None:
        ni.write_sidecar(out, "A01", {
            "version": ni.IDENTITY_VERSION, "well": "A01", "inputs": ni.inputs_digest(out, "A01"), "reference": "t_c",
            "timepoints": {tp: {"verdict": v, "reason": WHY if v == ni.CONTRADICTED else "a reason"}
                           for tp, v in verdicts.items()}})
    return out


def _metrics(out, baseline="earliest"):
    data = load_nanowell_data(out)
    return site_metrics(data, baseline), data


def test_a_contradicted_timepoint_joins_nothing_and_says_why(tmp_path):
    metrics, data = _metrics(_out(tmp_path, {"t_c": ni.REFERENCE, "t_a": ni.CONTRADICTED, "t_d": ni.CONFIRMED}))
    assert data.identity["A01"]["t_a"] == (ni.CONTRADICTED, WHY)
    at = metrics.set_index(["nw_q", "nw_r", "timepoint"])
    assert at.loc[(0, 0, "t_a"), "state"] == IDENTITY_CONTRADICTED
    assert at.loc[(0, 0, "t_a"), "reason"] == WHY
    assert math.isnan(at.loc[(0, 0, "t_a"), "count_fc"])
    assert at.loc[(0, 0, "t_d"), "count_fc"] == 4.0                    # the confirmed timepoint still joins
    assert sorted({(q, r) for q, r in zip(metrics["nw_q"], metrics["nw_r"])}) == [(0, 0), (1, 0)]  # no ghost
    reasons = exclusion_reason(metrics, "count_fc").groupby(metrics["timepoint"]).agg(set)
    assert reasons["t_a"] == {IDENTITY_CONTRADICTED}
    assert dc.reason_label(IDENTITY_CONTRADICTED) == "identity contradicted"


def test_a_contradicted_baseline_leaves_every_fold_change_from_it_undefined(tmp_path):
    out = _out(tmp_path, {"t_c": ni.REFERENCE, "t_a": ni.CONTRADICTED, "t_d": ni.CONFIRMED})
    metrics, _ = _metrics(out, baseline_at("t_a"))
    at = metrics.set_index(["nw_q", "nw_r", "timepoint"])
    assert math.isnan(at.loc[(0, 0, "t_d"), "count_fc"])
    reason = exclusion_reason(metrics, "count_fc")[metrics["timepoint"].eq("t_d")].iloc[0]
    assert reason == f"baseline {IDENTITY_CONTRADICTED}"
    assert dc.reason_label(reason) == "baseline identity contradicted"


def test_without_a_sidecar_every_timepoint_reads_not_checked_and_the_figure_says_so(tmp_path):
    metrics, data = _metrics(_out(tmp_path, None))
    assert set(data.identity["A01"].values()) == {(ni.NOT_CHECKED,
                                                   "the identity check has not been run on these tables")}
    assert at_count(metrics, "t_a") == 4.0 / 2.0                        # nothing left out: unverified, not refuted
    assert identity_note(data, ["A01"]).startswith("Nanowell identity — A01 not checked")


def at_count(metrics, tp):
    return metrics.set_index(["nw_q", "nw_r", "timepoint"]).loc[(0, 0, tp), "count_fc"]


@pytest.mark.parametrize("verdicts, note", [
    ({"t_c": ni.REFERENCE, "t_a": ni.CONFIRMED, "t_d": ni.CONFIRMED}, ""),
    ({"t_c": ni.REFERENCE, "t_a": ni.INCONCLUSIVE, "t_d": ni.CONFIRMED}, "Nanowell identity — A01 not confirmed"),
    ({"t_c": ni.REFERENCE, "t_a": ni.CONTRADICTED, "t_d": ni.CONFIRMED}, "Nanowell identity — A01 t_a contradicted (left out)"),
])
def test_the_footer_note_follows_the_verdicts(tmp_path, verdicts, note):
    _, data = _metrics(_out(tmp_path, verdicts))
    said = identity_note(data, ["A01"])
    assert said.startswith(note) if note else said == ""


def test_a_sidecar_for_other_tables_is_not_believed(tmp_path):
    out = _out(tmp_path, {"t_c": ni.REFERENCE, "t_a": ni.CONTRADICTED, "t_d": ni.CONFIRMED})
    table = out / "nanowells" / "A01.csv"
    table.write_bytes(table.read_bytes() + b"\n")                       # the tables changed after the check
    metrics, data = _metrics(out)
    assert {v for v, _ in data.identity["A01"].values()} == {ni.NOT_CHECKED}
    assert IDENTITY_CONTRADICTED not in set(metrics["state"])


def test_the_page_s_figures_carry_the_note(tmp_path):
    """Through ``controller.draw_view``, the one composition the page and Batch Export share."""
    from matplotlib.figure import Figure

    from review.features.nanowell.controller import draw_view

    metrics, data = _metrics(_out(tmp_path, {"t_c": ni.REFERENCE, "t_a": ni.CONTRADICTED, "t_d": ni.CONFIRMED}))
    fig = Figure(figsize=(8, 6))
    draw_view(fig, "trajectories", metrics, data, groups={"A01": ["A01"]}, metric="n", channel=None,
              axis="groups", timepoint="t_d", control="", baseline="earliest", cohort=False, well="A01",
              well_timepoint="t_d")
    texts = " ".join(t.get_text() for t in fig.texts)
    assert "Nanowell identity — A01 t_a contradicted" in texts


def test_the_export_carries_nothing_measured_at_a_contradicted_timepoint(tmp_path):
    """R9: the row under an address at a contradicted timepoint was measured on another physical nanowell."""
    from review.features.nanowell.series_compute import long_format

    metrics, data = _metrics(_out(tmp_path, {"t_c": ni.REFERENCE, "t_a": ni.CONTRADICTED, "t_d": ni.CONFIRMED}))
    table = long_format(metrics, data.channels).set_index(["nw_q", "nw_r", "timepoint"])
    row = table.loc[(0, 0, "t_a")]
    assert row["state"] == IDENTITY_CONTRADICTED and row["reason"] == WHY
    assert row[["n_cells", "confluence", "area_est_cells", "imaged_fraction", "visible_fraction"]].isna().all()
    assert table.loc[(0, 0, "t_d"), "area_est_cells"] == 8


# ── the line plots mark what was not confirmed ────────────────────────────────
def _figure(out, view, axis="time"):
    from matplotlib.figure import Figure

    from review.features.nanowell.controller import draw_view

    metrics, data = _metrics(out)
    fig = Figure(figsize=(8, 6))
    draw_view(fig, view, metrics, data, groups={"A01": ["A01"]}, metric="n", channel=None, axis=axis,
              timepoint="t_d", control="", baseline="earliest", cohort=False, well="A01", well_timepoint="t_d")
    return fig


def _bands(fig):
    return [(round(float(p.get_x() + p.get_width() / 2), 3), p.aw_verdict, p.get_hatch())
            for ax in fig.axes for p in ax.patches if p.get_gid() == "nw-identity-band"]


def _segments(fig, style):
    from matplotlib.collections import LineCollection

    out = []
    for ax in fig.axes:
        for coll in ax.collections:
            if isinstance(coll, LineCollection) and (coll.get_linestyle()[0][1] is not None) == (style == "--"):
                out += [tuple(round(float(x), 3) for x in seg[:, 0]) for seg in coll.get_segments()]
    return out


def test_trajectories_hatch_an_inconclusive_timepoint_and_dash_the_lines_through_it(tmp_path):
    """Asked for: an inconclusive timepoint is labelled as such in the line plots, not only in the footer."""
    fig = _figure(_out(tmp_path, {"t_c": ni.REFERENCE, "t_a": ni.INCONCLUSIVE, "t_d": ni.CONFIRMED}),
                  "trajectories")
    assert _bands(fig) == [(5.0, ni.INCONCLUSIVE, "////")]               # t_a is at 5 h
    assert [t.get_text() for t in fig.texts + fig.axes[0].texts if t.get_gid() == "nw-identity-mark"] == \
        ["inconclusive"]
    dashed, solid = _segments(fig, "--"), _segments(fig, "-")
    assert dashed and all(5.0 in seg for seg in dashed)                  # every segment touching t_a is dashed
    assert not any(5.0 in seg for seg in solid)
    footer = " ".join(t.get_text() for t in fig.texts if t.get_gid() == "nw-traj-footer")
    assert "Hatched: nanowell identity not confirmed (// inconclusive); lines through them dashed." in footer


def test_a_contradicted_timepoint_is_cross_hatched_and_its_gap_explained(tmp_path):
    fig = _figure(_out(tmp_path, {"t_c": ni.REFERENCE, "t_a": ni.CONTRADICTED, "t_d": ni.CONFIRMED}),
                  "trajectories")
    assert _bands(fig) == [(5.0, ni.CONTRADICTED, "xxxx")]
    assert not any(5.0 in seg for seg in _segments(fig, "-") + _segments(fig, "--"))   # no value there: a gap


def test_a_well_never_checked_is_said_once_not_hatched_everywhere(tmp_path):
    fig = _figure(_out(tmp_path, None), "trajectories")
    assert _bands(fig) == [] and _segments(fig, "--") == []
    footer = " ".join(t.get_text() for t in fig.texts if t.get_gid() == "nw-traj-footer")
    assert "Nanowell identity — A01 not checked" in footer


def test_the_distribution_over_time_hatches_the_swarm_and_dashes_the_median(tmp_path):
    fig = _figure(_out(tmp_path, {"t_c": ni.REFERENCE, "t_a": ni.INCONCLUSIVE, "t_d": ni.CONFIRMED}),
                  "distribution")
    (x, verdict, hatch), = _bands(fig)
    assert (verdict, hatch) == (ni.INCONCLUSIVE, "////")
    ax = fig.axes[0]
    swarm_x = dict(zip([t.get_text() for t in ax.get_xticklabels()], ax.get_xticks()))
    assert x == pytest.approx(swarm_x[next(k for k in swarm_x if "t_a" in k or "5" in k)])
    medians = [line for line in ax.lines if line.get_linewidth() == 1.2]
    styles = {tuple(round(v, 3) for v in line.get_xdata()): line.get_linestyle() for line in medians}
    assert styles and all((style == "--") == (x in xs) for xs, style in styles.items())
