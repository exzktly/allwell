"""Smoke tests for analyze_remote_dialog (v5.1.0).

Requires PySide6 + pytest-qt. Skipped when those aren't installed (e.g.
the lean CI runner used for the pure-logic test suite).

Coverage:
* RemoteFormDialog populates correctly in Add and Edit modes.
* OK is disabled until alias + ssh_alias are filled and the alias
  matches the validator.
* result_host round-trips through the dataclass.
* ManageRemotesDialog reflects the on-disk file and refreshes after a
  write_remote / delete_remote.
* BootstrapLogDialog spawns the bootstrap subprocess and surfaces its
  stdout — we substitute a tiny shim script so the test runs in
  milliseconds, not minutes.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

# Headless Qt setup. The viewer-app fixtures already do this in
# conftest.py but they're not active for this module.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QDialogButtonBox  # noqa: E402

from services.remote_config import RemoteHost, load_remotes, write_remote  # noqa: E402

# Imported after the importorskip so the module fails fast on a bare
# Python runner without Qt instead of importing analyze_remote_dialog.
from analyze_remote_dialog import (  # noqa: E402
    BootstrapLogDialog, ManageRemotesDialog, RemoteFormDialog,
)


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point ALLWELL_REMOTES at an isolated tmp file so dialog
    interactions don't touch the developer's real config."""
    cfg = tmp_path / "remotes.toml"
    monkeypatch.setenv("ALLWELL_REMOTES", str(cfg))
    return cfg


# ── RemoteFormDialog ────────────────────────────────────────────────────────


def test_form_add_mode_defaults_match_dataclass(qtbot, isolated_config):
    dlg = RemoteFormDialog()
    qtbot.addWidget(dlg)
    assert dlg._name_edit.text() == ""
    assert dlg._ssh_alias_edit.text() == ""
    # Defaults from RemoteHost dataclass.
    assert dlg._project_dir_edit.text() == "~/allwell"
    assert dlg._staging_dir_edit.text() == "~/.allwell-staging"
    assert dlg._python_edit.text() == "~/miniforge3/envs/allwell/bin/python"
    assert dlg._cpu_only_cb.isChecked() is False
    assert dlg._cache_budget_spin.value() == 200
    # OK disabled until both required fields are non-empty.
    ok_btn = dlg._buttons.button(QDialogButtonBox.Ok)
    assert not ok_btn.isEnabled()


def test_form_edit_mode_populates_from_host_and_locks_name(qtbot, isolated_config):
    host = RemoteHost(
        name="bigbox", ssh_alias="user@big.example",
        remote_project_dir="~/code/allwell",
        cpu_only=True, cache_budget_gb=42,
        extra_args=("--workers", "16"),
    )
    dlg = RemoteFormDialog(host=host)
    qtbot.addWidget(dlg)
    assert dlg._name_edit.text() == "bigbox"
    assert dlg._name_edit.isReadOnly()
    assert dlg._ssh_alias_edit.text() == "user@big.example"
    assert dlg._cpu_only_cb.isChecked() is True
    assert dlg._cache_budget_spin.value() == 42
    assert dlg._extra_args_edit.text() == "--workers 16"


def test_form_ok_enabled_only_with_valid_alias(qtbot, isolated_config):
    dlg = RemoteFormDialog(existing_names={"taken"})
    qtbot.addWidget(dlg)
    ok_btn = dlg._buttons.button(QDialogButtonBox.Ok)

    dlg._name_edit.setText("good")
    dlg._ssh_alias_edit.setText("alias")
    assert ok_btn.isEnabled()

    # Alias with disallowed character.
    dlg._name_edit.setText("not good!")
    assert not ok_btn.isEnabled()

    # Conflict with existing name.
    dlg._name_edit.setText("taken")
    assert not ok_btn.isEnabled()


