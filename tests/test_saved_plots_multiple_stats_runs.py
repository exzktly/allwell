"""A saved plot carries every comparison the Statistics tab had run, not one.

The Statistics tab keys its runs by ``(val_col, target_t, stat)`` so that
testing a second channel — or a second statistic, or a second timepoint — adds
a comparison rather than replacing the first, and a Bar plot draws whichever of
the stored runs describe the bars in front of it.

``stats.pairwise`` is a derived view of that store meaning "the last run
recorded". Capturing it saved one comparison out of however many the figure was
drawing, so a plot of GFP bars saved after a CY5 test had been run came back
from Restore carrying the CY5 config — which matches no bar on it — and
therefore with no brackets at all.

These tests pin the capture side (every stored run reaches the record) and the
restore side (every saved run is re-run and recorded), both without the data
layer: ``compute_pairwise`` is deterministic and tested elsewhere, and what
broke here was which runs were handed to it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from review.domain.batch_models import BarGroup
from review.features.saved_plots import collect as collect_mod
from review.features.saved_plots import controller as controller_mod
from review.features.saved_plots.collect import _capture_stats_config
from review.features.saved_plots.controller import _restore_stats
from review.features.stats.state import StatsState

_GFP = "gfp_mean_intensity"
_CY5 = "cy5_mean_intensity"
_TEST = "t-test (Welch)"


def _record(val_col: str, *, stat: str = "mean", target_t: float = 0.0) -> dict:
    return {"a": "G1", "b": "G2", "p": 0.01, "p_adj": 0.01, "test": _TEST,
            "stat": stat, "val_col": val_col, "target_t": target_t}


def _stats_with(*records: dict) -> StatsState:
    """A stats slice holding one stored run per record, in the order given."""
    st = StatsState()
    st.groups = [BarGroup("G1", solo_wells=["A01", "A02"]),
                 BarGroup("G2", solo_wells=["B01", "B02"])]
    for rec in records:
        st.record_run([rec])
    return st


def _captured_runs(cfg) -> list[dict]:
    """The per-run blocks of a captured stats config.

    Asserted rather than indexed so a record that carries no per-run blocks
    fails saying that, instead of raising a ``KeyError`` that measures nothing.
    """
    assert cfg is not None, "no stats block was captured at all"
    runs = cfg.get("runs")
    assert isinstance(runs, list), (
        f"the record carries no per-run blocks; its keys are {sorted(cfg)}")
    return runs


def _keyed_columns(st: StatsState) -> list:
    """The value column of each stored run, with a contextless run as None.

    A run stored under no key is a real (wrong) outcome, so it has to compare
    as a value rather than raise while the assertion is being built.
    """
    return [None if key is None else key[0] for key in st.runs]


def _ctx(st: StatsState) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(
        stats=st, plot=SimpleNamespace(use_fov_replicates=False)))


@pytest.fixture
def stub_threshold(monkeypatch):
    """Resolve each run's threshold from its own value column, without a dataset.

    Distinct per column so a test can tell which column a captured block was
    resolved against — the threshold is a per-channel cut, and two runs on two
    channels must not share one.
    """
    monkeypatch.setattr(collect_mod, "_threshold_for_val_col",
                        lambda ctx, val_col: 100.0 if val_col == _GFP else 250.0)
    return None


# ── capture ──────────────────────────────────────────────────────────────────

def test_every_stored_run_reaches_the_saved_record(stub_threshold):
    st = _stats_with(_record(_GFP), _record(_CY5))
    cfg = _capture_stats_config(_ctx(st))
    assert [run["val_col"] for run in _captured_runs(cfg)] == [_GFP, _CY5]


def test_two_statistics_on_one_channel_are_both_captured(stub_threshold):
    """A Mean comparison and a Fraction comparison of the same bars sit on two
    panels of one figure; saving must not drop either."""
    st = _stats_with(_record(_GFP, stat="mean"), _record(_GFP, stat="fraction"))
    cfg = _capture_stats_config(_ctx(st))
    assert [run["statistic"] for run in _captured_runs(cfg)] == ["mean", "fraction"]


def test_each_run_resolves_its_own_threshold(stub_threshold):
    """The ON cutoff is per channel, so a two-channel record must not carry one
    channel's cut against the other's column."""
    st = _stats_with(_record(_GFP), _record(_CY5))
    cfg = _capture_stats_config(_ctx(st))
    assert [run["threshold"] for run in _captured_runs(cfg)] == [100.0, 250.0]


def test_the_most_recent_run_still_sits_at_the_top_level(stub_threshold):
    """Readers of a single run's context — ``build_summary``, an operator
    reading the JSON — keep one shape whatever the record holds."""
    st = _stats_with(_record(_GFP), _record(_CY5))
    cfg = _capture_stats_config(_ctx(st))
    assert cfg["val_col"] == _CY5 and cfg["threshold"] == 250.0
    assert cfg["test"] == _TEST and cfg["statistic"] == "mean"


def test_no_runs_means_no_stats_block(stub_threshold):
    st = _stats_with()
    assert _capture_stats_config(_ctx(st)) is None


def test_fewer_than_two_groups_means_no_stats_block(stub_threshold):
    st = _stats_with(_record(_GFP))
    st.groups = st.groups[:1]
    assert _capture_stats_config(_ctx(st)) is None


# ── restore ──────────────────────────────────────────────────────────────────

@pytest.fixture
def recording_pairwise(monkeypatch):
    """Stand in for ``compute_pairwise``, recording the config it was asked for.

    Returns one record echoing the requested context, so the restored store is
    keyed exactly as a real run's would be.
    """
    asked: list[dict] = []

    def _fake(ctx, *, groups, target_t, val_col, statistic, threshold, test,
              use_fov=True):
        asked.append({"val_col": val_col, "statistic": statistic,
                      "threshold": threshold, "target_t": target_t,
                      "test": test, "use_fov": use_fov})
        return [_record(val_col, stat=statistic, target_t=target_t or 0.0)]

    monkeypatch.setattr(controller_mod, "_compute_pairwise", _fake)
    monkeypatch.setattr(controller_mod, "_deserialize_stats_groups",
                        lambda ctx, data: [BarGroup(g["name"]) for g in (data or [])])
    monkeypatch.setattr(controller_mod, "_refresh_stats_group_views", lambda ctx: None)
    return asked


def _restore_ctx(st: StatsState) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(stats=st, feature_slice=lambda name: st),
        notify=SimpleNamespace(set_status=lambda text: None))


def _cfg(*runs: dict) -> dict:
    return {"groups": [{"name": "G1"}, {"name": "G2"}], "use_fov": False,
            "runs": [dict(r) for r in runs]}


def _run_block(val_col: str, *, statistic: str = "mean", threshold: float = 1.0) -> dict:
    return {"test": _TEST, "target_t": 0.0, "val_col": val_col,
            "statistic": statistic, "threshold": threshold}


def test_restore_re_runs_every_saved_comparison(recording_pairwise):
    st = StatsState()
    _restore_stats(_restore_ctx(st),
                   _cfg(_run_block(_GFP, threshold=100.0),
                        _run_block(_CY5, threshold=250.0)))
    assert [a["val_col"] for a in recording_pairwise] == [_GFP, _CY5]
    assert [a["threshold"] for a in recording_pairwise] == [100.0, 250.0]
    assert _keyed_columns(st) == [_GFP, _CY5]


def test_restore_replaces_comparisons_left_over_from_this_session(recording_pairwise):
    """The record's runs are what the plot was drawn with; a comparison run
    earlier in the session is not, and must not survive into the restored plot."""
    st = _stats_with(_record("stale_column"))
    _restore_stats(_restore_ctx(st), _cfg(_run_block(_GFP)))
    assert _keyed_columns(st) == [_GFP]


def test_a_record_written_before_runs_were_plural_still_restores(recording_pairwise):
    """Older records carry their single run at the top level and no ``runs``
    list; reading them as a one-run list keeps them working."""
    st = StatsState()
    legacy = {"groups": [{"name": "G1"}, {"name": "G2"}],
              "use_fov": False, **_run_block(_GFP, threshold=100.0)}
    _restore_stats(_restore_ctx(st), legacy)
    assert [a["val_col"] for a in recording_pairwise] == [_GFP]
    assert _keyed_columns(st) == [_GFP]


def test_the_fov_replicate_mode_reaches_every_run(recording_pairwise):
    st = StatsState()
    cfg = _cfg(_run_block(_GFP), _run_block(_CY5))
    cfg["use_fov"] = True
    _restore_stats(_restore_ctx(st), cfg)
    assert [a["use_fov"] for a in recording_pairwise] == [True, True]


def test_a_comparison_that_cannot_be_re_run_is_reported(monkeypatch):
    """Silence would leave "the data for this test is gone" looking exactly like
    "this plot never had brackets" — which is the one thing the figure cannot
    show for itself."""
    monkeypatch.setattr(controller_mod, "_compute_pairwise",
                        lambda ctx, **kw: [] if kw["val_col"] == _CY5
                        else [_record(kw["val_col"])])
    monkeypatch.setattr(controller_mod, "_deserialize_stats_groups",
                        lambda ctx, data: [BarGroup(g["name"]) for g in (data or [])])
    monkeypatch.setattr(controller_mod, "_refresh_stats_group_views", lambda ctx: None)
    said: list[str] = []
    st = StatsState()
    ctx = SimpleNamespace(
        state=SimpleNamespace(stats=st, feature_slice=lambda name: st),
        notify=SimpleNamespace(set_status=said.append))
    _restore_stats(ctx, _cfg(_run_block(_GFP), _run_block(_CY5)))
    assert _keyed_columns(st) == [_GFP]
    assert said and "1 of its 2 saved comparisons" in said[0]


def test_nothing_is_said_when_every_comparison_came_back(recording_pairwise):
    said: list[str] = []
    st = StatsState()
    ctx = SimpleNamespace(
        state=SimpleNamespace(stats=st, feature_slice=lambda name: st),
        notify=SimpleNamespace(set_status=said.append))
    _restore_stats(ctx, _cfg(_run_block(_GFP), _run_block(_CY5)))
    assert said == []
