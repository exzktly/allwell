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
    estimate_thresholds_from_controls,
    frac_curve,
    optimize_between_populations,
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
def test_one_outlying_control_cell_no_longer_moves_the_plate_gate(dtype):
    """Moving a single negative-control cell moved the threshold by ~200 units."""
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


def test_the_plateau_reaches_the_run_log(tmp_path, caplog):
    """The tie is only useful if it is printed, so assert the line, not the field.

    A bimodal positive population against a tight negative one leaves the two
    fractions constant across the whole gap between the modes, so hundreds of
    grid points score identically and the reported threshold is the lowest of
    them. That is exactly the case the reader has to be told about.
    """
    rng = np.random.default_rng(11)
    pos = np.concatenate([rng.normal(200, 20, 200), rng.normal(2000, 100, 200)])
    neg = rng.normal(200, 20, 400)
    _write_well_csv(tmp_path / "plate_A01.csv", pos)
    _write_well_csv(tmp_path / "plate_H12.csv", neg)
    log = logging.getLogger("test_plateau")
    with caplog.at_level(logging.INFO, logger="test_plateau"):
        out = estimate_thresholds_from_controls(
            tmp_path, fluor_channels=["GFP"], csv_prefix="plate",
            pos_well="A01", neg_well="H12", log=log)
    assert "gfp" in out
    assert "is shared by" in caplog.text
    assert "grid point" in caplog.text
    # The range it names really is the range of the tied thresholds, and the
    # value it reports really is the lowest of them.
    thresholds = sweep_thresholds(*sweep_bounds(pos, neg), 512)
    fa, fb = frac_curve(pos, thresholds), frac_curve(neg, thresholds)
    diffs = relative_gap(fa, fb, n_a=pos.size, n_b=neg.size)
    tied = thresholds[diffs == diffs.max()]
    assert f"{len(tied)} of 512" in caplog.text
    # The bounds are printed through the same rounding as the stored value, so
    # the headline and the plateau line cannot name the same threshold twice
    # with two different numbers.
    assert f"{round_stored_threshold(float(tied.min())):.4g}" in caplog.text
    assert f"{round_stored_threshold(float(tied.max())):.4g}" in caplog.text
    assert out["gfp"] == pytest.approx(round_stored_threshold(float(tied.min())))


def test_a_contiguous_plateau_is_described_as_a_span(tmp_path, caplog):
    """The wording has to match the shape: adjacent grid points do span a range."""
    rng = np.random.default_rng(11)
    pos = np.concatenate([rng.normal(200, 20, 200), rng.normal(2000, 100, 200)])
    neg = rng.normal(200, 20, 400)
    _write_well_csv(tmp_path / "plate_A01.csv", pos)
    _write_well_csv(tmp_path / "plate_H12.csv", neg)
    thresholds = sweep_thresholds(*sweep_bounds(pos, neg), 512)
    fa, fb = frac_curve(pos, thresholds), frac_curve(neg, thresholds)
    result = best_from_curves(thresholds, fa, fb,
                              relative_gap(fa, fb, n_a=pos.size, n_b=neg.size),
                              n_a=pos.size, n_b=neg.size)
    assert result.tie_count > 1
    assert result.tie_contiguous, "this fixture is meant to produce one run"
    log = logging.getLogger("test_span_wording")
    with caplog.at_level(logging.INFO, logger="test_span_wording"):
        estimate_thresholds_from_controls(
            tmp_path, fluor_channels=["GFP"], csv_prefix="plate",
            pos_well="A01", neg_well="H12", log=log)
    assert "spanning" in caplog.text
    assert "not adjacent on the grid" not in caplog.text


def test_two_separated_maxima_are_not_described_as_a_span(tmp_path, caplog,
                                                          monkeypatch):
    """A tie is a plateau only when its points are adjacent on the grid.

    ``tie_span`` is ``max - min`` of the tied thresholds, which for a bimodal
    objective covers the lower-scoring ground between the two maxima. Printing
    that as a span states that every threshold in it scored the best, which is
    the degraded-output-looks-like-good-output failure the plateau line exists
    to prevent.

    The objective is replaced rather than reverse-engineered from cell values:
    the assertion is about what the run *says* when the scan comes out bimodal,
    and ``best_from_curves`` and the log line both still run for real.
    """
    import pipeline.threshold_optimize as mod

    def bimodal(fa, fb, *, n_a=None, n_b=None):
        diffs = np.full(np.asarray(fa).shape, 0.10)
        diffs[len(diffs) // 4] = 0.90
        diffs[(3 * len(diffs)) // 4] = 0.90
        return diffs

    monkeypatch.setattr(mod, "relative_gap", bimodal)
    rng = np.random.default_rng(3)
    _write_well_csv(tmp_path / "plate_A01.csv", rng.normal(900, 200, 300))
    _write_well_csv(tmp_path / "plate_H12.csv", rng.normal(300, 200, 300))
    log = logging.getLogger("test_separated_maxima")
    with caplog.at_level(logging.INFO, logger="test_separated_maxima"):
        mod.estimate_thresholds_from_controls(
            tmp_path, fluor_channels=["GFP"], csv_prefix="plate",
            pos_well="A01", neg_well="H12", log=log)
    assert "is shared by 2 of" in caplog.text, caplog.text
    assert "not adjacent on the grid" in caplog.text, (
        "two separated maxima were reported as if every threshold between "
        f"them had scored the best: {caplog.text!r}"
    )
    assert "spanning" not in caplog.text


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
