"""Run-time detection of an Analyze run's schema and channels (``services.run_detect``).

Qt-free. Every fixture folder carries the litter a real one does — a
``.DS_Store``, AppleDouble ``._`` sidecars, a stray text file — because the
scan that detection rests on samples whatever is on disk (CLAUDE.md §7.1 #4).

Every frozen value in these specs differs from what the files carry, so a
resolver that returned the spec untouched, or fell back to its frozen values
on a partial guess, fails the assertions rather than passing them by accident.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.run_detect import (
    DetectionError,
    channel_option_errors,
    missing_input_error,
    resolve_detection,
)

_FROZEN = {
    "filename_schema": "channel:well:experiment:fov:timepoint",
    "filename_sep": "-",
    "nuclear_token": "NIR",
    "fluor_tokens": ["YFP"],
    "phase_token": "",
    "segmentation_method": "stardist",
}


def _plate(root: Path, channels=("DAPI", "GFP", "mCherry"),
           wells=("A01", "A02", "B01"), exp="Exp1") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for ch in channels:
        for well in wells:
            for fov in ("f01", "f02"):
                (root / f"{exp}_{ch}_{well}_{fov}_t01.tif").write_bytes(b"")
    (root / ".DS_Store").write_bytes(b"\0")
    (root / f"._{exp}_{channels[0]}_{wells[0]}_f01_t01.tif").write_bytes(b"\0")
    (root / "notes.txt").write_text("acquired Tuesday")
    return root


def _spec(raw, **opts) -> dict:
    return {"opts": {"raw": str(raw), **_FROZEN, **opts}, "auto_detect": True}


def test_detection_replaces_every_frozen_layout_value(tmp_path):
    spec, layout = resolve_detection(_spec(_plate(tmp_path / "plate")))
    opts = spec["opts"]
    assert opts["filename_schema"] == "experiment:channel:well:fov:timepoint"
    assert opts["filename_sep"] == "_"
    assert opts["nuclear_token"] == "DAPI"
    assert opts["fluor_tokens"] == ["GFP", "mCherry"]
    assert opts["phase_token"] == ""
    assert layout is not None and layout.channels_from == "scan"
    assert "segmentation channel DAPI" in layout.summary()


def test_detection_touches_nothing_else_in_opts(tmp_path):
    spec, _ = resolve_detection(_spec(_plate(tmp_path / "plate"), workers="3"))
    assert spec["opts"]["workers"] == "3"
    assert spec["opts"]["segmentation_method"] == "stardist"


def test_a_spec_that_did_not_ask_is_returned_unchanged(tmp_path):
    spec = _spec(_plate(tmp_path / "plate"))
    spec["auto_detect"] = False
    out, layout = resolve_detection(spec)
    assert out is spec and layout is None
    assert out["opts"]["filename_sep"] == "-"


def test_a_single_channel_plate_fails_rather_than_keeping_the_frozen_schema(tmp_path):
    """One channel means a constant channel token, so no channel slot can be
    placed and the guess is not confident. The form would leave its own
    values; an unattended run must not."""
    raw = _plate(tmp_path / "plate", channels=("DAPI",))
    with pytest.raises(DetectionError, match="No well and channel pair"):
        resolve_detection(_spec(raw))


def test_an_empty_input_fails_and_says_where(tmp_path):
    raw = tmp_path / "empty"
    raw.mkdir()
    (raw / ".DS_Store").write_bytes(b"\0")
    with pytest.raises(DetectionError, match="No image filenames were found in 'empty'"):
        resolve_detection(_spec(raw))


def test_an_input_that_was_never_written_fails(tmp_path):
    with pytest.raises(DetectionError):
        resolve_detection(_spec(tmp_path / "not_yet"))


def test_a_blank_input_fails_with_the_chaining_advice(tmp_path):
    spec = _spec("")
    spec["opts"]["raw"] = ""
    with pytest.raises(DetectionError, match="Chain it after a job"):
        resolve_detection(spec)
    assert missing_input_error(spec) is not None
    assert missing_input_error(_spec(tmp_path)) is None


def test_the_converter_s_roles_win_over_the_scan_s_guess(tmp_path):
    """The scan would give DAPI the segmentation role (a strong nuclear name);
    the converter's page said GFP. The page is what the user chose."""
    raw = _plate(tmp_path / "plate", channels=("DAPI", "GFP"))
    spec = _spec(raw)
    spec["upstream_layout"] = {
        "schema": "experiment:channel:well:fov:timepoint", "separator": "_",
        "nuclear_token": "GFP", "fluor_tokens": ["DAPI"], "phase_token": "",
    }
    out, layout = resolve_detection(spec)
    assert out["opts"]["nuclear_token"] == "GFP"
    assert out["opts"]["fluor_tokens"] == ["DAPI"]
    assert layout.channels_from == "upstream" and layout.schema_from == "upstream"
    assert "recorded by the converter" in layout.summary()


