"""Per-field image-quality metrics (``pipeline.field_quality``).

Most of this file exists to keep the focus statistic honest. The obvious
choice — variance of the Laplacian over mean squared — was specified first and
measured second, and the measurement disqualified it: across a thirty-fold
range of cell count, in perfect focus throughout, it spans 6.7x. A focus metric
that moves with cell density reports the wells where a cytotoxic compound
worked as out of focus, which is the worst thing a quality layer can do.

So the tests below are built so that a right and a wrong statistic give
different answers (CLAUDE.md §7.8 rule 2): each varies one thing the metric must
ignore, across a range wide enough that a confounded metric cannot hide.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter, laplace

from pipeline.field_quality import (
    ACQUISITION_METRICS, BIOLOGICAL_METRICS, METRIC_KEYS,
    background_level, dynamic_range, field_metrics,
    saturated_fraction,
)

N = 256


def _frame(n_cells=120, defocus=0.0, gain=1.0, pedestal=100.0, seed=0,
           sigma=1.2):
    """A field with the mess real data has: shot noise and a sensor pedestal."""
    r = np.random.default_rng(seed)
    img = np.zeros((N, N), float)
    yy, xx = np.mgrid[0:N, 0:N]
    for y, x in zip(r.integers(14, N - 14, n_cells),
                    r.integers(14, N - 14, n_cells)):
        sl = (slice(max(0, y - 16), y + 16), slice(max(0, x - 16), x + 16))
        d2 = (yy[sl] - y) ** 2 + (xx[sl] - x) ** 2
        img[sl] += 3000.0 * np.exp(-d2 / (2 * sigma ** 2))
    if defocus > 0:
        img = gaussian_filter(img, defocus)
    return r.poisson(np.clip(img * gain + pedestal, 0, None)).astype(np.float32)


def _laplacian_over_mean_squared(image):
    """The statistic that was specified and then disqualified. Kept so the
    tests can show what they exclude rather than assert it."""
    arr = np.asarray(image, float)
    return float(np.var(laplace(arr)) / np.mean(arr) ** 2)


# ── focus, measured at each nucleus's own outline ───────────────────────────
#
# The first implementation was a normalised Tenengrad over the whole frame,
# and two measurements retired it. Nucleus size moved it 7.8x over a fourfold
# radius range against a defocus signal of 3.7x -- a confounder twice the
# size of the signal, with sharp and blurred frames overlapping completely.
# And a frame with half of it blurred at sigma 2 moved it only 14%, against a
# flag that fires at five MADs from the plate median: it missed the case the
# whole layer is for.
#
# ``edge_focus`` measures at the nucleus outline instead, because defocus is a
# property of the OPTICS -- it sets the width of the intensity transition at
# an edge, and that width is the same whatever the object's size. Every test
# below is built so a right and a wrong statistic give different answers.


def _discs(n_cells=14, radius=8.0, defocus=0.0, gain=1.0, pedestal=100.0,
           noise=25.0, seed=0, size_px=256):
    """A labelled field of discs -- an edge the estimator can actually read."""
    r = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size_px, 0:size_px]
    lab = np.zeros((size_px, size_px), np.int32)
    img = np.zeros((size_px, size_px), float)
    placed, k = [], 0
    for _ in range(n_cells * 12):
        if len(placed) >= n_cells:
            break
        y, x = r.integers(int(radius) + 12, size_px - int(radius) - 12, 2)
        if any((y - py) ** 2 + (x - px) ** 2 < (3.0 * radius) ** 2
               for py, px in placed):
            continue
        placed.append((y, x))
        k += 1
        d = np.sqrt((yy - y) ** 2 + (xx - x) ** 2)
        lab[d <= radius] = k
        img += 3000.0 * (d <= radius)
    img = gaussian_filter(img, 0.8)          # the objective's own PSF, always
    if defocus:
        img = gaussian_filter(img, defocus)
    img = img * gain + pedestal + r.normal(0, noise * gain, img.shape)
    return img, lab


def _field(**kw):
    """The field's focus: the median of its cells' outlines."""
    from pipeline.field_quality import cell_focus_by_label, field_focus
    img, lab = _discs(**kw)
    return field_focus(cell_focus_by_label(img, lab).values())


def test_focus_is_invariant_to_exposure_and_to_a_sensor_pedestal():
    """Excludes any statistic whose normalisation does not normalise.

    ``var(laplace)/mean**2`` -- the first thing specified -- spans 30x on gain
    and ~700x on pedestal, because shot-noise variance scales with the gain
    rather than its square and the Laplacian amplifies it. Dividing by the
    cell's own inside-minus-outside contrast removes both exactly.
    """
    vals = [_field(gain=g) for g in (0.25, 0.5, 1.0, 2.0, 4.0)]
    assert max(vals) / min(vals) < 1.10
    vals = [_field(pedestal=p) for p in (0, 50, 100, 400, 1000)]
    assert max(vals) / min(vals) < 1.10

    rejected = [_laplacian_over_mean_squared(_discs(gain=g)[0])
                for g in (0.25, 4.0)]
    assert max(rejected) / min(rejected) > 5, (
        "the fixture must be wide enough that a confounded metric cannot hide")


def test_focus_does_not_track_how_many_cells_are_in_the_field():
    """A metric that moves with cell count reports the wells where a cytotoxic
    compound worked as out of focus."""
    vals = [_field(n_cells=n) for n in (6, 14, 24)]
    assert max(vals) / min(vals) < 1.15


def test_focus_does_not_track_how_BIG_the_nuclei_are():
    """The measurement that retired the whole-frame statistic.

    Over a fourfold radius range the frame-level Tenengrad spans 7.8x while
    the entire defocus signal spans 3.7x — so on a plate with two cell lines,
    or where confluence changes nuclear size between conditions, it reads the
    difference as focus. Measuring at the outline is what fixes it, and the
    bar here is set below the defocus response asserted two tests down.
    """
    vals = [_field(radius=r, n_cells=10) for r in (4.0, 6.0, 8.0, 12.0, 16.0)]
    assert max(vals) / min(vals) < 1.5

    # And the fixture really does span a size range a confounded metric could
    # not survive, or the assertion above says nothing (§7.8 rule 2).
    blind = [float(np.var(laplace(_discs(radius=r, n_cells=10)[0])))
             for r in (4.0, 16.0)]
    # 2.2x on this 256px fixture. The bar is what *this* fixture produces, not
    # the better number the module docstring's 512px table reports, so the
    # test says something true about the frames it actually builds.
    assert max(blind) / min(blind) > 2


def test_focus_falls_monotonically_with_defocus_and_by_enough_to_matter():
    """Monotone to σ=3; beyond that it flattens rather than continuing down.

    Stated as what it is rather than as what would read better. On this 256 px
    fixture σ=4 comes back a hair *above* σ=3 (0.0467 against 0.0452); on a
    320 px one with the same discs it stays below. The cause is the same in
    both: past σ≈3 the outline has spread beyond the ring that reads it and
    the background ring is picking up the neighbouring cells, so the estimator
    is measuring less and less of an edge.

    That bound is acceptable and it is bounded: severe defocus is also where
    segmentation fails, so there is often no outline to read at all, and the
    ranking that matters — in focus against out of it — is intact with a
    factor of seven between them. It is asserted here so nobody reads the
    column as a severity scale past σ=3.
    """
    vals = [_field(defocus=d) for d in (0.0, 0.5, 1.0, 1.5, 2.0, 3.0)]
    assert vals == sorted(vals, reverse=True), vals
    assert vals[0] / vals[-1] > 5, "and by enough to be a signal"

    # And past the monotone range it must still not climb back toward sharp.
    beyond = _field(defocus=4.0)
    assert beyond < vals[vals.index(max(vals))] / 5


def test_sharp_and_defocused_fields_do_not_overlap_across_size_and_count():
    """The discriminative claim, stated as the separation it actually is.

    What matters is not that the metric responds to defocus but that the
    response is bigger than the spread caused by everything it must ignore --
    which is exactly where the frame-level statistic failed.
    """
    sharp = [_field(radius=r, n_cells=n, seed=s)
             for r in (4.0, 8.0, 16.0) for n in (8, 20) for s in (0, 1)]
    soft = [_field(radius=r, n_cells=n, seed=s, defocus=2.0)
            for r in (4.0, 8.0, 16.0) for n in (8, 20) for s in (0, 1)]
    assert min(sharp) > max(soft)
    assert min(sharp) / max(soft) > 2.0, "and with room to put a cut between"


def test_a_noisy_dim_cell_does_not_read_as_sharper_than_it_is():
    """Excludes the raw boundary Laplacian, without its noise floor removed.

    Noise adds Laplacian energy, so without subtracting the background ring's
    own contribution a dim noisy cell reads as *sharper* — the false-negative
    direction, and the same defect that disqualified ``var(laplace)/mean**2``
    at frame scale. Measured, the drift over this range is 1.86x uncorrected
    and 1.31x corrected.
    """
    vals = [_field(noise=q) for q in (10.0, 25.0, 75.0, 150.0)]
    assert max(vals) / min(vals) < 1.4

    # The defect, reproduced: skip the floor subtraction and the same range
    # moves further. Without this the bar above could be met by a metric that
    # ignores noise because it ignores the image.
    from scipy.ndimage import binary_dilation, binary_erosion
    raw = []
    for q in (10.0, 150.0):
        img, lab = _discs(noise=q)
        m = lab == 1
        band = binary_dilation(m, iterations=3) & ~binary_erosion(m, iterations=3)
        far = binary_dilation(m, iterations=6) & ~binary_dilation(m, iterations=3)
        deep = binary_erosion(m, iterations=4)
        c = float(np.median(img[deep]) - np.median(img[far]))
        raw.append(float(np.percentile(np.abs(laplace(img))[band], 95)) / c)
    assert max(raw) / min(raw) > 1.5


def test_an_outline_that_cannot_be_measured_reports_nothing_rather_than_zero():
    """Undefined is not zero (§7 #3): a cell nobody could measure and a cell
    measured and found soft must not read alike."""
    from pipeline.field_quality import edge_focus, field_focus

    flat = np.full((40, 40), 7.0, np.float32)
    disc = np.zeros((40, 40), bool)
    yy, xx = np.mgrid[0:40, 0:40]
    disc[np.sqrt((yy - 20) ** 2 + (xx - 20) ** 2) <= 8] = True
    assert edge_focus(flat, disc) is None, "no contrast is no measurement"
    # A mask darker than its surroundings does not describe this image.
    assert edge_focus(-flat, disc) is None
    assert edge_focus(np.zeros((4, 4)), np.ones((4, 4), bool)) is None
    assert field_focus([]) is None
    assert field_focus([None, None]) is None
    assert field_focus([None, 0.3, 0.5]) == 0.4


def test_every_nucleus_the_pipeline_keeps_is_measurable():
    """Excludes the ring that leaves the smallest nuclei with no interior.

    A fixed 3 px ring needs an interior after eroding by ``w + 1``, and at the
    pipeline's own ``min_nucleus_area_px`` of 50 (about 4 px radius) there is
    none — so a plate of small nuclei, a low magnification or a binned camera
    carried no focus measurement at all, silently. The ring scales with the
    nucleus now.
    """
    from pipeline.field_quality import cell_focus_by_label

    for radius in (4.0, 5.0, 6.0, 8.0, 16.0):
        img, lab = _discs(radius=radius, n_cells=8)
        got = cell_focus_by_label(img, lab)
        measured = [v for v in got.values() if v is not None]
        assert got and len(measured) == len(got), (
            f"radius {radius}: {len(measured)}/{len(got)} measurable")


# ── saturation: computable only where the dtype still exists ────────────────

def test_saturation_is_none_when_the_caller_cannot_say_what_the_maximum_was():
    """The defect this signature exists to prevent.

    ``pipeline.io.load_2d_gray`` casts every input to float32 unconditionally,
    so a saturation metric computed at quantification time against "the dtype
    maximum" compares to 3.4e38 and is **identically zero for every field of
    every plate, for ever** — a number stored where nothing was computed.
    """
    assert saturated_fraction(_frame(), dtype_max=None) is None


def test_saturation_counts_pixels_at_the_sensor_maximum():
    arr = np.zeros((10, 10), np.uint16)
    arr[:2, :] = 65535
    assert saturated_fraction(arr, dtype_max=65535) == pytest.approx(0.2)
    assert saturated_fraction(arr, dtype_max=65535.0) == pytest.approx(0.2)


def test_an_empty_frame_has_no_saturation_fraction():
    assert saturated_fraction(np.zeros((0, 0), np.uint16), dtype_max=65535) is None


# ── spread and background ───────────────────────────────────────────────────

def test_dynamic_range_is_not_decided_by_one_hot_pixel():
    """Excludes ``max - min``. One dead pixel is not a dynamic range."""
    arr = np.full((64, 64), 100.0, np.float32)
    arr[:32] = 200.0
    arr[0, 0] = 60000.0
    assert dynamic_range(arr) == pytest.approx(100.0, abs=1.0)


def test_background_is_measured_outside_the_nuclei_when_a_mask_is_given():
    arr = np.full((32, 32), 50.0, np.float32)
    labels = np.zeros((32, 32), np.int32)
    labels[:16] = 1
    arr[:16] = 5000.0
    assert background_level(arr, labels) == pytest.approx(50.0)
    # With no mask it is the whole frame, which is a different number and an
    # honest one.
    assert background_level(arr) == pytest.approx(2525.0)


def test_a_mask_of_the_wrong_shape_is_ignored_rather_than_crashing():
    """A mask that does not fit the frame is a known real state — the
    Playground's read-out reports it — and it must not take a run down."""
    arr = np.full((8, 8), 3.0, np.float32)
    assert background_level(arr, np.zeros((4, 4), np.int32)) == pytest.approx(3.0)


# ── the record ──────────────────────────────────────────────────────────────

def test_a_field_record_carries_every_declared_metric():
    """Excludes the record that quietly drops a key. The roster is named once
    so the writer, the reader and the scorer cannot drift."""
    rec = field_metrics(_frame(), labels=np.zeros((N, N), np.int32),
                        dtype_max=None, areas=[10, 20, 30])
    assert set(rec) == set(METRIC_KEYS)


def test_every_metric_is_either_a_number_or_absent_never_a_stand_in():
    rec = field_metrics(np.zeros((4, 4), np.float32), labels=None,
                        dtype_max=None, areas=None)
    assert rec["focus"] is None
    assert rec["nuc_count"] is None, "no label image means no count, not zero"
    assert rec["nuc_area_median"] is None


def test_a_label_image_with_no_nuclei_counts_zero_rather_than_nothing():
    """The other side of the line: zero nuclei *is* a measurement when there
    was a label image to measure."""
    rec = field_metrics(_frame(), labels=np.zeros((N, N), np.int32),
                        dtype_max=None)
    assert rec["nuc_count"] == 0


def test_the_two_metric_groups_are_disjoint_and_cover_the_roster():
    """Excludes the split that leaves a metric in neither group — which would
    be a metric nothing decides how to score."""
    assert not set(ACQUISITION_METRICS) & set(BIOLOGICAL_METRICS)
    assert set(ACQUISITION_METRICS) | set(BIOLOGICAL_METRICS) == set(METRIC_KEYS)


# ── the record on disk, and the gap it must not hide ────────────────────────

def test_a_record_round_trips_through_the_collected_block(tmp_path):
    from pipeline.field_quality import (
        collect_field_quality, records_for_scoring, write_well_field_quality,
    )

    fields = [{"fov": i, "timepoint": "0h", "focus": 27.0 + i} for i in range(4)]
    write_well_field_quality(tmp_path, "A01", fields=fields, measured_of=4)
    block = collect_field_quality(tmp_path)

    assert block["partial_wells"] == []
    assert block["measured"] == 4 and block["measured_of"] == 4
    rows = records_for_scoring(block)
    assert len(rows) == 4
    assert rows[0]["well"] == "A01"
    assert rows[0]["key"] == ("A01", 0, "0h")
    assert rows[0]["partial_plate"] is False


def test_a_well_measured_in_part_is_recorded_as_partial(tmp_path):
    """The gap a resumed or incremental run leaves.

    ``_process_or_resume`` returns cached records without calling
    ``process_image_group`` at all, and ``well_skip.incremental_for_well``
    skips whole wells — so a block can cover a biased subset while looking
    exactly like a block that covered the plate.
    """
    from pipeline.field_quality import (
        collect_field_quality, records_for_scoring, write_well_field_quality,
    )

    write_well_field_quality(tmp_path, "A01",
                             fields=[{"fov": 0, "timepoint": "0h"}],
                             measured_of=8)
    block = collect_field_quality(tmp_path)
    assert block["partial_wells"] == ["A01"]
    assert all(r["partial_plate"] for r in records_for_scoring(block))


def test_a_record_with_no_expected_total_is_not_called_partial(tmp_path):
    """Undefined is not a shortfall. A caller that cannot say how many fields
    a well holds must not have that read as "some are missing"."""
    from pipeline.field_quality import (
        collect_field_quality, write_well_field_quality,
    )

    write_well_field_quality(tmp_path, "A01",
                             fields=[{"fov": 0}], measured_of=None)
    assert collect_field_quality(tmp_path)["partial_wells"] == []


def test_collecting_from_a_directory_that_never_ran_is_empty_not_an_error(tmp_path):
    from pipeline.field_quality import collect_field_quality, records_for_scoring

    block = collect_field_quality(tmp_path)
    assert block["wells"] == {} and block["partial_wells"] == []
    assert records_for_scoring(block) == []
    assert records_for_scoring(None) == []


def test_a_corrupt_per_well_file_is_skipped_rather_than_taking_the_block_down(tmp_path):
    from pipeline.field_quality import (
        FIELD_QUALITY_DIRNAME, collect_field_quality, write_well_field_quality,
    )

    write_well_field_quality(tmp_path, "A01", fields=[{"fov": 0}], measured_of=1)
    (tmp_path / FIELD_QUALITY_DIRNAME / "B02.json").write_text("{not json")
    block = collect_field_quality(tmp_path)
    assert set(block["wells"]) == {"A01"}


def test_a_record_that_cannot_be_written_does_not_fail_the_run(tmp_path,
                                                               monkeypatch):
    """Best-effort, like run_report: a run that cannot write its own
    bookkeeping must still produce data.

    The failure is injected rather than provoked with a read-only directory:
    the suite runs as root here, where the permission bits do not apply and
    the write succeeds — a test that passes because the failure never happened
    proves nothing about the handler.
    """
    # Patched on the module that OWNS the writer, not on the one that
    # re-exports it: a re-export is a binding, so patching ``tempfile``
    # through ``field_quality`` reaches a name the writer no longer resolves.
    import pipeline.field_quality as fq
    import pipeline.field_quality_record as fqr

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(fqr.tempfile, "mkstemp", _boom)
    assert fq.write_well_field_quality(tmp_path, "A01", fields=[],
                                       measured_of=1) is None


def test_the_staging_directory_is_hidden_so_it_is_not_dataset_output():
    from pipeline.field_quality import FIELD_QUALITY_DIRNAME
    assert FIELD_QUALITY_DIRNAME.startswith(".")


def test_the_smallest_nuclei_carry_a_known_bias_and_it_is_bounded():
    """**A limitation, pinned so it cannot quietly get worse.**

    At the pipeline's smallest accepted nucleus (``min_nucleus_area_px`` 50,
    about 4 px radius) the interior left after eroding the ring is one or two
    pixels wide and sits right against the transition, so it cannot give a
    noise estimate separate from the edge. ``edge_focus`` falls back to the
    background-only floor there, which is the biased estimator the two-sided
    one replaced — and the bias reads *sharper*.

    Measured, that makes the score **non-monotone at radius 4**: it falls from
    sharp to one stop of defocus and then rises again. The fallback is
    preferred to the alternative, which was returning ``None`` for every cell
    at two stops of defocus — going silent at exactly the defocus the metric
    exists to catch.

    So this asserts what is actually true rather than what would be nice: at
    radius 4 the sharp field still reads far above every defocused one, and
    the rise among the defocused values stays inside a stated bound. A change
    that widens it fails here instead of shipping quietly.
    """
    sharp = _field(radius=4.0, n_cells=20, seed=0, defocus=0.0)
    soft = [_field(radius=4.0, n_cells=20, seed=0, defocus=d)
            for d in (1.0, 2.0, 3.0)]
    assert all(v is not None for v in soft), (
        "the fallback exists so the smallest nuclei never go silent")
    # The sharp-versus-soft separation survives, which is what the gate needs.
    assert sharp > max(soft) * 2.0, (sharp, soft)
    # And the known rise among the soft values is bounded. It is a rise -- the
    # bias is real -- so this is a ceiling on it, not a monotonicity claim.
    assert max(soft) / min(soft) < 2.5, soft

    # Larger nuclei have an interior to measure and stay monotone, which is
    # what localises the defect rather than leaving it a property of the
    # estimator as a whole.
    big = [_field(radius=8.0, n_cells=20, seed=0, defocus=d)
           for d in (0.0, 1.0, 2.0, 3.0)]
    assert all(big[i] > big[i + 1] for i in range(len(big) - 1)), big
