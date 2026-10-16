"""The defects the second independent review of Section P found, each pinned (tracker P15)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
from tifffile import imwrite

import process_microscopy as pm
from pipeline import nanowell_run as nr
from pipeline import nanowell_task as nt
from tests._nanowell_run_fixture import build_well, paint_cell, threshold_segmenter
from tests.test_nanowell_cli import SCHEMA, WELL, _run_main, _write_plate
from tests.test_nanowell_reanchor import _plate_grids
from util.acquisition_record import write_record_file

SETTINGS = nr.NanowellSettings("100um")


@pytest.fixture(scope="module")
def two_timepoints():
    first, record = build_well(rows=1, cols=1, tile=(900, 900), timepoint="0h", radius_fraction=0.26)
    second, second_record = build_well(rows=1, cols=1, tile=(900, 900), timepoint="24h", rotation=9.0,
                                       array_shift_um=(100.0, -60.0), seed=2, radius_fraction=0.26)
    for rendered in (first, second):
        paint_cell(rendered, dict(zip(rendered.array.addresses, rendered.sites_um))[(1, 2)], dx_um=3.0)
    record["timepoints"].update(second_record["timepoints"])
    record["tiles"] += second_record["tiles"]
    images = {("0h", t): v for t, v in first.images.items()} | {("24h", t): v for t, v in second.images.items()}
    return record, (lambda tp, tile: images.get((tp, tile)))


# 1 ── a channel missing for one timepoint or tile ───────────────────────────
def test_a_channel_with_no_image_at_one_timepoint_leaves_a_value_less_column_not_a_short_one(two_timepoints):
    record, load = two_timepoints
    gfp = {"GFP": lambda tp, tile: None if tp == "0h" else load(tp, tile) * 0 + 5.0}
    outcome = nr.run_well("A01", record, ["0h", "24h"], load, SETTINGS, segmenter=threshold_segmenter(), channels=gfp)
    lengths = {name: len(values) for name, values in outcome.cells.items()}
    assert len(set(lengths.values())) == 1 and lengths["label"] == 2           # one painted cell per timepoint
    rows = nt.cell_records(outcome, "A01", ["GFP"], {"0h": 4.3, "24h": 4.3})
    assert [r["GFP_mean_intensity"] for r in rows] == [None, pytest.approx(5.0)]
    sites = {r["timepoint"]: r for r in outcome.sites if (r["nw_q"], r["nw_r"]) == (1, 2)}
    assert sites["0h"]["GFP_integrated"] is None and sites["24h"]["GFP_integrated"] is not None


# 2 ── a recomputed timepoint's montage replaces the old one ─────────────────
def test_a_recomputed_timepoints_montage_replaces_the_old_one_and_a_vanished_one_is_dropped(tmp_path):
    import zipfile as z
    out_zip = tmp_path / "A01_out.zip"
    with z.ZipFile(out_zip, "w") as zf:
        zf.writestr("A01_0h_montage.png", b"old0")
        zf.writestr("A01_24h_montage.png", b"old24")
        zf.writestr("A01_48h_montage.png", b"old48")
        zf.writestr("exp_PHASE_A01_F0_0h_labels.tif", b"keep")
    staging = tmp_path / "staging"
    staging.mkdir()
    from skimage.io import imsave
    imsave(str(staging / "A01_0h_montage.png"), np.full((8, 8), 255, np.uint8), check_contrast=False)
    nt.update_montage_archive(out_zip, staging, "A01", ["0h", "24h"])           # 24h now has no montage
    with z.ZipFile(out_zip) as zf:
        names = set(zf.namelist())
        assert names == {"A01_0h_montage.png", "A01_48h_montage.png", "exp_PHASE_A01_F0_0h_labels.tif"}
        assert zf.read("A01_48h_montage.png") == b"old48" and zf.read("exp_PHASE_A01_F0_0h_labels.tif") == b"keep"
        assert zf.read("A01_0h_montage.png") != b"old0"


# 3, 4 ── --force clears checkpoints; a failing well leaves a trace ──────────
def _plate_for_task(tmp_path, record, rendered):
    plate = _write_plate(tmp_path, rendered, record)
    return plate, plate / "in" / f"{WELL}.zip", plate / "out"


def _call_task(zip_path, out, *, force=False, incremental=False):
    kwargs = {"_nanowell": {"plate_format": "100um", "margin_um": None, "cell_diameter_um": 15.0, "square_edge_deg": 0.0}}
    out.mkdir(parents=True, exist_ok=True)
    return pm.process_well_zip_task(WELL, zip_path, out, kwargs, "PHASE", ["GFP"], "measurements", SCHEMA, "_",
                                    incremental, None, "key", force)


def test_force_clears_the_resume_checkpoints_and_a_plain_run_keeps_them(tmp_path, monkeypatch):
    rendered, record = build_well(rows=1, cols=1, tile=(900, 900), timepoint="0h")
    _, zip_path, out = _plate_for_task(tmp_path, record, rendered)
    from pipeline.fov_checkpoint import _staging_dir_for
    staging = _staging_dir_for(out, WELL, "key")
    seen = []

    def spy(*args, staging_dir=None, **kwargs):
        seen.append(sorted(p.name for p in Path(staging_dir).glob(".fov_*.done")))
        return [], []

    monkeypatch.setattr(nt, "process_well_zip", spy)
    for force in (False, True):
        staging.mkdir(parents=True, exist_ok=True)
        (staging / ".fov_nw_0h.done").touch()
        (staging / ".fov_nw_0h.records.json").write_text("{}")
        with pytest.raises(FileNotFoundError):            # the stub wrote no grid record: the lifecycle stops there
            _call_task(zip_path, out, force=force)
            raise FileNotFoundError
        monkeypatch.setattr(nt, "write_well_report", lambda *a, **k: None, raising=False)
    assert seen == [[".fov_nw_0h.done"], []]


def test_a_nanowell_well_that_fails_leaves_a_worker_trace_in_errors(tmp_path, monkeypatch):
    rendered, record = build_well(rows=1, cols=1, tile=(900, 900), timepoint="0h")
    _, zip_path, out = _plate_for_task(tmp_path, record, rendered)

    def boom(*a, **k):
        raise nt.NanowellInputError("no tiles for this well")

    monkeypatch.setattr(nt, "process_well_zip", boom)
    with pytest.raises(nt.NanowellInputError):
        _call_task(zip_path, out)
    traces = list((out / "errors").glob("*"))
    assert traces and "no tiles for this well" in traces[0].read_text()


# 5 ── what counts as failed; the end-of-run pass cannot fail a finished run ──
@pytest.mark.parametrize("status, failed", [(nr.UNADDRESSED, []), (nr.OK, []), (nr.INCOMPLETE_GRID, ["0h"]),
                                            (nr.NO_RECORD, ["0h"])])
def test_only_a_timepoint_with_no_rows_counts_as_failed(tmp_path, monkeypatch, status, failed):
    rendered, record = build_well(rows=1, cols=1, tile=(900, 900), timepoint="0h")
    _, zip_path, out = _plate_for_task(tmp_path, record, rendered)
    real = nr.locate_timepoint

    def forced(tp, *a, **k):
        located = real(tp, *a, **k)
        if status == nr.UNADDRESSED:
            located.status = status
            located.grid = {**located.grid, "status": status}                 # as locate_timepoint records it
        elif status != nr.OK:
            located.status, located.reason, located.fit, located.indexed, located.grid = status, "forced", None, (), {}
        return located

    monkeypatch.setattr(nr, "locate_timepoint", forced)
    from pipeline import nanowell_measure as nm
    monkeypatch.setattr(nm, "cellpose_segmenter", lambda *a, **k: threshold_segmenter())
    _, got = _call_task(zip_path, out)
    assert got == failed


def test_the_end_of_run_pass_logs_and_records_a_failing_rerun_instead_of_raising(tmp_path, monkeypatch):
    grids, _ = _plate_grids()
    folder = tmp_path / "nanowells"
    folder.mkdir()
    for well, grid in grids.items():
        grid["settings"] = nr.NanowellSettings("100um").key()
        (folder / f"{well}_grid.json").write_text(json.dumps(grid))
    monkeypatch.setattr(nt, "run_zip_task", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("segmenter died")))
    kwargs = {"_nanowell": {"plate_format": "100um"}}
    nt.nanowell_endgame({"plate_format": "100um"}, tmp_path, [("B02", tmp_path / "B02.zip")], kwargs, "PHASE", ["GFP"],
                        "measurements", SCHEMA, "_", "key", grouper=None, parse=None)
    info = json.loads((tmp_path / "pipeline_info.json").read_text())
    assert info["nanowell"]["reanchor"]["remaining"]["B02/24h"].startswith("the re-run failed: RuntimeError")


# 6 ── an added timepoint is checked against the one kept ────────────────────
def test_an_added_timepoint_is_checked_against_the_kept_one(two_timepoints):
    record, load = two_timepoints
    base = {"timepoint": "0h", "angle_deg": 0.0}
    wrong_pitch = nr.run_well("A01", record, ["24h"], load, SETTINGS, segmenter=threshold_segmenter(),
                              reference={**base, "pitch_um": 120.0, "roster_size": 700})
    assert wrong_pitch.timepoints["24h"]["consistency"] == "pitch_mismatch"
    ok = nr.run_well("A01", record, ["24h"], load, SETTINGS, segmenter=threshold_segmenter(),
                     reference={**base, "pitch_um": 172.0, "roster_size": 5})
    assert ok.timepoints["24h"]["consistency"] == "roster_mismatch" and "against 5" in ok.timepoints["24h"]["consistency_detail"]
    assert {r["consistency"] for r in ok.sites} == {"roster_mismatch"}
    same = nr.run_well("A01", record, ["24h"], load, SETTINGS, segmenter=threshold_segmenter(),
                       reference={**base, "pitch_um": 172.0, "roster_size": len(ok.sites)})
    assert same.timepoints["24h"]["consistency"] == "consistent"


# 7 ── the stage offset is checked for spread ────────────────────────────────
def test_a_stage_axis_running_against_the_images_is_not_trusted():
    grid = {f"F{k}": (400.0 * (k % 2) + 10.0, 400.0 * (k // 2) + 20.0) for k in range(4)}
    tiles = [{"well": "A01", "timepoint": "0h", "field": f, "mosaic_xy_px": None,
              "stage_xy_um": [x + 100.0, y + 200.0]} for f, (x, y) in grid.items()]
    assert nr.stage_offset({"tiles": tiles}, "A01", "0h", grid, tolerance_um=172.0) == (100.0, 200.0)
    flipped = [{**t, "stage_xy_um": [t["stage_xy_um"][0], -t["stage_xy_um"][1]]} for t in tiles]     # y runs backwards
    assert nr.stage_offset({"tiles": flipped}, "A01", "0h", grid, tolerance_um=172.0) is None


# 8 ── fluorescence files are paired with tokens by position ─────────────────
def test_a_token_is_not_found_by_looking_for_it_in_the_filename(tmp_path, monkeypatch):
    rendered, record = build_well(rows=1, cols=1, tile=(900, 900), timepoint="0h")
    truth = dict(zip(rendered.array.addresses, rendered.sites_um))
    paint_cell(rendered, truth[(1, 2)], dx_um=3.0)
    glow = type("G", (), {"images": {f: i * 0 + 10.0 for f, i in rendered.images.items()},
                          "true_left_top_um": rendered.true_left_top_um})()
    paint_cell(glow, truth[(1, 2)], dx_um=3.0, amount=200.0)
    plate = tmp_path / "plate"
    (plate / "in").mkdir(parents=True)
    tif = tmp_path / "f.tif"
    with zipfile.ZipFile(plate / "in" / f"{WELL}.zip", "w") as zf:
        for fov, image in rendered.images.items():
            for channel, frame in (("PHASE", image), ("GFP", glow.images[fov])):
                imwrite(str(tif), frame.astype("float32"))
                zf.write(tif, f"GFPrun_{channel}_{WELL}_{fov}_0h.tif")            # the experiment is called GFPrun
    write_record_file(plate / "in" / "acquisition.json", record)
    out = _run_main(monkeypatch, plate, "--nanowell_format", "100um")
    import csv
    import gzip

    row = next(csv.DictReader(gzip.open(out / f"measurements_{WELL}.csv.gz", "rt", newline="")))
    assert float(row["GFP_mean_intensity"]) > 100.0            # the glow, not the phase image under the same stem


# 9 ── only wells made under these settings take part in the plate fit ────────
def test_a_well_analysed_under_other_settings_is_not_in_the_plate_fit(tmp_path):
    folder = tmp_path / "nanowells"
    folder.mkdir()
    (folder / "A01_grid.json").write_text(json.dumps({"scheme_version": 1, "well": "A01", "settings": {"plate_format": "100um"}, "timepoints": {}}))
    (folder / "A02_grid.json").write_text(json.dumps({"scheme_version": 1, "well": "A02", "settings": {"plate_format": "50um"}, "timepoints": {}}))
    assert set(nt._read_grids(tmp_path, {"plate_format": "100um"})) == {"A01"}
    assert set(nt._read_grids(tmp_path)) == {"A01", "A02"}


def test_the_reference_timepoint_is_the_earliest_in_time_not_the_first_in_the_file():
    grids, _ = _plate_grids()
    block = grids["B02"]["timepoints"]
    # "6h" comes first in the file, but no other well was anchored at it: choosing it leaves nothing to fit from.
    grids["B02"]["timepoints"] = {"24h": block["24h"], "6h": dict(block["0h"]), "0h": block["0h"]}
    predictions, reasons = nt.plan_reanchor(grids)
    assert "B02" in predictions and ("B02", "24h") not in reasons


# 10 ── a checkpoint remembers how the timepoint was located ─────────────────
def test_a_checkpoint_written_for_an_unaddressed_timepoint_is_not_resumed_as_addressed(tmp_path):
    store = nt.TimepointCheckpoints(tmp_path / "s", "k")
    store.save("0h", {"radius_um": 1.0, "record_digest": "d", "located": ["unaddressed", None]}, nr.Measured("0h"))
    assert store.load("0h", {"radius_um": 1.0, "record_digest": "d", "located": ["unaddressed", None]}) is not None
    assert store.load("0h", {"radius_um": 1.0, "record_digest": "d", "located": ["ok", "plate_rigid"]}) is None


# 11 ── a refused folder-mode run leaves no nanowell block in the sidecar ─────
def test_a_folder_mode_plate_is_refused_before_the_sidecar_claims_nanowell_mode(tmp_path, monkeypatch, capsys):
    plate = tmp_path / "plate"
    (plate / "in" / WELL).mkdir(parents=True)
    imwrite(str(plate / "in" / WELL / f"exp_PHASE_{WELL}_F0_0h.tif"), np.zeros((8, 8), np.float32))
    imwrite(str(plate / "in" / WELL / f"exp_GFP_{WELL}_F0_0h.tif"), np.zeros((8, 8), np.float32))
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, plate, "--nanowell_format", "100um")
    assert "needs the wells as zips" in capsys.readouterr().err
    info = json.loads((plate / "out" / "pipeline_info.json").read_text())
    assert "nanowell" not in info


def test_a_checkpoint_is_keyed_on_how_the_timepoint_was_located(two_timepoints):
    record, load = two_timepoints
    keys = []

    class Spy:
        def load(self, tp, key):
            keys.append(("load", tp, key))
            return None

        def save(self, tp, key, measured):
            keys.append(("save", tp, key))

    nr.run_well("A01", record, ["24h"], load, SETTINGS, segmenter=threshold_segmenter(), checkpoint=Spy())
    (_, _, load_key), (_, _, save_key) = keys
    assert load_key == save_key and load_key["located"] == ["ok", "fiducial"]


def test_a_pass_that_cannot_even_read_the_plate_is_recorded_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(nt, "reanchor_plate", lambda *a, **k: (_ for _ in ()).throw(OSError("disk gone")))
    nt.nanowell_endgame({"plate_format": "100um"}, tmp_path, [], {"_nanowell": {"plate_format": "100um"}}, "PHASE",
                        ["GFP"], "measurements", SCHEMA, "_", "key", grouper=None, parse=None)
    info = json.loads((tmp_path / "pipeline_info.json").read_text()) if (tmp_path / "pipeline_info.json").exists() else {}
    assert info.get("nanowell", {}).get("reanchor", {}).get("error") == "OSError: disk gone"
