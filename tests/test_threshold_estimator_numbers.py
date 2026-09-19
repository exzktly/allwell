"""What the two auto-threshold estimators are allowed to store, and to claim.

Three defects from the Analyze audit, all in the same family — a number the
software could not really justify, written down as if it could:

* **C2** — the control-based objective ``|fa-fb| / max(|fa|,|fb|)`` saturates at
  exactly 1.0 wherever the negative control is empty, however few positive cells
  are above the cut. ``argmax`` then breaks the resulting tie by first index, so
  the rule actually implemented was *the lowest grid point above the negative
  control's maximum* — one outlying control cell moved a plate's gate by two
  hundred intensity units, and the log called it a relative gap of 1.000.
* **C3** — both estimators stored ``float(round(x))``, so a channel normalised
  to 0–1 stored ``0.0``: a gate that passes every cell, written as the one value
  that also means "this channel was never measured".
* **C11** — ``_index_members`` judged archive members with its own ``tif``/
  ``tiff`` list while the pipeline can write ``.png``, and it did so *before* the
  role and schema checks, so a PNG well left ``unplaceable`` empty and the run
  said only "skipping GFP — only 0 sample(s)".

The populations here are drawn from a seeded ``numpy`` generator at the audit's
own parameters, and every one is checked in float32 as well as float64 — a
shared numeric helper that behaves differently for the two is how a previous
refactor lost error bars.
"""

from __future__ import annotations

import logging
import zipfile

import numpy as np
import pytest

from pipeline.auto_threshold import _index_members, _SchemaReader, _otsu_per_channel
from pipeline.threshold_optimize import (
    best_from_curves,
    candidate_thresholds,
    estimate_thresholds_from_controls,
    frac_curve,
    optimize_between_populations,
    read_channel_values,
    relative_gap,
    round_stored_threshold,
    sweep_bounds,
    sweep_thresholds,
)

tifffile = pytest.importorskip("tifffile")


def _populations(dtype=np.float64, n=500):
    """The audit's own scenario: pos ~ N(600, 250), neg ~ N(400, 200)."""
    rng = np.random.default_rng(0)
    pos = rng.normal(600, 250, n).astype(dtype)
    neg = rng.normal(400, 200, n).astype(dtype)
    return pos, neg


# ── C2: the objective cannot saturate, and says how flat it was ──────────────

@pytest.mark.parametrize("dtype", [np.float64, np.float32])
def test_a_single_positive_cell_cannot_score_a_perfect_gap(dtype):
    """One cell above the cut against an empty control is not perfect separation.

    The unsmoothed objective scored exactly 1.0 for both "97 % on against 0 %"
    and "1 cell on against 0", because the numerator and the denominator are the
    same number whenever ``fb`` is zero. Laplace support has to separate them.
    """
    n = 500
    thin = np.zeros(2)            # fa, fb curves as bare arrays
    thin[0] = 1.0 / n             # one positive cell above the cut
    lots = np.array([0.97, 0.0])
    one_cell = relative_gap(np.array([thin[0]], dtype=dtype),
                            np.array([0.0], dtype=dtype), n_a=n, n_b=n)[0]
    almost_all = relative_gap(np.array([lots[0]], dtype=dtype),
                              np.array([0.0], dtype=dtype), n_a=n, n_b=n)[0]
    assert one_cell < 0.9, (
        f"one positive cell against an empty control still scores {one_cell:.4f}"
    )
    assert almost_all > 0.99
    assert one_cell < almost_all - 0.2