def test_a_converter_with_no_roles_still_supplies_the_schema(tmp_path):
    raw = _plate(tmp_path / "plate")
    spec = _spec(raw)
    spec["upstream_layout"] = {"schema": "experiment:channel:well:fov:timepoint",
                               "separator": "_", "fluor_tokens": []}
    out, layout = resolve_detection(spec)
    assert layout.schema_from == "upstream" and layout.channels_from == "scan"
    assert out["opts"]["nuclear_token"] == "DAPI"


def test_converter_roles_naming_channels_the_files_lack_fail(tmp_path):
    raw = _plate(tmp_path / "plate", channels=("DAPI", "GFP"))
    spec = _spec(raw)
    spec["upstream_layout"] = {"schema": "experiment:channel:well:fov:timepoint",
                               "separator": "_", "nuclear_token": "Hoechst",
                               "fluor_tokens": ["GFP"]}
    with pytest.raises(DetectionError, match="Hoechst"):
        resolve_detection(spec)


def test_roles_with_no_segmentation_channel_fail(tmp_path):
    raw = _plate(tmp_path / "plate", channels=("DAPI", "GFP"))
    spec = _spec(raw)
    spec["upstream_layout"] = {"schema": "experiment:channel:well:fov:timepoint",
                               "separator": "_", "nuclear_token": "",
                               "fluor_tokens": ["DAPI", "GFP"]}
    with pytest.raises(DetectionError, match="segmentation role"):
        resolve_detection(spec)


# ── options that name a channel, checked against the detected set ───────────

def test_a_stale_smfish_mark_fails_the_run(tmp_path):
    spec = _spec(_plate(tmp_path / "plate"), smfish_tokens=["YFP"])
    with pytest.raises(DetectionError, match="smFISH is marked on YFP"):
        resolve_detection(spec)


def test_a_valid_smfish_mark_is_kept(tmp_path):
    spec, _ = resolve_detection(_spec(_plate(tmp_path / "plate"),
                                      smfish_tokens=["mCherry"]))
    assert spec["opts"]["smfish_tokens"] == ["mCherry"]


def test_a_cellpose_nuclei_channel_the_input_lacks_fails(tmp_path):
    """The pipeline ignores an unknown one silently, so this is the only place
    a stale value can be caught."""
    spec = _spec(_plate(tmp_path / "plate"), segmentation_method="cellpose",
                 nuclear_token="GFP", cellpose_model="cyto3",
                 cellpose_nuclei_channel="YFP")
    with pytest.raises(DetectionError, match="Cellpose nuclei channel is 'YFP'"):
        resolve_detection(spec)


def test_watershed_needs_a_cytoplasm_channel_the_input_carries():
    base = {"nuclear_token": "DAPI", "fluor_tokens": ["GFP", "mCherry"],
            "segmentation_method": "stardist_seeded_watershed_cell"}
    assert channel_option_errors({**base, "cytoplasm_token": "GFP"}) == []
    assert channel_option_errors({**base, "cytoplasm_token": "YFP"})
    assert channel_option_errors({**base, "cytoplasm_token": ""})
    assert channel_option_errors({**base, "cytoplasm_token": "DAPI"})


def test_label_free_without_a_detected_phase_channel_fails(tmp_path):
    spec = _spec(_plate(tmp_path / "plate"), segmentation_method="cellpose_labelfree")
    with pytest.raises(DetectionError, match="phase/brightfield"):
        resolve_detection(spec)


