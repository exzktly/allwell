"""Fast-lane Qt-free tests for `datasets.subset.subset_compute` and
`datasets.subset.apply`.
"""

from __future__ import annotations

import csv
import json
import zipfile

import pytest

from datasets.subset import apply as subset_apply
from datasets.subset import subset_compute as sc
from tests.fixtures.dataset_builder import JUNK_MEMBERS, STRAY_MEMBERS, build_dataset


def _build(root, **kw):
    kw.setdefault("wells", ["A01", "B02", "C03"])
    kw.setdefault("timepoints", ["0h", "24h", "48h", "72h"])
    kw.setdefault("fovs", ("F001", "F002"))
    return build_dataset(root, **kw)


def test_subset_keeps_original_hours_no_rewriting(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src)
    filt = sc.SubsetFilter(
        wells=frozenset({"A01"}), timepoint_labels=frozenset({"24h", "48h"}),
        fovs=frozenset({"F001"}),
    )
    plan = sc.build_subset_plan(src, dest, filt)
    subset_apply.execute_plan(plan)

    with (src / "out" / "measurements_A01.csv").open() as f:
        src_rows = {r["filename"]: r for r in csv.DictReader(f)}
    with (dest / "out" / "measurements_A01.csv").open() as f:
        dest_rows = list(csv.DictReader(f))

    assert {r["timepoint"] for r in dest_rows} == {"24h", "48h"}
    assert {r["fov"] for r in dest_rows} == {"F001"}
    for row in dest_rows:
        src_row = src_rows[row["filename"]]
        assert row["timepoint"] == src_row["timepoint"]
        assert row["timepoint_hours"] == src_row["timepoint_hours"]
        assert row["filename"] == src_row["filename"]

    with zipfile.ZipFile(dest / "in" / "A01.zip") as zf:
        dest_members = set(zf.namelist())
    with zipfile.ZipFile(src / "in" / "A01.zip") as zf:
        src_members = set(zf.namelist())
    assert dest_members == {m for m in src_members if "_F001_" in m and (m.endswith("_24h.tif") or m.endswith("_48h.tif"))}


def test_source_untouched(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src)
    with zipfile.ZipFile(src / "in" / "A01.zip") as zf:
        before = set(zf.namelist())

    filt = sc.SubsetFilter(wells=frozenset({"A01"}), timepoint_labels=frozenset({"0h"}))
    plan = sc.build_subset_plan(src, dest, filt)
    subset_apply.execute_plan(plan)

    with zipfile.ZipFile(src / "in" / "A01.zip") as zf:
        after = set(zf.namelist())
    assert before == after


def test_only_selected_wells_kept(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src)
    filt = sc.SubsetFilter(wells=frozenset({"A01", "C03"}))
    plan = sc.build_subset_plan(src, dest, filt)
    assert plan.wells_kept == ("A01", "C03")
    subset_apply.execute_plan(plan)
    assert (dest / "out" / "measurements_A01.csv").exists()
    assert (dest / "out" / "measurements_C03.csv").exists()
    assert not (dest / "out" / "measurements_B02.csv").exists()


def test_no_filter_keeps_everything(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src)
    plan = sc.build_subset_plan(src, dest, sc.SubsetFilter())
    assert set(plan.wells_kept) == {"A01", "B02", "C03"}
    subset_apply.execute_plan(plan)
    with (src / "out" / "measurements_A01.csv").open() as f:
        src_rows = list(csv.DictReader(f))
    with (dest / "out" / "measurements_A01.csv").open() as f:
        dest_rows = list(csv.DictReader(f))
    assert len(dest_rows) == len(src_rows)


def test_destination_same_as_source_refused(tmp_path):
    src = tmp_path / "src"
    _build(src)
    with pytest.raises(sc.SubsetError, match="different folder"):
        sc.build_subset_plan(src, src, sc.SubsetFilter())


def test_destination_already_finished_dataset_refused(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src)
    _build(dest, wells=["Z09"])
    with pytest.raises(sc.SubsetError, match="already holds a finished dataset"):
        sc.build_subset_plan(src, dest, sc.SubsetFilter())


