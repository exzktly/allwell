"""Qt-free planning for the OME-TIFF converter (``datasets.ome_import``)."""

from __future__ import annotations

import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")

from datasets.ome_import.apply import convert  # noqa: E402
from datasets.ome_import.ome_import_compute import (  # noqa: E402
    ConversionParams,
    OmeImportError,
    channel_tokens_from_roles,
    default_channel_roles,
    default_dest,
    guess_channel_role,
    plan_conversion,
    read_layout,
    selection_target,
    strip_ome_suffix,
)


def _write_multi(path, *, positions, size_t=1, size_c=1, channel_names=("GFP",)):
    with tifffile.TiffWriter(str(path), ome=True) as tw:
        for _ in range(positions):
            arr = np.zeros((size_t, size_c, 6, 6), np.uint16)
            tw.write(arr, metadata={"axes": "TCYX", "Channel": {"Name": list(channel_names)}})
    return path


def _write_packed(path, *, n_frames, size_c=1, channel_names=("GFP",)):
    data = np.zeros((n_frames, size_c, 6, 6), np.uint16)
    for f in range(n_frames):
        for c in range(size_c):
            data[f, c] = f * 10 + c
    tifffile.imwrite(
        str(path), data, ome=True, photometric="minisblack",
        metadata={"axes": "TCYX", "Channel": {"Name": list(channel_names)}},
    )
    return path


def test_default_dest_and_strip_suffix(tmp_path):
    assert strip_ome_suffix("plate.ome.tif") == "plate"
    assert strip_ome_suffix("plate.ome.tiff") == "plate"
    assert default_dest(tmp_path / "plate.ome.tif") == tmp_path / "plate_dataset"


def test_plan_multi_image_derives_fovs(tmp_path):
    p = _write_multi(tmp_path / "m.ome.tif", positions=6, size_t=2)
    plan = plan_conversion(read_layout(p), ConversionParams(grid_rows=1, grid_cols=3))
    assert not plan.single_image
    assert plan.fovs_per_well == 2
    assert plan.n_timepoints == 2
    assert plan.well_labels == ["A01", "A02", "A03"]


def test_plan_multi_image_fovs_mismatch(tmp_path):
    p = _write_multi(tmp_path / "m.ome.tif", positions=6)
    with pytest.raises(OmeImportError):
        plan_conversion(read_layout(p),
                        ConversionParams(grid_rows=2, grid_cols=2, fovs_per_well=2))


def test_plan_single_image_frame_orders(tmp_path):
    p = _write_packed(tmp_path / "p.ome.tif", n_frames=12)
    layout = read_layout(p)
    # single timepoint needs wells×fovs == frames.
    with pytest.raises(OmeImportError):
        plan_conversion(layout, ConversionParams(grid_rows=1, grid_cols=3,
                                                 fovs_per_well=2, frame_order="single"))
    # positions-within-timepoint: 6 positions × 2 timepoints.
    plan = plan_conversion(layout, ConversionParams(
        grid_rows=1, grid_cols=3, fovs_per_well=2, frame_order="pos_in_time"))
    assert plan.single_image
    assert plan.n_positions == 6
    assert plan.n_timepoints == 2


def test_plan_single_image_needs_fovs_with_timepoints(tmp_path):
    p = _write_packed(tmp_path / "p.ome.tif", n_frames=12)
    with pytest.raises(OmeImportError, match="FOVs per well"):
        plan_conversion(read_layout(p),
                        ConversionParams(grid_rows=1, grid_cols=3, frame_order="pos_in_time"))


def test_plan_grid_overflow_rejected(tmp_path):
    p = _write_packed(tmp_path / "p.ome.tif", n_frames=4)
    with pytest.raises(OmeImportError):
        plan_conversion(read_layout(p), ConversionParams(
            grid_rows=2, grid_cols=2, origin="H12", fovs_per_well=1))


