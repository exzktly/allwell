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
    background_level, dynamic_range, field_metrics, focus_score,
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


# ── focus: the three things it must ignore, and the one it must not ─────────

def test_focus_is_invariant_to_exposure():
    """Excludes ``var(laplace)/mean**2``, which spans 30x over this range.

    Shot-noise variance scales with the gain, not with its square, and the
    Laplacian amplifies it — so the "normalisation" does not normalise.
    """
    values = [focus_score(_frame(gain=g)) for g in (0.25, 0.5, 1.0, 2.0, 4.0)]
    assert max(values) / min(values) < 1.10

    rejected = [_laplacian_over_mean_squared(_frame(gain=g))
                for g in (0.25, 4.0)]
    assert max(rejected) / min(rejected) > 5, (
        "the fixture must be wide enough that a confounded metric cannot hide")


def test_focus_is_invariant_to_an_additive_sensor_pedestal():
    """Excludes the same statistic, which spans ~700x over this range.

    Every sCMOS has an offset; the rejected metric's denominator absorbs it
    while its numerator does not.
    """
    values = [focus_score(_frame(pedestal=p)) for p in (0, 50, 100, 400, 1000)]
    assert max(values) / min(values) < 1.10

    rejected = [_laplacian_over_mean_squared(_frame(pedestal=p))
                for p in (0, 1000)]
    assert max(rejected) / min(rejected) > 50


def test_focus_does_not_track_how_many_cells_are_in_the_field():
    """The finding that decided the statistic, and the reason this layer can
    be trusted near a cytotoxic compound.

    A metric that moves with cell count reports the wells where the treatment
    worked as out of focus.
    """
    values = [focus_score(_frame(n_cells=n)) for n in (20, 60, 120, 300)]
    assert max(values) / min(values) < 1.10

    rejected = [_laplacian_over_mean_squared(_frame(n_cells=n))
                for n in (20, 300)]
    # 2.9x on this 256px fixture; 6.7x on the 512px frames the module
    # docstring's table was measured on. The bar is set to what *this* fixture
    # produces rather than to the better number, so the test says something
    # true about the frames it actually builds.
    assert max(rejected) / min(rejected) > 2.5


def test_focus_falls_monotonically_with_defocus():
    values = [focus_score(_frame(defocus=d)) for d in (0.0, 1.0, 2.0, 4.0)]
    assert values == sorted(values, reverse=True)
    assert values[0] / values[-1] > 4, "and by enough to be a signal"


def test_sharp_and_defocused_fields_do_not_overlap_across_cell_counts():
    """The discriminative claim, stated as the separation it actually is.

    Excludes the metric that merely *responds* to defocus: what matters is
    whether the response is bigger than the spread caused by everything the
    metric is supposed to ignore.
    """
    sharp = [focus_score(_frame(n_cells=n, seed=s))
             for n in (20, 120, 300) for s in range(2)]
    blurred = [focus_score(_frame(n_cells=n, defocus=2.0, seed=s))
               for n in (20, 120, 300) for s in range(2)]
    assert min(sharp) > max(blurred)


def test_a_frame_with_no_variance_has_no_focus_rather_than_zero():
    """Undefined is not zero (CLAUDE.md §7 #3). A blank field and a field whose
    focus was not measured must not read alike."""
    assert focus_score(np.zeros((16, 16), np.float32)) is None
    assert focus_score(np.full((16, 16), 7.0, np.float32)) is None
    assert focus_score(np.zeros((0, 0))) is None


def test_focus_survives_a_frame_carrying_nan_or_inf():
    arr = _frame()
    arr[3, 3] = np.nan
    arr[4, 4] = np.inf
    assert focus_score(arr) is not None


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
    import pipeline.field_quality as fq

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(fq.tempfile, "mkstemp", _boom)
    assert fq.write_well_field_quality(tmp_path, "A01", fields=[],
                                       measured_of=1) is None


def test_the_staging_directory_is_hidden_so_it_is_not_dataset_output():
    from pipeline.field_quality import FIELD_QUALITY_DIRNAME
    assert FIELD_QUALITY_DIRNAME.startswith(".")
