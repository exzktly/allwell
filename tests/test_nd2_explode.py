"""Converting a Nikon ND2 into the per-well archives the pipeline reads.

Every case goes through a file ``tests/_nd2_fixture`` wrote and the real
``nd2`` library read back, and every plane carries a value derived from all
three of its coordinates — so a plane written under the wrong name is a
*different number*, and the member listing alone can never be the whole
assertion.
"""

from __future__ import annotations

import zipfile

import numpy as np
import pytest
import tifffile

pytest.importorskip("nd2")

from pipeline.nd2_explode import explode_nd2, resolve_well_map  # noqa: E402
from pipeline.nd2_read import read_nd2_layout  # noqa: E402
from tests._nd2_fixture import plate_value, write_nd2  # noqa: E402


def _plate(tmp_path, **kwargs):
    kwargs.setdefault("n_pos", 4)
    kwargs.setdefault("n_t", 3)
    kwargs.setdefault("n_c", 2)
    kwargs.setdefault("well_names", ["C07", "C07", "A02", "A02"])
    kwargs.setdefault("channel_names", ["DAPI", "GFP"])
    return write_nd2(tmp_path / "Plate7.nd2", **kwargs)


def _members(out, well):
    return sorted(zipfile.ZipFile(out / f"{well}.zip").namelist())


def _plane(out, well, member):
    with zipfile.ZipFile(out / f"{well}.zip") as archive, archive.open(member) as fh:
        return int(np.unique(tifffile.imread(fh))[0])


# ── the mapping ──────────────────────────────────────────────────────────────

def test_positions_naming_one_well_become_that_well_s_fields(tmp_path):
    result = explode_nd2(_plate(tmp_path), tmp_path / "out", experiment="Exp")
    assert result.wells == ["A02", "C07"]
    assert result.fovs_per_well == 2


def test_each_member_holds_the_plane_its_name_claims(tmp_path):
    """The desync check. The fixture's second well is positions 2 and 3, so
    ``A02``'s field 1 is position 3 — a converter that grouped positions the
    other way round would write a complete, correctly sized archive with every
    image one well out."""
    out = tmp_path / "out"
    explode_nd2(_plate(tmp_path, times_ms=None), out, experiment="Exp")
    for fov, position in ((0, 2), (1, 3)):
        for c, chan in enumerate(("DAPI", "GFP")):
            for t in range(3):
                got = _plane(out, "A02", f"Exp_{chan}_A02_f{fov}_t{t}.tif")
                assert got == plate_value(position, t, c), (
                    f"A02 field {fov}, channel {chan}, timepoint {t}")


def test_the_grid_is_the_fallback_for_an_unnamed_file(tmp_path):
    src = _plate(tmp_path, well_names=None)
    out = tmp_path / "out"
    result = explode_nd2(src, out, experiment="Exp", well_source="grid",
                         grid_rows=2, grid_cols=2)
    assert result.wells == ["A01", "A02", "B01", "B02"]
    assert result.fovs_per_well == 1


def test_an_unnamed_file_is_refused_by_name_and_pointed_at_the_grid(tmp_path):
    layout = read_nd2_layout(_plate(tmp_path, well_names=None))
    with pytest.raises(ValueError, match="plate grid"):
        resolve_well_map(layout, well_source="file", name="Plate7.nd2")


def test_a_fovs_per_well_that_contradicts_the_file_is_refused(tmp_path):
    """Rather than silently preferring one of the two answers."""
    layout = read_nd2_layout(_plate(tmp_path))
    with pytest.raises(ValueError, match="field\\(s\\) of view each"):
        resolve_well_map(layout, well_source="file", fovs_per_well=3,
                         name="Plate7.nd2")


# ── timepoint names ──────────────────────────────────────────────────────────

def test_a_timed_plate_is_named_in_elapsed_hours(tmp_path):
    """Through the shared renderer, so an acquisition exported as an ND2 and
    as a CZI converts to the same timepoint names."""
    src = _plate(tmp_path, times_ms=[0, 4 * 3_600_000, 8 * 3_600_000])
    result = explode_nd2(src, tmp_path / "out", experiment="Exp")
    assert result.timepoints == ["0h", "4h", "8h"]
    assert "Exp_DAPI_C07_f0_4h.tif" in _members(tmp_path / "out", "C07")