def test_plan_explicit_well_order_non_rectangular(tmp_path):
    # 3 positions, an explicit non-rectangular ordering — the plan uses that order
    # and count, ignoring the (seed) grid fields entirely.
    p = _write_multi(tmp_path / "m.ome.tif", positions=3)
    plan = plan_conversion(
        read_layout(p),
        ConversionParams(grid_rows=1, grid_cols=1,
                         well_order=("A01", "B02", "H12")),
    )
    assert plan.well_labels == ["A01", "B02", "H12"]
    assert plan.n_wells == 3
    assert plan.fovs_per_well == 1


def test_plan_well_order_count_mismatch_rejected(tmp_path):
    p = _write_multi(tmp_path / "m.ome.tif", positions=3)
    with pytest.raises(OmeImportError):
        plan_conversion(
            read_layout(p),
            ConversionParams(grid_rows=1, grid_cols=1,
                             well_order=("A01", "B02")),  # 2 wells, 3 positions
        )


def test_plan_well_order_duplicate_rejected(tmp_path):
    p = _write_multi(tmp_path / "m.ome.tif", positions=2)
    with pytest.raises(OmeImportError):
        plan_conversion(
            read_layout(p),
            ConversionParams(grid_rows=1, grid_cols=1,
                             well_order=("A01", "A01")),
        )


def test_selection_target_multi_image_reports_positions(tmp_path):
    p = _write_multi(tmp_path / "m.ome.tif", positions=6)
    layout = read_layout(p)
    # FOVs auto → target is the raw position budget, no implied well count.
    t = selection_target(layout, ConversionParams(grid_rows=1, grid_cols=1))
    assert t.positions == 6
    assert t.noun == "stage position"
    assert t.expected_wells is None
    assert "6 stage position(s)" in t.hint()


def test_selection_target_expected_wells_when_fovs_pinned(tmp_path):
    p = _write_multi(tmp_path / "m.ome.tif", positions=6)
    layout = read_layout(p)
    # 6 positions ÷ 2 FOVs → expect 3 wells.
    t = selection_target(layout, ConversionParams(grid_rows=1, grid_cols=1, fovs_per_well=2))
    assert t.expected_wells == 3
    assert "3 well(s) at 2 FOV(s)/well" in t.hint()


def test_selection_target_single_image_packed_positions_unknown(tmp_path):
    p = _write_packed(tmp_path / "p.ome.tif", n_frames=12)
    layout = read_layout(p)
    # "single" frame order → every frame is a position, so the budget is known.
    single = selection_target(layout, ConversionParams(
        grid_rows=1, grid_cols=1, frame_order="single"))
    assert single.positions == 12
    assert single.noun == "frame"
    # Packed timepoints → positions fold with timepoints, budget not knowable.
    packed = selection_target(layout, ConversionParams(
        grid_rows=1, grid_cols=1, frame_order="pos_in_time"))
    assert packed.positions is None
    assert "timepoints" in packed.hint()


def test_guess_channel_role_by_name():
    assert guess_channel_role("DAPI") == "nuclear"
    assert guess_channel_role("Hoechst 33342") == "nuclear"
    assert guess_channel_role("NIR") == "nuclear"
    assert guess_channel_role("RFP") == "nuclear"     # secondary segmentation guess
    assert guess_channel_role("Phase") == "phase"
    assert guess_channel_role("Brightfield") == "phase"
    assert guess_channel_role("GFP") == "fluor"


def test_default_channel_roles_picks_one_nuclear():
    # DAPI is a strong nuclear stain; the rest fluorescent.
    roles = default_channel_roles(["DAPI", "GFP", "RFP"])
    assert roles == {"DAPI": "nuclear", "GFP": "fluor", "RFP": "fluor"}


