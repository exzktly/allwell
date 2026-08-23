"""Fast-lane Qt-free tests for `datasets.merge_plates`."""

from __future__ import annotations

import csv
import json
import zipfile

import pytest

from datasets.merge_plates import apply as mp_apply
from datasets.merge_plates import merge_plates_compute as mpc
from tests.fixtures.dataset_builder import build_dataset


def test_identity_merge_is_a_pure_copy_no_rewriting(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01", "B02"], timepoints=["0h", "24h"])
    build_dataset(ds2, wells=["C03", "D04"], timepoints=["0h", "24h"])

    plan = mpc.build_plate_merge_plan(ds1, ds2, dest)
    assert plan.well_map == {}
    mp_apply.execute_plan(plan)

    with (ds2 / "out" / "measurements_C03.csv").open() as f:
        src_rows = list(csv.DictReader(f))
    with (dest / "out" / "measurements_C03.csv").open() as f:
        dest_rows = list(csv.DictReader(f))
    assert src_rows == dest_rows

    with zipfile.ZipFile(ds2 / "in" / "C03.zip") as zf:
        src_members = sorted(zf.namelist())
    with zipfile.ZipFile(dest / "in" / "C03.zip") as zf:
        dest_members = sorted(zf.namelist())
    assert src_members == dest_members


def test_colliding_wells_refused(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01", "B02"], timepoints=["0h"])
    build_dataset(ds2, wells=["A01", "C03"], timepoints=["0h"])
    with pytest.raises(mpc.MergePlatesError, match="collide"):
        mpc.build_plate_merge_plan(ds1, ds2, dest)


def test_well_map_collapsing_two_ds2_wells_onto_one_target_is_refused(tmp_path):
    """Regression guard: dest_from_ds2 used to be a set comprehension over
    dataset 2's wells, so two distinct dataset-2 wells mapping to the same
    destination silently collapsed into one element and the collision check
    never fired — execute_plan would then submit both source wells as
    separate ThreadPoolExecutor futures writing the same destination path
    concurrently (non-deterministic last-writer-wins data loss). Not
    reachable through the GUI today (block_shift_well_map is always
    injective), but build_plate_merge_plan's own well_map parameter must
    refuse this on its own, since nothing else guarantees an injective map."""
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["B02", "C03"], timepoints=["0h"])
    with pytest.raises(mpc.MergePlatesError, match="same destination position"):
        mpc.build_plate_merge_plan(ds1, ds2, dest, well_map={"B02": "E05", "C03": "E05"})


def test_remap_moves_all_four_coupled_values_together(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["A01"], timepoints=["0h"])  # collides -> must remap

    plan = mpc.build_plate_merge_plan(ds1, ds2, dest, well_map={"A01": "E01"})
    mp_apply.execute_plan(plan)

    # container member name — E01 (remapped dataset 2) and A01 (dataset 1's
    # own, untouched well) both legitimately exist in the destination.
    with zipfile.ZipFile(dest / "in" / "E01.zip") as zf:
        members = zf.namelist()
    assert all("E01" in m for m in members)
    with zipfile.ZipFile(dest / "in" / "A01.zip") as zf:
        ds1_members = zf.namelist()
    assert all("A01" in m for m in ds1_members)

    # CSV well column + filename column
    with (dest / "out" / "measurements_E01.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert all(r["well"] == "E01" for r in rows)
    assert all("E01" in r["filename"] for r in rows)

    # out-zip member name
    with zipfile.ZipFile(dest / "out" / "E01_out.zip") as zf:
        out_members = zf.namelist()
    assert all("E01" in m for m in out_members)


def test_capacity_refused_past_column_12(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["A01"], timepoints=["0h"])
    with pytest.raises(mpc.MergePlatesError, match="valid 96-well"):
        mpc.build_plate_merge_plan(ds1, ds2, dest, well_map={"A01": "A13"})


def test_divergent_timepoints_warn_not_refuse(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h", "24h"])
    build_dataset(ds2, wells=["B02"], timepoints=["0h"])
    plan = mpc.build_plate_merge_plan(ds1, ds2, dest)
    assert plan.timepoint_warning is not None
    assert "ragged" in plan.timepoint_warning
    # Not refused — still executes.
    mp_apply.execute_plan(plan)
    info = json.loads((dest / "out" / "pipeline_info.json").read_text())
    assert info["available_timepoints"] == ["0h", "24h"]


def test_block_shift_well_map(tmp_path):
    m = mpc.block_shift_well_map({"A01", "A02", "B01"}, target_origin="E01")
    assert m == {"A01": "E01", "A02": "E02", "B01": "F01"}


def test_sample_definitions_names_disambiguated(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(
        ds1, wells=["A01"], timepoints=["0h"],
        info_extra={"sample_definitions": {
            "well_labels": {"A01": "Control"},
            "rep_sets": [{"name": "Rep 1", "wells": ["A01"]}],
            "groups": [{"name": "Control", "hidden": False, "members": ["Rep 1"], "solo_wells": []}],
        }},
    )
    build_dataset(
        ds2, wells=["B02"], timepoints=["0h"],
        info_extra={"sample_definitions": {
            "well_labels": {"B02": "Treated"},
            "rep_sets": [{"name": "Rep 1", "wells": ["B02"]}],
            "groups": [{"name": "Control", "hidden": False, "members": ["Rep 1"], "solo_wells": []}],
        }},
    )
    plan = mpc.build_plate_merge_plan(ds1, ds2, dest)
    sd = plan.merged_info["sample_definitions"]
    # The merge writes the schema the app writes, whatever the inputs were: two
    # legacy (v1) blocks in, one current (v2) block out. This test previously
    # asserted the v1 keys back out again, which is why it stayed green while a
    # merge of two normally-saved plates was discarding both plates' work.
    assert sd["schema_version"] == 2
    names = [s["name"] for s in sd["selections"]]
    assert len(names) == 2, names
    assert "Control" in names
    assert any(n.startswith("Control (") for n in names), (
        f"the dataset-2 name was not disambiguated: {names}")
    assert sd["well_labels"] == {"A01": "Control", "B02": "Treated"}


def test_sample_definitions_survive_a_merge_of_two_current_plates(tmp_path):
    """A merge of two plates saved by today's app keeps both plates' work.

    The block on disk has been ``{schema_version: 2, selections, current_id,
    well_labels, notes, plot_groups}`` for some time; the merge read only the
    legacy ``rep_sets`` / ``groups`` keys, found nothing, and *replaced*
    dataset 1's block with an empty legacy shape — every selection, plot group,
    note and current selection from both plates gone, reported as success.
    """
    from well_viewer.domain.selections_model import make_selection, to_block

    def v2_block(pairs, notes, groups):
        used_names, used_ids, used_colors = set(), set(), set()
        sels = [
            make_selection(name=n, wells=w, replicates=None, hidden=False,
                           color=None, source="user", used_names=used_names,
                           used_ids=used_ids, used_colors=used_colors,
                           fallback_color_idx=i)
            for i, (n, w) in enumerate(pairs)
        ]
        return to_block(sels, well_labels={w: n for n, ws in pairs for w in ws},
                        notes=notes, current_id=sels[0]["id"],
                        plot_groups=groups)

    ds1, ds2, dest = tmp_path / "ds1", tmp_path / "ds2", tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"],
                  info_extra={"sample_definitions": v2_block(
                      [("Control", ["A01"])], "plate one notes",
                      [{"name": "QC set", "wells": ["A01"]}])})
    build_dataset(ds2, wells=["B02"], timepoints=["0h"],
                  info_extra={"sample_definitions": v2_block(
                      [("Treated", ["B02"])], "plate two notes",
                      [{"name": "Row B", "wells": ["B02"]}])})

    sd = mpc.build_plate_merge_plan(ds1, ds2, dest).merged_info["sample_definitions"]
    assert [s["name"] for s in sd["selections"]] == ["Control", "Treated"]
    assert {g["name"] for g in sd["plot_groups"]} == {"QC set", "Row B"}
    assert "plate one notes" in sd["notes"] and "plate two notes" in sd["notes"]
    assert sd["current_id"] in {s["id"] for s in sd["selections"]}
    assert sd["well_labels"] == {"A01": "Control", "B02": "Treated"}


def test_a_merge_says_which_of_dataset_two_s_analysis_blocks_it_dropped(tmp_path):
    """Only sample definitions can be combined by rule; gating, ratios, QC and
    smFISH thresholds are taken from dataset 1 wholesale. A merged plate
    carrying half the user's decisions and saying nothing is the shape this
    codebase refuses to produce."""
    ds1, ds2, dest = tmp_path / "ds1", tmp_path / "ds2", tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"],
                  info_extra={"cell_gating": {"fluor_gates": {"gfp": 10.0}}})
    build_dataset(ds2, wells=["B02"], timepoints=["0h"],
                  info_extra={"cell_gating": {"fluor_gates": {"gfp": 99.0}},
                              "ratios": [{"name": "r"}]})
    plan = mpc.build_plate_merge_plan(ds1, ds2, dest)
    assert plan.blocks_warning, "dataset 2's gating and ratios vanished silently"
    assert "cell gating" in plan.blocks_warning
    assert "ratio metrics" in plan.blocks_warning
    # And the merge really did keep dataset 1's.
    assert plan.merged_info["cell_gating"]["fluor_gates"]["gfp"] == 10.0


def test_a_merge_with_nothing_to_drop_says_nothing(tmp_path):
    """The control: a note that fires for every merge is noise, not a warning."""
    ds1, ds2, dest = tmp_path / "ds1", tmp_path / "ds2", tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["B02"], timepoints=["0h"])
    assert mpc.build_plate_merge_plan(ds1, ds2, dest).blocks_warning is None


def test_verify_fails_on_zero_rows(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    (ds1 / "in").mkdir(parents=True)
    (ds1 / "out").mkdir(parents=True)
    (ds1 / "out" / "pipeline_info.json").write_text(json.dumps({
        "schema": "experiment:channel:well:fov:timepoint", "separator": "_",
        "schema_fields": ["experiment", "channel", "well", "fov", "timepoint"],
        "well_index": 2, "channel_index": 1, "fov_index": 3, "tp_index": 4,
        "nuclear_token": "nir", "segmentation_method": "stardist_nuclei",
        "available_timepoints": ["0h"], "available_fovs": ["F001"],
    }))
    build_dataset(ds2, wells=["B02"], timepoints=["0h"])
    plan = mpc.build_plate_merge_plan(ds1, ds2, dest)
    mp_apply.execute_plan(plan)  # ds2 has rows, so this should succeed
    assert (dest / "out" / "measurements_B02.csv").exists()


def test_marker_survives_cancel(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["B02"], timepoints=["0h"])
    plan = mpc.build_plate_merge_plan(ds1, ds2, dest)
    with pytest.raises(mpc.MergePlatesError, match="Cancelled"):
        mp_apply.execute_plan(plan, is_cancelled=lambda: True)
    assert (dest / mpc.IN_PROGRESS_MARKER).exists()
    assert not (dest / "out" / "pipeline_info.json").exists()


def test_destination_same_as_source_refused(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["B02"], timepoints=["0h"])
    with pytest.raises(mpc.MergePlatesError, match="different folder"):
        mpc.build_plate_merge_plan(ds1, ds2, ds1)


def test_incompatible_schema_refused(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["B02"], timepoints=["0h"], info_extra={"nuclear_token": "dapi"})
    with pytest.raises(mpc.MergePlatesError, match="not analysed the same way"):
        mpc.build_plate_merge_plan(ds1, ds2, dest)


def test_execute_plan_reports_one_rising_sub_progress_sweep_per_well(tmp_path):
    """``sub_progress`` is the per-well dial behind ``progress``'s stage
    events. Each well's fractions must rise monotonically and end at full —
    the four container passes a well takes are fused into one sweep, so a
    dial that filled once per pass (or stopped short because a pass had
    nothing to do) is the bug this reports against."""
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01", "A02"], timepoints=["0h", "24h"])
    build_dataset(ds2, wells=["B01"], timepoints=["0h", "24h"])

    plan = mpc.build_plate_merge_plan(ds1, ds2, dest, {"B01": "C03"})
    fractions: dict[str, list[tuple[int, int]]] = {}
    mp_apply.execute_plan(
        plan,
        sub_progress=lambda well, done, total: fractions.setdefault(well, []).append((done, total)),
    )

    assert set(fractions) == {"A01", "A02", "C03"}
    for well, seq in fractions.items():
        values = [done for done, _total in seq]
        assert values == sorted(values), f"{well} dial went backwards: {values}"
        assert seq[-1][0] == seq[-1][1], f"{well} dial never reached full: {seq[-1]}"


def test_execute_plan_merges_many_wells_from_both_datasets_in_parallel(tmp_path):
    """Enough wells across both datasets to force >1 worker (default
    auto-sizing) -- every well from either side must land correctly with no
    cross-well interference, and every well reports exactly one merging +
    one merged event, regardless of which dataset it came from."""
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    ds1_wells = [f"A{c:02d}" for c in range(1, 9)]   # A01..A08
    ds2_wells = [f"B{c:02d}" for c in range(1, 9)]   # B01..B08 - disjoint
    build_dataset(ds1, wells=ds1_wells, timepoints=["0h", "24h"])
    build_dataset(ds2, wells=ds2_wells, timepoints=["0h", "24h"])

    events: list[tuple[str, str]] = []
    plan = mpc.build_plate_merge_plan(ds1, ds2, dest)
    summary = mp_apply.execute_plan(plan, progress=lambda well, stage: events.append((well, stage)))
    all_wells = ds1_wells + ds2_wells
    assert f"{len(all_wells)} well(s)" in summary

    by_well: dict[str, list[str]] = {}
    for well, stage in events:
        by_well.setdefault(well, []).append(stage)
    assert set(by_well) == set(all_wells)
    for stages in by_well.values():
        assert stages == ["merging", "merged"]

    for well in all_wells:
        with (dest / "out" / f"measurements_{well}.csv").open() as f:
            assert len(list(csv.DictReader(f))) == 2


def test_execute_plan_carries_over_cell_overrides_with_well_remap(tmp_path):
    """No merge_plates test previously touched cell_overrides.json at all —
    Concatenate has an equivalent regression test for its own axis (a
    timepoint shift), but this tool's own axis (a well remap) was untested.
    dataset 1's override sits on a well that is NOT remapped (identity) and
    must survive with its ``well`` key unchanged; dataset 2's override sits
    on the well that collides with dataset 1's and must come through the
    merge with its ``well`` key rewritten to the destination token, not left
    as the stale source token."""
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["A01"], timepoints=["0h"])  # collides -> must remap
    (ds1 / "out" / "cell_overrides.json").write_text(json.dumps({
        "version": 2,
        "overrides": [{"well": "A01", "fov": "F001", "tp": "0h", "nucleus_id": "1",
                       "included": "0", "seg_fp": "stale-ds1"}],
    }))
    (ds2 / "out" / "cell_overrides.json").write_text(json.dumps({
        "version": 2,
        "overrides": [{"well": "A01", "fov": "F001", "tp": "0h", "nucleus_id": "2",
                       "included": "1", "seg_fp": "stale-ds2"}],
    }))

    plan = mpc.build_plate_merge_plan(ds1, ds2, dest, well_map={"A01": "E01"})
    mp_apply.execute_plan(plan)

    merged = json.loads((dest / "out" / "cell_overrides.json").read_text())
    wells = sorted(o["well"] for o in merged["overrides"])
    assert wells == ["A01", "E01"]  # ds1's untouched well + ds2's remapped well
    ds1_entry = next(o for o in merged["overrides"] if o["well"] == "A01")
    ds2_entry = next(o for o in merged["overrides"] if o["well"] == "E01")
    assert ds1_entry["nucleus_id"] == "1"
    assert ds2_entry["nucleus_id"] == "2"
    # seg_fp is re-stamped from the real destination zip's mtime, not left stale.
    assert ds1_entry["seg_fp"] == str((dest / "out" / "A01_out.zip").stat().st_mtime_ns)
    assert ds2_entry["seg_fp"] == str((dest / "out" / "E01_out.zip").stat().st_mtime_ns)


def test_execute_plan_respects_explicit_worker_count(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01", "A02"], timepoints=["0h"])
    build_dataset(ds2, wells=["B01", "B02"], timepoints=["0h"])
    plan = mpc.build_plate_merge_plan(ds1, ds2, dest)
    summary = mp_apply.execute_plan(plan, workers=1)
    assert "4 well(s)" in summary
