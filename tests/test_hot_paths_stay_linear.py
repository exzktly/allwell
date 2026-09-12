"""The four complexity fixes, pinned by counting rather than by timing.

Every one of these was a real freeze, every one was fixed, and none of them was
guarded afterwards — ``ARCHITECTURE.md`` records the measurements and nothing
holds them. A wall-clock assertion would be the obvious guard and is the wrong
one: it fails on a loaded container and passes on a fast one, so it gets a
generous band and then catches nothing.

What each of these counts instead is **the operation the fix removed**, at two
input sizes. That is deterministic, identical on every machine, and fails on
exactly the regression that happened rather than on the weather.

The ratios are deliberately loose. A linear algorithm's count roughly doubles
when the input does; a quadratic one quadruples. Anything under 3x separates
them with room to spare, and a tighter bound would only invent flakes.
"""

from __future__ import annotations

import numpy as np
import pytest

# ── 1. DBSCAN's frontier set is maintained, not rebuilt ─────────────────────
#
# ``_dbscan`` used to rebuild ``seen = set(nbrs)`` once per core point expanded,
# which copies the whole accumulated frontier every time and makes expansion
# quadratic in cluster size. Colony detection runs inline on the GUI thread: a
# confluent well of 16,000 cells took 10.69 s per redraw, and 1.06 s after.
#
# The removed operation is the *set construction* the rebuild performed: the
# fixed version builds one frontier set per call to ``_dbscan``, however large
# the cluster grows, while the quadratic version builds one per core point
# expanded. Counting calls needs the module's own ``set`` name, which Python
# resolves through module globals before builtins.


def _dbscan_set_calls(n_points: int) -> int:
    from review.domain import spatial_compute as sc

    # One dense blob, so the whole input is a single cluster and the frontier
    # grows to n — which is the shape the quadratic rebuild was quadratic in.
    rng = np.random.default_rng(0)
    pts = rng.normal(scale=1.0, size=(n_points, 2))

    calls = {"n": 0}

    def counting(iterable=()):
        calls["n"] += 1
        return set(iterable)

    # A scoped context rather than ``monkeypatch.undo()``: undo discards the
    # whole record, so it would silently revert any unrelated patch a caller
    # made earlier in the same test.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sc, "set", counting, raising=False)
        sc._dbscan(pts, eps=5.0, min_samples=3)
    return calls["n"]


def test_dbscan_frontier_does_not_grow_quadratically():
    small = _dbscan_set_calls(200)
    large = _dbscan_set_calls(400)
    assert small > 0, "the counter saw nothing — the patch missed its target"
    assert small == 1 and large == 1, (
        f"``_dbscan`` built {small} frontier sets at 200 points and {large} at "
        "400. It builds exactly one per call however large the cluster grows; "
        "one per core point expanded is the quadratic rebuild that froze "
        "Review on a confluent well."
    )


# ── 2. First visits go through a set, not a list prefix ─────────────────────
#
# ``_first_visits`` used to ask ``c not in cells[:i]``. At 36,000 positions — a
# 96-well plate at 16 fields over 24 timepoints — that alone took five seconds,
# on the UI thread, per keystroke. The removed operation is the element
# comparison a list scan performs and a set does not.


class _CountingCell:
    """A hashable that records how often it is compared."""

    comparisons = 0

    def __init__(self, value):
        self.value = value

    def __hash__(self):
        return hash(self.value)

    def __eq__(self, other):
        type(self).comparisons += 1
        return isinstance(other, _CountingCell) and self.value == other.value


def _first_visit_comparisons(n_cells: int) -> tuple[int, int]:
    """Return ``(comparisons, len(result))`` for a run of *n_cells* distinct cells."""
    from datasets.ome_import.stage_layout import _first_visits

    # Every cell distinct, so a list scan reads the whole prefix every time and
    # a set answers from the hash. Equal hashes are what a set compares, and
    # distinct values rarely collide.
    cells = [_CountingCell(i) for i in range(n_cells)]
    _CountingCell.comparisons = 0
    out = _first_visits(cells)
    return _CountingCell.comparisons, len(out)