def test_filter_matching_nothing_refused(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src)
    with pytest.raises(sc.SubsetError, match="No wells match"):
        sc.build_subset_plan(src, dest, sc.SubsetFilter(wells=frozenset({"H12"})))


def test_verify_fails_on_zero_rows(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src, wells=["A01"])
    filt = sc.SubsetFilter(timepoint_labels=frozenset({"999h"}))  # matches nothing
    plan = sc.build_subset_plan(src, dest, filt)
    with pytest.raises(sc.SubsetError, match="zero measurement rows"):
        subset_apply.execute_plan(plan)
    assert not (dest / "out" / "pipeline_info.json").exists()


def test_marker_survives_cancel(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src, wells=["A01"])
    plan = sc.build_subset_plan(src, dest, sc.SubsetFilter())
    with pytest.raises(sc.SubsetError, match="Cancelled"):
        subset_apply.execute_plan(plan, is_cancelled=lambda: True)
    assert (dest / sc.IN_PROGRESS_MARKER).exists()
    assert not (dest / "out" / "pipeline_info.json").exists()


def test_cell_overrides_filtered_and_restamped(tmp_path):
    """Regression guard: cell_overrides.json stores fov/tp already normalized
    (CellKey.as_tuple, review/data/image_identity.py) -- "0h"/"24h"
    normalize to "0"/"24" -- while the source CSV's raw timepoint column
    stays "0h"/"24h". _copy_one_well used to key its membership-test set off
    the raw CSV values unnormalized, so this exact comparison failed for
    every override with a parseable timepoint label and silently dropped
    every one of them (0 carried over, no log line about the loss)."""
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src, wells=["A01"], timepoints=["0h", "24h"], fovs=("F001",))
    overrides = {
        "version": 2,
        "overrides": [
            {"well": "A01", "fov": "F001", "tp": "0", "nucleus_id": "1",
             "included": "0", "seg_fp": "old"},
            {"well": "A01", "fov": "F001", "tp": "24", "nucleus_id": "2",
             "included": "0", "seg_fp": "old"},
        ],
    }
    (src / "out" / "cell_overrides.json").write_text(json.dumps(overrides))

    filt = sc.SubsetFilter(timepoint_labels=frozenset({"0h"}))
    plan = sc.build_subset_plan(src, dest, filt)
    subset_apply.execute_plan(plan)

    result = json.loads((dest / "out" / "cell_overrides.json").read_text())
    assert len(result["overrides"]) == 1
    kept = result["overrides"][0]
    assert kept["tp"] == "0"
    dest_zip = dest / "out" / "A01_out.zip"
    assert kept["seg_fp"] == f"{dest_zip.stat().st_mtime_ns}"
    assert kept["seg_fp"] != "old"


def test_restart_detection_and_clear(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src, wells=["A01"])
    plan = sc.build_subset_plan(src, dest, sc.SubsetFilter())
    with pytest.raises(sc.SubsetError):
        subset_apply.execute_plan(plan, is_cancelled=lambda: True)

    assert sc.destination_has_marker(sc.DatasetPaths(root=dest, in_dir=dest / "in", out_dir=dest / "out"))
    plan2 = sc.build_subset_plan(src, dest, sc.SubsetFilter())
    assert plan2.dest_is_restart
    subset_apply.execute_plan(plan2)
    assert (dest / "out" / "pipeline_info.json").exists()


def test_execute_plan_copies_many_wells_in_parallel(tmp_path):
    """Enough wells to force >1 worker (default auto-sizing) -- every well
    must land correctly with no cross-well interference, and every well must
    report exactly one copying + one terminal event."""
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    wells = [f"{r}{c:02d}" for r in "ABCD" for c in range(1, 5)]  # 16 wells
    _build(src, wells=wells, timepoints=["0h", "24h"], fovs=("F001",))

    events: list[tuple[str, str]] = []
    plan = sc.build_subset_plan(src, dest, sc.SubsetFilter())
    summary = subset_apply.execute_plan(plan, progress=lambda well, stage: events.append((well, stage)))
    assert f"{len(wells)} well(s)" in summary

    by_well: dict[str, list[str]] = {}
    for well, stage in events:
        by_well.setdefault(well, []).append(stage)
    assert set(by_well) == set(wells)
    for stages in by_well.values():
        assert stages == ["copying", "done"]

    for well in wells:
        with (dest / "out" / f"measurements_{well}.csv").open() as f:
            assert len(list(csv.DictReader(f))) == 2


