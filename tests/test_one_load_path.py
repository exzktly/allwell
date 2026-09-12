"""One path in: whatever puts a frame in the row cache applies every layer.

The dataset loader used to read its wells itself — ``load_well_csv``, a direct
``dataset.cache[key] = frame``, and its own ``record_fov_verdict`` — while a
cache miss went through ``get_rows``. Two paths, and they did not carry the same
things: the loader applied manual per-cell curation as well as QC and crop, but
``get_rows`` ran only the two registered projectors. Nothing noticed, because
nothing compared them.

It is reachable, not theoretical. ``persistence/reset_scopes.py`` drops the row
cache when the user resets QC parameters, and ``channel_state_controller`` drops
it to recover from a stale cache; neither clears
``runtime.review.included_overrides``. So the wells came back from disk with the
user's exclusions gone from ``Included`` while the override map still held them —
cells the user had excluded silently back in every plot, export and statistic,
with the software showing no sign that anything had been dropped.

These tests pin the converged path (roadmap B4b): ``load_and_project`` is the
only way in, ``WellRepository.load_well`` is what the loader calls, and manual
curation is a registered projector like QC and crop rather than something the
loader remembers to do.
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from review.core.dataset_state import DatasetState
from review.data.curation import apply_curation_to_cache
from review.data.data_loading import get_rows
from review.data.well_repository import WellRepository
from review.features.registry import build_feature_registry


def _write_well(tmp_path, label="A01", rows=3):
    """A minimal well CSV of *rows* cells, all included."""
    path = tmp_path / f"{label}.csv"
    pd.DataFrame({
        "well": [label] * rows,
        "fov": ["1"] * rows,
        "timepoint": ["0"] * rows,
        "nucleus_id": [str(i + 1) for i in range(rows)],
        "Included": [1] * rows,
        "MeanIntensity": [10.0 * (i + 1) for i in range(rows)],
    }).to_csv(path, index=False)
    return path


@pytest.fixture
def ctx(tmp_path):
    """A context with the real ``DatasetState`` + repository, no Qt."""
    dataset = DatasetState()
    dataset.well_paths["A01"] = _write_well(tmp_path)
    obj = SimpleNamespace(
        state=SimpleNamespace(
            dataset=dataset,
            review_image=SimpleNamespace(override_version=0),
            qc=SimpleNamespace(params=None),
        ),
        runtime=SimpleNamespace(review=SimpleNamespace(included_overrides={})),
    )
    obj.state.repo = WellRepository(obj)
    # Building the registry is what registers the projectors: the wiring lives
    # with the one module allowed to name a feature.
    build_feature_registry()
    return obj


def _included(frame_or_ctx, label="A01"):
    """The ``Included`` column, from a frame or from a ctx's cached well."""
    if hasattr(frame_or_ctx, "state"):
        return list(frame_or_ctx.state.dataset.cache[label]["Included"])
    return list(frame_or_ctx["Included"])


# ── the defect this phase closes ──────────────────────────────────────────


def test_curation_survives_the_row_cache_being_dropped(ctx):
    """A cleared cache must not quietly re-include cells the user excluded.

    The override map is the source of truth and it outlives the cache, so the
    frame rebuilt from the CSV has to be re-curated on its way in. Before the
    load paths converged this came back ``[1, 1, 1]`` — the user's exclusion
    gone from the rows every plot reads, while the map still listed it.
    """
    get_rows(ctx, "A01")
    ctx.runtime.review.included_overrides[("A01", "1", "0", "2")] = "0"
    apply_curation_to_cache(ctx, "A01")
    assert _included(ctx) == [1, 0, 1]

    ctx.state.dataset.cache.clear()
    assert ctx.runtime.review.included_overrides, "the map outlives the cache"

    get_rows(ctx, "A01")
    assert _included(ctx) == [1, 0, 1], (
        "a well reloaded after a cache clear came back uncurated — the "
        "curation projector is no longer registered, or the lazy path no "
        "longer runs the registered projectors"
    )


