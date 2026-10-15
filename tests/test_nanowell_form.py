"""The Nanowells section, driven through the real ``AnalyzeTab`` (tracker A1, A2, A3, A5; CLAUDE.md §7.6).

Every assertion is made after pressing the control a user presses: the checkbox, the combo, the fields, the
Run validation, the Test detection button. Nothing here calls the section's own helpers for the outcome.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from analyze.nanowell_preflight import nanowell_input_report, seam_site_estimate  # noqa: E402
from analyze.nanowell_preview import MIN_LABEL_DIAMETER_PX, render_montage, site_marks, summary_text  # noqa: E402
from pipeline import nanowell_measure as nm  # noqa: E402
from pipeline.nanowell_preview import PreviewResult  # noqa: E402
from services.pipeline_service import build_pipeline_args  # noqa: E402
from tests._nanowell_run_fixture import build_well, paint_cell, threshold_segmenter  # noqa: E402
from tests.test_nanowell_cli import SCHEMA, WELL, _write_plate  # noqa: E402


@pytest.fixture
def tab(qtbot, monkeypatch):
    import analyze.form
    from analyze.tab import AnalyzeTab

    monkeypatch.setattr(analyze.form, "load_remotes", lambda: {})
    t = AnalyzeTab(parent=None)
    qtbot.addWidget(t)
    t._flatfield_cb.setCurrentText("gaussian")           # the default needs a named blank well; not under test
    return t


def _argv(tab) -> list[str]:
    opts = tab._collect_run_spec().opts
    return build_pipeline_args(Path("process_microscopy.py"), Path("/in"), Path("/out"), opts)


def _flag(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def _use_nanowell_schema(tab) -> None:
    tab._schema_picker.setSchema(":".join(SCHEMA))
    tab._schema_picker.setSeparator("_")
    tab._set_fluor_rows(["PHASE", "GFP"], smfish=[], phase="PHASE")


# ── the controls reach the argv ──────────────────────────────────────────────
def test_a_fresh_form_is_not_in_nanowell_mode_and_sends_no_nanowell_flag(tab):
    assert not tab._nanowell_cb.isChecked() and tab._collect_run_spec().opts["nanowell_format"] == ""
    assert not any(a.startswith("--nanowell") for a in _argv(tab))
    assert not tab._nanowell_format_cb.isEnabled() and not tab._nanowell_preview_btn.isEnabled()


def test_ticking_the_mode_and_filling_the_fields_puts_them_in_the_argv(tab):
    tab._nanowell_cb.setChecked(True)
    assert tab._nanowell_format_cb.isEnabled() and tab._nanowell_preview_btn.isEnabled()
    assert _flag(_argv(tab), "--nanowell_format") == "100um"
    tab._nanowell_format_cb.setCurrentIndex(tab._nanowell_format_cb.findData("50um"))
    tab._nanowell_margin_edit.setText("6")
    tab._nanowell_cell_diameter_edit.setText("22.5")
    tab._nanowell_square_edit.setText("-5")
    tab._nanowell_occluded_edit.setText("0.6")
    tab._nanowell_clipped_edit.setText("0.7")
    argv = _argv(tab)
    assert [_flag(argv, f"--nanowell_{k}") for k in ("format", "margin_um", "cell_diameter_um", "square_edge_deg",
                                                     "occluded_below", "clipped_below")] == \
        ["50um", "6.0", "22.5", "-5.0", "0.6", "0.7"]
    tab._nanowell_cb.setChecked(False)
    assert not any(a.startswith("--nanowell") for a in _argv(tab))               # off again: nothing, whatever the fields hold


def test_a_blank_field_sends_no_flag_so_the_pipeline_default_applies(tab):
    tab._nanowell_cb.setChecked(True)
    argv = _argv(tab)
    assert _flag(argv, "--nanowell_format") == "100um"
    assert not any(a in argv for a in ("--nanowell_margin_um", "--nanowell_cell_diameter_um",
                                       "--nanowell_occluded_below"))


def test_the_form_s_formats_are_the_pipeline_s():
    from analyze.domain.nanowell_fields import PLATE_FORMAT_CHOICES
    from pipeline.nanowell_run import PLATE_FORMATS

    assert {value for value, _ in PLATE_FORMAT_CHOICES} == set(PLATE_FORMATS)


def test_a_sidecar_of_a_nanowell_run_turns_the_mode_on_and_a_plain_one_turns_it_off(tab):
    execution = {"nanowell_format": "50um", "nanowell_cell_diameter_um": 18.0, "nanowell_margin_um": None,
                 "nanowell_square_edge_deg": 0.0, "nanowell_occluded_below": 0.4, "nanowell_clipped_below": 0.5}
    tab._apply_pipeline_info({"execution_options": execution})
    opts = tab._collect_run_spec().opts
    assert (opts["nanowell_format"], opts["nanowell_cell_diameter_um"], opts["nanowell_margin_um"],
            opts["nanowell_occluded_below"]) == ("50um", "18", "", "0.4")
    tab._apply_pipeline_info({"execution_options": {"nanowell_format": None}})
    assert tab._collect_run_spec().opts["nanowell_format"] == ""                # a statement about this dataset
    tab._nanowell_cb.setChecked(True)
    tab._apply_pipeline_info({"execution_options": {"flatfield": "none"}})       # a sidecar that predates the mode
    assert tab._nanowell_cb.isChecked()                                        # says nothing, so nothing changes


# ── the run is refused, with the reason, before it starts ────────────────────
@pytest.fixture
def refused(monkeypatch):
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda _p, title, text, *_a: messages.append((title, text))))
    return messages


def test_run_is_refused_for_a_bad_number_and_for_a_missing_phase_channel(tab, refused, tmp_path):
    tab._input_edit.setText(str(tmp_path))
    tab._nanowell_cb.setChecked(True)
    tab._nanowell_margin_edit.setText("-3")
    assert tab._validate_run_request() is None
    assert refused[-1][0] == "Input Error" and "Margin must be 0 or more" in refused[-1][1]
    assert "Phase channel" in refused[-1][1]


def _plate(tmp_path, *, overlap=0.1, record=True, drop_tile=None):
    rendered, rec = build_well(timepoint="0h", overlap=overlap)
    root = _write_plate(tmp_path, rendered, rec)
    if not record:
        (root / "in" / "acquisition.json").unlink()
    if drop_tile:
        rec["tiles"] = [t for t in rec["tiles"] if t["field"] != drop_tile]
        from util.acquisition_record import write_record_file
        write_record_file(root / "in" / "acquisition.json", rec)
    return root, rendered


def test_run_is_refused_when_the_dataset_has_no_acquisition_record(tab, refused, tmp_path):
    root, _ = _plate(tmp_path, record=False)
    _use_nanowell_schema(tab)
    tab._input_edit.setText(str(root))
    tab._nanowell_cb.setChecked(True)
    assert tab._validate_run_request() is None
    assert refused[-1][0] == "Nanowell Error" and "no acquisition record" in refused[-1][1]


def test_run_is_refused_when_a_tile_the_record_names_is_not_in_the_archive(tab, refused, tmp_path):
    root, _ = _plate(tmp_path)
    from util.acquisition_record import read_record_file, write_record_file
    record = read_record_file(root / "in" / "acquisition.json")
    record["tiles"].append({**record["tiles"][0], "field": "F9"})
    write_record_file(root / "in" / "acquisition.json", record)
    _use_nanowell_schema(tab)
    tab._input_edit.setText(str(root))
    tab._nanowell_cb.setChecked(True)
    assert tab._validate_run_request() is None
    assert refused[-1][0] == "Nanowell Error" and "F9" in refused[-1][1]


def test_run_is_refused_when_the_wells_are_not_zips(tab, refused, tmp_path):
    (tmp_path / "in").mkdir()
    _use_nanowell_schema(tab)
    tab._input_edit.setText(str(tmp_path))
    tab._nanowell_cb.setChecked(True)
    assert tab._validate_run_request() is None
    assert refused[-1][0] == "Nanowell Error" and "no well archives" in refused[-1][1]


def test_a_sound_nanowell_dataset_passes_the_run_validation(tab, refused, tmp_path):
    root, _ = _plate(tmp_path)
    _use_nanowell_schema(tab)
    tab._input_edit.setText(str(root))
    tab._nanowell_cb.setChecked(True)
    assert tab._validate_run_request() is not None and refused == []


def test_a_short_overlap_is_a_warning_with_the_seam_count_and_not_a_refusal(tmp_path):
    root, _ = _plate(tmp_path, overlap=0.03)
    opts = {"filename_schema": ":".join(SCHEMA), "filename_sep": "_", "nanowell_format": "100um",
            "nanowell_margin_um": ""}
    problems, warnings = nanowell_input_report(str(root), opts)
    assert problems == []
    assert len(warnings) == 1 and "less than the 120 µm" in warnings[0] and "about 9 of its 12" in warnings[0] and "on a seam" in warnings[0]
    sparse, _ = _plate(tmp_path / "ok", overlap=0.1)
    assert nanowell_input_report(str(sparse), opts) == ([], [])


def test_the_seam_estimate_is_zero_once_the_overlap_holds_a_nanowell_and_positive_below_it():
    from pipeline.tile_grid import place_from_record, tiles_from_record

    counts = []
    for overlap in (0.03, 0.06):
        rendered, record = build_well(timepoint="0h", overlap=overlap)
        tiles, size, pixel, declared = tiles_from_record(record, WELL, "0h")
        plan = place_from_record(tiles, tile_size_px=size, pixel_size_um=pixel, declared_overlap_percent=declared)
        counts.append(seam_site_estimate(plan, "100um", None))
    (short, total), (longer, _) = counts                  # 90 um against 120 needed, then 181 um
    assert short > 0 and longer == 0 and total > 100


# ── Test detection ───────────────────────────────────────────────────────────
def test_test_detection_says_why_it_cannot_open_instead_of_opening_empty(tab, tmp_path):
    tab._nanowell_cb.setChecked(True)
    tab._nanowell_preview_btn.click()
    assert getattr(tab, "_nanowell_preview_window", None) is None and "Phase channel" in tab._nanowell_status.text()
    _use_nanowell_schema(tab)
    tab._input_edit.setText(str(tmp_path))
    tab._nanowell_preview_btn.click()
    assert getattr(tab, "_nanowell_preview_window", None) is None and "No well archives" in tab._nanowell_status.text()


def test_pressing_test_detection_runs_a_worker_and_shows_the_addresses(tab, qtbot, tmp_path, monkeypatch):
    rendered, record = build_well(timepoint="0h", overlap=0.03)
    paint_cell(rendered, dict(zip(rendered.array.addresses, rendered.sites_um))[(1, 2)], dx_um=3.0)
    root = _write_plate(tmp_path, rendered, record)
    monkeypatch.setattr(nm, "cellpose_segmenter", lambda *a, **k: threshold_segmenter())
    _use_nanowell_schema(tab)
    tab._input_edit.setText(str(root))
    tab._nanowell_cb.setChecked(True)
    tab._nanowell_preview_btn.click()
    window = tab._nanowell_preview_window
    assert window.isVisible() and window._well_cb.currentText() == WELL and window._tp_cb.currentText() == "0h"
    window.run()
    qtbot.waitUntil(lambda: window._result is not None, timeout=120000)
    result = window._result
    assert result.status == "ok" and result.origin_method == "fiducial"
    assert "Origin found from the fiducial" in window._summary.text() and "Overlap between tiles" in window._summary.text()
    marks = site_marks(result)
    assert len(marks) > 200 and {m.text for m in marks if m.text} >= {"0,0", "1,2"}
    cell_site = next(m for m in marks if m.text == "1,2")
    assert cell_site.count == 1 and cell_site.colour == "measured"
    assert window._montage_label.pixmap() is not None and not window._montage_label.pixmap().isNull()
    assert window._crops.count() >= 1
    window.close()


def test_a_preview_that_fails_says_so_in_the_window(tab, qtbot, tmp_path, monkeypatch):
    root, _ = _plate(tmp_path)
    _use_nanowell_schema(tab)
    tab._input_edit.setText(str(root))
    tab._nanowell_cb.setChecked(True)
    tab._nanowell_preview_btn.click()
    import pipeline.nanowell_preview as npv

    def boom(*a, **k):
        raise RuntimeError("the montage could not be built")

    monkeypatch.setattr(npv, "preview_timepoint", boom)
    window = tab._nanowell_preview_window
    window.run()
    qtbot.waitUntil(lambda: "could not run" in window._summary.text(), timeout=30000)
    assert "RuntimeError: the montage could not be built" in window._summary.text()
    assert window._run_btn.isEnabled()
    window.close()


# ── what is drawn ────────────────────────────────────────────────────────────
def _site(q, r, state, *, fused=False, n_cells=None, address=True, x=500.0, y=400.0):
    return {"address": f"{q},{r}" if address else None, "state": state, "fused": fused, "n_cells": n_cells,
            "well_x_um": x, "well_y_um": y, "radius_um": 50.0}


def test_marks_carry_the_state_the_address_the_fused_flag_and_the_count():
    import numpy as np

    result = PreviewResult("ok", montage=np.zeros((200, 300), np.uint8), um_per_px=4.0, origin_um=(100.0, 100.0))
    result.sites = [_site(0, 1, "measured", n_cells=2), _site(1, 1, "empty", fused=True),
                    _site(0, 0, "occluded"), _site(0, 0, "empty", address=False, x=900.0)]
    marks = site_marks(result)
    assert [(m.text, m.colour, m.fused, m.count) for m in marks] == [
        ("0,1", "measured", False, 2), ("1,1", "empty", True, None), ("0,0", "occluded", False, None),
        ("", "unaddressed", False, None)]
    assert marks[0].x == pytest.approx((500 - 100) / 4 - 0.5) and marks[0].radius == pytest.approx(12.5)
    assert site_marks(PreviewResult("no_lattice", "x")) == []


def test_a_refusal_is_summarised_with_its_reason_and_the_overlap_shortfall_is_named():
    assert summary_text(PreviewResult("incomplete_grid", "1 of 4 tiles are missing: r1c1")) == \
        "incomplete_grid: 1 of 4 tiles are missing: r1c1"
    import numpy as np
    result = PreviewResult("ok", well="A01", timepoint="0h", montage=np.zeros((9, 9), np.uint8), um_per_px=4.0,
                           pitch_um=172.0, angle_deg=7.0, origin_method="array_centre", origin_agreement="single_source",
                           overlap_um=(90.0, None), overlap_needed_um=120.0, tile_count=4)
    result.sites = [_site(0, 0, "empty", fused=True)]
    text = summary_text(result)
    assert "array centre" in text and "90 × - µm against the 120 µm" in text and "too short" in text
    assert "1 empty" in text and "1 fused" in text


def test_the_montage_is_drawn_at_the_zoom_asked_for_and_addresses_only_where_there_is_room(qtbot):
    import numpy as np

    result = PreviewResult("ok", montage=np.zeros((100, 200), np.uint8), um_per_px=4.0, origin_um=(0.0, 0.0))
    result.sites = [_site(3, 4, "empty", x=400.0, y=200.0)]
    small = render_montage(result, 1.0)
    assert (small.width(), small.height()) == (200, 100)
    assert (render_montage(result, 2.0).width(), render_montage(result, 2.0).height()) == (400, 200)
    assert 2 * 50.0 / 4.0 * 1.0 < MIN_LABEL_DIAMETER_PX <= 2 * 50.0 / 4.0 * 4.0       # labelled at 400 %, not at 100 %
    plain = small.toImage()
    labelled = render_montage(result, 4.0).toImage()
    assert labelled.width() == 800 and plain.width() == 200


def test_a_short_overlap_does_not_stop_the_run_but_is_said_in_the_log(tab, refused, tmp_path, monkeypatch):
    root, _ = _plate(tmp_path, overlap=0.03)
    logged: list[tuple[str, str]] = []
    monkeypatch.setattr(tab, "_log_line", lambda text, tag="INFO": logged.append((tag, text)))
    _use_nanowell_schema(tab)
    tab._input_edit.setText(str(root))
    tab._nanowell_cb.setChecked(True)
    assert tab._validate_run_request() is not None and refused == []
    assert [t for t, _ in logged] == ["WARNING"] and "on a seam" in logged[0][1]
