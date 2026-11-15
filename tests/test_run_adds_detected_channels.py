"""Channels found in the input at run time are added to the listed ones.

``services.run_detect.add_detected_channels`` is a union, where full run-time
detection replaces the list: whatever the form lists is kept, in its order,
and every other channel the filenames carry is appended. The Qt-free half is
tested on the function; one test drives the real ``AnalyzeTab`` — the checkbox
a user ticks and the Run path's ``start_run`` — and asserts on the options
``PipelineRunner.start`` receives (CLAUDE.md §7.4 #1, §7.8 rule 3).

Each fixture is built so the right answer differs from the wrong ones: the
listed channels are in an order the files do not use and include one the files
lack, so a replacement (detection's own behaviour), a re-sort, or a filter to
the carried channels each produce a different list.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.run_detect import DetectionError, add_detected_channels

_SCHEMA = {"filename_schema": "experiment:channel:well:fov:timepoint", "filename_sep": "_"}


def _plate(root: Path, channels=("DAPI", "GFP", "mCherry", "Cy5")) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for ch in channels:
        for well in ("A01", "A02", "B01"):
            for fov in ("f01", "f02"):
                (root / f"Exp1_{ch}_{well}_{fov}_t01.tif").write_bytes(b"")
    # The litter a real folder carries (CLAUDE.md §7.1 #4).
    (root / ".DS_Store").write_bytes(b"\0")
    (root / f"._Exp1_{channels[0]}_A01_f01_t01.tif").write_bytes(b"\0")
    (root / "notes.txt").write_text("acquired Tuesday")
    return root


def _spec(raw, *, add=True, **opts) -> dict:
    base = {"raw": str(raw), **_SCHEMA, "nuclear_token": "DAPI",
            "fluor_tokens": ["mCherry", "YFP"], "phase_token": "",
            "segmentation_method": "stardist"}
    return {"opts": {**base, **opts}, "add_detected_channels": add}


def test_the_input_s_channels_are_appended_to_the_listed_ones(tmp_path):
    out, note = add_detected_channels(_spec(_plate(tmp_path / "p")))
    # Listed first, in their order, YFP kept though no file carries it; then
    # the carried channels not listed, in first-seen order; never DAPI.
    assert out["opts"]["fluor_tokens"] == ["mCherry", "YFP", "Cy5", "GFP"]
    assert note == ("Added channels found in the input: Cy5, GFP (beside the 2 "
                    "listed). Channels run: mCherry, YFP, Cy5, GFP.")


def test_a_spec_that_does_not_ask_comes_back_unchanged(tmp_path):
    spec = _spec(_plate(tmp_path / "p"), add=False)
    out, note = add_detected_channels(spec)
    assert out is spec and note == ""
    assert out["opts"]["fluor_tokens"] == ["mCherry", "YFP"]


def test_a_listed_channel_in_another_case_is_not_added_twice(tmp_path):
    """The pipeline writes each channel's columns under its lower-cased name."""
    out, _ = add_detected_channels(_spec(_plate(tmp_path / "p"), fluor_tokens=["gfp"]))
    assert out["opts"]["fluor_tokens"] == ["gfp", "Cy5", "mCherry"]


def test_nothing_new_says_so(tmp_path):
    out, note = add_detected_channels(
        _spec(_plate(tmp_path / "p"), fluor_tokens=["GFP", "mCherry", "Cy5"]))
    assert out["opts"]["fluor_tokens"] == ["GFP", "mCherry", "Cy5"]
    assert note.startswith("No channels beyond those listed were found")


def test_listed_channels_survive_full_detection_replacing_them(tmp_path):
    """``start_run`` passes the form's list separately, because full run-time
    detection has already replaced ``opts["fluor_tokens"]`` by then."""
    spec = _spec(_plate(tmp_path / "p"), fluor_tokens=["GFP", "mCherry"])
    out, _ = add_detected_channels(spec, ["YFP", "GFP"])
    assert out["opts"]["fluor_tokens"] == ["YFP", "GFP", "mCherry", "Cy5"]


def test_label_free_quantifies_the_phase_channel_too(tmp_path):
    raw = _plate(tmp_path / "p", channels=("BF", "GFP"))
    out, _ = add_detected_channels(_spec(
        raw, nuclear_token="BF", phase_token="BF", fluor_tokens=[],
        segmentation_method="cellpose_labelfree"))
    assert out["opts"]["fluor_tokens"] == ["BF", "GFP"]


def test_a_schema_without_a_channel_field_refuses(tmp_path):
    with pytest.raises(DetectionError, match="has no channel field"):
        add_detected_channels(_spec(_plate(tmp_path / "p"),
                                    filename_schema="experiment:ignore:well:fov:timepoint"))