def test_form_result_host_round_trip(qtbot, isolated_config):
    dlg = RemoteFormDialog()
    qtbot.addWidget(dlg)
    dlg._name_edit.setText("dev")
    dlg._ssh_alias_edit.setText("dev-box")
    dlg._cpu_only_cb.setChecked(True)
    dlg._cache_budget_spin.setValue(123)
    dlg._extra_args_edit.setText("--workers 8 --tf_threads 4")
    host = dlg.result_host()
    assert host.name == "dev"
    assert host.ssh_alias == "dev-box"
    assert host.cpu_only is True
    assert host.cache_budget_gb == 123
    assert host.extra_args == ("--workers", "8", "--tf_threads", "4")


# ── ManageRemotesDialog ─────────────────────────────────────────────────────


def test_manage_dialog_lists_existing_remotes(qtbot, isolated_config):
    write_remote(
        RemoteHost(name="alpha", ssh_alias="alpha-host"),
        path=isolated_config,
    )
    write_remote(
        RemoteHost(name="beta", ssh_alias="beta-host"),
        path=isolated_config,
    )
    dlg = ManageRemotesDialog()
    qtbot.addWidget(dlg)
    names = {
        dlg._list.item(i).data(Qt.UserRole)
        for i in range(dlg._list.count())
    }
    assert names == {"alpha", "beta"}


def test_manage_dialog_empty_state_disables_buttons(qtbot, isolated_config):
    dlg = ManageRemotesDialog()
    qtbot.addWidget(dlg)
    assert dlg._list.count() == 0
    assert dlg._empty_hint.isVisible()
    # Selection-dependent buttons start disabled.
    assert not dlg._edit_btn.isEnabled()
    assert not dlg._delete_btn.isEnabled()
    assert not dlg._test_btn.isEnabled()
    assert not dlg._bootstrap_btn.isEnabled()


def test_manage_dialog_reload_after_external_write(qtbot, isolated_config):
    dlg = ManageRemotesDialog()
    qtbot.addWidget(dlg)
    assert dlg._list.count() == 0

    # Simulate the user adding a remote elsewhere.
    write_remote(
        RemoteHost(name="late", ssh_alias="late-host"),
        path=isolated_config,
    )
    dlg._reload()
    assert dlg._list.count() == 1
    assert dlg._list.item(0).data(Qt.UserRole) == "late"


def test_manage_dialog_emits_remotes_changed_after_delete(qtbot, isolated_config, monkeypatch):
    write_remote(
        RemoteHost(name="doomed", ssh_alias="x"),
        path=isolated_config,
    )
    dlg = ManageRemotesDialog()
    qtbot.addWidget(dlg)
    dlg._list.setCurrentRow(0)

    # Auto-confirm the QMessageBox.
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)

    received: list = []
    dlg.remotes_changed.connect(lambda: received.append(True))
    dlg._on_delete()
    assert received == [True]
    assert load_remotes(isolated_config) == {}


# ── BootstrapLogDialog ──────────────────────────────────────────────────────


def test_bootstrap_dialog_streams_subprocess_stdout(qtbot, tmp_path, monkeypatch):
    """Substitute the real bootstrap with a tiny shim that prints three
    lines and exits cleanly. The dialog should see the lines flow into
    its log pane and end with a Complete title."""
    shim = tmp_path / "_bootstrap_lib.py"
    shim.write_text(
        "import sys, time\n"
        "for i in range(3):\n"
        "    print(f'fake bootstrap line {i}')\n"
        "    sys.stdout.flush()\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    # Point the dialog at our shim by symlinking scripts/_bootstrap_lib.py
    # to it for the duration of the test. (The dialog resolves the path
    # relative to its own __file__.)
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    target = scripts_dir / "_bootstrap_lib.py"
    backup = target.with_suffix(".py.bak")
    target.rename(backup)
    try:
        target.write_text(shim.read_text(), encoding="utf-8")
        dlg = BootstrapLogDialog(parent=None, alias="testhost")
        qtbot.addWidget(dlg)
        deadline = time.monotonic() + 10
        while dlg._exit_code is None and time.monotonic() < deadline:
            qtbot.wait(50)
        assert dlg._exit_code == 0
        text = dlg._log.toPlainText()
        assert "fake bootstrap line 0" in text
        assert "fake bootstrap line 2" in text
        assert "complete" in dlg._title_lbl.text().lower()
    finally:
        if target.exists():
            target.unlink()
        backup.rename(target)