@pytest.mark.parametrize("dtype", [np.float64, np.float32])
def test_smoothing_holds_the_gate_when_the_two_populations_overlap(dtype):
    """On *these* populations, one control outlier no longer moves the gate.

    Measured on this fixture — overlapping N(600,250) against N(400,200), with
    the outlier at 1100 — the shift is 200.2 units unsmoothed and 0.0 smoothed,
    so the rule genuinely bites here.

    Deliberately not named for the general claim. ``relative_gap``'s docstring
    records the case where it does *not* hold: on two well-separated 400-cell
    populations the reported threshold is the plateau's lower edge, which is set
    by where the negative control's fraction-on reaches zero, and smoothing does
    not touch that edge — the same one-cell move shifts the gate by 1252 units
    either way. What ships against that is the reported plateau, not the
    smoothing, and a test named for the general claim would have said otherwise.
    """
    pos, neg = _populations(dtype)
    clean = optimize_between_populations(pos, neg)
    outlier = np.concatenate([neg[:-1], np.array([1100.0], dtype=dtype)])
    shifted = optimize_between_populations(pos, outlier)
    assert abs(shifted.threshold - clean.threshold) < 50.0, (
        f"one control outlier moved the gate from {clean.threshold:.1f} to "
        f"{shifted.threshold:.1f}"
    )
    # And the gate it keeps is one a meaningful share of the positives clear.
    assert shifted.frac_a > 0.05


def test_the_result_reports_how_many_grid_points_tied():
    """A plateau has to be visible as a plateau, not presented as an argmax."""
    pos, neg = _populations()
    thresholds = sweep_thresholds(*sweep_bounds(pos, neg), 512)
    fa, fb = frac_curve(pos, thresholds), frac_curve(neg, thresholds)
    unsupported = best_from_curves(thresholds, fa, fb, relative_gap(fa, fb),
                                   n_a=pos.size, n_b=neg.size)
    assert unsupported.tie_count > 1
    assert unsupported.tie_span > 0.0
    # The reported threshold is the lowest of the tied run, so the span is the
    # distance from it to the highest.
    tied = thresholds[relative_gap(fa, fb) == unsupported.difference]
    assert unsupported.threshold == pytest.approx(float(tied.min()))
    assert unsupported.tie_span == pytest.approx(float(tied.max() - tied.min()))


def test_the_supported_objective_collapses_the_plateau():
    pos, neg = _populations()
    supported = optimize_between_populations(pos, neg)
    assert supported.difference < 1.0
    assert supported.tie_count == 1


def test_the_fold_change_path_keeps_the_unsmoothed_objective():
    """Counts are optional, and without them nothing changes.

    The viewer's fold-change scan adjusts each fraction before the gap is taken,
    so its values are no longer ``k / n`` — they exceed 1 and go negative — and
    there is no ``k`` to add one to. Passing no counts must leave the old
    objective exactly as it was, including its sign-safe magnitude denominator.
    """
    fa = np.array([0.5, -0.2, 1.4, 0.0])
    fb = np.array([0.0, 0.4, 0.7, 0.0])
    got = relative_gap(fa, fb)
    want = np.array([1.0, 1.5, 0.5, 0.0])
    assert got == pytest.approx(want)


# ── C3: an estimator that succeeded never stores 0.0 ─────────────────────────

@pytest.mark.parametrize(
    "value,expected",
    [
        (0.4048, 0.405),          # the audit's normalised-channel Otsu value
        (0.000123456, 0.000123),
        (547.7, 548.0),           # unchanged where integers were already right
        (915.1, 915.0),
        (1234.5, 1234.0),
        (-0.4048, -0.405),
        (0.0, 0.0),               # a real zero stays a real zero
    ],
)
def test_round_stored_threshold_keeps_three_significant_figures(value, expected):
    assert round_stored_threshold(value) == pytest.approx(expected)


def test_a_sub_one_channel_stores_a_usable_gate(caplog):
    """A 0–1 normalised channel used to store 0.0 — a gate that passes everything.

    Driven through ``_otsu_per_channel``, the function that actually writes the
    sidecar entry, rather than through the rounding helper alone.
    """
    rng = np.random.default_rng(3)
    values = np.concatenate([rng.normal(0.15, 0.03, 400),
                             rng.normal(0.70, 0.05, 400)])
    with caplog.at_level(logging.INFO, logger="test"):
        log = logging.getLogger("test")
        out = _otsu_per_channel(["gfp"], {"gfp": list(values)},
                                {"gfp": 8}, {"gfp": 0}, log)
    assert "gfp" in out
    stored = out["gfp"]
    assert stored != 0.0, "the estimator stored the value that means 'not computed'"
    frac_on = float(np.count_nonzero(values > stored)) / values.size
    assert 0.3 < frac_on < 0.7, (
        f"stored threshold {stored!r} passes {frac_on:.1%} of the cells"
    )