def test_default_channel_roles_uses_nir_or_rfp_when_no_strong_stain():
    # No DAPI/Hoechst → NIR / RFP is the best-guess segmentation channel.
    assert default_channel_roles(["GFP", "NIR"]) == {"GFP": "fluor", "NIR": "nuclear"}
    assert default_channel_roles(["GFP", "RFP"]) == {"GFP": "fluor", "RFP": "nuclear"}


def test_default_channel_roles_strong_stain_beats_secondary_regardless_of_order():
    # RFP is a secondary nuclear guess, but a real DAPI stain wins even when RFP
    # comes first.
    roles = default_channel_roles(["RFP", "DAPI", "GFP"])
    assert roles == {"RFP": "fluor", "DAPI": "nuclear", "GFP": "fluor"}


def test_default_channel_roles_falls_back_to_first_channel():
    # No nuclear-looking name at all → the first channel is assigned nuclear so
    # segmentation has a channel to run on.
    roles = default_channel_roles(["GFP", "YFP"])
    assert roles["GFP"] == "nuclear"
    assert roles["YFP"] == "fluor"


def test_default_channel_roles_fallback_skips_a_phase_channel():
    # The positional fallback must not take a brightfield channel: segmenting an
    # uncorrected phase image with a fluorescence routine measures nothing. The
    # channel list arrives in sorted-filename order, so DIC leads it often.
    roles = default_channel_roles(["DIC", "GFP", "mCherry"])
    assert roles == {"DIC": "phase", "GFP": "nuclear", "mCherry": "fluor"}
    assert default_channel_roles(["BF", "YFP"]) == {"BF": "phase", "YFP": "nuclear"}


def test_default_channel_roles_uses_a_phase_channel_only_as_a_last_resort():
    # Every channel reads as phase → one still has to take the segmentation role,
    # because segmentation needs an input.
    roles = default_channel_roles(["BF", "DIC"])
    assert roles["BF"] == "nuclear"
    assert roles["DIC"] == "phase"


def test_default_channel_roles_stain_still_beats_a_leading_phase_channel():
    roles = default_channel_roles(["DIC", "GFP", "DAPI"])
    assert roles == {"DIC": "phase", "GFP": "fluor", "DAPI": "nuclear"}


def test_default_channel_roles_keeps_only_first_nuclear():
    roles = default_channel_roles(["DAPI", "Hoechst", "GFP"])
    assert roles["DAPI"] == "nuclear"
    assert roles["Hoechst"] == "fluor"   # demoted — only one nuclear


def test_channel_tokens_from_roles_aggregates():
    # The phase channel is named as such AND stays in the fluor list: the
    # pipeline quantifies it (it only skips its background correction), and the
    # Analyze form carries the Phase designation as a checkbox on a channel row,
    # so a phase token outside fluor_tokens has no row to be ticked on.
    tokens = channel_tokens_from_roles([
        ("DAPI", "nuclear"), ("GFP", "fluor"), ("RFP", "fluor"),
        ("Phase", "phase"), ("Junk", "skip"),
    ])
    assert tokens.nuclear_token == "DAPI"
    assert tokens.fluor_tokens == ["GFP", "RFP", "Phase"]
    assert tokens.phase_token == "Phase"
    info = tokens.as_pipeline_info()
    assert info == {"nuclear_token": "DAPI",
                    "fluor_tokens": ["GFP", "RFP", "Phase"],
                    "phase_token": "Phase"}


def test_channel_tokens_from_roles_keeps_the_phase_channel_in_place():
    # Order follows the input pairs, so the phase channel keeps its position
    # rather than being appended or dropped.
    tokens = channel_tokens_from_roles([
        ("DIC", "phase"), ("GFP", "fluor"), ("RFP", "nuclear"),
    ])
    assert tokens.fluor_tokens == ["DIC", "GFP"]
    assert tokens.phase_token == "DIC"
    assert tokens.nuclear_token == "RFP"


