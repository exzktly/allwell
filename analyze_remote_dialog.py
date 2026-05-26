"""Manage Remotes GUI — v5.1.0.

Three QDialog classes that let the user add, edit, delete, test, and
bootstrap remote-execution hosts without leaving the AllWell GUI.
Driven by the existing ``services.remote_config`` persistence layer
(``write_remote`` / ``delete_remote``) and reuses the v5.0.0 queue +
QTimer event pattern for the live bootstrap log.

Hierarchy:

* :class:`ManageRemotesDialog` — top-level list + actions, opened from
  AnalyzeTab's "Manage…" button.
* :class:`RemoteFormDialog` — the add/edit form.
* :class:`BootstrapLogDialog` — modal sub-dialog that streams the
  ``scripts/_bootstrap_lib.py`` subprocess output line by line so the
  user sees progress through the 10–30 minute pip install.
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from services.remote_config import (
    RemoteHost,
    delete_remote,
    load_remotes,
    resolve_ssh_binary,
    write_remote,
)
from services.subprocess_kill import signal_subprocess


# Alias must look like a TOML bare key — letters, digits, dash, underscore.
_ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]+$")


# ────────────────────────────────────────────────────────────────────────────
# RemoteFormDialog — add / edit
# ────────────────────────────────────────────────────────────────────────────


class RemoteFormDialog(QDialog):
    """Form for one ``RemoteHost``.

    Constructed with ``host=None`` for Add (defaults pre-filled from the
    dataclass) or with an existing ``RemoteHost`` for Edit (fields
    populated from it; the alias field is read-only in edit mode since
    renaming = delete-then-add and would invalidate any cached
    selection in the parent combobox).
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        host: Optional[RemoteHost] = None,
        existing_names: Optional[set[str]] = None,
    ) -> None:
        super().__init__(parent)
        self._editing = host is not None
        self._existing_names = set(existing_names or set())
        if host is None:
            host = RemoteHost(name="", ssh_alias="")

        self.setWindowTitle("Edit Remote" if self._editing else "Add Remote")
        self.setModal(True)
        self.resize(520, 380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        title = QLabel("Edit remote host" if self._editing else "Add a new remote host", self)
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(8)

        self._name_edit = QLineEdit(host.name, self)
        self._name_edit.setReadOnly(self._editing)
        if self._editing:
            self._name_edit.setToolTip(
                "Alias is the [remote.<name>] section key. Read-only "
                "when editing — delete and re-add to rename."
            )
        form.addRow("Alias *", self._name_edit)

        ssh_row = QWidget(self)
        ssh_row_layout = QHBoxLayout(ssh_row)
        ssh_row_layout.setContentsMargins(0, 0, 0, 0)
        ssh_row_layout.setSpacing(6)
        self._ssh_alias_edit = QLineEdit(host.ssh_alias, ssh_row)
        self._ssh_alias_edit.setPlaceholderText("user@host or ~/.ssh/config alias")
        ssh_row_layout.addWidget(self._ssh_alias_edit, 1)
        self._test_btn = QPushButton("Test SSH", ssh_row)
        self._test_btn.setProperty("variant", "secondary")
        self._test_btn.clicked.connect(self._test_ssh)
        ssh_row_layout.addWidget(self._test_btn)
        form.addRow("SSH alias *", ssh_row)

        self._test_result_lbl = QLabel("", self)
        self._test_result_lbl.setObjectName("Muted")
        layout.addLayout(form)
        layout.addWidget(self._test_result_lbl)

        more = QFormLayout()
        more.setLabelAlignment(Qt.AlignRight)
        more.setSpacing(8)

        self._project_dir_edit = QLineEdit(host.remote_project_dir, self)
        more.addRow("Remote project dir", self._project_dir_edit)

        self._staging_dir_edit = QLineEdit(host.remote_staging_dir, self)
        more.addRow("Remote staging dir", self._staging_dir_edit)

        self._python_edit = QLineEdit(host.remote_python, self)
        more.addRow("Remote python", self._python_edit)

        self._cpu_only_cb = QCheckBox("CPU only (disable GPU on remote)", self)
        self._cpu_only_cb.setChecked(host.cpu_only)
        more.addRow("", self._cpu_only_cb)

        self._cache_budget_spin = QSpinBox(self)
        self._cache_budget_spin.setRange(1, 10000)
        self._cache_budget_spin.setSuffix(" GB")
        self._cache_budget_spin.setValue(host.cache_budget_gb)
        more.addRow("Cache budget", self._cache_budget_spin)

        self._extra_args_edit = QLineEdit(" ".join(host.extra_args), self)
        self._extra_args_edit.setPlaceholderText("--workers 16 --tf_threads 8")
        more.addRow("Extra args", self._extra_args_edit)

        layout.addLayout(more)
        layout.addStretch(1)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self,
        )
        self._buttons.accepted.connect(self._on_ok)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        # Live validation: disable OK until the form is fillable.
        self._name_edit.textChanged.connect(self._refresh_ok_state)
        self._ssh_alias_edit.textChanged.connect(self._refresh_ok_state)
        self._refresh_ok_state()

        self._restore_geometry()

    # ── validation ────────────────────────────────────────────────────────

    def _refresh_ok_state(self) -> None:
        name = self._name_edit.text().strip()
        ssh_alias = self._ssh_alias_edit.text().strip()
        ok = bool(name) and bool(ssh_alias) and _ALIAS_RE.match(name) is not None
        if ok and not self._editing and name in self._existing_names:
            ok = False
        self._buttons.button(QDialogButtonBox.Ok).setEnabled(ok)

    def _on_ok(self) -> None:
        name = self._name_edit.text().strip()
        if not _ALIAS_RE.match(name):
            QMessageBox.critical(
                self, "Invalid Alias",
                "Alias must contain only letters, digits, dashes, and underscores.",
            )
            return
        if not self._editing and name in self._existing_names:
            QMessageBox.critical(
                self, "Duplicate Alias",
                f"A remote called '{name}' already exists. Pick a different alias "
                "or close this dialog and use Edit instead.",
            )
            return
        try:
            host = self.result_host()
        except ValueError as exc:
            QMessageBox.critical(self, "Invalid Form", str(exc))
            return
        try:
            write_remote(host)
        except OSError as exc:
            QMessageBox.critical(
                self, "Save Failed",
                f"Could not write to remotes.toml:\n{exc}",
            )
            return
        self.accept()

    def result_host(self) -> RemoteHost:
        """Build a ``RemoteHost`` from the current form state.

        Public so callers (e.g. the Test SSH button before OK is hit)
        can preview the host without committing.
        """
        raw_extras = self._extra_args_edit.text().strip()
        extras: tuple[str, ...] = tuple(raw_extras.split()) if raw_extras else ()
        return RemoteHost(
            name=self._name_edit.text().strip(),
            ssh_alias=self._ssh_alias_edit.text().strip(),
            remote_project_dir=self._project_dir_edit.text().strip() or "~/allwell",
            remote_staging_dir=self._staging_dir_edit.text().strip() or "~/.allwell-staging",
            remote_python=self._python_edit.text().strip()
                or "~/miniforge3/envs/allwell/bin/python",
            cpu_only=self._cpu_only_cb.isChecked(),
            extra_args=extras,
            cache_budget_gb=self._cache_budget_spin.value(),
        )

    # ── inline test ────────────────────────────────────────────────────────

    def _test_ssh(self) -> None:
        """Fire a one-shot ``ssh <alias> echo ok``.

        Runs in a worker thread so the dialog stays responsive. The
        result lands in ``_test_result_lbl`` via a tiny queue + QTimer
        — same pattern as PipelineRunner but cut to one event.
        """
        alias = self._ssh_alias_edit.text().strip()
        if not alias:
            self._test_result_lbl.setText("Set an SSH alias first.")
            return

        self._test_btn.setEnabled(False)
        self._test_result_lbl.setText(f"Testing {alias}…")
        result_q: queue.Queue = queue.Queue()

        def _runner() -> None:
            try:
                proc = subprocess.run(
                    [resolve_ssh_binary(),
                     "-o", "BatchMode=yes",
                     "-o", "ConnectTimeout=10",
                     alias, "echo", "ok"],
                    capture_output=True, text=True, timeout=15,
                )
                if proc.returncode == 0 and proc.stdout.strip() == "ok":
                    result_q.put(("ok", ""))
                else:
                    result_q.put(("fail", (proc.stderr or proc.stdout).strip()))
            except (subprocess.TimeoutExpired, OSError) as exc:
                result_q.put(("fail", str(exc)))

        threading.Thread(target=_runner, daemon=True).start()

        def _poll() -> None:
            try:
                outcome, detail = result_q.get_nowait()
            except queue.Empty:
                QTimer.singleShot(120, _poll)
                return
            self._test_btn.setEnabled(True)
            if outcome == "ok":
                self._test_result_lbl.setText(f"✓ {self._ssh_alias_edit.text().strip()} reachable.")
            else:
                msg = detail.splitlines()[-1] if detail else "(no error message)"
                self._test_result_lbl.setText(f"✗ {msg[:120]}")

        QTimer.singleShot(120, _poll)

    # ── geometry persistence ──────────────────────────────────────────────

    def _settings(self) -> QSettings:
        return QSettings("AllWell", "AllWellApp")

    def _restore_geometry(self) -> None:
        geom = self._settings().value("dialog/remote_form_geometry")
        if geom is not None:
            self.restoreGeometry(geom)

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt override
        self._settings().setValue("dialog/remote_form_geometry", self.saveGeometry())
        super().closeEvent(event)


