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
    ResumeState,
    clear_run_marker,
    default_dest,
    destination_problem,
    read_run_marker,
    resume_state,
    write_run_marker,
    guess_channel_role,
    parse_count,
    RawFields,
    plan_conversion,
    read_layout,
    oriented,
    selection_target,
    stage_path_cycles,
    stage_path_for,
    suggest_layout,
    wells_from_names,
    workable_splits,
    strip_ome_suffix,
)
from pipeline.ome_explode import OmeLayout  # noqa: E402
from util.plate_grid import block_well_labels  # noqa: E402


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


def test_plan_rejects_a_file_missing_image_planes():
    """An incomplete source is refused by the planner, so the page's readout
    carries the real reason and the Convert button never enables — rather than
    the conversion dying part-way through on a page tifffile zero-fills."""
    layout = OmeLayout(
        n_positions=6, size_t=1, size_c=1, size_z=1,
        channel_names=["GFP"], timepoint_tokens=["t0"],
        axes_per_series=["YX"] * 6,
        total_planes=6, missing_planes=2, incomplete_positions=[4, 5],
        first_missing_plane=0,
    )
    with pytest.raises(OmeImportError) as exc:
        plan_conversion(layout, ConversionParams(grid_rows=1, grid_cols=6))
    assert "no image data" in str(exc.value)


def test_plan_reports_incompleteness_before_a_grid_mismatch():
    """The incompleteness check runs first: no grid makes a gapped file
    convertible, so an unrelated divisibility error would be a red herring."""
    layout = OmeLayout(
        n_positions=6, size_t=1, size_c=1, size_z=1,
        channel_names=["GFP"], timepoint_tokens=["t0"],
        axes_per_series=["YX"] * 6,
        total_planes=6, missing_planes=2, incomplete_positions=[4, 5],
        first_missing_plane=0,
    )
    with pytest.raises(OmeImportError) as exc:
        plan_conversion(layout, ConversionParams(grid_rows=1, grid_cols=4))
    assert "no image data" in str(exc.value)
    assert "don't divide evenly" not in str(exc.value)


# ── reading the form as typed ────────────────────────────────────────────────


def test_parse_count_separates_blank_from_malformed():
    assert parse_count("") == (None, True)          # blank is a real answer
    assert parse_count("  ") == (None, True)
    assert parse_count("3") == (3, True)
    assert parse_count("-3") == (-3, True)          # parses; range is a problem
    assert parse_count("two") == (None, False)
    assert parse_count("1.5") == (None, False)


def test_raw_fields_report_malformed_values_rather_than_coercing_them():
    fields = RawFields(rows="1", cols="4", fovs="0", workers="abc")
    assert fields.problems() == [
        "FOVs per well must be 1 or more, or blank for auto.",
        "Workers must be a whole number, or blank for auto.",
    ]
    # …and a clean form has nothing to say.
    assert RawFields(rows="1", cols="4", fovs="", workers="2").problems() == []


def test_raw_fields_skip_the_grid_when_the_order_is_hand_drawn():
    """The grid boxes are disabled then, so their contents are not an input and
    a stale value in one must not block a run that does not read it."""
    fields = RawFields(rows="two", cols="", well_order=("C03", "C04"))
    assert fields.hand_drawn
    assert fields.problems() == []
    assert fields.to_params().well_order == ("C03", "C04")


def test_raw_fields_to_params_matches_the_page_defaults():
    params = RawFields(rows="2", cols="3", origin=" b02 ", fovs="", workers="",
                       direction="col", frame_order="pos_in_time").to_params()
    assert (params.grid_rows, params.grid_cols) == (2, 3)
    assert params.origin == "b02"
    assert params.fovs_per_well is None      # blank = derive
    assert params.workers == 0               # blank = auto
    assert params.direction == "col"
    assert params.well_order == ()


def test_summary_names_the_wells_and_which_input_produced_them(tmp_path):
    p = _write_multi(tmp_path / "m.ome.tif", positions=3)
    layout = read_layout(p)

    grid = plan_conversion(layout, ConversionParams(grid_rows=1, grid_cols=3))
    assert "3 well(s) A01→A03 (from the grid)" in grid.summary()

    clicked = plan_conversion(layout, ConversionParams(
        grid_rows=1, grid_cols=3, well_order=("H12", "H11", "G11")))
    assert "3 well(s) H12→G11 (explicit path)" in clicked.summary()


