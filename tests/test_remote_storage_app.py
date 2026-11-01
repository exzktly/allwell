"""Manage Remotes → Storage…, reached the way a user reaches it: through the real window.

Slow lane — this constructs the whole ``AllWellApp``. The Storage browser's own
behaviour is held by ``tests/test_remote_storage_dialog.py`` (which builds the
dialogs directly) and ``services.remote_storage``'s files; what only this file can
notice is the route being broken: Analyze's **Manage…** button not opening Manage
Remotes, Manage Remotes' **Storage…** not opening the browser on the selected host,
or the browser built with a cache that cannot reach that host.

Only the transport is replaced: ``DatasetCache._ssh_script`` runs each script in
local ``bash`` against a staging folder in a temporary directory, so the listing
and the deletion the test drives really happen on disk. The two ``exec`` calls are
replaced by functions that drive the dialog and return, because a modal loop never
returns under the offscreen platform (``tests/conftest.py::_no_modal_dialogs``).
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

pytestmark = pytest.mark.slow

RUN = "run-00000000000000a1-00000000000000b2"
DAY = 86400


def _pump(qapp, n: int = 15) -> None:
    for _ in range(n):
        qapp.processEvents()


@pytest.fixture
def staged_host(tmp_path, monkeypatch):
    """A remote host whose staging folder is a local directory holding one old,
    labelled, downloaded run folder, and a ``remotes.toml`` naming it."""
    from services.remote_config import RemoteHost, write_remote
    from services import remote_storage as rs
    from services.remote_upload import DatasetCache

    staging = tmp_path / "staging"
    run = staging / "runs" / RUN
    (run / "out").mkdir(parents=True)
    (run / "out" / "A01_out.zip").write_bytes(b"x" * 1000)
    (run / rs.RUN_LABEL).write_text(json.dumps({"run_id": RUN, "project": "MyProject",
                                                 "output_dir": "/u/MyProject/out"}))
    (run / rs.RUN_FETCHED).write_text("1")
    t = time.time() - 40 * DAY
    for p in [*run.rglob("*"), run]:
        os.utime(p, (t, t))

    monkeypatch.setenv("ALLWELL_REMOTES", str(tmp_path / "remotes.toml"))
    host = RemoteHost(name="cluster", ssh_alias="me@cluster", remote_staging_dir=str(staging))
    write_remote(host)
    monkeypatch.setattr(
        DatasetCache, "_ssh_script",
        lambda self, script, *, check, timeout_s=None: subprocess.run(
            ["bash", "-s"], input=script, capture_output=True, text=True, timeout=60))
    return host, run


def test_analyze_manage_then_storage_lists_and_deletes_on_the_selected_host(qapp, qtbot, monkeypatch,
                                                                           staged_host):
    from allwell import AllWellApp
    from analyze import remote_storage_dialog as rsd
    from analyze.remote_dialog import ManageRemotesDialog

    host, run = staged_host
    seen: dict = {}

    def manage_exec(dialog):
        seen["manage"] = dialog
        names = [dialog._list.item(i).text() for i in range(dialog._list.count())]
        seen["hosts"] = names
        dialog._list.setCurrentRow(0)
        assert dialog._storage_btn.isEnabled()
        dialog._storage_btn.click()
        return 0

    def storage_exec(dialog):
        seen["storage"] = dialog
        qtbot.waitUntil(lambda: dialog._tree.topLevelItemCount() == 1 and not dialog._busy, timeout=15_000)
        row = dialog._tree.topLevelItem(0)
        seen["row"] = [row.text(c) for c in range(dialog._tree.columnCount())]
        monkeypatch.setattr(dialog, "_confirm", lambda *_a: True)
        row.setSelected(True)
        dialog._delete_btn.click()
        qtbot.waitUntil(lambda: dialog._tree.topLevelItemCount() == 0 and not dialog._busy, timeout=15_000)
        return 0

    monkeypatch.setattr(ManageRemotesDialog, "exec", manage_exec)
    monkeypatch.setattr(rsd.RemoteStorageDialog, "exec", storage_exec)

    win = AllWellApp(None)
    qtbot.addWidget(win)
    win.show()
    _pump(qapp)
    win._on_titlebar_mode_seg(1)                       # Analyze
    _pump(qapp)

    win._analyze._manage_remotes_btn.click()
    _pump(qapp)

    assert "manage" in seen, "Analyze's Manage… did not open Manage Remotes"
    assert seen["hosts"] and seen["hosts"][0].startswith("cluster"), seen["hosts"]
    assert "storage" in seen, "Manage Remotes' Storage… did not open the Storage browser"
    assert seen["storage"]._host.name == "cluster"
    assert seen["row"][0] == "MyProject" and seen["row"][2] == RUN, seen["row"]
    assert not run.exists(), "Delete… in the browser did not remove the folder on the host"
    assert seen["storage"]._status.text().startswith("1 folder(s) deleted."), seen["storage"]._status.text()