def test_label_free_with_a_brightfield_channel_resolves(tmp_path):
    raw = _plate(tmp_path / "plate", channels=("GFP", "BF"))
    spec, _ = resolve_detection(_spec(raw, segmentation_method="cellpose_labelfree"))
    assert spec["opts"]["phase_token"] == "BF"


def test_blank_input_round_trips_as_blank_not_dot():
    """``Path("")`` is ``.``, which would read as a real input folder."""
    from services.run_spec import RunSpec

    spec = RunSpec(opts={"raw": ""})
    assert spec.to_json()["opts"]["raw"] == ""
    assert RunSpec.from_json(spec.to_json()).opts["raw"] == ""
    assert missing_input_error(spec.to_json()) is not None


def test_auto_detect_and_upstream_layout_round_trip():
    from services.run_spec import RunSpec

    spec = RunSpec(opts={"raw": "/d"}, auto_detect=True,
                   upstream_layout={"schema": "a:b", "separator": "_"})
    again = RunSpec.from_json(spec.to_json())
    assert again.auto_detect is True
    assert again.upstream_layout == {"schema": "a:b", "separator": "_"}
    assert "auto_detect" not in again.runner_opts()


# ── review findings: converter-shaped plates, method-aware roles, exact case ─

def _converter_plate(root: Path, channels, fovs: int, wells=None) -> Path:
    """The converters' real layout: ``in/<well>.zip``, members written field
    by field with the channel varying fastest (``pipeline/ome_explode.py``)."""
    import zipfile

    ind = root / "in"
    ind.mkdir(parents=True)
    wells = wells or [f"{r}{c:02d}" for r in "ABCDEFGH" for c in range(1, 13)]
    for well in wells:
        with zipfile.ZipFile(ind / f"{well}.zip", "w") as zf:
            for f in range(fovs):
                for ch in channels:
                    zf.writestr(f"exp_{ch}_{well}_f{f:02d}_t01.tif", b"x")
            zf.writestr("__MACOSX/._junk", b"x")
    return root


@pytest.mark.parametrize("channels,fovs", [
    (("DAPI", "GFP", "RFP", "Cy5", "BF"), 9),   # lost BF silently
    (("DAPI", "GFP", "RFP"), 4),                # read one channel, not recognised
])
def test_a_full_converter_plate_keeps_every_channel(tmp_path, channels, fovs):
    raw = _converter_plate(tmp_path / "ds", channels, fovs)
    spec, layout = resolve_detection(_spec(raw))
    assert [layout.nuclear_token, *layout.fluor_tokens] == ["DAPI", *channels[1:]]


def test_the_form_s_sample_reaches_every_channel_of_a_converter_plate(tmp_path):
    """The same aliasing sat under the form's Detect button."""
    from util.schema_infer import infer_from_dir

    raw = _converter_plate(tmp_path / "ds", ("DAPI", "GFP", "RFP", "Cy5", "BF"), 9)
    assert infer_from_dir(raw).channels == ["DAPI", "GFP", "RFP", "Cy5", "BF"]


def test_converter_roles_are_checked_against_every_name_not_a_sample(tmp_path):
    raw = _converter_plate(tmp_path / "ds", ("DAPI", "GFP", "RFP", "Cy5", "BF"), 9)
    spec = _spec(raw)
    spec["upstream_layout"] = {
        "schema": "experiment:channel:well:fov:timepoint", "separator": "_",
        "nuclear_token": "DAPI", "fluor_tokens": ["GFP", "RFP", "Cy5", "BF"],
        "phase_token": "BF"}
    out, _ = resolve_detection(spec)
    assert out["opts"]["fluor_tokens"] == ["GFP", "RFP", "Cy5", "BF"]


def test_the_form_s_segmentation_channel_wins_when_the_input_carries_it(tmp_path):
    """Which channel to segment is a choice; the heuristic would take DAPI."""
    raw = _plate(tmp_path / "plate", channels=("DAPI", "CellMask", "GFP"))
    out, layout = resolve_detection(_spec(
        raw, segmentation_method="cellpose", cellpose_model="cyto3",
        nuclear_token="CellMask", cellpose_nuclei_channel="DAPI"))
    assert out["opts"]["nuclear_token"] == "CellMask"
    assert out["opts"]["fluor_tokens"] == ["DAPI", "GFP"]
    assert layout.segmentation_from == "form"
    assert "(as set on the form)" in layout.summary()