def test_destination_problem_flags_a_folder_that_already_holds_a_conversion(tmp_path):
    assert destination_problem("") == ""
    assert destination_problem(tmp_path / "fresh") == ""

    used = tmp_path / "used"
    (used / "in").mkdir(parents=True)
    (used / "in" / "A01.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    assert "already holds converted wells" in destination_problem(used).lower()

    finished = tmp_path / "finished"
    (finished / "out").mkdir(parents=True)
    (finished / "out" / "pipeline_info.json").write_text("{}")
    assert "finished dataset" in destination_problem(finished)


# ── the layout the file already knows ────────────────────────────────────────


def _plate_scan(path, rows, cols, fovs, *, serpentine=True):
    """An OME-TIFF whose Plane elements carry real plate stage coordinates."""
    pitch, fov = 9000.0, 600.0
    coords = []
    for r in range(rows):
        line = list(range(cols))
        if serpentine and r % 2:
            line.reverse()
        for c in line:
            for f in range(fovs):
                coords.append((c * pitch + (f % 2) * fov, r * pitch + (f // 2) * fov))
    planes = "".join(
        f'<Plane TheT="{i}" TheC="0" TheZ="0" PositionX="{x}" PositionY="{y}"/>'
        for i, (x, y) in enumerate(coords))
    mapping = "".join(
        f'<TiffData FirstT="{i}" FirstC="0" FirstZ="0" IFD="{i}" PlaneCount="1"/>'
        for i in range(len(coords)))
    xml = (
        '<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06">'
        '<Image ID="Image:0" Name="scan"><Pixels ID="Pixels:0" '
        'DimensionOrder="XYZCT" Type="uint16" SizeX="6" SizeY="6" SizeC="1" '
        f'SizeZ="1" SizeT="{len(coords)}">'
        '<Channel ID="Channel:0:0" Name="DAPI" SamplesPerPixel="1"/>'
        f'{planes}{mapping}</Pixels></Image></OME>')
    tifffile.imwrite(str(path), np.zeros((len(coords), 6, 6), np.uint16),
                     description=xml, metadata=None, photometric="minisblack")
    return path


def test_suggest_layout_reads_the_grid_and_fovs_from_stage_coordinates(tmp_path):
    layout = read_layout(_plate_scan(tmp_path / "scan.ome.tif", 2, 3, 4))
    suggestion = suggest_layout(layout)
    assert (suggestion.rows, suggestion.cols) == (2, 3)
    assert suggestion.fovs_per_well == 4
    assert suggestion.direction == "row"
    assert suggestion.n_positions == 24
    # A serpentine is left as grid fields the user can still edit, not frozen
    # into an explicit path.
    assert suggestion.well_order == ()
    assert "2×3 wells × 4 FOV(s)" in suggestion.describe()


def test_suggest_layout_reads_a_raster_as_a_grid_that_does_not_snake(tmp_path):
    """A raster used to be handed over as an explicit order, because the grid
    fields could only express a snake. They can say either now, so the offer is
    the editable grid plus an unticked Snake box — and an explicit order is kept
    for paths a grid genuinely cannot describe."""
    layout = read_layout(_plate_scan(tmp_path / "r.ome.tif", 2, 3, 1,
                                     serpentine=False))
    suggestion = suggest_layout(layout, origin="B02")
    assert (suggestion.rows, suggestion.cols) == (2, 3)
    assert suggestion.serpentine is False
    assert suggestion.well_order == ()
    assert "no snake" in suggestion.describe()
    # And the wells it means are the raster ones, not the snake's.
    assert block_well_labels(2, 3, origin="B02", direction=suggestion.direction,
                             serpentine=False) == [
        "B02", "B03", "B04", "C02", "C03", "C04"]


def test_suggest_layout_is_none_when_the_file_says_nothing_useful(tmp_path):
    p = _write_packed(tmp_path / "bare.ome.tif", n_frames=6)   # no Plane positions
    assert suggest_layout(read_layout(p)) is None


def test_a_plan_refuses_a_source_that_is_not_a_sound_ome_tiff(tmp_path):
    """plan_conversion gates on the whole validation, not only missing planes,
    so the page's readout carries whichever fault the file actually has."""
    layout = OmeLayout(
        n_positions=1, size_t=0, size_c=1, size_z=1, channel_names=["GFP"],
        timepoint_tokens=[], axes_per_series=["YX"], single_image=True,
        n_frames=4, named_ome=True, has_ome_xml=False, series_kind="uniform",
    )
    with pytest.raises(OmeImportError, match="no readable OME metadata"):
        plan_conversion(layout, ConversionParams(grid_rows=1, grid_cols=4))


# ── finishing an interrupted conversion ──────────────────────────────────────


def test_the_marker_makes_an_interrupted_run_resumable(tmp_path):
    """Without it a half-finished folder is indistinguishable from someone
    else's dataset, and the destination check has to refuse both."""
    src = _write_packed(tmp_path / "s.ome.tif", n_frames=6)
    dest = tmp_path / "out"
    params = ConversionParams(grid_rows=1, grid_cols=6, experiment="e")

    assert read_run_marker(dest) is None
    assert resume_state(dest, src, params) == ResumeState()

    write_run_marker(dest, src, params)
    (dest / "in").mkdir(parents=True)
    (dest / "in" / "A01.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)

    state = resume_state(dest, src, params)
    assert state.resumable and state.wells_done == 1
    assert "Resuming" in state.hint()
    # …and the folder stops being a refusal while that is true.
    assert destination_problem(dest, src, params) == ""

    clear_run_marker(dest)
    assert read_run_marker(dest) is None
    assert "already holds converted wells" in destination_problem(
        dest, src, params).lower()


def test_a_marker_from_different_settings_is_refused_not_resumed(tmp_path):
    """Two mappings produce the same filenames from different images, so the
    names on disk cannot tell them apart — only the recorded settings can."""
    src = _write_packed(tmp_path / "s.ome.tif", n_frames=6)
    dest = tmp_path / "out"
    write_run_marker(dest, src, ConversionParams(grid_rows=1, grid_cols=6))
    # A well on disk: a marker with nothing behind it describes a run that
    # wrote nothing, which is not a mismatch to warn about but an empty folder.
    (dest / "in").mkdir(parents=True, exist_ok=True)
    (dest / "in" / "A01.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)

    for changed in (
        ConversionParams(grid_rows=2, grid_cols=3),
        ConversionParams(grid_rows=1, grid_cols=6, origin="B02"),
        ConversionParams(grid_rows=1, grid_cols=6, direction="col"),
        ConversionParams(grid_rows=1, grid_cols=6, fovs_per_well=2),
        ConversionParams(grid_rows=1, grid_cols=6, experiment="other"),
        ConversionParams(grid_rows=1, grid_cols=6, well_order=("A01", "A02")),
    ):
        state = resume_state(dest, src, changed)
        assert state.mismatched, changed
        assert not state.resumable, changed
        assert "different settings" in destination_problem(dest, src, changed)

    # Workers is not part of the mapping — it changes speed, not output.
    faster = ConversionParams(grid_rows=1, grid_cols=6, workers=4)
    assert resume_state(dest, src, faster).resumable


def test_a_marker_for_another_source_file_is_a_mismatch(tmp_path):
    a = _write_packed(tmp_path / "a.ome.tif", n_frames=6)
    b = _write_packed(tmp_path / "b.ome.tif", n_frames=6)
    dest = tmp_path / "out"
    params = ConversionParams(grid_rows=1, grid_cols=6)
    write_run_marker(dest, a, params)
    (dest / "in").mkdir(parents=True, exist_ok=True)
    (dest / "in" / "A01.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    assert resume_state(dest, b, params).mismatched


def test_a_source_replaced_at_the_same_path_is_a_mismatch(tmp_path):
    """The path alone does not identify a file. A re-export, a restore, or the
    next plate saved over the last is a different source, and resuming across
    that leaves half of one plate and half of another under one set of names."""
    src = _write_packed(tmp_path / "s.ome.tif", n_frames=6)
    dest = tmp_path / "out"
    params = ConversionParams(grid_rows=1, grid_cols=6)
    write_run_marker(dest, src, params)
    (dest / "in").mkdir(parents=True, exist_ok=True)
    (dest / "in" / "A01.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    assert resume_state(dest, src, params).resumable

    _write_packed(src, n_frames=8)                 # same name, different plate
    assert resume_state(dest, src, params).mismatched


def test_a_marker_with_nothing_behind_it_does_not_block_the_folder(tmp_path):
    """A run that failed before its first archive, or was cancelled and then had
    its settings changed, leaves a hidden marker in an otherwise empty folder.
    There is nothing there for a second mapping to contaminate, and "empty the
    folder" does not clear a dotfile."""
    src = _write_packed(tmp_path / "s.ome.tif", n_frames=6)
    dest = tmp_path / "out"
    write_run_marker(dest, src, ConversionParams(grid_rows=1, grid_cols=6))

    changed = ConversionParams(grid_rows=2, grid_cols=3)
    assert resume_state(dest, src, changed) == ResumeState()
    assert destination_problem(dest, src, changed) == ""


def test_convert_finishes_an_interrupted_run_and_says_so(tmp_path):
    from pipeline.ome_explode import OmeExplodeCancelled

    src = _write_packed(tmp_path / "s.ome.tif", n_frames=6)
    dest = tmp_path / "out"
    params = ConversionParams(grid_rows=1, grid_cols=6, experiment="e", workers=1)

    ticks = {"n": 0}

    def stop_after_a_couple_of_wells():
        ticks["n"] += 1
        return ticks["n"] > 2

    with pytest.raises(OmeExplodeCancelled):
        convert(src, dest, params, is_cancelled=stop_after_a_couple_of_wells)
    partial = sorted(p.stem for p in (dest / "in").glob("*.zip"))
    assert partial and len(partial) < 6
    assert read_run_marker(dest) is not None          # the run is still open

    summary = convert(src, dest, params)
    assert f"{len(partial)} kept from an interrupted run" in summary
    assert len(sorted((dest / "in").glob("*.zip"))) == 6
    # Finished: the folder is a dataset now, not an interrupted run.
    assert read_run_marker(dest) is None


def test_frame_order_and_timepoints_come_from_the_repeat_pattern(tmp_path):
    """An OME <Image> has no position dimension, so the file's DimensionOrder
    cannot say how positions and timepoints interleave in its T axis — the one
    setting the user had to guess. The coordinates say it outright."""
    from datasets.ome_import.stage_layout import infer_frame_order

    pitch = 9000.0
    positions = [(c * pitch, r * pitch) for r in range(2) for c in range(3)]
    assert infer_frame_order(positions) == ("single", 6, 1)
    assert infer_frame_order(positions * 4) == ("pos_in_time", 6, 4)
    assert infer_frame_order(
        [p for p in positions for _ in range(4)]) == ("time_in_pos", 6, 4)
    # Neither pattern, so nothing is claimed.
    assert infer_frame_order(positions + positions[:2]) is None
    assert infer_frame_order([]) is None


def test_wells_come_from_the_metadata_when_it_names_them():
    """A StageLabel or Image name is the only thing in an OME-TIFF that says
    where on the *plate* a position was; coordinates give a shape, not a place."""
    assert wells_from_names(["B02", "B03"]) == ["B02", "B03"]
    assert wells_from_names(["Well B2, Site 1", "Well B2, Site 2"]) == ["B02", "B02"]
    assert wells_from_names(["scan_B02_f0", "scan_C03_f0"]) == ["B02", "C03"]
    # All or nothing: a half-parsed set is a half-labelled plate.
    assert wells_from_names(["B02", "Position 3"]) == []
    assert wells_from_names(["B02 and C03"]) == []
    assert wells_from_names([]) == []


def test_a_named_scan_needs_no_origin_at_all(tmp_path):
    """The strongest case: the file names its own wells, so the visit order is
    read rather than inferred and nothing is left for the user to enter."""
    pitch = 9000.0
    coords = [(c * pitch, r * pitch) for r in range(2) for c in range(3)]
    wells = ["C04", "C05", "C06", "D04", "D05", "D06"]
    images = "".join(
        f'<Image ID="Image:{i}" Name="{wells[i]}"><Pixels ID="Pixels:{i}" '
        f'DimensionOrder="XYZCT" Type="uint16" SizeX="6" SizeY="6" SizeC="1" '
        f'SizeZ="1" SizeT="1">'
        f'<Channel ID="Channel:{i}:0" Name="DAPI" SamplesPerPixel="1"/>'
        f'<StageLabel Name="Well {wells[i]}"/>'
        f'<Plane TheT="0" TheC="0" TheZ="0" PositionX="{x}" PositionY="{y}"/>'
        f'<TiffData FirstT="0" FirstC="0" FirstZ="0" IFD="{i}" PlaneCount="1"/>'
        f'</Pixels></Image>'
        for i, (x, y) in enumerate(coords))
    p = tmp_path / "named.ome.tif"
    tifffile.imwrite(str(p), np.zeros((len(coords), 6, 6), np.uint16),
                     description=('<OME xmlns="http://www.openmicroscopy.org/'
                                  f'Schemas/OME/2016-06">{images}</OME>'),
                     metadata=None, photometric="minisblack")

    suggestion = suggest_layout(read_layout(p))
    assert suggestion.origin == "C04"
    assert suggestion.well_order == ("C04", "C05", "C06", "D04", "D05", "D06")
    assert "starting at C04" in suggestion.describe()


def test_a_refusal_names_the_splits_the_frame_count_can_actually_take():
    """A refusal that only says the current grid does not fit leaves the user
    guessing at a number the file has already decided. 6958 frames is
    2 x 7 x 7 x 71 — it does not divide by 96, 48 or 24, so no full-plate
    reading exists at all, and the few that do are worth naming."""
    assert workable_splits(6958) == [
        (1, 6958), (2, 3479), (7, 994), (14, 497), (49, 142), (71, 98)]
    assert workable_splits(0) == []
    assert workable_splits(12) == [(1, 12), (2, 6), (3, 4), (4, 3), (6, 2), (12, 1)]


def test_the_split_refusal_carries_those_splits(tmp_path):
    p = _write_packed(tmp_path / "s.ome.tif", n_frames=98)
    with pytest.raises(OmeImportError) as exc:
        plan_conversion(read_layout(p), ConversionParams(grid_rows=3, grid_cols=4))
    assert "can only split as" in str(exc.value)
    assert "14 position(s) × 7 timepoint(s)" in str(exc.value)


# ── the stage path draws one cycle ───────────────────────────────────────────


def _timelapse_layout(cycles=4, wobble=0.31):
    """A packed single-image file: 2x3 wells, 4 fields each, imaged *cycles* times.

    The repeats carry stage wobble, because that is what a microscope writes and
    because a fixture of bit-identical repeats cannot tell a working revisit test
    from one comparing floats for equality.
    """
    pitch, fov = 9000.0, 600.0
    once = [
        (col * pitch + (f % 2) * fov, row * pitch + (f // 2) * fov)
        for well in range(6)
        for row, col in [divmod(well, 3)]
        for f in range(4)
    ]
    points = [
        (x + wobble * ((t % 3) - 1), y - wobble * 0.7 * (t % 2))
        for t in range(cycles) for x, y in once
    ]
    return OmeLayout(
        n_positions=1, size_t=len(points), size_c=1, size_z=1,
        channel_names=["GFP"], timepoint_tokens=[], axes_per_series=[],
        single_image=True, n_frames=len(points), stage_positions=points,
    ), len(once)


def test_the_stage_path_draws_one_cycle_of_a_timelapse():
    """A timelapse walks the same route once per timepoint, so drawing every
    coordinate is one map overdrawn N times — every dot on a dot, every line on
    a line, and the numbering running to N times the map's real length."""
    layout, per_cycle = _timelapse_layout(cycles=4)
    points = stage_path_for(layout, "A01")

    assert len(points) == per_cycle
    assert len(layout.stage_positions) == per_cycle * 4      # the file has them all
    # And it is the *first* cycle: the path still starts at the anchor well.
    assert points[0] == (0.0, 0.0)


def test_the_path_reports_what_it_left_out():
    """A picture quietly showing a quarter of the file's coordinates is the
    silent substitution CLAUDE.md §7 forbids, so the counts are available for
    the caption to say so."""
    layout, per_cycle = _timelapse_layout(cycles=4)
    assert stage_path_cycles(layout) == (per_cycle, per_cycle * 4)

    once, _ = _timelapse_layout(cycles=1)
    drawn, total = stage_path_cycles(once)
    assert drawn == total                     # nothing repeats, nothing to say


def test_a_timelapse_still_suggests_the_fields_of_one_cycle():
    """The count the form is filled with is fields per well, not visits per
    well: a four-field map over four timepoints is four fields, not sixteen."""
    layout, _ = _timelapse_layout(cycles=4)
    suggestion = suggest_layout(layout, "A01")
    assert (suggestion.rows, suggestion.cols) == (2, 3)
    assert suggestion.fovs_per_well == 4
    assert suggestion.frame_order == "pos_in_time"
    assert suggestion.n_timepoints == 4


# ── mirroring the stage's axes ───────────────────────────────────────────────


def test_oriented_is_the_same_layout_when_nothing_is_mirrored():
    """The page keys its inference cache on the identity of what this returns,
    so a fresh object per call would re-infer on every keystroke — 190 ms of it
    on a full plate, on the UI thread."""
    layout, _ = _timelapse_layout(cycles=1)
    assert oriented(layout) is layout
    assert oriented(layout, flip_x=False, flip_y=False) is layout


def test_oriented_reflects_only_the_coordinates():
    layout, _ = _timelapse_layout(cycles=1)
    flipped = oriented(layout, flip_x=True)

    assert flipped is not layout
    assert layout.stage_positions[1][0] > 0                 # the original stands
    assert [x for x, _ in flipped.stage_positions] == [
        -x for x, _ in layout.stage_positions]
    assert [y for _, y in flipped.stage_positions] == [
        y for _, y in layout.stage_positions]
    # Everything that is not a coordinate is carried across untouched: the flip
    # is about which way the stage ran, not about what the file holds.
    assert (flipped.n_frames, flipped.size_c, flipped.channel_names) == (
        layout.n_frames, layout.size_c, layout.channel_names)


def test_mirroring_moves_the_wells_a_conversion_would_write():
    """Not just the picture. Mirroring only the drawing would leave the map the
    user checks the layout against disagreeing with the layout the run uses —
    and agreeing with it before the flip, which is worse than no control."""
    layout, _ = _timelapse_layout(cycles=1)
    straight = suggest_layout(layout, "A01")
    flipped = suggest_layout(oriented(layout, flip_x=True), "A01")

    assert (flipped.rows, flipped.cols) == (straight.rows, straight.cols)
    assert flipped.fovs_per_well == straight.fovs_per_well
    # Same block, walked from the other end.
    assert list(flipped.well_order) == ["A03", "A02", "A01", "B03", "B02", "B01"]

    path = stage_path_for(oriented(layout, flip_x=True), "A03")
    assert path[0] == (2.0, 0.0)                    # anchored at the first well
    assert path[-1][0] < path[0][0]                 # and it runs leftwards


def _positional_ome(path, rows=2, cols=3):
    """A scan whose every frame is filled with its own stage-position index.

    The pixel value is the evidence: it says which position an image came from,
    so an archive can be read back as a position→well mapping rather than as a
    set of filenames that merely look plausible.
    """
    pitch = 9000.0
    coords = [(c * pitch, r * pitch) for r in range(rows) for c in range(cols)]
    planes = "".join(
        f'<Plane TheT="{i}" TheC="0" TheZ="0" PositionX="{x}" PositionY="{y}"/>'
        for i, (x, y) in enumerate(coords))
    mapping = "".join(
        f'<TiffData FirstT="{i}" FirstC="0" FirstZ="0" IFD="{i}" PlaneCount="1"/>'
        for i in range(len(coords)))
    xml = (
        '<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06">'
        '<Image ID="Image:0" Name="scan"><Pixels ID="Pixels:0" '
        'DimensionOrder="XYZCT" Type="uint16" SizeX="4" SizeY="4" SizeC="1" '
        f'SizeZ="1" SizeT="{len(coords)}">'
        '<Channel ID="Channel:0:0" Name="DAPI" SamplesPerPixel="1"/>'
        f'{planes}{mapping}</Pixels></Image></OME>')
    tifffile.imwrite(
        str(path),
        np.stack([np.full((4, 4), i, np.uint16) for i in range(len(coords))]),
        description=xml, metadata=None, photometric="minisblack")
    return path


def _convert_mirrored(src, dest, *, flip_x=False, flip_y=False):
    """Convert *src* as the page would with those boxes ticked, and read back
    which stage position ended up in which well."""
    import io
    import zipfile

    layout = oriented(read_layout(str(src)), flip_x=flip_x, flip_y=flip_y)
    suggestion = suggest_layout(layout, "A01")
    params = ConversionParams(
        grid_rows=suggestion.rows, grid_cols=suggestion.cols, origin="A01",
        direction=suggestion.direction, serpentine=suggestion.serpentine,
        fovs_per_well=suggestion.fovs_per_well,
        frame_order=suggestion.frame_order or "single",
        well_order=tuple(suggestion.well_order), experiment="p")
    plan_conversion(layout, params)
    convert(str(src), str(dest), params)

    found = {}
    for archive in sorted((dest / "in").glob("*.zip")):
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                arr = tifffile.imread(io.BytesIO(zf.read(member)))
                assert int(arr.min()) == int(arr.max())   # untouched, not resampled
                found[archive.stem.upper()] = int(arr.max())
    return found


def test_mirroring_reflects_the_wells_a_conversion_writes(tmp_path):
    """The end of the chain, asserted on the archives rather than on anything
    the page computed: the flip has to move which well each *image* is filed
    under. The pixels are never touched — a mirrored run writes the same frame
    it always did, into the reflected well."""
    src = _positional_ome(tmp_path / "flip.ome.tif")

    straight = _convert_mirrored(src, tmp_path / "d0")
    assert straight == {"A01": 0, "A02": 1, "A03": 2,
                        "B01": 3, "B02": 4, "B03": 5}

    # Left to right, about the Y axis: columns reverse, rows stay.
    assert _convert_mirrored(src, tmp_path / "dx", flip_x=True) == {
        "A01": 2, "A02": 1, "A03": 0, "B01": 5, "B02": 4, "B03": 3}
    # Top to bottom, about the X axis: rows reverse, columns stay.
    assert _convert_mirrored(src, tmp_path / "dy", flip_y=True) == {
        "A01": 3, "A02": 4, "A03": 5, "B01": 0, "B02": 1, "B03": 2}
    # Both at once, which is a 180-degree turn of the block.
    assert _convert_mirrored(src, tmp_path / "dxy", flip_x=True, flip_y=True) == {
        "A01": 5, "A02": 4, "A03": 3, "B01": 2, "B02": 1, "B03": 0}


# ── shapes, and a sweep that does not snake ──────────────────────────────────


def _shaped_ome(path, cells, *, fovs=1):
    """A file visiting exactly *cells* in order, each frame holding its index."""
    pitch, fov = 9000.0, 600.0
    coords = [
        (c * pitch + (f % 2) * fov, r * pitch + (f // 2) * fov)
        for r, c in cells for f in range(fovs)
    ]
    planes = "".join(
        f'<Plane TheT="{i}" TheC="0" TheZ="0" PositionX="{x}" PositionY="{y}"/>'
        for i, (x, y) in enumerate(coords))
    mapping = "".join(
        f'<TiffData FirstT="{i}" FirstC="0" FirstZ="0" IFD="{i}" PlaneCount="1"/>'
        for i in range(len(coords)))
    xml = (
        '<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06">'
        '<Image ID="Image:0" Name="scan"><Pixels ID="Pixels:0" '
        'DimensionOrder="XYZCT" Type="uint16" SizeX="4" SizeY="4" SizeC="1" '
        f'SizeZ="1" SizeT="{len(coords)}">'
        '<Channel ID="Channel:0:0" Name="DAPI" SamplesPerPixel="1"/>'
        f'{planes}{mapping}</Pixels></Image></OME>')
    tifffile.imwrite(
        str(path),
        np.stack([np.full((4, 4), i, np.uint16) for i in range(len(coords))]),
        description=xml, metadata=None, photometric="minisblack")
    return path


def test_an_l_shaped_scan_converts_into_the_wells_it_visited(tmp_path):
    """End to end, on the archives. The shape used to be refused outright, so
    the run could only happen by clicking five wells in the right order."""
    src = _shaped_ome(tmp_path / "l.ome.tif",
                      [(0, 0), (0, 1), (0, 2), (1, 0), (2, 0)])
    found = _convert_mirrored(src, tmp_path / "dl")
    assert found == {"A01": 0, "A02": 1, "A03": 2, "B01": 3, "C01": 4}
    # No archive for the wells the scan never visited.
    assert "B02" not in found and "C03" not in found


def test_a_scan_that_skips_a_row_keeps_the_wells_it_skipped_empty(tmp_path):
    """Rows A and C, B never imaged. Closing the gap up writes every image of
    the third row into the second."""
    src = _shaped_ome(tmp_path / "gap.ome.tif",
                      [(0, 0), (0, 1), (2, 0), (2, 1)])
    found = _convert_mirrored(src, tmp_path / "dg")
    assert found == {"A01": 0, "A02": 1, "C01": 2, "C02": 3}


def test_a_raster_converts_without_snaking(tmp_path):
    """The grid could only ever generate a snake, so a raster had to be clicked
    well by well. Read as a snake it mislabels every second line backwards —
    the right well count, every other row mirrored."""
    src = _shaped_ome(tmp_path / "raster.ome.tif",
                      [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2)])
    layout = read_layout(src)
    suggestion = suggest_layout(layout, "A01")
    assert suggestion.serpentine is False
    assert suggestion.well_order == ()          # the grid fields can say this now

    assert _convert_mirrored(src, tmp_path / "draster") == {
        "A01": 0, "A02": 1, "A03": 2, "B01": 3, "B02": 4, "B03": 5}
    # And the snake the page would have forced instead, for contrast.
    params = ConversionParams(grid_rows=2, grid_cols=3, origin="A01",
                              serpentine=True, fovs_per_well=1, experiment="p")
    convert(str(src), str(tmp_path / "dsnake"), params)
    import io
    import zipfile

    snaked = {}
    for archive in sorted((tmp_path / "dsnake" / "in").glob("*.zip")):
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                snaked[archive.stem.upper()] = int(
                    tifffile.imread(io.BytesIO(zf.read(member))).max())
    assert snaked == {"A01": 0, "A02": 1, "A03": 2, "B03": 3, "B02": 4, "B01": 5}


def test_a_scan_with_rows_of_different_lengths_converts(tmp_path):
    """Row A across seven columns, rows B and C across six, swept as a snake.
    Nothing writes an archive for the two wells at the end of the short rows."""
    cells = ([(0, c) for c in range(7)]
             + [(1, c) for c in range(5, -1, -1)]
             + [(2, c) for c in range(6)])
    src = _shaped_ome(tmp_path / "ragged.ome.tif", cells)
    found = _convert_mirrored(src, tmp_path / "dr")

    assert len(found) == 19
    assert {k: v for k, v in found.items() if k.startswith("A")} == {
        "A01": 0, "A02": 1, "A03": 2, "A04": 3, "A05": 4, "A06": 5, "A07": 6}
    # Row B ran backwards, so its wells hold the frames in reverse.
    assert {k: v for k, v in found.items() if k.startswith("B")} == {
        "B06": 7, "B05": 8, "B04": 9, "B03": 10, "B02": 11, "B01": 12}
    assert "B07" not in found and "C07" not in found
