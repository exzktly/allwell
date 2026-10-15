"""The Analyze form's custom plate format (tracker A7) and tile grid for a dataset with no acquisition record
(tracker P15, A8), driven through the real AnalyzeTab: the controls, the argv they become, the run validation
that reads the input, and a sidecar restoring them."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from tests.test_nanowell_form import _argv, _flag, _plate, _use_nanowell_schema, refused, tab  # noqa: E402,F401


def _custom(tab, diameter="60", pitch="120"):  # noqa: F811
    tab._nanowell_cb.setChecked(True)
    tab._nanowell_format_cb.setCurrentIndex(tab._nanowell_format_cb.findData("custom"))
    tab._nanowell_diameter_edit.setText(diameter)
    tab._nanowell_pitch_edit.setText(pitch)


def test_the_custom_format_shows_its_geometry_and_sends_it(tab):  # noqa: F811
    tab._nanowell_cb.setChecked(True)
    # The section starts collapsed, so the rows' own state is what is asked: hidden for a known format.
    assert all(row.isHidden() for row in tab._nanowell_custom_rows)
    _custom(tab)
    assert not any(row.isHidden() for row in tab._nanowell_custom_rows)
    argv = _argv(tab)
    assert (_flag(argv, "--nanowell_format"), _flag(argv, "--nanowell_diameter_um"),
            _flag(argv, "--nanowell_pitch_um")) == ("custom", "60.0", "120.0")
    tab._nanowell_format_cb.setCurrentIndex(tab._nanowell_format_cb.findData("100um"))
    argv = _argv(tab)
    assert "--nanowell_diameter_um" not in argv and "--nanowell_pitch_um" not in argv  # left in the box, not sent


def test_a_custom_format_without_its_geometry_or_with_touching_nanowells_is_refused(tab, refused, tmp_path):  # noqa: F811
    _use_nanowell_schema(tab)
    tab._input_edit.setText(str(tmp_path))
    _custom(tab, pitch="")
    assert tab._validate_run_request() is None
    assert "needs the nanowell diameter and the lattice pitch" in refused[-1][1]
    _custom(tab, diameter="100", pitch="100")
    assert tab._validate_run_request() is None and "would touch its neighbours" in refused[-1][1]


def test_the_tile_grid_reaches_the_argv_with_its_scan_order(tab):  # noqa: F811
    tab._nanowell_cb.setChecked(True)
    tab._nanowell_grid_edit.setText("2x2")
    tab._nanowell_scan_cb.setCurrentIndex(tab._nanowell_scan_cb.findData("bottom-right-column-serpentine"))
    tab._nanowell_overlap_edit.setText("10")
    tab._nanowell_pixel_edit.setText("4.3")
    argv = _argv(tab)
    assert (_flag(argv, "--nanowell_grid"), _flag(argv, "--nanowell_scan"), _flag(argv, "--nanowell_overlap_percent"),
            _flag(argv, "--nanowell_pixel_size_um")) == ("2x2", "bottom-right-column-serpentine", "10.0", "4.3")
    tab._nanowell_grid_edit.setText("")
    tab._nanowell_overlap_edit.setText("")
    tab._nanowell_pixel_edit.setText("")
    assert not any(a in _argv(tab) for a in ("--nanowell_grid", "--nanowell_scan"))  # no grid: none of it


def _grid(tab, grid="2x2"):  # noqa: F811
    tab._nanowell_grid_edit.setText(grid)
    tab._nanowell_overlap_edit.setText("10")
    tab._nanowell_pixel_edit.setText("4.3")


def test_without_a_record_the_grid_lets_the_run_go_and_says_how_tiles_will_be_placed(tab, refused, tmp_path,  # noqa: F811
                                                                                   monkeypatch):
    root, _ = _plate(tmp_path, record=False)
    _use_nanowell_schema(tab)
    tab._input_edit.setText(str(root))
    tab._nanowell_cb.setChecked(True)
    assert tab._validate_run_request() is None and "describe the tile grid instead" in refused[-1][1]
    logged = []
    monkeypatch.setattr(tab, "_log_line", lambda text, level=None: logged.append(text))
    _grid(tab)
    assert tab._validate_run_request() is not None
    assert any("the tiles will be placed from the 2 x 2 grid in top-left-row order" in line for line in logged)


def test_a_grid_too_small_for_the_wells_fields_is_refused_by_name(tab, refused, tmp_path):  # noqa: F811
    root, _ = _plate(tmp_path, record=False)
    _use_nanowell_schema(tab)
    tab._input_edit.setText(str(root))
    tab._nanowell_cb.setChecked(True)
    _grid(tab, grid="1x2")
    assert tab._validate_run_request() is None
    assert "A well has more fields than the tile grid holds: A01 at 0h: 4 fields for a 1 x 2 grid" in refused[-1][1]


def test_the_too_many_fields_check_reads_a_channel_last_in_the_name(tmp_path):
    """The last token carries the extension (``PH.tif``); the check must still find the phase channel's fields."""
    import zipfile

    from analyze.nanowell_preflight import _pattern_report

    (tmp_path / "A01.zip").parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(tmp_path / "A01.zip", "w") as zf:
        for k in range(1, 7):
            zf.writestr(f"A01_F{k}_T0_PH.tif", b"")
    problems, _ = _pattern_report([tmp_path / "A01.zip"], {
        "nanowell_grid": "2x2", "filename_schema": "well:fov:timepoint:channel", "filename_sep": "_",
        "phase_token": "PH"})
    assert problems and "A01 at T0: 6 fields for a 2 x 2 grid" in problems[0]