def test_an_untimed_plate_stays_positional_and_says_why(tmp_path):
    lines: list[str] = []
    result = explode_nd2(_plate(tmp_path, times_ms=None), tmp_path / "out",
                         experiment="Exp", log_fn=lines.append)
    assert result.timepoints == ["t0", "t1", "t2"]
    said = " ".join(lines)
    assert "named by position" in said
    assert "records no acquisition time" in said


def test_the_stride_selects_from_the_elapsed_tokens(tmp_path):
    src = _plate(tmp_path, n_t=4,
                 times_ms=[0, 4 * 3_600_000, 8 * 3_600_000, 12 * 3_600_000])
    result = explode_nd2(src, tmp_path / "out", experiment="Exp",
                         timepoint_stride=2)
    assert result.timepoints == ["0h", "8h"]


def test_a_bad_stride_is_refused_before_anything_is_written(tmp_path):
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="stride must be 1 or more"):
        explode_nd2(_plate(tmp_path), out, experiment="Exp", timepoint_stride=0)
    assert not (out / "C07.zip").exists()


# ── the run ──────────────────────────────────────────────────────────────────

def test_progress_is_reported_against_what_the_run_writes(tmp_path):
    seen: list[tuple[int, int]] = []
    explode_nd2(_plate(tmp_path), tmp_path / "out", experiment="Exp",
                progress_fn=lambda d, t: seen.append((d, t)))
    # 2 wells x 2 FOVs x 3 timepoints x 2 channels
    assert seen[-1] == (24, 24)


def test_a_well_reads_full_only_once_its_archive_is_published(tmp_path):
    """The converter pages draw a green ✓ on the full step, so it must not
    arrive while the well's archive is still under its staging name — the last
    plane used to report ``planes / planes`` from inside ``atomic_zip``, before
    the archive was closed, verified and renamed onto ``<WELL>.zip``."""
    out = tmp_path / "out"
    at_full: dict[str, bool] = {}
    early: list[tuple[str, int, int]] = []

    def _record(w, d, t):
        if d >= t:
            at_full[w] = (out / f"{w}.zip").is_file()
        elif (out / f"{w}.zip").is_file():
            early.append((w, d, t))

    explode_nd2(_plate(tmp_path), out, experiment="Exp", workers=1,
                well_progress_fn=_record)
    assert at_full and all(at_full.values()), at_full
    assert early == []


def test_resuming_leaves_its_own_finished_wells_alone(tmp_path):
    out = tmp_path / "out"
    src = _plate(tmp_path)
    explode_nd2(src, out, experiment="Exp")
    again = explode_nd2(src, out, experiment="Exp", resume=True)
    assert sorted(again.wells_resumed) == ["A02", "C07"]
    assert again.images_written == 0


def test_a_cancelled_run_publishes_no_partial_archive(tmp_path):
    from pipeline.nd2_explode import Nd2ExplodeCancelled

    out = tmp_path / "out"
    with pytest.raises(Nd2ExplodeCancelled):
        explode_nd2(_plate(tmp_path), out, experiment="Exp",
                    should_abort=lambda: True)
    assert not list(out.glob("*.zip"))


def test_the_experiment_token_defaults_to_the_file_name(tmp_path):
    """A constant default would give every plate the same token, so two of
    them concatenated later would be indistinguishable."""
    explode_nd2(_plate(tmp_path), tmp_path / "out")
    assert _members(tmp_path / "out", "C07")[0].startswith("Plate7_")


def test_an_unsound_file_is_refused_before_anything_is_written(tmp_path):
    out = tmp_path / "out"
    src = _plate(tmp_path, channel_names=["488 nm", "488nm"])
    with pytest.raises(ValueError, match="reduce to the filename token"):
        explode_nd2(src, out, experiment="Exp")
    assert not out.exists() or not list(out.glob("*.zip"))
