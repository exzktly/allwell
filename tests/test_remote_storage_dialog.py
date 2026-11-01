"""The Storage browser in Manage Remotes, driven through its own buttons.

The remote is a staging tree in a temp directory and every script the dialog
sends runs in local bash, so a test that presses Delete checks the folder is
gone from disk — not that a function was called.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402

from analyze import remote_storage_dialog as rsd  # noqa: E402
from analyze.remote_dialog import ManageRemotesDialog, RemoteFormDialog  # noqa: E402
from services import remote_storage as rs  # noqa: E402
from services.remote_config import RemoteHost, load_remotes, write_remote  # noqa: E402

DAY = 86400


class _LocalCache:
    def __init__(self, host: RemoteHost):
        self.host = host

    def _ssh_script(self, script, *, check, timeout_s=None):
        return subprocess.run(["bash", "-s"], input=script, capture_output=True, text=True, timeout=60)


def _age(path, days):
    t = time.time() - days * DAY
    os.utime(path, (t, t))


def _run_folder(staging, name, project, days=40):
    d = staging / "runs" / name
    (d / "out").mkdir(parents=True)
    (d / "out" / "A1_out.zip").write_bytes(b"x" * 2000)
    (d / rs.RUN_LABEL).write_text(json.dumps({"project": project, "created": 1_700_000_000,
                                               "output_dir": f"/u/{project}/out"}))
    for p in (d / "out" / "A1_out.zip", d / "out", d / rs.RUN_LABEL, d):
        _age(p, days)
    return d


@pytest.fixture
def staged(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLWELL_REMOTES", str(tmp_path / "remotes.toml"))
    staging = tmp_path / "staging"
    _run_folder(staging, "run-aaa", "ProjA")
    _run_folder(staging, "run-bbb", "ProjB")
    host = RemoteHost(name="h", ssh_alias="me@h", remote_staging_dir=str(staging))
    write_remote(host)
    return host, staging


def _open(qtbot, host):
    dlg = rsd.RemoteStorageDialog(None, host, cache=_LocalCache(host))
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: dlg._tree.topLevelItemCount() == 2 and not dlg._busy, timeout=10_000)
    return dlg


def _row(dlg, folder):
    for i in range(dlg._tree.topLevelItemCount()):
        row = dlg._tree.topLevelItem(i)
        if row.text(2) == folder:
            return row
    raise AssertionError(f"no row for {folder}")


def test_the_browser_shows_each_folder_s_project_and_size(qtbot, staged):
    host, _ = staged
    dlg = _open(qtbot, host)
    row = _row(dlg, "run-bbb")
    assert row.text(0) == "ProjB"
    assert row.text(1) == "Run"
    # The 2,000-byte output plus the label and directory entries ``du -sb`` counts.
    assert row.text(3) == rsd.human_bytes(row.data(0, Qt.UserRole).size)
    assert 2000 <= row.data(0, Qt.UserRole).size < 3000
    assert row.text(5) == "40 d ago"
    assert row.text(6) == "Never"
    assert "2 run folder(s), 0 dataset(s)" in dlg._summary.text()


def test_delete_removes_only_the_selected_folder_from_the_server(qtbot, staged, monkeypatch):
    host, staging = staged
    dlg = _open(qtbot, host)
    prompts: list[str] = []
    monkeypatch.setattr(dlg, "_confirm", lambda _title, text: prompts.append(text) or True)
    _row(dlg, "run-aaa").setSelected(True)
    assert dlg._delete_btn.isEnabled()

    qtbot.mouseClick(dlg._delete_btn, Qt.LeftButton)
    qtbot.waitUntil(lambda: dlg._tree.topLevelItemCount() == 1 and not dlg._busy, timeout=10_000)

    assert sorted(p.name for p in (staging / "runs").iterdir()) == ["run-bbb"]
    assert "run run-aaa (project ProjA)" in prompts[0]
    assert dlg._status.text().startswith("1 folder(s) deleted.")


def test_a_refused_confirmation_deletes_nothing(qtbot, staged, monkeypatch):
    host, staging = staged
    dlg = _open(qtbot, host)
    monkeypatch.setattr(dlg, "_confirm", lambda *_a: False)
    _row(dlg, "run-aaa").setSelected(True)
    qtbot.mouseClick(dlg._delete_btn, Qt.LeftButton)
    assert not dlg._busy
    assert sorted(p.name for p in (staging / "runs").iterdir()) == ["run-aaa", "run-bbb"]


def test_keep_forever_writes_the_expiry_on_the_server(qtbot, staged):
    host, staging = staged
    dlg = _open(qtbot, host)
    _row(dlg, "run-aaa").setSelected(True)
    qtbot.mouseClick(dlg._keep_btn, Qt.LeftButton)
    qtbot.waitUntil(lambda: _row(dlg, "run-aaa").text(6) == "Never (kept)" and not dlg._busy, timeout=10_000)
    assert (staging / "runs" / "run-aaa" / rs.RUN_EXPIRY).read_text().strip() == rs.KEEP


def test_the_host_rule_is_saved_and_remove_expired_honours_keep(qtbot, staged, monkeypatch):
    host, staging = staged
    (staging / "runs" / "run-aaa" / rs.RUN_EXPIRY).write_text("keep\n")
    dlg = _open(qtbot, host)
    saved: list[RemoteHost] = []
    dlg.host_changed.connect(saved.append)
    dlg._host_days.setValue(30)
    assert _row(dlg, "run-bbb").text(6) == "Never"      # unsaved: nothing re-drawn yet
    qtbot.mouseClick(dlg._save_rule_btn, Qt.LeftButton)

    assert load_remotes()["h"].storage_expiry_days == 30
    assert saved and saved[0].storage_expiry_days == 30
    assert _row(dlg, "run-bbb").text(6) == "Expired"
    assert _row(dlg, "run-aaa").text(6) == "Never (kept)"

    monkeypatch.setattr(dlg, "_confirm", lambda *_a: True)
    qtbot.mouseClick(dlg._sweep_btn, Qt.LeftButton)
    qtbot.waitUntil(lambda: dlg._tree.topLevelItemCount() == 1 and not dlg._busy, timeout=10_000)
    assert sorted(p.name for p in (staging / "runs").iterdir()) == ["run-aaa"]
    assert "removed expired run run-bbb" in dlg._status.text()


def test_manage_remotes_opens_the_browser_on_the_selected_host(qtbot, staged, monkeypatch):
    host, _ = staged
    opened: list[RemoteHost] = []

    class _Fake:
        def __init__(self, _parent, h):
            opened.append(h)
            self.host_changed = type("S", (), {"connect": lambda *_a: None})()

        def exec(self):
            return 0

        def deleteLater(self):
            pass

    monkeypatch.setattr(rsd, "RemoteStorageDialog", _Fake)
    dlg = ManageRemotesDialog(None)
    qtbot.addWidget(dlg)
    assert not dlg._storage_btn.isEnabled()
    dlg._list.setCurrentRow(0)
    assert dlg._storage_btn.isEnabled()
    qtbot.mouseClick(dlg._storage_btn, Qt.LeftButton)
    assert [h.name for h in opened] == ["h"]


def test_editing_a_host_keeps_the_settings_the_form_does_not_show(qtbot):
    host = RemoteHost(name="h", ssh_alias="me@h", storage_expiry_days=12, auto_gc=True,
                      measured_bandwidth_mbps=42.0)
    form = RemoteFormDialog(None, host=host)
    qtbot.addWidget(form)
    result = form.result_host()
    assert (result.storage_expiry_days, result.auto_gc, result.measured_bandwidth_mbps) == (12, True, 42.0)