def test_a_hidden_custom_number_does_not_refuse_a_known_format():
    from analyze.domain.nanowell_fields import nanowell_option_errors

    errors = nanowell_option_errors({"nanowell_format": "100um", "nanowell_diameter_um": "abc",
                                        "nanowell_pitch_um": "-5"})
    assert not any("diameter" in e or "pitch" in e for e in errors)


def test_a_partial_grid_is_refused_with_what_it_lacks(tab, refused, tmp_path):  # noqa: F811
    _use_nanowell_schema(tab)
    tab._input_edit.setText(str(tmp_path))
    tab._nanowell_cb.setChecked(True)
    tab._nanowell_grid_edit.setText("5x5")
    assert tab._validate_run_request() is None
    assert "A tile grid needs the tile overlap as well" in refused[-1][1]
    assert "A tile grid needs the pixel size as well" in refused[-1][1]


def test_a_sidecar_restores_the_custom_format_and_the_grid(tab):  # noqa: F811
    tab._apply_nanowell_settings({"nanowell_format": "custom", "nanowell_diameter_um": 60.0, "nanowell_pitch_um": 120.0,
                                  "nanowell_grid": "5x5", "nanowell_scan": "top-right-row-serpentine",
                                  "nanowell_overlap_percent": 10.0, "nanowell_pixel_size_um": 0.65})
    opts = tab._collect_run_spec().opts
    assert (opts["nanowell_format"], opts["nanowell_diameter_um"], opts["nanowell_pitch_um"]) == ("custom", "60", "120")
    assert (opts["nanowell_grid"], opts["nanowell_scan"], opts["nanowell_overlap_percent"],
            opts["nanowell_pixel_size_um"]) == ("5x5", "top-right-row-serpentine", "10", "0.65")
    assert not any(row.isHidden() for row in tab._nanowell_custom_rows)


def test_test_detection_without_a_record_places_the_tiles_from_the_grid(tab, qtbot, tmp_path, monkeypatch):  # noqa: F811
    """The button through to the worker, on a dataset with no acquisition record: the tiles are placed from the
    grid the form describes, as the run would place them, and the nanowells are addressed."""
    from pipeline import nanowell_measure as nm
    from tests._nanowell_run_fixture import threshold_segmenter
    from tests.test_nanowell_cli import WELL

    root, _ = _plate(tmp_path, record=False)
    monkeypatch.setattr(nm, "cellpose_segmenter", lambda *a, **k: threshold_segmenter())
    _use_nanowell_schema(tab)
    tab._input_edit.setText(str(root))
    tab._nanowell_cb.setChecked(True)
    tab._nanowell_preview_btn.click()
    assert getattr(tab, "_nanowell_preview_window", None) is None or not tab._nanowell_preview_window.isVisible()
    assert "describe the tile grid" in tab._nanowell_status.text()
    _grid(tab)
    tab._nanowell_preview_btn.click()
    window = tab._nanowell_preview_window
    assert window.isVisible() and window._well_cb.currentText() == WELL and window._tp_cb.currentText() == "0h"
    window.run()
    qtbot.waitUntil(lambda: window._result is not None, timeout=120000)
    assert window._result.status == "ok" and window._result.tile_count == 4
    window.close()