def test_a_non_finite_otsu_result_is_skipped_not_stored(caplog, monkeypatch):
    """``0.0`` and ``nan`` are both "no gate"; neither may be written as one.

    This one passes on the unfixed code too, and for a reason that no longer
    holds: ``round(nan)`` raises, so the old ``float(round(...))`` fell into the
    broad ``except Exception`` beside it and skipped the channel by accident.
    ``round_stored_threshold`` returns a non-finite value unchanged rather than
    raising, so the skip is now an explicit branch and this pins it.
    """
    import pipeline.auto_threshold as at

    monkeypatch.setattr(at, "threshold_otsu", lambda arr: float("nan"))
    log = logging.getLogger("test")
    with caplog.at_level(logging.WARNING, logger="test"):
        out = _otsu_per_channel(["gfp"], {"gfp": [1.0, 2.0, 3.0]},
                                {"gfp": 1}, {"gfp": 0}, log)
    assert out == {}
    assert "non-finite" in caplog.text


def _write_well_csv(path, values, channel="gfp"):
    import csv as _csv

    with open(path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["cell_id", f"{channel}_mean_intensity"])
        for i, v in enumerate(values):
            w.writerow([i, f"{float(v):.6f}"])


def _bimodal_scores(shape, *, split: bool):
    """A score curve with a known tie shape, for the run-log wording tests.

    The objective is replaced rather than coaxed out of cell values: the
    assertion is about what the run *says* when the scan comes out a given
    shape, and ``plateau_pick`` and the log line both still run for real. Under
    the exact candidate grid a genuine tie is rare by construction — each
    candidate sits between two neighbouring pooled values, so crossing one
    always changes a count — which is the improvement, and also why the wording
    has to be pinned through an injected curve.
    """
    scores = np.full(shape, 0.10)
    n = len(scores)
    if split:
        scores[n // 4] = 0.90
        scores[(3 * n) // 4] = 0.90
    else:
        scores[n // 4: n // 4 + 40] = 0.90
    return scores


def test_a_contiguous_plateau_reaches_the_run_log_as_a_span(tmp_path, caplog,
                                                            monkeypatch):
    """A run of equally-scoring cuts is stated, and its midpoint is what is stored.

    ``argmax`` alone reports the bottom of such a run, which is not an arbitrary
    choice among equals: it is the cut set by the brightest cell in the negative
    control, the least stable number in the data. Both facts belong in the log —
    that the answer came off a plateau at all, and which point of it was taken.
    """
    import pipeline.threshold_optimize as mod

    monkeypatch.setattr(
        mod, "supported_separation",
        lambda ka, na, kb, nb, **kw: _bimodal_scores(np.shape(ka), split=False))
    rng = np.random.default_rng(3)
    _write_well_csv(tmp_path / "plate_A01.csv", rng.normal(900, 200, 300))
    _write_well_csv(tmp_path / "plate_H12.csv", rng.normal(300, 200, 300))
    log = logging.getLogger("test_span_wording")
    with caplog.at_level(logging.INFO, logger="test_span_wording"):
        out = mod.estimate_thresholds_from_controls(
            tmp_path, fluor_channels=["GFP"], csv_prefix="plate",
            pos_well="A01", neg_well="H12", log=log)
    assert "40 candidate cut(s) scored that same maximum" in caplog.text
    assert "a contiguous run spanning" in caplog.text
    assert "NOT adjacent on the grid" not in caplog.text
    assert "the midpoint of the run around the best is what was stored" in caplog.text

    # And it really is the midpoint: rebuild the grid the scan used and check
    # the stored value against the middle of the injected run, not against the
    # first index the scan would otherwise have reported.
    pos = read_channel_values(tmp_path / "plate_A01.csv", "gfp")
    neg = read_channel_values(tmp_path / "plate_H12.csv", "gfp")
    grid = candidate_thresholds(pos, neg)
    run = np.flatnonzero(_bimodal_scores(grid.shape, split=False) == 0.90)
    assert out["gfp"] == pytest.approx(
        round_stored_threshold(float(grid[(run[0] + run[-1]) // 2])))
    assert out["gfp"] != pytest.approx(
        round_stored_threshold(float(grid[run[0]])))


def test_two_separated_maxima_are_not_described_as_a_span(tmp_path, caplog,
                                                          monkeypatch):
    """A tie is a plateau only when its points are adjacent on the grid.

    ``tie_span`` is ``max - min`` of the tied thresholds, which for a bimodal
    objective covers the lower-scoring ground between the two maxima. Printing
    that as a span states that every threshold in it scored the best, which is
    the degraded-output-looks-like-good-output failure the plateau line exists
    to prevent.
    """
    import pipeline.threshold_optimize as mod

    monkeypatch.setattr(
        mod, "supported_separation",
        lambda ka, na, kb, nb, **kw: _bimodal_scores(np.shape(ka), split=True))
    rng = np.random.default_rng(3)
    _write_well_csv(tmp_path / "plate_A01.csv", rng.normal(900, 200, 300))
    _write_well_csv(tmp_path / "plate_H12.csv", rng.normal(300, 200, 300))
    log = logging.getLogger("test_separated_maxima")
    with caplog.at_level(logging.INFO, logger="test_separated_maxima"):
        mod.estimate_thresholds_from_controls(
            tmp_path, fluor_channels=["GFP"], csv_prefix="plate",
            pos_well="A01", neg_well="H12", log=log)
    assert "2 candidate cut(s) scored that same maximum" in caplog.text, caplog.text
    assert "NOT adjacent on the grid" in caplog.text, (
        "two separated maxima were reported as if every threshold between "
        f"them had scored the best: {caplog.text!r}"
    )
    assert "contiguous run spanning" not in caplog.text


def test_two_separated_maxima_are_not_reported_as_one_plateau():
    """The same fact at the seam that computes it."""
    thresholds = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
    diffs = np.array([0.1, 0.9, 0.2, 0.3, 0.2, 0.9, 0.1])
    ones = np.ones_like(thresholds)
    result = best_from_curves(thresholds, ones, ones, diffs, n_a=10, n_b=10)
    assert result.tie_count == 2
    assert result.tie_span == pytest.approx(4.0)
    assert not result.tie_contiguous, (
        "thresholds 2.0 and 6.0 tied with 3.0-5.0 scoring lower between them"
    )


def test_one_run_of_adjacent_grid_points_is_contiguous():
    thresholds = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    diffs = np.array([0.1, 0.9, 0.9, 0.9, 0.2])
    ones = np.ones_like(thresholds)
    result = best_from_curves(thresholds, ones, ones, diffs, n_a=10, n_b=10)
    assert result.tie_count == 3
    assert result.tie_contiguous


def test_a_lone_maximum_is_trivially_contiguous():
    thresholds = np.array([1.0, 2.0, 3.0])
    diffs = np.array([0.1, 0.9, 0.2])
    ones = np.ones_like(thresholds)
    result = best_from_curves(thresholds, ones, ones, diffs, n_a=10, n_b=10)
    assert result.tie_count == 1
    assert result.tie_contiguous


def test_a_single_best_threshold_says_nothing_about_a_plateau(tmp_path, caplog):
    rng = np.random.default_rng(0)
    _write_well_csv(tmp_path / "plate_A01.csv", rng.normal(600, 250, 500))
    _write_well_csv(tmp_path / "plate_H12.csv", rng.normal(400, 200, 500))
    log = logging.getLogger("test_no_plateau")
    with caplog.at_level(logging.INFO, logger="test_no_plateau"):
        estimate_thresholds_from_controls(
            tmp_path, fluor_channels=["GFP"], csv_prefix="plate",
            pos_well="A01", neg_well="H12", log=log)
    assert "is shared by" not in caplog.text


# ── C11: one definition of "is this one of the run's images" ─────────────────

def _out_zip(path, names):
    """An output archive carrying *names* plus the mess a real one carries."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("A01/", b"")                       # a directory entry
        zf.writestr(".DS_Store", b"\x00\x01")
        zf.writestr("._exp_GFP_A01_1_0_tophat.tif", b"AppleDouble")
        zf.writestr("__MACOSX/._exp_GFP_A01_1_0_tophat.tif", b"AppleDouble")
        zf.writestr("notes.txt", b"hello")
        for name in names:
            zf.writestr(name, b"not really an image")
    return path


def test_png_output_members_are_indexed(tmp_path):
    """A PNG well was invisible, and invisible in a way nothing could report."""
    path = _out_zip(tmp_path / "A01_out.zip", [
        "exp_GFP_A01_1_0_tophat.png",
        "exp_DAPI_A01_1_0_labels.png",
    ])
    reader = _SchemaReader("experiment:channel:well:fov:timepoint", "_")
    with zipfile.ZipFile(path) as zf:
        labels, tophats, unplaceable = _index_members(zf, reader, {"gfp"})
    assert labels == {("1", "0"): "exp_DAPI_A01_1_0_labels.png"}
    assert tophats == {("gfp", "1", "0"): "exp_GFP_A01_1_0_tophat.png"}
    assert unplaceable == []


def test_a_schema_mismatch_is_still_reported_for_png(tmp_path):
    """The evidence behind the "could not be placed" warning must survive too.

    The members were dropped before the role/schema check, so ``unplaceable``
    stayed empty and the warning naming the schema could not fire at all.
    """
    path = _out_zip(tmp_path / "A01_out.zip", ["nonsense_labels.png"])
    reader = _SchemaReader("experiment:channel:well:fov:timepoint", "_")
    with zipfile.ZipFile(path) as zf:
        labels, _tophats, unplaceable = _index_members(zf, reader, {"gfp"})
    assert labels == {}
    assert unplaceable == ["nonsense"]


def test_the_mess_in_a_real_archive_is_never_indexed(tmp_path):
    """Sidecars, dotfiles, directory entries and non-images are not members."""
    path = _out_zip(tmp_path / "A01_out.zip", ["exp_DAPI_A01_1_0_labels.tif"])
    reader = _SchemaReader("experiment:channel:well:fov:timepoint", "_")
    with zipfile.ZipFile(path) as zf:
        labels, tophats, unplaceable = _index_members(zf, reader, {"gfp"})
    assert labels == {("1", "0"): "exp_DAPI_A01_1_0_labels.tif"}
    assert tophats == {}
    assert unplaceable == []


# ── the rule holds on every estimator, including the interactive one ─────────

def test_every_otsu_estimator_uses_the_one_rounding_rule():
    """Three estimators answer "what threshold?", not two.

    The pipeline's control-well and Otsu passes were fixed and the Cell Gating
    tab's own Auto-threshold button was not, so the defect stayed live on the
    path a user reaches by pressing a button: a 0-1 normalised channel's Otsu
    threshold of 0.2496 was stored as 0.0, which gates 100% of cells and is
    also the value meaning "never measured".
    """
    import ast
    import inspect

    import review.data.auto_threshold as viewer_at

    src = inspect.getsource(viewer_at)
    assert "_threshold_otsu" in src and len(src) > 5_000, len(src)

    tree = ast.parse(src)
    bare = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name) and n.func.id == "round"
    ]
    assert not bare, (
        f"lines {bare} round a threshold with the builtin instead of "
        "pipeline.threshold_optimize.round_stored_threshold, so a sub-1 "
        "channel's gate is stored as 0.0"
    )
    assert "round_stored_threshold" in src


def test_the_viewers_estimator_keeps_a_sub_one_gate(monkeypatch):
    """Driven through the real function, not asserted from its source."""
    import numpy as np

    import review.data.auto_threshold as viewer_at

    rng = np.random.default_rng(3)
    values = np.concatenate([rng.normal(0.15, 0.03, 300),
                             rng.normal(0.70, 0.05, 300)])
    monkeypatch.setattr(viewer_at, "_threshold_otsu",
                        lambda arr: float(np.float64(0.24962132375636537)))
    out = viewer_at.thresholds_from_samples(
        ["gfp"], {"gfp": list(values)}, {}, {}, lambda _m: None)
    assert out["gfp"] > 0.0, (
        "a 0-1 channel's gate was stored as 0.0, which passes every cell and "
        "is the value that also means 'this channel was never measured'"
    )
    assert out["gfp"] == pytest.approx(round_stored_threshold(0.24962132375636537))


# ── the two estimators the module promises cannot disagree ──────────────────

@pytest.mark.parametrize("seed_a, seed_b, kind", [
    (1, 2, "normal"),
    (4, 5, "lognormal"),
    (11, 12, "separated"),
])
def test_the_viewer_and_the_pipeline_choose_the_same_threshold(seed_a, seed_b, kind):
    """`threshold_optimize`'s own docstring says they cannot differ.

    They did, twice over. The pipeline passed population sizes to `relative_gap`
    and the Cell Gating tab's scan did not — and then the two grids differed as
    well, the tab scanning a user-typed linspace and the pipeline a percentile-
    clipped one. Measured before the fix: grid points 1104 vs 1209 on
    overlapping populations, 1231 vs 2366 on a skewed pair.

    Both entry points now reduce to `optimize_between_populations`, so this
    drives each one as its own caller does — a DataFrame through the tab's
    helper, arrays through the pipeline's — rather than reassembling either
    scan here. Reassembling it is what let the two drift in the first place: a
    test that rebuilds the viewer's scan by hand agrees with whatever it
    rebuilt, not with what the tab runs.
    """
    import pandas as pd

    from review.features.home.cell_gating.gating_compute import (
        _optimize_thresh_frac_on_between_wells,
    )

    ra, rb = np.random.default_rng(seed_a), np.random.default_rng(seed_b)
    if kind == "normal":
        a, b = ra.normal(600, 250, 300), rb.normal(400, 200, 300)
    elif kind == "lognormal":
        a, b = ra.lognormal(6, 0.8, 400), rb.lognormal(5.6, 0.8, 400)
    else:
        a, b = ra.normal(2000, 100, 400), rb.normal(200, 20, 400)

    def _frame(values):
        return pd.DataFrame({"area_px": [100] * len(values),
                             "Included": [1] * len(values),
                             "gfp_mean_intensity": values})

    lo, hi = float(min(a.min(), b.min())), float(max(a.max(), b.max()))
    viewer = _optimize_thresh_frac_on_between_wells(
        _frame(a), _frame(b), channel_key="gfp",
        min_threshold=lo, max_threshold=hi)
    pipeline = optimize_between_populations(a, b, min_threshold=lo, max_threshold=hi)
    assert viewer.threshold == pytest.approx(pipeline.threshold), (
        f"the Cell Gating tab would report {viewer.threshold!r} and the "
        f"pipeline {pipeline.threshold!r} for the same two populations"
    )
    assert viewer.difference == pytest.approx(pipeline.difference)
    assert viewer.objective == pipeline.objective
    assert (viewer.k_a, viewer.k_b) == (pipeline.k_a, pipeline.k_b)


def test_the_fold_changed_branch_stays_unsmoothed():
    """Not an oversight: those fractions are not `k / n` any more.

    `scale_bar_value` adjusts each fraction against a control or a baseline, so
    the result exceeds 1 and goes negative — there is no `k` to add one to, and
    smoothing it would be arithmetic on a quantity that is no longer a count.
    """
    from review.features.home.cell_gating.gating_compute import (
        scan_fc_fractions,
    )

    rng = np.random.default_rng(7)
    a, b = rng.normal(600, 250, 300), rng.normal(400, 200, 300)
    ctrl = rng.normal(300, 150, 300)
    thresholds = sweep_thresholds(*sweep_bounds(a, b), 256)
    _fa, _fb, fc_diffs = scan_fc_fractions(thresholds, a, b, ctrl_values=ctrl)
    raw_fa, raw_fb, _ = scan_fc_fractions(thresholds, a, b)
    assert not np.allclose(fc_diffs, relative_gap(raw_fa, raw_fb,
                                                 n_a=a.size, n_b=b.size)), (
        "the fixture no longer distinguishes the two branches")
    assert np.allclose(fc_diffs, relative_gap(_fa, _fb)), (
        "the fold-changed branch was smoothed; its fractions are not counts")