def test_the_eager_and_lazy_loads_apply_the_same_layers(ctx):
    """``repo.load_well`` (what the loader calls) and ``get_rows`` (the cache
    miss) must produce the same frame, layer for layer.

    Compared as whole frames rather than by listing the layers, so a projector
    added to one path and not the other fails here without this test having to
    be taught what the projectors are.
    """
    ctx.runtime.review.included_overrides[("A01", "1", "0", "3")] = "0"

    lazy = get_rows(ctx, "A01").copy()
    ctx.state.dataset.cache.clear()
    eager = ctx.state.repo.load_well("A01").copy()

    pd.testing.assert_frame_equal(lazy, eager)
    assert list(eager["Included"]) == [1, 1, 0]
    for column in ("QC_Pass", "Crop_Pass"):
        assert column in eager.columns, f"{column} missing from the eager load"


# ── the seam itself ───────────────────────────────────────────────────────


def test_load_well_registers_the_path_it_was_given(ctx, tmp_path):
    """The loader's first pass over a new dataset knows the CSV location and
    the repository is what records it — the loader no longer touches
    ``well_paths`` itself."""
    path = _write_well(tmp_path, label="B02", rows=2)

    frame = ctx.state.repo.load_well("B02", path)

    assert ctx.state.dataset.well_paths["B02"] == path
    assert len(frame) == 2
    assert ctx.state.repo.rows("B02") is frame, "the second read is a cache hit"


def test_load_well_records_the_fov_verdict(ctx):
    """The loader used to record this itself, because it built the frame
    outside ``get_rows``. It travels with the load now, so a well cannot enter
    the cache unrecorded and leave ``_report_unusable_fov_labels`` with nothing
    to report."""
    ctx.state.repo.load_well("A01")
    assert "A01" in ctx.state.dataset.fov_integrity


def test_a_fresh_load_does_not_strand_the_other_wells_aggregates(ctx, tmp_path):
    """``rows_version`` is part of the ``aggregate_well`` memo key, so bumping it
    while walking a plate discards what the walk has already computed.

    The projector writes onto a frame ``load_and_project`` built a moment ago,
    which can have no memo of its own — so there is nothing to invalidate and
    everything to lose. QC and crop never bump for the same reason; curation
    cannot simply stop, because the dataset loader's bulk call depends on it,
    so the projector path turns it off and the direct callers keep it.
    """
    ctx.state.dataset.well_paths["B02"] = _write_well(tmp_path, label="B02")
    ctx.runtime.review.included_overrides.update({
        ("A01", "1", "0", "2"): "0",
        ("B02", "1", "0", "1"): "0",
    })

    before = ctx.state.dataset.rows_version
    assert _included(ctx.state.repo.load_well("A01")) == [1, 0, 1]
    assert _included(ctx.state.repo.load_well("B02")) == [0, 1, 1]
    assert ctx.state.dataset.rows_version == before, (
        "a fresh load bumped rows_version, which strands every aggregate "
        "computed for the wells read before it")

    # The direct call still bumps: its callers — the dataset loader above all —
    # project onto frames that are already cached and do not invalidate after.
    apply_curation_to_cache(ctx)
    assert ctx.state.dataset.rows_version > before


def test_curation_is_a_registered_projector_not_a_caller_s_errand():
    """The roster of what a fresh frame gets is one list.

    Asserted through the registry rather than by naming the function: the point
    is that the projection is *registered*, so every load applies it, rather
    than being remembered at each call site — which is how the loader and the
    lazy path came to disagree.
    """
    from review.data.cache_projectors import iter_cache_projectors

    build_feature_registry()
    registered = {fn.__name__ for fn in iter_cache_projectors()}

    assert "apply_curation_to_cache" in registered
    assert {"apply_qc_to_cache", "apply_crop_to_cache"} <= registered
