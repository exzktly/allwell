"""Named Analyze presets: save the form by name, load it back on another form.

Driven through the real menu on a real ``AnalyzeTab`` — the save and load
actions are triggered from ``_presets_menu`` as a click would, not by calling
the store — and loaded into a *second* tab, so every restored value can only
have come from the file. The fixture is the all-non-default run of
``tests/test_analyze_job_edit.py``, which proves its own values differ from a
fresh form's, so a field the load fails to restore shows as a difference.
"""

from __future__ import annotations

import copy
import json

import pytest

from analyze.domain import presets as store
from analyze.presets import DELETE_MENU, SAVE_ACTION
from tests.test_analyze_job_edit import _OPTS, _SPEC, _STANDARD_CORRECTIONS

#: Per-run values unlike both ``_OPTS`` and a fresh form's, put on the form a preset is loaded into: each must
#: survive the load untouched.
_OTHER_RUN = {"raw": "/data/other_plate", "scratch_dir": "/scratch/other", "only_wells": ["E5"],
              "skip_wells": ["F6", "G7"], "force_wells": ["E5"], "force": False, "incremental": True,
              "allow_low_disk": False, "nanowell_registration_from": "/data/other_plate/out"}


@pytest.fixture
def presets_file(tmp_path, monkeypatch):
    """This test's own presets file: the app folder ``ALLWELL_STATE_DIR`` names is shared by a worker's tests."""
    path = tmp_path / "analyze_presets.json"
    monkeypatch.setattr(store, "presets_path", lambda: path)
    return path


def _tab(qtbot):
    from analyze.tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)
    t._remote_cb.addItem("bench-box", userData="bench-box")
    return t


def _action(menu, text):
    menu.aboutToShow.emit()               # what opening the menu does: rebuild it from the file
    for action in menu.actions():
        if action.text() == text:
            return action.menu() or action      # a submenu's own action carries its title
    raise AssertionError(f"no {text!r} in {[a.text() for a in menu.actions()]}")


def _save_through_menu(tab, monkeypatch, name, *, replace=None):
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: (name, True)))
    asked = []

    def question(*a, **k):
        asked.append(a[2])
        return QMessageBox.Yes if replace else QMessageBox.No
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    _action(tab._presets_menu, SAVE_ACTION).trigger()
    return asked


def test_a_saved_preset_loads_every_setting_into_another_form(qtbot, monkeypatch, presets_file):
    first = _tab(qtbot)
    first.hydrate_job(_SPEC)
    _save_through_menu(first, monkeypatch, "  Nanowell   plate ")      # the name is tidied

    second = _tab(qtbot)
    second.hydrate_options(_OTHER_RUN)
    # The nanowell section restores its registration source only alongside a plate format, so set it directly.
    second._nanowell_registration_edit.setText(_OTHER_RUN["nanowell_registration_from"])
    before = second._collect_run_options()
    assert {k: before[k] for k in _OTHER_RUN if k != "raw"} == {k: v for k, v in _OTHER_RUN.items() if k != "raw"}
    _action(second._presets_menu, "Nanowell plate").trigger()
    after = second._collect_run_options()

    wanted = {k: v for k, v in _OPTS.items() if k not in store.PER_RUN_KEYS}
    assert {k: after[k] for k in wanted} == wanted
    # Nothing a preset leaves out moved: the folder, the wells, the existing-output choice, the safety override.
    assert {k: after[k] for k in store.PER_RUN_KEYS} == {k: before[k] for k in store.PER_RUN_KEYS}
    assert str(after["raw"]) == _OTHER_RUN["raw"]


def test_a_standard_plate_s_corrections_round_trip(qtbot, monkeypatch, presets_file):
    first = _tab(qtbot)
    spec = copy.deepcopy(first.collect_spec())
    spec["opts"].update(_STANDARD_CORRECTIONS)
    first.hydrate_job(spec)
    _save_through_menu(first, monkeypatch, "Standard")
    second = _tab(qtbot)
    second.hydrate_job(_SPEC)                                  # a nanowell form, which sends no corrections
    _action(second._presets_menu, "Standard").trigger()
    after = second._collect_run_options()
    assert {k: after[k] for k in _STANDARD_CORRECTIONS} == _STANDARD_CORRECTIONS
    assert after["nanowell_format"] == ""


def test_the_file_holds_no_per_run_key(qtbot, monkeypatch, presets_file):
    tab = _tab(qtbot)
    tab.hydrate_job(_SPEC)
    _save_through_menu(tab, monkeypatch, "P")
    saved = json.loads(presets_file.read_text())["presets"]["P"]["opts"]
    assert not set(saved) & set(store.PER_RUN_KEYS)
    assert saved["nuclear_token"] == "DAPI" and saved["fluor_tokens"] == ["GFP", "RFP", "BF"]


def test_saving_over_a_name_asks_and_a_no_keeps_the_old_preset(qtbot, monkeypatch, presets_file):
    tab = _tab(qtbot)
    tab.hydrate_job(_SPEC)
    _save_through_menu(tab, monkeypatch, "P")
    tab._nuclear_token_edit.setText("HOECHST")
    asked = _save_through_menu(tab, monkeypatch, "P", replace=False)
    assert asked and "already exists" in asked[0]
    assert store.load_presets()["P"]["opts"]["nuclear_token"] == "DAPI"
    _save_through_menu(tab, monkeypatch, "P", replace=True)
    assert store.load_presets()["P"]["opts"]["nuclear_token"] == "HOECHST"


def test_delete_through_the_menu(qtbot, monkeypatch, presets_file):
    from PySide6.QtWidgets import QMessageBox

    tab = _tab(qtbot)
    _save_through_menu(tab, monkeypatch, "Gone")
    _save_through_menu(tab, monkeypatch, "Kept")
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    sub = _action(tab._presets_menu, DELETE_MENU)
    [a for a in sub.actions() if a.text() == "Gone"][0].trigger()
    assert list(store.load_presets()) == ["Kept"]
    tab._presets_menu.aboutToShow.emit()
    assert "Gone" not in [a.text() for a in tab._presets_menu.actions()]


def test_an_unreadable_file_is_said_and_never_saved_over(qtbot, presets_file):
    path = presets_file
    path.write_text("{not json")
    tab = _tab(qtbot)
    tab._presets_menu.aboutToShow.emit()
    texts = {a.text(): a for a in tab._presets_menu.actions()}
    assert "Presets file could not be read" in texts
    assert not texts[SAVE_ACTION].isEnabled()
    with pytest.raises(store.PresetFileError):
        store.save_preset("P", {"nuclear_token": "DAPI"})
    assert path.read_text() == "{not json"


def test_the_store_orders_names_and_rejects_a_blank_one(tmp_path):
    path = tmp_path / "presets.json"
    for name in ("beta", "Alpha", "gamma"):
        store.save_preset(name, {"nuclear_token": name}, path)
    assert store.sorted_names(store.load_presets(path)) == ["Alpha", "beta", "gamma"]
    with pytest.raises(ValueError):
        store.save_preset("   ", {}, path)
    assert store.delete_preset("beta", path) and not store.delete_preset("beta", path)
