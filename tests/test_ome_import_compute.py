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
    selection_target,
    suggest_layout,
    strip_ome_suffix,
)
from pipeline.ome_explode import OmeLayout  # noqa: E402


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
    assert "3 well(s) H12→G11 (clicked path)" in clicked.summary()


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


def test_suggest_layout_gives_a_raster_scan_an_explicit_order(tmp_path):
    """The grid fields can only express a serpentine, so anything else has to be
    handed over as the order itself — placed at the origin the user chose."""
    layout = read_layout(_plate_scan(tmp_path / "r.ome.tif", 2, 3, 1,
                                     serpentine=False))
    suggestion = suggest_layout(layout, origin="B02")
    assert suggestion.well_order == ("B02", "B03", "B04", "C02", "C03", "C04")
    assert "as an explicit path" in suggestion.describe()


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