def test_first_visits_does_not_compare_against_every_earlier_cell():
    n_small, n_large = 300, 600
    small, kept_small = _first_visit_comparisons(n_small)
    large, kept_large = _first_visit_comparisons(n_large)
    # Without this the check is vacuous: a ``_first_visits`` that returns an
    # empty list makes no comparisons at all and would pass every bound below.
    assert (kept_small, kept_large) == (n_small, n_large), (
        f"``_first_visits`` kept {kept_small} of {n_small} and {kept_large} of "
        f"{n_large} distinct cells. Every input here is distinct, so it must "
        "return all of them — the comparison counts below measure nothing "
        "until it does."
    )
    # A set answers almost every membership test from the hash alone, so the
    # honest linear expectation here is "close to zero", not "doubles". The
    # ceiling is derived from the input rather than written as a literal, so it
    # keeps meaning the same thing if the sizes above change.
    assert large <= max(small * 3, 4 * n_large), (
        f"doubling the input took element comparisons from {small} to {large}. "
        "A list-prefix scan is back, and it cost five seconds per keystroke on "
        "a plate timelapse."
    )


# ── 3. Watershed seeds are whole-array work, not a pass per nucleus ─────────
#
# ``nucleus_seed_markers`` used to ask ``count_nonzero(labels == nid)`` and
# ``center_of_mass(labels == nid)`` per nucleus — one and two full passes over
# the frame each. On 1500 nuclei in a 2048² frame that measured 1.43 s and
# 23.67 s, roughly 25 s of every FOV, invisible in the per-FOV timing line.
#
# The removed operation is the whole-array pass, and the fixed version makes a
# fixed number of them however many nuclei there are.


def _label_frame(n_nuclei: int) -> np.ndarray:
    """A label image with *n_nuclei* 2x2 blobs, ids 1..n."""
    side = int(np.ceil(np.sqrt(n_nuclei))) * 3 + 3
    labels = np.zeros((side, side), dtype=np.int32)
    for i in range(n_nuclei):
        r, c = divmod(i, int(np.ceil(np.sqrt(n_nuclei))))
        labels[r * 3 + 1:r * 3 + 3, c * 3 + 1:c * 3 + 3] = i + 1
    return labels


@pytest.mark.parametrize("n_nuclei", [4, 400])
def test_seed_markers_make_a_fixed_number_of_whole_array_passes(n_nuclei, monkeypatch):
    scipy_ndimage = pytest.importorskip("scipy.ndimage")
    from pipeline import stardist as sd

    calls = {"com": 0}
    real = scipy_ndimage.center_of_mass

    def counting(*a, **k):
        calls["com"] += 1
        return real(*a, **k)

    monkeypatch.setattr(scipy_ndimage, "center_of_mass", counting)
    markers, n_markers, _below = sd.nucleus_seed_markers(_label_frame(n_nuclei), 1)

    assert n_markers == n_nuclei, "the fixture or the arithmetic changed"
    assert calls["com"] == 1, (
        f"{calls['com']} centre-of-mass passes for {n_nuclei} nuclei. This is "
        "one whole-array call regardless of nucleus count; per-nucleus was "
        "23.67 s on a real frame."
    )


# ── 4. The string-timepoint fallback runs only for a row that needs it ──────
#
# ``aggregate_with_threshold_df`` resolves each row's timepoint from the numeric
# ``timepoint_hours`` column and falls back to parsing the string ``timepoint``
# only for rows the numeric column is missing. Both fallback halves widen a
# categorical column to object and strip every row through a per-element call —
# together about nine tenths of the function's cost, and on a plate whose
# numeric column is complete the result is never read. 96 wells of 21,600 rows:
# 1,091 ms before, 180 ms after.


def _plate_frame(n_rows: int):
    pd = pytest.importorskip("pandas")
    return pd.DataFrame({
        "timepoint_hours": np.repeat([0.0, 24.0], n_rows // 2),
        "timepoint": np.repeat(["0h", "24h"], n_rows // 2),
        "gfp_mean_intensity": np.linspace(1.0, 100.0, n_rows),
        "area_px": np.full(n_rows, 50.0),
        "fov": np.full(n_rows, "1"),
        "Included": np.ones(n_rows, dtype=int),
    })


def test_a_complete_numeric_timepoint_column_never_reaches_the_string_parser(monkeypatch):
    from review.data import data_loading as dl

    called = {"n": 0}
    real = dl._ordinal_timepoints_df

    def counting(*a, **k):
        called["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(dl, "_ordinal_timepoints_df", counting)
    points = dl.aggregate_with_threshold_df(_plate_frame(400), threshold=10.0)

    assert points, "the fixture produced no aggregate — the guard proves nothing"
    assert called["n"] == 0, (
        "the string-timepoint fallback ran although every row carries a numeric "
        "timepoint. It widens a categorical column and strips every row; on a "
        "96-well plate that was 1,091 ms against 180 ms."
    )