def test_execute_plan_sub_progress_is_one_rising_sweep_per_well(tmp_path):
    """``sub_progress`` is the per-well dial behind ``progress``'s stage
    events. Copying one well is four container passes (read then write, for
    the raw input and again for the QC archive); they are fused into a single
    sweep, so each well's fractions must rise monotonically and end at full
    rather than filling from empty once per pass."""
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src, wells=["A01", "B02"], timepoints=["0h", "24h"], fovs=("F001",))

    plan = sc.build_subset_plan(src, dest, sc.SubsetFilter())
    fractions: dict[str, list[tuple[int, int]]] = {}
    subset_apply.execute_plan(
        plan,
        sub_progress=lambda well, done, total: fractions.setdefault(well, []).append((done, total)),
    )

    assert set(fractions) == {"A01", "B02"}
    for well, seq in fractions.items():
        values = [done for done, _total in seq]
        assert values == sorted(values), f"{well} dial went backwards: {values}"
        assert seq[-1][0] == seq[-1][1], f"{well} dial never reached full: {seq[-1]}"


def test_execute_plan_respects_explicit_worker_count(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src, wells=["A01", "B02", "C03"], timepoints=["0h"], fovs=("F001",))
    plan = sc.build_subset_plan(src, dest, sc.SubsetFilter())
    summary = subset_apply.execute_plan(plan, workers=1)
    assert "3 well(s)" in summary


def test_folder_mode_source_refused(tmp_path):
    """Regression guard: build_subset_plan used to never check
    has_folder_mode_output (unlike Concatenate and Merge plates, which both
    refuse folder-mode input outright) -- the source's out/<WELL>/ QC images
    were silently dropped from the destination while apply.execute_plan still
    reported success."""
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src, wells=["A01"], timepoints=["0h"])
    (src / "out" / "A01").mkdir()  # folder-mode QC output alongside the zip
    with pytest.raises(sc.SubsetError, match="per-well folders"):
        sc.build_subset_plan(src, dest, sc.SubsetFilter())


def test_zero_padded_numeric_fov_tokens_filter_correctly(tmp_path):
    """Regression guard: row_matches/member_matches/build_subset_plan compared
    each row's/member's own FOV -- always read through normalize_fov -- against
    filt.fovs raw (the unnormalized UI checkbox labels the page's
    selected_fovs() carries). A purely-numeric FOV token like "001" normalizes
    to "1", so selecting FOV "001" in the UI silently matched nothing: every
    row and member was dropped, available_fovs became [], and the run died at
    _verify with "wrote zero measurement rows" -- pointing at the wrong cause.
    Non-numeric tokens ("F001") pass normalize_fov unchanged, which is why the
    existing fixture's default fovs=("F001",) never exercised this."""
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src, wells=["A01"], timepoints=["0h"], fovs=("001", "002"))
    filt = sc.SubsetFilter(fovs=frozenset({"001"}))
    plan = sc.build_subset_plan(src, dest, filt)
    assert plan.merged_info["available_fovs"] == ["001"]
    subset_apply.execute_plan(plan)

    with (dest / "out" / "measurements_A01.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert rows and {r["fov"] for r in rows} == {"001"}


def test_channel_scoped_role_image_filtered_by_timepoint(tmp_path):
    """Regression guard: member_matches used to call parse_image_filename
    directly with no channel/fluor-token discovery. A channel-scoped role
    suffix like "_tophat_gfp" (dataset_builder writes one per well/fov/tp,
    mirroring what the real pipeline's tophat-correction step writes)
    doesn't resolve its role without trying each known channel token as the
    discriminator, so len(parts) != len(schema.fields) fired and the
    "unparseable -> keep" fallback let a filtered-out timepoint's tophat
    image leak into the destination zip."""
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src, wells=["A01"], timepoints=["0h", "24h"], fovs=("F001",))
    filt = sc.SubsetFilter(timepoint_labels=frozenset({"0h"}))
    plan = sc.build_subset_plan(src, dest, filt)
    subset_apply.execute_plan(plan)

    with zipfile.ZipFile(dest / "out" / "A01_out.zip") as zf:
        members = set(zf.namelist())
    assert any("_tophat_gfp" in m for m in members), "kept timepoint's tophat image should survive"
    assert not any("_24h_tophat_gfp" in m for m in members), (
        f"filtered-out 24h tophat image leaked into destination: {members}"
    )
    assert not any("_24h_labels" in m for m in members)