def test_an_input_with_no_matching_names_refuses(tmp_path):
    """Running the listed channels alone would look like a checked input."""
    raw = tmp_path / "p"
    raw.mkdir()
    (raw / "notes.txt").write_text("nothing here")
    with pytest.raises(DetectionError, match="no channel names could be read"):
        add_detected_channels(_spec(raw))


def test_options_that_name_a_channel_are_checked_against_the_merged_list(tmp_path):
    raw = _plate(tmp_path / "p")
    out, _ = add_detected_channels(_spec(raw, smfish_tokens=["Cy5"]))
    assert "Cy5" in out["opts"]["fluor_tokens"]
    with pytest.raises(DetectionError, match="smFISH is marked on Cy3"):
        add_detected_channels(_spec(raw, smfish_tokens=["Cy3"]))


# ── through the real form ───────────────────────────────────────────────────

@pytest.fixture
def tab(qtbot, monkeypatch):
    pytest.importorskip("PySide6")
    import analyze.run_footer
    from analyze.tab import AnalyzeTab

    monkeypatch.setattr(analyze.run_footer, "load_remotes", lambda: {})
    t = AnalyzeTab(parent=None)
    qtbot.addWidget(t)
    t._flatfield_cb.setValue("gaussian")    # not what is under test
    started: list[dict] = []
    monkeypatch.setattr(t._runner, "start",
                        lambda _pipeline, opts, **_kw: started.append(dict(opts)))
    logged: list[str] = []
    monkeypatch.setattr(t, "_log_line", lambda text, tag="INFO": logged.append(text))
    t.started, t.logged = started, logged
    return t


def _set_rows(tab, tokens):
    tab._fluor_rows[0][0].setText(tokens[0])
    for token in tokens[1:]:
        tab._fluor_add_row(token)


def test_ticking_the_box_runs_the_listed_and_the_input_s_channels(tab, tmp_path):
    raw = _plate(tmp_path / "p")
    tab._input_edit.setText(str(raw))
    tab._schema_picker.setSeparator("_")
    tab._schema_picker.setSchema(_SCHEMA["filename_schema"])
    tab._nuclear_token_edit.setText("DAPI")
    _set_rows(tab, ["mCherry", "YFP"])

    tab._add_detected_channels_cb.setChecked(True)
    spec = tab._collect_run_spec()
    assert spec.add_detected_channels is True
    assert tab.start_run(spec) is None

    assert tab.started[-1]["fluor_tokens"] == ["mCherry", "YFP", "Cy5", "GFP"]
    assert tab._detection_note.startswith("Added channels found in the input: Cy5, GFP")
    assert tab._detection_note in tab.logged


def test_without_the_box_the_listed_channels_run_alone(tab, tmp_path):
    raw = _plate(tmp_path / "p")
    tab._input_edit.setText(str(raw))
    tab._schema_picker.setSeparator("_")
    tab._schema_picker.setSchema(_SCHEMA["filename_schema"])
    tab._nuclear_token_edit.setText("DAPI")
    _set_rows(tab, ["mCherry", "YFP"])
    assert tab.start_run(tab._collect_run_spec()) is None
    assert tab.started[-1]["fluor_tokens"] == ["mCherry", "YFP"]
    assert tab._detection_note == ""


def test_with_the_box_ticked_a_blank_channel_list_is_not_a_problem(tab):
    tab._fluor_rows[0][0].setText("")
    titles = [p.title for p in tab._run_problems(live=True)]
    assert "Channel Error" in titles
    tab._add_detected_channels_cb.setChecked(True)
    assert "Channel Error" not in [p.title for p in tab._run_problems(live=True)]


def test_a_queued_job_restores_the_box(tab):
    tab._add_detected_channels_cb.setChecked(True)
    spec = tab.collect_spec()
    assert spec["add_detected_channels"] is True
    tab._add_detected_channels_cb.setChecked(False)
    tab.hydrate_job(spec)
    assert tab._add_detected_channels_cb.isChecked()


def test_with_full_detection_too_the_listed_channels_are_kept(tab, tmp_path):
    """Full run-time detection replaces the list; this box puts the form's
    rows back in front of what it found. ``start_run`` must read the rows
    before detection runs, or YFP is lost."""
    raw = _plate(tmp_path / "p")
    tab._input_edit.setText(str(raw))
    tab._nuclear_token_edit.setText("DAPI")
    _set_rows(tab, ["YFP"])
    tab._auto_detect_cb.setChecked(True)
    tab._add_detected_channels_cb.setChecked(True)
    assert tab.start_run(tab._collect_run_spec()) is None
    # Detection's own order (first seen in the sorted names) after the rows.
    assert tab.started[-1]["fluor_tokens"] == ["YFP", "Cy5", "GFP", "mCherry"]
    assert tab._detection_note.startswith("Detected schema")
    assert ("Added channels found in the input: Cy5, GFP, mCherry (beside the 1 "
            "listed)") in tab._detection_note
