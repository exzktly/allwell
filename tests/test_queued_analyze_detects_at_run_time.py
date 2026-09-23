"""A queued Analyze run detects its schema and channels when it starts.

Driven through the real ``AnalyzeTab`` (§7.8 rule 3): the checkbox a user
ticks, the "+ Queue" button they press, and ``start_from_queue`` — with the
assertion at the boundary, on the options ``PipelineRunner.start`` actually
receives (§7.4 #1), not on anything the tab computed along the way.

The chain test goes through the real ``QueueBridge``, in the order the app
runs it: a converter job finishes, the queue materialises the Analyze job
chained after it, and dispatches it (§7.7 #1).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal  # noqa: E402


def _plate(root, channels=("DAPI", "GFP", "mCherry")):
    root.mkdir(parents=True, exist_ok=True)
    for ch in channels:
        for well in ("A01", "A02", "B01"):
            for fov in ("f01", "f02"):
                (root / f"Exp1_{ch}_{well}_{fov}_t01.tif").write_bytes(b"")
    (root / ".DS_Store").write_bytes(b"\0")
    (root / f"._Exp1_{channels[0]}_A01_f01_t01.tif").write_bytes(b"\0")
    return root


@pytest.fixture
def tab(qtbot, monkeypatch, tmp_path):
    import analyze.form
    from analyze.tab import AnalyzeTab

    monkeypatch.setattr(analyze.form, "load_remotes", lambda: {})
    t = AnalyzeTab(parent=None)
    qtbot.addWidget(t)
    # The flat-field default needs a named blank well; not what is under test.
    t._flatfield_cb.setCurrentText("gaussian")
    # A form whose layout differs from the plate's in every detected value.
    t._schema_picker.setSeparator("-")
    t._nuclear_token_edit.setText("NIR")
    started: list[dict] = []
    monkeypatch.setattr(t._runner, "start",
                        lambda _pipeline, opts, **_kw: started.append(dict(opts)))
    shown: list[str] = []
    monkeypatch.setattr(t._dashboard, "set_status", lambda text: shown.append(text))
    logged: list[str] = []
    monkeypatch.setattr(t, "_log_line", lambda text, tag="INFO": logged.append(text))
    t.shown, t.logged = shown, logged
    finished: list[tuple] = []
    t.jobFinished.connect(lambda ok, msg, path: finished.append((ok, msg, path)))
    t.started = started
    t.finished = finished
    return t


def _queue_from_form(tab) -> dict:
    jobs: list[dict] = []
    tab.set_queue_sink(jobs.append)
    tab._queue_btn.click()
    assert len(jobs) == 1
    return jobs[0]


def test_the_checkbox_queues_a_job_whose_input_does_not_exist_yet(
        tab, tmp_path, monkeypatch):
    """Everything detection replaces, and everything that reads the input, is
    deferred. Each of these would refuse the form without the checkbox."""
    from PySide6.QtWidgets import QMessageBox

    refused: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda _p, title, *_a: refused.append(title)))
    tab._input_edit.setText(str(tmp_path / "not_written_yet"))
    tab._schema_picker.setSchema("well:well")            # invalid
    tab._flatfield_cb.setCurrentText("empty_well")
    tab._background_well_edit.setText("H12")             # absent from no input

    tab.set_queue_sink(lambda _spec: None)
    assert tab._validate_run_request(for_queue=True) is None
    assert refused == ["Schema Error"]                   # refused without it

    refused.clear()
    tab._auto_detect_cb.setChecked(True)
    spec = _queue_from_form(tab)
    assert refused == []
    assert spec["auto_detect"] is True
    assert spec["opts"]["background_well"] == "H12"


def test_the_run_button_still_asks_the_input_about_the_blank_well(tab, tmp_path,
                                                                   monkeypatch):
    """Only the queue may name a folder not written yet."""
    from PySide6.QtWidgets import QMessageBox

    refused: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda _p, title, *_a: refused.append(title)))
    tab._input_edit.setText(str(_plate(tmp_path / "plate")))
    tab._flatfield_cb.setCurrentText("empty_well")
    tab._background_well_edit.setText("H12")
    assert tab._validate_run_request() is None
    assert refused == ["Background Well Error"]


def test_the_picker_greys_out_and_run_stays_armed_with_an_invalid_schema(tab):
    tab._schema_picker.setSchema("well:well")          # invalid on purpose
    assert not tab._run_btn.isEnabled()
    tab._auto_detect_cb.setChecked(True)
    assert not tab._schema_picker.isEnabled()
    assert tab._nuclear_row_widget.isEnabled()          # still a choice
    assert tab._run_btn.isEnabled()
    assert tab._input_edit.isEnabled()
    tab._auto_detect_cb.setChecked(False)
    assert tab._schema_picker.isEnabled()
    assert not tab._run_btn.isEnabled()


def test_start_from_queue_hands_the_runner_the_detected_layout(tab, tmp_path):
    raw = tmp_path / "plate"
    tab._input_edit.setText(str(raw))
    tab._auto_detect_cb.setChecked(True)
    spec = _queue_from_form(tab)
    assert spec["opts"]["nuclear_token"] == "NIR"       # frozen at enqueue
    _plate(raw)                                         # written later

    tab.start_from_queue(spec)

    assert tab.finished == [], tab.finished
    assert len(tab.started) == 1
    opts = tab.started[0]
    assert opts["filename_schema"] == "experiment:channel:well:fov:timepoint"
    assert opts["filename_sep"] == "_"
    assert opts["nuclear_token"] == "DAPI"
    assert opts["fluor_tokens"] == ["GFP", "mCherry"]
    note = tab._detection_note
    assert note.startswith("Detected schema experiment:channel:well:fov:timepoint")
    assert "segmentation channel DAPI" in note
    # Said where the user can see it, because no field on the form holds it.
    assert note in tab.logged
    assert tab.shown[-1] == note


def test_the_finished_job_s_message_carries_what_was_detected(tab, tmp_path):
    raw = _plate(tmp_path / "plate")
    tab._input_edit.setText(str(raw))
    tab._auto_detect_cb.setChecked(True)
    tab.start_from_queue(_queue_from_form(tab))
    tab._run_ok, tab._run_error = True, None
    tab._log_q.put(("finished", None))
    tab._poll_log()
    assert tab.finished[-1][0] is True
    assert tab.finished[-1][1] == tab._detection_note != ""


def test_without_the_checkbox_the_frozen_layout_runs(tab, tmp_path):
    raw = _plate(tmp_path / "plate")
    tab._input_edit.setText(str(raw))
    spec = _queue_from_form(tab)
    tab.start_from_queue(spec)
    assert tab.started[0]["nuclear_token"] == "NIR"
    assert tab._detection_note == ""


def test_undecidable_detection_fails_the_job_and_starts_nothing(tab, tmp_path):
    raw = tmp_path / "plate"
    tab._input_edit.setText(str(raw))
    tab._auto_detect_cb.setChecked(True)
    spec = _queue_from_form(tab)
    _plate(raw, channels=("DAPI",))                     # one channel: not confident

    tab.start_from_queue(spec)

    assert tab.started == []
    assert len(tab.finished) == 1
    ok, message, _path = tab.finished[0]
    assert ok is False and "No well and channel pair" in message


def test_a_job_never_given_an_input_fails_with_the_reason(tab):
    tab._input_edit.setText("")
    spec = _queue_from_form(tab)
    assert spec["opts"]["raw"] == ""
    tab.start_from_queue(spec)
    assert tab.started == []
    assert tab.finished and "no input folder" in tab.finished[0][1]


def test_hydrating_a_job_without_the_flag_unticks_the_box(tab):
    tab._auto_detect_cb.setChecked(True)
    spec = tab.collect_spec()
    del spec["auto_detect"]                  # a job queued before the flag existed
    tab.hydrate_job(spec)
    assert not tab._auto_detect_cb.isChecked()


def test_the_form_s_detect_button_writes_what_the_shared_rule_says(tab, tmp_path,
                                                                    monkeypatch):
    """The button and a queued run share ``scan_pipeline_info``; swapping it
    out must change what the button writes."""
    import analyze.domain.input as analyze_input

    raw = _plate(tmp_path / "plate")
    monkeypatch.setattr(analyze_input, "scan_pipeline_info",
                        lambda _guess: ({"nuclear_token": "Sentinel"}, None))
    tab._input_edit.setText(str(raw))
    tab._detect_schema_from_input()
    assert tab._nuclear_token_edit.text() == "Sentinel"


# ── the chain: converter → Analyze, through the real bridge ─────────────────

class _FakeConverter(QObject):
    jobFinished = Signal(bool, str, str)

    def __init__(self) -> None:
        super().__init__()
        self.started: list[dict] = []

    def start_from_queue(self, spec):
        self.started.append(spec)

    def show_job(self, spec):
        pass

    def stop(self):
        pass

    def is_busy(self):
        return False


def test_a_chained_analyze_job_runs_with_the_converter_s_roles(qapp, tab, tmp_path):
    from datasets.job_queue.controller import QueueBridge
    from services.job_kinds import CHAIN_KIND, CHAIN_TARGET
    from services.job_queue import JobSpec

    bridge = QueueBridge(state_dir=tmp_path / "state")
    converter = _FakeConverter()
    bridge.register("ome_import", converter)
    bridge.register("analyze", tab)

    # The page's roles deliberately disagree with the scan's own guess (which
    # would make DAPI the segmentation channel), so only the handed-over roles
    # can produce GFP here.
    bridge.add_job(JobSpec(id="conv", kind="ome_import", label="conv", spec={
        "source": "/acq/plate.ome.tif", "destination": str(tmp_path / "out"),
        "channel_roles": [["DAPI", "fluor"], ["GFP", "nuclear"]],
    }))
    tab._input_edit.setText("")
    tab._auto_detect_cb.setChecked(True)
    bridge.add_job(JobSpec(id="ana", kind="analyze", label="ana",
                           spec=_queue_from_form(tab)))
    assert bridge.set_chain("ana", "conv")
    assert bridge.queue.get("ana").chain_target == CHAIN_TARGET["analyze"]
    assert bridge.queue.get("ana").chain_kind == CHAIN_KIND

    bridge.start()
    assert len(converter.started) == 1
    dest = _plate(tmp_path / "out", channels=("DAPI", "GFP"))
    converter.jobFinished.emit(True, "", str(dest))

    assert bridge.queue.get("conv").layout["nuclear_token"] == "GFP"
    assert len(tab.started) == 1, tab.finished
    opts = tab.started[0]
    assert opts["raw"] == dest
    assert opts["nuclear_token"] == "GFP"
    assert opts["fluor_tokens"] == ["DAPI"]
    assert opts["filename_schema"] == "experiment:channel:well:fov:timepoint"
    assert "recorded by the converter" in tab._detection_note


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# ── what the queue row says ──────────────────────────────────────────────────

def test_the_row_names_a_blank_unchained_job_and_the_deferred_layout(qtbot):
    from datasets.job_queue.row import JobRow
    from services.job_queue import JobSpec

    blank = JobSpec(id="a", kind="analyze", label="a",
                    spec={"opts": {"raw": ""}, "auto_detect": True})
    row = JobRow(blank)
    qtbot.addWidget(row)
    detail = row._compose_detail(blank)
    assert "no input folder" in detail
    assert "schema and channels read from the input when it runs" in detail

    chained = JobSpec(id="b", kind="analyze", label="b", chain_from="a",
                      spec={"opts": {"raw": ""}})
    assert "no input folder" not in row._compose_detail(chained)

    ready = JobSpec(id="c", kind="analyze", label="c",
                    spec={"opts": {"raw": "/data/plate"}})
    assert row._compose_detail(ready) == "/data/plate"


def test_the_queue_accepts_an_unwritten_input_without_detection(tab, tmp_path,
                                                                monkeypatch):
    """A job with a fixed layout, queued after the converter that will write
    its input: nothing that reads the input can be asked yet."""
    from PySide6.QtWidgets import QMessageBox

    refused: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda _p, title, *_a: refused.append(title)))
    tab._input_edit.setText(str(tmp_path / "not_written_yet"))
    tab._flatfield_cb.setCurrentText("empty_well")
    tab._background_well_edit.setText("H12")
    spec = _queue_from_form(tab)
    assert refused == []
    assert spec["auto_detect"] is False


# ── resuming a detection run ─────────────────────────────────────────────────

def test_resuming_a_suspended_detection_run_detects_again(tab, tmp_path,
                                                         monkeypatch):
    """The banner's Resume rebuilt the spec field by field and dropped
    ``auto_detect``, so the resumed run ran the form's frozen layout."""
    from types import SimpleNamespace

    raw = _plate(tmp_path / "plate")
    tab._input_edit.setText(str(raw))
    tab._auto_detect_cb.setChecked(True)
    recorded = tab.collect_spec()                  # what a suspend record holds
    assert recorded["opts"]["nuclear_token"] == "NIR"
    offer = SimpleNamespace(spec=recorded, headline="suspended")
    monkeypatch.setattr(type(tab), "_resumable", lambda self, *a, **k: offer)
    monkeypatch.setattr(type(tab), "_forget_resumable", lambda self, o: None)

    tab._resume_suspended()

    assert len(tab.started) == 1, tab.finished
    assert tab.started[0]["incremental"] is True
    assert tab.started[0]["nuclear_token"] == "DAPI"
    assert tab.started[0]["filename_sep"] == "_"


