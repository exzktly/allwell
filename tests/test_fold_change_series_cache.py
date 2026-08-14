"""``ControlSeriesCache`` — the per-redraw memo of fold-change denominators.

Two things are pinned here. That the memo returns the *same numbers* the
un-memoised helpers do, which is the only reason it is allowed to exist; and
that it actually collapses repeated lookups, because a memo that silently
stops memoising looks exactly like one that works — the plot is still correct,
it is just slow again, and nothing fails.

The counts matter. Before this cache, a 24-member plate of replicate sets over
6 timepoints with a single control rebuilt the control's whole series 882 times
per redraw on the vs-control + vs-t0 path, because the cross-axis stacking
blocks resolve a control inside the member loop. It is 75 now, and 3 of those
are the control.
"""
from __future__ import annotations

from well_viewer.domain import fold_change as _fc
from well_viewer.domain.control_ref import ControlRef
from well_viewer.domain.fold_change_series_cache import ControlSeriesCache

from tests.conftest import MockApp, make_agg


def _app():
    pts = {
        "A01": [make_agg(0.0, 100.0), make_agg(1.0, 100.0), make_agg(2.0, 100.0)],
        "A02": [make_agg(0.0, 100.0), make_agg(1.0, 100.0), make_agg(2.0, 100.0)],
        "B01": [make_agg(0.0, 100.0), make_agg(1.0, 200.0), make_agg(2.0, 400.0)],
        "B02": [make_agg(0.0, 100.0), make_agg(1.0, 200.0), make_agg(2.0, 400.0)],
    }
    selections = [
        {"name": "CTRL", "wells": ["A01", "A02"], "hidden": False},
        {"name": "TREAT", "wells": ["B01", "B02"], "hidden": False},
    ]
    return MockApp(per_well_pts=pts, selections=selections)


def _cache(ctx, **over):
    kw = dict(threshold=0.0, val_col="mean_gfp_intensity", use_sem=True,
              per_fov_spread=False, fluor_gates=None)
    kw.update(over)
    return ControlSeriesCache(ctx, **kw), kw


# ── agreement with the helpers it replaces ───────────────────────────────────

def test_stats_at_matches_control_stats_at_for_bar():
    ctx = getattr(_app(), "_ctx", None) or _app()
    cache, kw = _cache(ctx)
    for label in ("CTRL", "TREAT"):
        for t in (0.0, 1.0, 2.0):
            assert cache.stats_at(label, t) == _fc.control_stats_at_for_bar(
                ctx, label, t, **kw)


def test_frac_at_matches_control_frac_at_for_bar():
    ctx = getattr(_app(), "_ctx", None) or _app()
    cache, kw = _cache(ctx)
    for t in (0.0, 1.0, 2.0):
        assert cache.frac_at("CTRL", t) == _fc.control_frac_at_for_bar(
            ctx, "CTRL", t, **kw)


def test_n_at_matches_control_n_at_for_bar():
    ctx = getattr(_app(), "_ctx", None) or _app()
    cache, kw = _cache(ctx)
    for t in (0.0, 1.0, 2.0):
        assert cache.n_at("CTRL", t) == _fc.control_n_at_for_bar(
            ctx, "CTRL", t, **kw)


def test_mean_at_matches_control_mean_at_for_bar():
    ctx = getattr(_app(), "_ctx", None) or _app()
    cache, kw = _cache(ctx)
    for t in (0.0, 1.0, 2.0):
        assert cache.mean_at("CTRL", t) == _fc.control_mean_at_for_bar(
            ctx, "CTRL", t, **kw)


# ── the "not sampled here" answer must survive memoisation ───────────────────

def test_a_timepoint_the_control_does_not_have_is_none_not_zero():
    """``None`` means undefined. Folding it to 0.0 would make an unresolvable
    denominator indistinguishable from a measured zero."""
    ctx = getattr(_app(), "_ctx", None) or _app()
    cache, _ = _cache(ctx)
    assert cache.stats_at("CTRL", 99.0) is None
    assert cache.mean_at("CTRL", 99.0) is None


def test_an_unknown_label_is_none_every_time():
    ctx = getattr(_app(), "_ctx", None) or _app()
    cache, _ = _cache(ctx)
    assert cache.stats_at("NOPE", 0.0) is None
    assert cache.stats_at("NOPE", 0.0) is None  # memoised empty, still None


# ── the memo actually memoises ───────────────────────────────────────────────

def test_repeat_lookups_of_one_label_build_the_series_once():
    ctx = getattr(_app(), "_ctx", None) or _app()
    cache, _ = _cache(ctx)
    for t in (0.0, 1.0, 2.0, 0.0, 1.0):
        cache.stats_at("CTRL", t)
    assert cache.builds == 1


def test_each_quantity_and_label_builds_once():
    ctx = getattr(_app(), "_ctx", None) or _app()
    cache, _ = _cache(ctx)
    for label in ("CTRL", "TREAT"):
        for t in (0.0, 1.0, 2.0):
            cache.stats_at(label, t)
            cache.frac_at(label, t)
            cache.n_at(label, t)
    # 2 labels x 3 quantities, independent of the 3 timepoints.
    assert cache.builds == 6


def test_mean_at_shares_the_stats_memo():
    ctx = getattr(_app(), "_ctx", None) or _app()
    cache, _ = _cache(ctx)
    cache.stats_at("CTRL", 0.0)
    cache.mean_at("CTRL", 1.0)
    assert cache.builds == 1


# ── the collector-level count, which is the number that regressed ────────────

def test_a_multi_member_redraw_builds_each_control_series_once():
    """The regression guard for the 882-build path.

    Counts real series builds through a full ``collect_bar_items`` call with
    both axes on — the configuration where the cross-axis stacking blocks
    resolve denominators inside the member loop.
    """
    from well_viewer.features.barplot.collect import collect_bar_items

    n_members, n_tps = 6, 4
    pts, selections = {}, []
    w = 0
    for m in range(n_members):
        wells = []
        for _ in range(2):
            tok = f"{'ABCDEFGH'[w // 12]}{w % 12 + 1:02d}"
            pts[tok] = [make_agg(float(t), 100.0 + 10 * m) for t in range(n_tps)]
            wells.append(tok)
            w += 1
        selections.append({"name": f"S{m:02d}", "wells": wells, "hidden": False})
    app = MockApp(per_well_pts=pts, selections=selections)
    ctx = getattr(app, "_ctx", app)
    ctx.state.plot.bar_pool_replicates = True

    counts: dict[str, int] = {}
    originals = {}
    for name in ("member_stats_series", "member_frac_series", "member_n_series"):
        originals[name] = getattr(_fc, name)

        def make(orig=originals[name], key=name):
            def wrapped(*a, **k):
                counts[key] = counts.get(key, 0) + 1
                return orig(*a, **k)
            return wrapped
        setattr(_fc, name, make())
    try:
        collect_bar_items(
            ctx, [float(t) for t in range(n_tps)],
            fc_state=_fc.FoldChangeState(
                vs_control_on=True, control=ControlRef.legacy("S00"), vs_t0_on=True),
        )
    finally:
        for name, orig in originals.items():
            setattr(_fc, name, orig)

    # One build per member for its own baseline, plus one for the control —
    # and crucially NOT multiplied by the timepoint count.
    expected = n_members + 1
    for name, got in counts.items():
        assert got == expected, (
            f"{name} built {got} series; expected {expected} "
            f"({n_members} members + 1 control, independent of {n_tps} timepoints)"
        )
