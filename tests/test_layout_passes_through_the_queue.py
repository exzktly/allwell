"""A converter's recorded layout survives a Concatenate on its way to Analyze.

Two CZI scans of one plate are converted, the two outputs concatenated, and
the concatenation analysed. Each converter records its filename schema and the
channel roles set on its page; the Analyze job at the end of the chain should
receive them as known, exactly as it would directly after one converter. It
may receive them only when both conversions agree — anything else is a guess,
and the Analyze job scans instead.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from services.job_kinds import LAYOUT_PRESERVING_KINDS, produced_layout
from services.job_queue import JobSpec

_ROLES = [["DAPI", "nuclear"], ["GFP", "fluor"], ["mCherry", "fluor"]]
_LAYOUT = produced_layout("czi_import", {"channel_roles": _ROLES})
_OTHER = produced_layout("czi_import", {"channel_roles": [["DAPI", "fluor"],
                                                          ["GFP", "nuclear"]]})


def test_the_two_fixture_layouts_differ():
    """Without this, the disagreement test below would measure nothing."""
    assert _LAYOUT != _OTHER


@pytest.mark.parametrize("kind", ["concatenate", "merge_plates"])
def test_two_agreeing_inputs_pass_their_layout_on(kind):
    assert produced_layout(kind, {}, [_LAYOUT, dict(_LAYOUT)]) == _LAYOUT


@pytest.mark.parametrize("kind", ["subset", "validate"])
def test_a_one_input_kind_passes_its_input_s_layout_on(kind):
    assert produced_layout(kind, {}, [_LAYOUT]) == _LAYOUT


def test_disagreeing_inputs_pass_nothing_on():
    assert produced_layout("concatenate", {}, [_LAYOUT, _OTHER]) is None


def test_an_input_read_from_a_folder_passes_nothing_on():
    assert produced_layout("concatenate", {}, [_LAYOUT, None]) is None
    assert produced_layout("concatenate", {}, [None, _LAYOUT]) is None


def test_a_short_upstream_list_passes_nothing_on():
    assert produced_layout("concatenate", {}, [_LAYOUT]) is None


def test_the_passed_layout_is_a_copy():
    out = produced_layout("subset", {}, [_LAYOUT])
    out["nuclear_token"] = "changed"
    assert _LAYOUT["nuclear_token"] == "DAPI"


@pytest.mark.parametrize("kind", ["analyze", "import_loose"])
def test_kinds_that_rename_or_have_their_own_record_pass_nothing(kind):
    assert kind not in LAYOUT_PRESERVING_KINDS
    assert produced_layout(kind, {}, [_LAYOUT]) is None


# ── through the bridge, the way the app runs it ──────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _run_chain(tmp_path, roles_a, roles_b, *, link_b=True):
    from PySide6.QtCore import QObject, Signal

    from datasets.job_queue.controller import QueueBridge

    class _Runner(QObject):
        jobFinished = Signal(bool, str, str)

        def __init__(self):
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

    bridge = QueueBridge(state_dir=tmp_path)
    runners = {k: _Runner() for k in ("czi_import", "concatenate", "analyze")}
    for kind, runner in runners.items():
        bridge.register(kind, runner)
    for job_id, roles in (("a", roles_a), ("b", roles_b)):
        bridge.queue.add(JobSpec(id=job_id, kind="czi_import", label=job_id,
                                 spec={"channel_roles": roles}))
    bridge.queue.add(JobSpec(id="cat", kind="concatenate", label="cat",
                             spec={"dataset2": "/data/on_disk"}))
    bridge.queue.add(JobSpec(id="an", kind="analyze", label="an",
                             spec={"opts": {}, "auto_detect": True}))
    assert bridge.set_link("cat", "dataset1", "a")
    if link_b:
        assert bridge.set_link("cat", "dataset2", "b")
    assert bridge.set_chain("an", "cat")
    bridge.start()
    runners["czi_import"].jobFinished.emit(True, "", "/real/a")
    runners["czi_import"].jobFinished.emit(True, "", "/real/b")
    runners["concatenate"].jobFinished.emit(True, "", "/real/combined")
    (started,) = runners["analyze"].started
    return started


def test_analyze_after_a_concatenate_of_two_conversions_gets_their_layout(qapp, tmp_path):
    spec = _run_chain(tmp_path, _ROLES, _ROLES)
    assert spec["opts"]["raw"] == "/real/combined"
    assert spec["upstream_layout"] == _LAYOUT


def test_analyze_gets_none_when_the_conversions_disagree(qapp, tmp_path):
    spec = _run_chain(tmp_path, _ROLES, [["DAPI", "fluor"], ["GFP", "nuclear"]])
    assert "upstream_layout" not in spec


def test_analyze_gets_none_when_one_dataset_came_from_disk(qapp, tmp_path):
    spec = _run_chain(tmp_path, _ROLES, _ROLES, link_b=False)
    assert "upstream_layout" not in spec