# ── the copy moves bytes, it does not rebuild them ───────────────────────────
def _members(path):
    with zipfile.ZipFile(path) as zf:
        return {i.filename: zf.read(i.filename) for i in zf.infolist() if not i.is_dir()}


def test_a_wells_only_subset_copies_each_archive_whole(tmp_path):
    """The commonest way to use Subset is the one a user would otherwise do by
    hand in Finder, so it must cost what Finder costs: no member of a wells-only
    subset is ever decompressed, and the destination archive is the source file
    byte for byte."""
    src, dest = tmp_path / "src", tmp_path / "dest"
    _build(src, junk=True, stray=True)
    plan = sc.build_subset_plan(src, dest, sc.SubsetFilter(wells=frozenset({"A01"})))
    subset_apply.execute_plan(plan)

    assert (dest / "in" / "A01.zip").read_bytes() == (src / "in" / "A01.zip").read_bytes()
    assert (dest / "out" / "A01_out.zip").read_bytes() == (src / "out" / "A01_out.zip").read_bytes()
    assert not (dest / "in" / "B02.zip").exists()


def test_a_filtered_subset_keeps_every_surviving_member_byte_identical(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _build(src, wells=["A01"], junk=True, stray=True)
    plan = sc.build_subset_plan(
        src, dest, sc.SubsetFilter(timepoint_labels=frozenset({"24h", "48h"})),
    )
    subset_apply.execute_plan(plan)

    for relative in ("in/A01.zip", "out/A01_out.zip"):
        source, copied = _members(src / relative), _members(dest / relative)
        assert copied, f"{relative} lost every member"
        assert set(copied) < set(source), f"{relative} was not actually filtered"
        for name, data in copied.items():
            assert data == source[name], f"{relative}::{name} came back different"


def test_junk_members_survive_a_filtered_subset(tmp_path):
    """A predicate that partitions an archive must first decide which members
    it is entitled to judge. ``.DS_Store``, a directory entry, an AppleDouble
    sidecar and a name that parses to nothing are not this dataset's business,
    and dropping them silently is data loss the run would report as success."""
    src, dest = tmp_path / "src", tmp_path / "dest"
    _build(src, wells=["A01"], junk=True, stray=True)
    plan = sc.build_subset_plan(
        src, dest, sc.SubsetFilter(timepoint_labels=frozenset({"24h"})),
    )
    subset_apply.execute_plan(plan)

    copied = _members(dest / "in" / "A01.zip")
    for name, data in {**JUNK_MEMBERS, **STRAY_MEMBERS}.items():
        assert copied.get(name) == data, f"{name} did not survive the filter"


def test_the_resolved_filter_sets_are_computed_once(tmp_path):
    """``member_matches`` reads them per member, so they must be fields rather
    than properties that re-normalize the whole label set on every read."""
    filt = sc.SubsetFilter(
        timepoint_labels=frozenset({"24h", "48h"}), fovs=frozenset({"001", "F002"}),
    )
    assert filt.timepoint_hours == frozenset({24.0, 48.0})
    assert filt.fov_tokens == frozenset({"1", "F002"})
    assert filt.timepoint_hours is filt.timepoint_hours
    assert filt.fov_tokens is filt.fov_tokens
    # The derived fields stay out of identity: two filters built from the same
    # three real fields are still equal and still hash alike.
    twin = sc.SubsetFilter(
        timepoint_labels=frozenset({"24h", "48h"}), fovs=frozenset({"001", "F002"}),
    )
    assert filt == twin and hash(filt) == hash(twin)
    assert sc.SubsetFilter().timepoint_hours is None
    assert sc.SubsetFilter().fov_tokens is None