def test_a_forced_incremental_run_keeps_its_detection_request(tab, tmp_path,
                                                             monkeypatch):
    """``resume_aware_spec`` forces incremental on an input with unfinished
    work behind it, and must not drop the rest of the spec doing so."""
    from services.run_spec import RunSpec

    monkeypatch.setattr(type(tab), "_input_has_unfinished_work",
                        lambda self, raw: "a suspended run")
    spec = RunSpec(opts={"raw": "/d"}, auto_detect=True,
                   upstream_layout={"schema": "a:b", "separator": "_"})
    out = tab.resume_aware_spec(spec)
    assert out.opts["incremental"] is True
    assert out.auto_detect is True
    assert out.upstream_layout == {"schema": "a:b", "separator": "_"}


# ── the pre-run report with detection on ────────────────────────────────────

def test_the_pre_run_report_describes_the_detected_layout(tab, tmp_path,
                                                         monkeypatch, qtbot):
    import analyze.preflight as preflight

    raw = _plate(tmp_path / "plate")
    seen: list[dict] = []
    monkeypatch.setattr(preflight, "_scan_coverage",
                        lambda _in, _out, opts: seen.append(dict(opts)))
    from types import SimpleNamespace

    monkeypatch.setattr(preflight, "plan_incremental_scope",
                        lambda _opts, _in: SimpleNamespace(preview="3 new images"))
    tab._preflight_input = str(raw)
    tab._preflight_dir = str(tmp_path / "plate" / "out")
    tab._preflight_scanning = ""
    assert tab._scope_line() == "3 new images"      # the sentence, without it
    tab._auto_detect_cb.setChecked(True)
    # Ticking repaints the readout from the (empty) input field; point the
    # report back at the plate.
    tab._preflight_input = str(raw)
    tab._preflight_dir = str(tmp_path / "plate" / "out")
    assert tab._scope_line() == ""        # counted under the frozen schema
    tab._start_coverage_scan()
    qtbot.waitUntil(lambda: bool(seen), timeout=5000)
    assert seen[0]["nuclear_token"] == "DAPI"
    assert seen[0]["filename_sep"] == "_"