# ────────────────────────────────────────────────────────────────────────────
# BootstrapLogDialog — live stdout from scripts/_bootstrap_lib.py
# ────────────────────────────────────────────────────────────────────────────


class BootstrapLogDialog(QDialog):
    """Streams bootstrap output line by line and lets the user cancel.

    Reuses the v5.0.0 PipelineRunner queue+QTimer pattern. Spawns
    ``python scripts/_bootstrap_lib.py <alias>`` in its own session so
    Cancel sends SIGTERM to the whole group (via the shared
    ``services.subprocess_kill.signal_subprocess`` helper).
    """

    # Same event vocabulary as PipelineRunner so the polling loop reads
    # cleanly. Future refactor could share the queue class outright.
    finished_ok = Signal(int)  # exit code; 0 = success

    def __init__(self, parent: Optional[QWidget], alias: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Bootstrap {alias}")
        self.setModal(True)
        self.resize(720, 480)

        self._alias = alias
        self._proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._log_q: queue.Queue = queue.Queue(maxsize=10000)
        self._started_at: Optional[float] = None
        self._exit_code: Optional[int] = None
        self._finished_emitted = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(8)

        self._title_lbl = QLabel(f"Bootstrapping {alias}…", self)
        self._title_lbl.setObjectName("SectionTitle")
        layout.addWidget(self._title_lbl)

        hint = QLabel(
            "First-time pip install of TensorFlow + StarDist can take "
            "10–30 minutes depending on the remote's CPU and disk.", self,
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._log = QPlainTextEdit(self)
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(20000)
        mono = QFont("Monaco")
        mono.setStyleHint(QFont.TypeWriter)
        self._log.setFont(mono)
        layout.addWidget(self._log, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._cancel_btn = QPushButton("Cancel", self)
        self._cancel_btn.setProperty("variant", "danger")
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._cancel_btn)
        self._close_btn = QPushButton("Close", self)
        self._close_btn.setEnabled(False)
        self._close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

        self._restore_geometry()
        QTimer.singleShot(0, self._spawn)
        QTimer.singleShot(80, self._poll)

    # ── lifecycle ────────────────────────────────────────────────────────

    def _spawn(self) -> None:
        repo_root = Path(__file__).resolve().parent
        bootstrap = repo_root / "scripts" / "_bootstrap_lib.py"
        if not bootstrap.is_file():
            self._append(f"[error] bootstrap script not found at {bootstrap}\n")
            self._mark_finished(2)
            return
        cmd = [sys.executable, str(bootstrap), self._alias]
        self._append(f"$ {' '.join(cmd)}\n")
        self._started_at = time.monotonic()
        kwargs: dict = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "bufsize": 1,
            "env": {**os.environ, "PYTHONUNBUFFERED": "1"},
        }
        if os.name == "posix":
            kwargs["start_new_session"] = True
        else:
            kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0,
            )
        try:
            self._proc = subprocess.Popen(cmd, **kwargs)
        except OSError as exc:
            self._append(f"[error] failed to launch bootstrap: {exc}\n")
            self._mark_finished(2)
            return

        def _reader() -> None:
            try:
                assert self._proc is not None and self._proc.stdout is not None
                for line in self._proc.stdout:
                    try:
                        self._log_q.put_nowait(("line", line))
                    except queue.Full:
                        # Buffer overflow — drop silently; the
                        # QPlainTextEdit maximumBlockCount cap handles
                        # the tail of a very long run gracefully.
                        pass
                self._proc.wait()
                self._log_q.put_nowait(("finished", self._proc.returncode))
            except Exception as exc:  # noqa: BLE001 — surface to dialog
                self._log_q.put_nowait(("error", str(exc)))

        self._reader_thread = threading.Thread(target=_reader, daemon=True)
        self._reader_thread.start()

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self._log_q.get_nowait()
                if kind == "line":
                    self._append(payload)
                elif kind == "error":
                    self._append(f"[error] {payload}\n")
                elif kind == "finished":
                    self._mark_finished(int(payload))
        except queue.Empty:
            pass
        if self._exit_code is None:
            QTimer.singleShot(80, self._poll)

    def _mark_finished(self, code: int) -> None:
        if self._finished_emitted:
            return
        self._finished_emitted = True
        self._exit_code = code
        elapsed = (
            time.monotonic() - self._started_at if self._started_at else 0.0
        )
        if code == 0:
            self._title_lbl.setText(
                f"Bootstrap of {self._alias} complete "
                f"({elapsed / 60:.1f} min)."
            )
        else:
            self._title_lbl.setText(
                f"Bootstrap of {self._alias} failed (exit {code})."
            )
        self._cancel_btn.setEnabled(False)
        self._close_btn.setEnabled(True)
        self._close_btn.setDefault(True)
        self.finished_ok.emit(code)

    def _on_cancel(self) -> None:
        if self._proc is None or self._exit_code is not None:
            self.reject()
            return
        self._append("\n[cancel] sending SIGTERM to bootstrap subprocess group…\n")
        signal_subprocess(self._proc)
        self._cancel_btn.setEnabled(False)

    # ── log helpers ──────────────────────────────────────────────────────

    def _append(self, text: str) -> None:
        # Match the AnalyzeTab idiom: auto-scroll only when the user was
        # already at the bottom, otherwise leave their viewport alone.
        bar = self._log.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        self._log.appendPlainText(text.rstrip("\n"))
        if at_bottom:
            bar.setValue(bar.maximum())

    # ── geometry persistence ──────────────────────────────────────────────

    def _settings(self) -> QSettings:
        return QSettings("AllWell", "AllWellApp")

    def _restore_geometry(self) -> None:
        geom = self._settings().value("dialog/bootstrap_log_geometry")
        if geom is not None:
            self.restoreGeometry(geom)

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt override
        # If still running, cancel before closing — the user may have
        # hit the window close (X) button instead of Cancel.
        if self._proc is not None and self._exit_code is None:
            signal_subprocess(self._proc)
        self._settings().setValue(
            "dialog/bootstrap_log_geometry", self.saveGeometry(),
        )
        super().closeEvent(event)