def test_convert_writes_dataset_folder(tmp_path):
    p = _write_packed(tmp_path / "p.ome.tif", n_frames=6, size_c=2,
                      channel_names=("DAPI", "GFP"))
    dest = tmp_path / "out_ds"
    params = ConversionParams(grid_rows=1, grid_cols=3, fovs_per_well=2,
                              frame_order="pos_in_time")
    summary = convert(p, dest, params)
    # Output is per-well zips (ready Analyze input), not loose TIFs.
    import zipfile
    zips = sorted(z.name for z in (dest / "in").glob("*.zip"))
    assert zips == ["A01.zip", "A02.zip", "A03.zip"]
    assert "3 well(s)" in summary
    with zipfile.ZipFile(dest / "in" / "A01.zip") as zf:
        members = zf.namelist()
    # 2 FOVs × 2 channels × 1 timepoint per well; experiment token = file stem.
    assert len(members) == 4
    assert "p_DAPI_A01_f0_t0.tif" in members


def test_convert_output_is_ready_analyze_input(tmp_path):
    # The per-well zips must be usable by resolve_input_output *directly* — no
    # WellPlateZipper regrouping pass (that's the point of zip-direct output).
    from services.input_resolution_service import resolve_input_output

    p = _write_packed(tmp_path / "p.ome.tif", n_frames=6)
    dest = tmp_path / "ds"
    convert(p, dest, ConversionParams(grid_rows=1, grid_cols=3, fovs_per_well=2,
                                      frame_order="pos_in_time"))
    logs: list[str] = []
    in_dir, out_dir, on_success = resolve_input_output(
        dest, log_fn=logs.append,
        filename_schema="experiment:channel:well:fov:timepoint", filename_sep="_")
    assert in_dir == dest / "in"
    assert out_dir == dest / "out"
    assert on_success is None  # nothing to clean up — already grouped
    assert not any("zipper" in m.lower() for m in logs)


def test_convert_multi_image_with_custom_origin_and_direction(tmp_path):
    # Every other convert()-level test in this file drives a single-image
    # packed file. apply.convert() forwards grid_rows/grid_cols/origin/
    # direction to pipeline.ome_explode.explode_ome_tiff as separate keyword
    # arguments (a different call chain than plan_conversion's dry-run math),
    # and until now nothing exercised that forwarding for a multi-image
    # (multi-position) source or a non-default origin/direction — a swapped
    # grid_rows/grid_cols or a dropped origin/direction kwarg in apply.py
    # would pass every existing convert()-level test here.
    import io
    import zipfile

    p = tmp_path / "m.ome.tif"
    with tifffile.TiffWriter(str(p), ome=True) as tw:
        for pos in range(6):
            arr = np.full((1, 1, 6, 6), pos, np.uint16)  # pixel value == position index
            tw.write(arr, metadata={"axes": "TCYX", "Channel": {"Name": ["GFP"]}})
    dest = tmp_path / "multi_ds"
    params = ConversionParams(grid_rows=2, grid_cols=3, origin="B02", direction="col")
    convert(p, dest, params)

    # Hand-derived serpentine-by-column labels for a 2x3 block from B02:
    # c=0 (even): B02, C02 -- c=1 (odd, reversed): C03, B03 -- c=2 (even): B04, C04.
    expected = {
        "B02.zip": (0, "B02"), "C02.zip": (1, "C02"), "C03.zip": (2, "C03"),
        "B03.zip": (3, "B03"), "B04.zip": (4, "B04"), "C04.zip": (5, "C04"),
    }
    zips = sorted(z.name for z in (dest / "in").glob("*.zip"))
    assert zips == sorted(expected)
    for zname, (pos, well) in expected.items():
        with zipfile.ZipFile(dest / "in" / zname) as zf:
            members = zf.namelist()
            assert members == [f"m_GFP_{well}_f0_t0.tif"]
            arr = tifffile.imread(io.BytesIO(zf.read(members[0])))
            assert int(arr.max()) == pos  # confirms position -> well mapping, not just naming