def test_cell_body_cellpose_refuses_a_segmentation_channel_chosen_by_name(tmp_path):
    raw = _plate(tmp_path / "plate", channels=("DAPI", "CellMask", "GFP"))
    with pytest.raises(DetectionError, match="segments whole cells"):
        resolve_detection(_spec(raw, segmentation_method="cellpose",
                                cellpose_model="cyto3", nuclear_token="NIR"))


def test_nuclei_cellpose_may_take_the_nuclear_stain_by_name(tmp_path):
    raw = _plate(tmp_path / "plate", channels=("DAPI", "CellMask", "GFP"))
    out, layout = resolve_detection(_spec(
        raw, segmentation_method="cellpose", cellpose_model="nuclei",
        nuclear_token="NIR"))
    assert out["opts"]["nuclear_token"] == "DAPI"
    assert layout.segmentation_from == "names"


def test_a_nuclei_channel_that_is_the_segmentation_channel_says_so():
    errors = channel_option_errors({
        "nuclear_token": "DAPI", "fluor_tokens": ["GFP"],
        "segmentation_method": "cellpose", "cellpose_nuclei_channel": "DAPI"})
    assert errors == ["The Cellpose nuclei channel is 'DAPI', which is the "
                      "segmentation channel, not a fluorescent one."]


def test_label_free_segments_the_phase_channel_and_quantifies_the_rest(tmp_path):
    raw = _plate(tmp_path / "plate", channels=("GFP", "BF"))
    out, layout = resolve_detection(_spec(raw, segmentation_method="cellpose_labelfree"))
    assert out["opts"]["nuclear_token"] == "BF"
    assert out["opts"]["phase_token"] == "BF"
    # Filename order (BF sorts first). GFP is the channel the heuristic would
    # have called the segmentation channel and then dropped from the list.
    assert out["opts"]["fluor_tokens"] == ["BF", "GFP"]
    assert layout.segmentation_from == "phase"


def test_label_free_takes_a_converter_s_natural_roles(tmp_path):
    raw = _plate(tmp_path / "plate", channels=("BF", "GFP"))
    spec = _spec(raw, segmentation_method="cellpose_labelfree")
    spec["upstream_layout"] = {
        "schema": "experiment:channel:well:fov:timepoint", "separator": "_",
        "nuclear_token": "", "fluor_tokens": ["BF", "GFP"], "phase_token": "BF"}
    out, _ = resolve_detection(spec)
    assert out["opts"]["nuclear_token"] == "BF"
    assert out["opts"]["fluor_tokens"] == ["BF", "GFP"]


def test_channel_checks_match_case_exactly_as_the_pipeline_does():
    base = {"nuclear_token": "DAPI", "fluor_tokens": ["GFP", "mCherry"]}
    assert channel_option_errors({**base, "smfish_tokens": ["gfp"]})
    assert channel_option_errors({**base, "smfish_tokens": ["GFP"]}) == []
    wshed = {**base, "segmentation_method": "stardist_seeded_watershed_cell"}
    assert channel_option_errors({**wshed, "cytoplasm_token": "mcherry"})
    assert channel_option_errors({**wshed, "cytoplasm_token": "mCherry"}) == []


def test_a_channel_in_one_well_only_is_still_listed(tmp_path):
    """The channel list comes from every name, not the 400-name sample — a
    channel the sample misses would be dropped from the run without a word."""
    import zipfile

    raw = _converter_plate(tmp_path / "ds", ("DAPI", "GFP", "RFP"), 9)
    with zipfile.ZipFile(raw / "in" / "H12.zip", "a") as zf:
        zf.writestr("exp_Cy7_H12_f00_t01.tif", b"x")
    from util.schema_infer import sample_stems
    assert not any("_Cy7_" in s for s in sample_stems(raw)), "fixture: sample must miss it"
    _spec_out, layout = resolve_detection(_spec(raw))
    assert layout.fluor_tokens == ["GFP", "RFP", "Cy7"]