# ────────────────────────────────────────────────────────────────────────────
# ManageRemotesDialog — top-level list + actions
# ────────────────────────────────────────────────────────────────────────────


class ManageRemotesDialog(QDialog):
    """List of configured remotes with Add / Edit / Delete / Test /
    Bootstrap buttons.

    Emits :attr:`remotes_changed` whenever the underlying file changes
    so the parent AnalyzeTab can refresh its "Run on:" combobox.
    """

    remotes_changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manage Remote Servers")
        self.setModal(True)
        self.resize(640, 420)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(12)

        # ── List ────────────────────────────────────────────────────────
        left = QVBoxLayout()
        left.setSpacing(8)
        title = QLabel("Configured Remotes", self)
        title.setObjectName("SectionTitle")
        left.addWidget(title)

        self._list = QListWidget(self)
        self._list.itemSelectionChanged.connect(self._refresh_button_state)
        left.addWidget(self._list, 1)
        self._empty_hint = QLabel(
            "No remotes configured yet. Click Add to create one.", self,
        )
        self._empty_hint.setObjectName("Muted")
        self._empty_hint.setWordWrap(True)
        left.addWidget(self._empty_hint)
        layout.addLayout(left, 1)

        # ── Buttons ─────────────────────────────────────────────────────
        right = QVBoxLayout()
        right.setSpacing(6)
        self._add_btn = QPushButton("Add…", self)
        self._add_btn.setProperty("variant", "primary")
        self._add_btn.clicked.connect(self._on_add)
        right.addWidget(self._add_btn)

        self._edit_btn = QPushButton("Edit…", self)
        self._edit_btn.clicked.connect(self._on_edit)
        right.addWidget(self._edit_btn)

        self._delete_btn = QPushButton("Delete", self)
        self._delete_btn.setProperty("variant", "danger")
        self._delete_btn.clicked.connect(self._on_delete)
        right.addWidget(self._delete_btn)

        right.addSpacing(8)
        self._test_btn = QPushButton("Test", self)
        self._test_btn.clicked.connect(self._on_test)
        right.addWidget(self._test_btn)

        self._bootstrap_btn = QPushButton("Bootstrap…", self)
        self._bootstrap_btn.clicked.connect(self._on_bootstrap)
        right.addWidget(self._bootstrap_btn)

        right.addStretch(1)
        self._close_btn = QPushButton("Close", self)
        self._close_btn.clicked.connect(self.accept)
        right.addWidget(self._close_btn)
        layout.addLayout(right)

        self._reload()
        self._restore_geometry()

    # ── list management ─────────────────────────────────────────────────

    def _reload(self) -> None:
        prev = self._selected_name()
        self._list.clear()
        try:
            remotes = load_remotes()
        except ValueError as exc:
            QMessageBox.critical(
                self, "remotes.toml error",
                f"Could not parse your remotes.toml:\n{exc}",
            )
            remotes = {}
        for name, host in remotes.items():
            item = QListWidgetItem(f"{name}    (ssh:{host.ssh_alias})", self._list)
            item.setData(Qt.UserRole, name)
        self._empty_hint.setVisible(self._list.count() == 0)
        if prev:
            for i in range(self._list.count()):
                if self._list.item(i).data(Qt.UserRole) == prev:
                    self._list.setCurrentRow(i)
                    break
        self._refresh_button_state()

    def _selected_name(self) -> Optional[str]:
        items = self._list.selectedItems()
        if not items:
            return None
        return items[0].data(Qt.UserRole)

    def _refresh_button_state(self) -> None:
        has_sel = self._selected_name() is not None
        self._edit_btn.setEnabled(has_sel)
        self._delete_btn.setEnabled(has_sel)
        self._test_btn.setEnabled(has_sel)
        self._bootstrap_btn.setEnabled(has_sel)

    # ── actions ─────────────────────────────────────────────────────────

    def _existing_names(self) -> set[str]:
        return {
            self._list.item(i).data(Qt.UserRole)
            for i in range(self._list.count())
        }

    def _on_add(self) -> None:
        dlg = RemoteFormDialog(
            self, host=None, existing_names=self._existing_names(),
        )
        if dlg.exec() == QDialog.Accepted:
            self._reload()
            self.remotes_changed.emit()

    def _on_edit(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        remotes = load_remotes()
        host = remotes.get(name)
        if host is None:
            QMessageBox.warning(self, "Gone",
                f"Remote '{name}' has been deleted from the file.")
            self._reload()
            return
        dlg = RemoteFormDialog(self, host=host)
        if dlg.exec() == QDialog.Accepted:
            self._reload()
            self.remotes_changed.emit()

    def _on_delete(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        ok = QMessageBox.question(
            self, "Delete Remote",
            f"Remove '{name}' from remotes.toml?\n\n"
            "This only edits the local config — nothing is changed on "
            "the remote machine.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ok != QMessageBox.Yes:
            return
        try:
            delete_remote(name)
        except OSError as exc:
            QMessageBox.critical(
                self, "Delete Failed",
                f"Could not write to remotes.toml:\n{exc}",
            )
            return
        self._reload()
        self.remotes_changed.emit()

    def _on_test(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        remotes = load_remotes()
        host = remotes.get(name)
        if host is None:
            return
        # Cheap inline test — same shape as RemoteFormDialog._test_ssh
        # but reported via a QMessageBox so the user gets unambiguous
        # ✓ / ✗ feedback from the list view.
        try:
            proc = subprocess.run(
                [resolve_ssh_binary(),
                 "-o", "BatchMode=yes",
                 "-o", "ConnectTimeout=10",
                 host.ssh_alias, "echo", "ok"],
                capture_output=True, text=True, timeout=15,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            QMessageBox.critical(
                self, f"Test failed: {name}",
                f"Could not reach {host.ssh_alias}:\n{exc}",
            )
            return
        if proc.returncode == 0 and proc.stdout.strip() == "ok":
            QMessageBox.information(
                self, f"Test passed: {name}",
                f"{host.ssh_alias} responded.",
            )
        else:
            QMessageBox.critical(
                self, f"Test failed: {name}",
                (proc.stderr or proc.stdout).strip()
                or "(ssh returned non-zero with no message)",
            )

    def _on_bootstrap(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        dlg = BootstrapLogDialog(self, alias=name)

        def _on_finish(code: int) -> None:
            self._reload()
            self.remotes_changed.emit()

        dlg.finished_ok.connect(_on_finish)
        dlg.exec()

    # ── geometry persistence ──────────────────────────────────────────────

    def _settings(self) -> QSettings:
        return QSettings("AllWell", "AllWellApp")

    def _restore_geometry(self) -> None:
        geom = self._settings().value("dialog/manage_remotes_geometry")
        if geom is not None:
            self.restoreGeometry(geom)

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt override
        self._settings().setValue(
            "dialog/manage_remotes_geometry", self.saveGeometry(),
        )
        super().closeEvent(event)
