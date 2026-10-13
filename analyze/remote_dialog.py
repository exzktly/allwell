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
  ``services/remote_bootstrap.py`` subprocess output line by line so the
  user sees progress through the 10–30 minute pip install.
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QToolButton,
    QVBoxLayout, QWidget,
)

from services.remote_config import (
    RemoteHost,
    delete_remote,
    load_remotes,
    write_remote,
)
from services import remote_config
from services import ssh_key_setup
from services.subprocess_kill import signal_subprocess
from services.remote_srun_retry import stray_words_message
from services.subprocess_text import TEXT_UTF8
from ui import theme


# Alias must look like a TOML bare key — letters, digits, dash, underscore.
_ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# How often a pending probe's result queue is checked, in milliseconds. One
# hop per tick, so the event loop owns the wait rather than the GUI thread.
_PROBE_POLL_MS = 120

# How long one bootstrap-log drain slice may hold the GUI thread, and how many
# lines it may carry — the same pair, for the same reason, as
# ``analyze.tab._POLL_BUDGET_S`` / ``_POLL_MAX_LINES``. Restated rather than
# imported: ``analyze.tab`` imports this module (lazily, from the Manage…
# button), so importing it back would close the cycle.
_POLL_BUDGET_S = 0.015
_POLL_MAX_LINES = 500
# The gap between slices when the queue has run dry. A slice that stopped
# because it hit a limit re-arms at once instead.
_POLL_IDLE_MS = 80


def poll_queue_on(owner: QWidget, result_q: queue.Queue, on_result) -> QTimer:
    """Call *on_result* on the GUI thread with the first item *result_q* yields.

    The returned timer is **parented to** *owner*, which is the whole point:
    Qt destroys a widget's children with it, so a dialog that is closed and
    deleted while a probe is still in flight takes its poller down with it. A
    free-standing ``QTimer.singleShot`` chain outlives the widget it was created
    for and its callback then touches a C++ object that no longer exists. Until
    these dialogs were destroyed on close, the leak was what hid that.
    """
    timer = QTimer(owner)
    timer.setInterval(_PROBE_POLL_MS)

    def _poll() -> None:
        try:
            result = result_q.get_nowait()
        except queue.Empty:
            return
        timer.stop()
        on_result(*result)

    timer.timeout.connect(_poll)
    timer.start()
    return timer


#: Set on the owning widget for the life of one probe. A dynamic property
#: rather than an attribute so it costs no ``__init__`` line in three dialog
#: classes and cannot be forgotten by a fourth.
_PROBE_IN_FLIGHT = "_aw_ssh_probe_in_flight"


def probe_in_flight(owner: QWidget) -> bool:
    """Whether *owner* has an ssh probe running that has not answered yet.

    Read it wherever a button's enabled state is recomputed from something
    other than the probe itself — a selection change, a wizard step advancing —
    so the recompute cannot hand back a control the probe is using.
    """
    return bool(owner.property(_PROBE_IN_FLIGHT))


def probe_ssh_async(
    owner: QWidget,
    ssh_alias: str,
    ssh_key_path: str,
    on_result,
    *,
    timeout: int = 15,
) -> bool:
    """Run one ``ssh <alias> echo ok`` off the GUI thread; call *on_result* with
    ``(ok, detail)`` on the GUI thread when it answers.

    The single definition of "test this host" for every button in this module.
    Three of them called :func:`services.ssh_key_setup.probe_ssh_connection`
    directly, and only one of the three did it on a worker thread — so a remote
    behind a VPN the user is not on froze the whole application, log pane and
    all, for the probe's full 15-second timeout, with no spinner and no label to
    say why.

    *on_result* is invoked at most once, and never after *owner* is destroyed.
    The worker always puts something, so a probe that raises ends the poll
    rather than leaving it spinning with the button stuck disabled.

    **One probe per owner at a time.** Returns ``False`` without starting
    anything when *owner* already has one in flight. Every caller disables its
    own button for the duration, which was thought to be enough and is not: the
    Manage Remotes list re-enables Test from ``itemSelectionChanged``, so
    picking another row mid-probe handed back a button still reading "Testing…"
    that started a second probe against a different host. Two pollers then ran
    on one dialog and whichever answered first popped its message box and
    re-enabled the button under the other. The refusal lives here rather than in
    each handler because it is a property of the owner, not of any one button,
    and there are three call sites that each got it wrong differently.
    """
    if probe_in_flight(owner):
        return False
    owner.setProperty(_PROBE_IN_FLIGHT, True)
    result_q: queue.Queue = queue.Queue()

    def _runner() -> None:
        try:
            result_q.put(ssh_key_setup.probe_ssh_connection(
                ssh_alias, ssh_key_path, timeout=timeout))
        except Exception as exc:
            result_q.put((False, f"error: {exc}"))

    def _finish(ok: bool, detail: str) -> None:
        # Cleared before the callback so a handler that re-tests from inside
        # its own result — or asks ``probe_in_flight`` while rebuilding its
        # buttons — sees the probe as finished, which it is.
        owner.setProperty(_PROBE_IN_FLIGHT, False)
        on_result(ok, detail)

    threading.Thread(target=_runner, daemon=True).start()
    poll_queue_on(owner, result_q, _finish)
    return True

# Prefilled into the "Scheduler command" field the first time a user
# switches a host to scheduled compute, so they have a working SLURM
# starting point to tweak rather than a blank box. CPU-only by default;
# add ``--gres=gpu:1`` (and uncheck "CPU only") for GPU partitions.
_DEFAULT_COMPUTE_LAUNCHER = "srun --cpus-per-task=16 --mem=64G --time=4:00:00"


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
        parent: QWidget | None = None,
        *,
        host: RemoteHost | None = None,
        existing_names: set[str] | None = None,
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

        # SSH key path + wizard launcher (v5.2.1). The key path field is
        # populated automatically by the wizard but left editable so a
        # power user can point at an existing key. Empty = use whatever
        # ~/.ssh/config provides (typical case for any developer with a
        # working ssh setup).
        key_row = QWidget(self)
        key_row_layout = QHBoxLayout(key_row)
        key_row_layout.setContentsMargins(0, 0, 0, 0)
        key_row_layout.setSpacing(6)
        self._ssh_key_edit = QLineEdit(host.ssh_key_path, key_row)
        self._ssh_key_edit.setPlaceholderText(
            "Empty = use ~/.ssh/config (recommended) or click Setup…"
        )
        key_row_layout.addWidget(self._ssh_key_edit, 1)
        self._setup_key_btn = QPushButton("Setup SSH Key…", key_row)
        self._setup_key_btn.setProperty("variant", "secondary")
        self._setup_key_btn.setToolTip(
            "Generate a passphrase-less Ed25519 key and walk through "
            "copying it to the remote so future runs don't need a password."
        )
        self._setup_key_btn.clicked.connect(self._open_ssh_key_wizard)
        key_row_layout.addWidget(self._setup_key_btn)
        form.addRow("SSH key (optional)", key_row)

        self._test_result_lbl = QLabel("", self)
        self._test_result_lbl.setObjectName("Muted")
        layout.addLayout(form)
        layout.addWidget(self._test_result_lbl)

        # ── Main (non-advanced) controls ────────────────────────────────
        main_form = QFormLayout()
        main_form.setLabelAlignment(Qt.AlignRight)
        main_form.setSpacing(8)

        # StarDist's switch only: Cellpose uses a GPU whenever the node it runs
        # on has one (pipeline.parallelism says why), and bootstrap ticks this
        # whenever the *login* node has no GPU, which is most clusters.
        self._cpu_only_cb = QCheckBox("StarDist on CPU only", self)
        self._cpu_only_cb.setToolTip(
            "Hide the GPU from StarDist (TensorFlow). Cellpose is not affected: "
            "it uses a GPU whenever the node it runs on has one.")
        self._cpu_only_cb.setChecked(host.cpu_only)
        main_form.addRow("", self._cpu_only_cb)

        # ── Where compute runs ──────────────────────────────────────────
        # Default: the pipeline runs on the SSH host itself. On an HPC
        # "lobby"/login node you instead acquire a compute node from the
        # batch scheduler — pick "On a node it schedules" and give the
        # srun command. Both map onto the single compute_launcher string
        # (empty = on this machine; non-empty = the scheduler command).
        self._compute_loc_combo = QComboBox(self)
        self._compute_loc_combo.addItem("On the remote machine itself", "local")
        self._compute_loc_combo.addItem("On a node it schedules (SLURM)", "scheduled")
        self._compute_loc_combo.setToolTip(
            "Where the analysis pipeline actually runs. Use 'On a node it "
            "schedules' when the SSH host is an HPC login/lobby node that "
            "forbids heavy compute and hands work to a batch scheduler."
        )
        main_form.addRow("Run compute", self._compute_loc_combo)

        # The scheduled-only controls (partition picker + scheduler command)
        # live inside one container that's shown/hidden as a unit when the
        # "Run compute" choice flips — a plain QFormLayout field would leave
        # its label stranded when collapsed.
        self._scheduled_box = QWidget(self)
        sched_layout = QVBoxLayout(self._scheduled_box)
        sched_layout.setContentsMargins(0, 0, 0, 0)
        sched_layout.setSpacing(6)

        # Compute-profile picker: the discrete options the cluster's
        # JupyterHub web UI would show (its spawner profile_list /
        # ProfilesSpawner.profiles), discovered by reading jupyterhub_config.py
        # on the remote — NOT a raw SLURM partition list. Selecting a profile
        # fills in the whole scheduler command; each item carries its launcher
        # as item data. The command stays editable so the user can verify it.
        prof_row = QHBoxLayout()
        prof_row.setSpacing(6)
        prof_row.addWidget(QLabel("Compute profile", self._scheduled_box))
        self._profile_combo = QComboBox(self._scheduled_box)
        self._profile_combo.setToolTip(
            "The discrete compute options your JupyterHub exposes. Click "
            "Fetch to read them from the cluster's jupyterhub_config.py over "
            "SSH; picking one fills in the scheduler command below."
        )
        self._profile_combo.activated.connect(self._on_profile_chosen)
        prof_row.addWidget(self._profile_combo, 1)
        self._fetch_prof_btn = QPushButton("Fetch", self._scheduled_box)
        self._fetch_prof_btn.setProperty("variant", "secondary")
        self._fetch_prof_btn.setToolTip(
            "Read jupyterhub_config.py on the remote and list its compute "
            "profiles (no JupyterHub webpage needed)."
        )
        self._fetch_prof_btn.clicked.connect(self._fetch_profiles)
        prof_row.addWidget(self._fetch_prof_btn)
        sched_layout.addLayout(prof_row)

        # Persistent feedback so the user can tell whether the picked profile
        # is the saved one, a pending pick, or a hand-edited command.
        self._profile_status_lbl = QLabel("", self._scheduled_box)
        self._profile_status_lbl.setObjectName("Muted")
        self._profile_status_lbl.setWordWrap(True)
        sched_layout.addWidget(self._profile_status_lbl)

        launcher_row = QHBoxLayout()
        launcher_row.setSpacing(6)
        launcher_row.addWidget(QLabel("Scheduler command", self._scheduled_box))
        self._compute_launcher_edit = QLineEdit(host.compute_launcher, self._scheduled_box)
        self._compute_launcher_edit.setPlaceholderText(
            "srun --cpus-per-task=16 --mem=64G --time=4:00:00   "
            "(add --gres=gpu:1 for GPU)"
        )
        # Hand-editing the command means it's no longer a pristine profile pick
        # — reflect that live (textEdited fires only on user edits, not our
        # programmatic setText, so no feedback loop).
        self._compute_launcher_edit.textEdited.connect(self._on_launcher_edited)
        launcher_row.addWidget(self._compute_launcher_edit, 1)
        sched_layout.addLayout(launcher_row)

        # Optional override for where jupyterhub_config.py lives on the remote
        # (blank → probe conventional locations like /etc/jupyterhub/...).
        jhub_row = QHBoxLayout()
        jhub_row.setSpacing(6)
        jhub_row.addWidget(QLabel("JupyterHub config", self._scheduled_box))
        self._jhub_path_edit = QLineEdit(host.jupyterhub_config_path, self._scheduled_box)
        self._jhub_path_edit.setPlaceholderText(
            "/etc/jupyterhub/jupyterhub_config.py  (optional — auto-detected if blank)"
        )
        jhub_row.addWidget(self._jhub_path_edit, 1)
        sched_layout.addLayout(jhub_row)

        # How long to wait for the scheduler to grant a node before giving up.
        # A queued / spinning-up node emits nothing, so this drives the ssh
        # keepalive tolerance (ServerAliveCountMax); the stock ~90s default is
        # far too short for a node that has to be provisioned.
        wait_row = QHBoxLayout()
        wait_row.setSpacing(6)
        wait_row.addWidget(QLabel("Max queue wait", self._scheduled_box))
        self._queue_wait_spin = QSpinBox(self._scheduled_box)
        self._queue_wait_spin.setRange(0, 1440)  # up to 24h; 0 = unlimited
        self._queue_wait_spin.setSuffix(" min")
        self._queue_wait_spin.setSpecialValueText("Wait indefinitely")
        self._queue_wait_spin.setValue(host.queue_wait_timeout_min)
        self._queue_wait_spin.setToolTip(
            "How long to wait for the scheduler to allocate (or spin up) a "
            "compute node before giving up. Raise this if your cluster queues "
            "or provisions nodes on demand. 0 = wait indefinitely."
        )
        wait_row.addWidget(self._queue_wait_spin)
        wait_row.addStretch(1)
        sched_layout.addLayout(wait_row)
        main_form.addRow(self._scheduled_box)

        # Restore the last-fetched profiles for this host (cached locally) so
        # reopening doesn't force a re-Fetch, and pre-select the one matching
        # the saved command so the user sees their saved choice reflected.
        self._populate_profile_combo(
            self._load_cached_profiles(), select_launcher=host.compute_launcher,
        )

        scheduled = bool(host.compute_launcher.strip())
        self._compute_loc_combo.setCurrentIndex(1 if scheduled else 0)
        self._scheduled_box.setVisible(scheduled)
        self._compute_loc_combo.currentIndexChanged.connect(
            self._on_compute_loc_changed
        )

        self._cache_budget_spin = QSpinBox(self)
        self._cache_budget_spin.setRange(1, 10000)
        self._cache_budget_spin.setSuffix(" GB")
        self._cache_budget_spin.setValue(host.cache_budget_gb)
        main_form.addRow("Cache budget", self._cache_budget_spin)

        self._extra_args_edit = QLineEdit(" ".join(host.extra_args), self)
        self._extra_args_edit.setPlaceholderText("--workers 16 --tf_threads 8")
        main_form.addRow("Extra args", self._extra_args_edit)

        layout.addLayout(main_form)

        # ── Advanced (collapsible) — remote paths ───────────────────────
        # v5.2.1: these three fields rarely need changing. The bootstrap
        # always installs to the conventional locations and writes the
        # correct values into remotes.toml automatically. Hiding them
        # behind a toggle de-clutters the form for the typical user
        # while still giving the edge-case user a clear path.
        defaults = RemoteHost(name=host.name or "_", ssh_alias=host.ssh_alias or "_")
        non_default_paths = (
            (host.remote_project_dir and host.remote_project_dir != defaults.remote_project_dir)
            or (host.remote_staging_dir and host.remote_staging_dir != defaults.remote_staging_dir)
            or (host.remote_python and host.remote_python != defaults.remote_python)
            or bool(host.remote_scratch_dir)
        )

        self._advanced_btn = QToolButton(self)
        self._advanced_btn.setText("Advanced (remote paths)")
        self._advanced_btn.setCheckable(True)
        self._advanced_btn.setArrowType(
            Qt.DownArrow if non_default_paths else Qt.RightArrow
        )
        self._advanced_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._advanced_btn.setStyleSheet("QToolButton { border: none; padding: 4px 0; }")
        self._advanced_btn.toggled.connect(self._on_advanced_toggled)
        layout.addWidget(self._advanced_btn)

        self._advanced_frame = QFrame(self)
        adv_form = QFormLayout(self._advanced_frame)
        adv_form.setLabelAlignment(Qt.AlignRight)
        adv_form.setSpacing(8)
        adv_form.setContentsMargins(20, 4, 0, 4)

        hint = QLabel(
            "Leave blank to use the bootstrap defaults. Change only if "
            "your remote installs miniforge to a non-standard location.",
            self._advanced_frame,
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        adv_form.addRow(hint)

        # Show the user's value if non-default, else leave the field
        # blank and surface the default via placeholder. result_host()
        # already falls back to dataclass defaults on empty strings.
        self._project_dir_edit = QLineEdit(
            "" if host.remote_project_dir == defaults.remote_project_dir else host.remote_project_dir,
            self._advanced_frame,
        )
        self._project_dir_edit.setPlaceholderText(defaults.remote_project_dir)
        adv_form.addRow("Remote project dir", self._project_dir_edit)

        self._staging_dir_edit = QLineEdit(
            "" if host.remote_staging_dir == defaults.remote_staging_dir else host.remote_staging_dir,
            self._advanced_frame,
        )
        self._staging_dir_edit.setPlaceholderText(defaults.remote_staging_dir)
        adv_form.addRow("Remote staging dir", self._staging_dir_edit)

        # Where the *pipeline* keeps its transient working files on the remote,
        # as distinct from the staging dir above (which holds the plate). Left
        # empty it lands on the remote's home directory, which on a cluster is
        # the volume with the small quota — so the placeholder and the tooltip
        # name the values a site actually uses, because someone who does not
        # already know their cluster's scratch path will not guess it.
        self._scratch_dir_edit = QLineEdit(host.remote_scratch_dir,
                                           self._advanced_frame)
        self._scratch_dir_edit.setPlaceholderText("~/.allwell/scratch (default)")
        self._scratch_dir_edit.setToolTip(
            "Where the pipeline writes its per-well working files on the remote.\n"
            "Leave empty to use the remote's home directory. On a cluster set it "
            "to real scratch —\n$TMPDIR, $SLURM_TMPDIR or /scratch/$USER — since "
            "a run's working set is several\nuncompressed images per field, and a "
            "home quota is the wrong volume for it."
        )
        adv_form.addRow("Remote scratch dir", self._scratch_dir_edit)

        self._python_edit = QLineEdit(
            "" if host.remote_python == defaults.remote_python else host.remote_python,
            self._advanced_frame,
        )
        self._python_edit.setPlaceholderText(defaults.remote_python)
        adv_form.addRow("Remote python", self._python_edit)

        self._advanced_frame.setVisible(non_default_paths)
        self._advanced_btn.setChecked(non_default_paths)
        layout.addWidget(self._advanced_frame)

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
        form_error = self.form_error()
        if form_error is not None:
            QMessageBox.critical(self, "Invalid Form", form_error)
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

    def form_error(self) -> str | None:
        """The reason this form cannot be saved, or ``None``.

        "On a node it schedules" with an empty Scheduler command saved as
        ``compute_launcher=""`` — which is the spelling of *run on the SSH host
        itself* — and reopening the host showed "On the remote machine itself",
        with no trace of the choice. A user who cleared the seeded ``srun`` line
        meaning to paste their own therefore ran a multi-hour segmentation on an
        HPC login node, the exact thing this control exists to prevent.
        """
        if (self._compute_loc_combo.currentData() == "scheduled"
                and not self._compute_launcher_edit.text().strip()):
            return (
                "“Run compute: on a node it schedules” needs a scheduler "
                "command — the line that asks your batch system for a compute "
                "node, e.g.\n\n    " + _DEFAULT_COMPUTE_LAUNCHER + "\n\n"
                "Saving it empty would mean “run on the SSH host itself”, "
                "which on a login node is what this setting exists to avoid. "
                "Fill it in, or switch Run compute back to “On the remote "
                "machine itself”."
            )
        if self._compute_loc_combo.currentData() == "scheduled":
            return stray_words_message(self._compute_launcher_edit.text())
        return None

    def result_host(self) -> RemoteHost:
        """Build a ``RemoteHost`` from the current form state.

        Public so callers (e.g. the Test SSH button before OK is hit)
        can preview the host without committing. Empty path fields
        fall back to the dataclass defaults — the v5.2.1 Advanced
        expander uses placeholders to communicate this so users
        don't need to retype the defaults.

        Those fallbacks are read off ``RemoteHost`` itself. Written out as
        string literals they were a second copy of three values the same class
        already defines — and the placeholders beside them are read from the
        dataclass, so changing a default would have moved the hint while the
        saved value kept the old path.
        """
        raw_extras = self._extra_args_edit.text().strip()
        extras: tuple[str, ...] = tuple(raw_extras.split()) if raw_extras else ()
        defaults = RemoteHost(name="_", ssh_alias="_")
        return RemoteHost(
            name=self._name_edit.text().strip(),
            ssh_alias=self._ssh_alias_edit.text().strip(),
            remote_project_dir=(self._project_dir_edit.text().strip()
                                or defaults.remote_project_dir),
            remote_staging_dir=(self._staging_dir_edit.text().strip()
                                or defaults.remote_staging_dir),
            remote_scratch_dir=self._scratch_dir_edit.text().strip(),
            remote_python=self._python_edit.text().strip() or defaults.remote_python,
            cpu_only=self._cpu_only_cb.isChecked(),
            extra_args=extras,
            cache_budget_gb=self._cache_budget_spin.value(),
            ssh_key_path=self._ssh_key_edit.text().strip(),
            compute_launcher=(
                self._compute_launcher_edit.text().strip()
                if self._compute_loc_combo.currentData() == "scheduled"
                else ""
            ),
            jupyterhub_config_path=self._jhub_path_edit.text().strip(),
            queue_wait_timeout_min=self._queue_wait_spin.value(),
        )

    # ── compute-location toggle ────────────────────────────────────────────

    def _on_compute_loc_changed(self, _idx: int) -> None:
        """Show/hide the scheduled-compute controls as the user flips between
        running compute on the SSH host vs. a scheduled node. On the first
        switch to scheduled, seed a working ``srun`` default so the user
        edits rather than starts from a blank box."""
        scheduled = self._compute_loc_combo.currentData() == "scheduled"
        self._scheduled_box.setVisible(scheduled)
        if scheduled and not self._compute_launcher_edit.text().strip():
            self._compute_launcher_edit.setText(_DEFAULT_COMPUTE_LAUNCHER)
        if scheduled:
            # Refresh the profile match/status for the now-visible command
            # (setText above doesn't fire textEdited).
            self._select_profile_for_launcher(
                self._compute_launcher_edit.text(), saved=True,
            )
        # Grow/shrink the dialog to fit the controls appearing/disappearing.
        self.adjustSize()

    def _on_profile_chosen(self, _idx: int) -> None:
        """Fill the scheduler command from the selected JupyterHub profile.

        Each profile item carries its full ``srun`` launcher as item data; the
        command field stays the source of truth (and editable) so the user can
        eyeball/tweak it. The placeholder item carries an empty launcher and is
        ignored."""
        launcher = self._profile_combo.currentData()
        if launcher:
            self._compute_launcher_edit.setText(str(launcher))
            self._set_profile_status(self._profile_combo.currentText(), saved=False)
        else:
            self._set_profile_status("", saved=False)

    def _on_launcher_edited(self, _text: str) -> None:
        """Keep the dropdown + status honest when the user hand-edits the
        command: re-select a matching profile if the text still equals one, or
        drop to the placeholder + "custom command" otherwise."""
        self._select_profile_for_launcher(
            self._compute_launcher_edit.text(), saved=False,
        )

    # ── profile cache + selection ──────────────────────────────────────────

    def _profiles_cache_key(self) -> str:
        """QSettings key for this host's last-fetched profiles. Keyed by the
        remote name (falling back to the ssh alias) so reopening the same host
        restores its list; empty when neither is known yet."""
        name = self._name_edit.text().strip() or self._ssh_alias_edit.text().strip()
        return f"remote_profiles/{name}" if name else ""

    def _load_cached_profiles(self) -> list[tuple[str, str]]:
        key = self._profiles_cache_key()
        if not key:
            return []
        raw = self._settings().value(key)
        if not raw:
            return []
        try:
            return [(str(n), str(launcher)) for n, launcher in json.loads(raw)]
        except (ValueError, TypeError):
            return []

    def _save_cached_profiles(self, profiles: list[tuple[str, str]]) -> None:
        key = self._profiles_cache_key()
        if key:
            self._settings().setValue(
                key, json.dumps([[n, launcher] for n, launcher in profiles])
            )

    def _populate_profile_combo(
        self, profiles: list[tuple[str, str]], *, select_launcher: str = "",
    ) -> None:
        """Rebuild the dropdown from ``profiles`` and select the one whose
        launcher matches ``select_launcher`` (the saved/current command)."""
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()
        if profiles:
            self._profile_combo.addItem(f"({len(profiles)} profile(s) — pick one)", "")
        else:
            self._profile_combo.addItem("(click Fetch to load JupyterHub profiles)", "")
        for name, launcher in profiles:
            self._profile_combo.addItem(name, launcher)
        self._profile_combo.blockSignals(False)
        self._select_profile_for_launcher(select_launcher, saved=True)

    def _select_profile_for_launcher(self, launcher: str, *, saved: bool) -> None:
        """Select the dropdown item whose launcher equals ``launcher`` (or the
        placeholder if none), and refresh the status line. ``saved`` marks
        whether the match reflects the persisted command (open/restore) vs a
        just-made edit."""
        target = launcher.strip()
        matched = ""
        for i in range(1, self._profile_combo.count()):
            if target and str(self._profile_combo.itemData(i)).strip() == target:
                matched = self._profile_combo.itemText(i)
                index = i
                break
        else:
            index = 0
        self._profile_combo.blockSignals(True)
        self._profile_combo.setCurrentIndex(index)
        self._profile_combo.blockSignals(False)
        self._set_profile_status(matched, saved=saved)

    def _set_profile_status(self, name: str, *, saved: bool) -> None:
        if name and saved:
            text = f"✓ Saved profile: {name}"
        elif name:
            text = f"Profile “{name}” selected — click OK to save."
        elif not self._compute_launcher_edit.text().strip():
            text = "No profile selected."
        elif self._profile_combo.count() <= 1:
            # A command is set but the profile list hasn't been loaded yet, so
            # we can't name a match — don't cry "custom".
            text = "Saved command set — click Fetch to match it to a profile."
        else:
            text = "Custom command (doesn't match a listed profile)."
        self._profile_status_lbl.setText(text)

    def _fetch_profiles(self) -> None:
        """Read ``jupyterhub_config.py`` on the remote (worker thread) and fill
        the profile dropdown with the cluster's discrete compute options — same
        queue + QTimer pattern as Test SSH so the dialog stays responsive. The
        result is cached locally so reopening the host doesn't re-Fetch."""
        alias = self._ssh_alias_edit.text().strip()
        if not alias:
            self._test_result_lbl.setText("Set an SSH alias first to fetch profiles.")
            return
        host = self.result_host()
        self._fetch_prof_btn.setEnabled(False)
        self._test_result_lbl.setText(
            f"Reading JupyterHub profiles from {alias}…"
        )
        result_q: queue.Queue = queue.Queue()

        def _runner() -> None:
            # Always put a result — if the query raises, _poll would otherwise
            # spin forever with the button stuck disabled (audit E17).
            try:
                ok, profiles, detail = remote_config.query_jupyterhub_profiles(host, timeout=25)
                result_q.put((ok, profiles, detail))
            except Exception as exc:
                result_q.put((False, [], f"error: {exc}"))

        threading.Thread(target=_runner, daemon=True).start()

        def _done(ok: bool, profiles: list, detail: str) -> None:
            self._fetch_prof_btn.setEnabled(True)
            if not ok:
                msg = detail.splitlines()[-1] if detail else "profile discovery failed"
                self._test_result_lbl.setText(f"✗ {msg[:160]}")
                return
            # Cache for next open, repopulate, and keep the current command's
            # profile selected if it's still in the freshly-fetched list.
            self._save_cached_profiles(profiles)
            self._populate_profile_combo(
                profiles, select_launcher=self._compute_launcher_edit.text(),
            )
            self._test_result_lbl.setText(
                f"✓ {len(profiles)} profile(s) from {alias}'s JupyterHub config."
            )

        poll_queue_on(self, result_q, _done)

    # ── advanced expander ──────────────────────────────────────────────────

    def _on_advanced_toggled(self, checked: bool) -> None:
        self._advanced_btn.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        self._advanced_frame.setVisible(checked)
        # Resize-to-fit so the dialog grows/shrinks with the section
        # rather than leaving empty space when collapsed.
        self.adjustSize()

    # ── SSH key wizard ─────────────────────────────────────────────────────

    def _open_ssh_key_wizard(self) -> None:
        """Open the v5.2.1 SSH-key setup wizard.

        Requires an SSH alias to be entered first; the wizard's whole
        purpose is to install a key on that alias's remote, so an
        empty alias means there's nothing useful for it to do.
        """
        alias = self._ssh_alias_edit.text().strip()
        if not alias:
            QMessageBox.information(
                self, "SSH alias required",
                "Enter an SSH alias first (e.g. 'user@host' or a name "
                "from your ~/.ssh/config), then click Setup SSH Key again.",
            )
            return
        dlg = SSHKeyWizardDialog(self, ssh_alias=alias)
        try:
            if dlg.exec() == QDialog.Accepted and dlg.installed_key_path:
                self._ssh_key_edit.setText(str(dlg.installed_key_path))
        finally:
            dlg.deleteLater()

    # ── inline test ────────────────────────────────────────────────────────

    def _test_ssh(self) -> None:
        """Fire a one-shot ``ssh <alias> echo ok`` off the GUI thread.

        Routes through ``ssh_key_setup.probe_ssh_connection`` (via
        :func:`probe_ssh_async`) so the current form's ``ssh_key_path`` — set by
        the Setup SSH Key wizard — is actually passed as ``-i <key>``. Pre-fix,
        this used bare ssh and would say "Permission denied" right after a
        successful wizard run, because the wizard's key sits at a non-default
        path that ssh's identity search does not try.

        The success line names *alias* as captured here, not the field's text at
        poll time: editing the alias while the probe was in flight made the
        dialog report "✓ newalias reachable" about a host it never contacted.
        """
        alias = self._ssh_alias_edit.text().strip()
        if not alias:
            self._test_result_lbl.setText("Set an SSH alias first.")
            return

        key_path = self._ssh_key_edit.text().strip()

        def _done(ok: bool, detail: str) -> None:
            self._test_btn.setEnabled(True)
            if ok:
                self._test_result_lbl.setText(f"✓ {alias} reachable.")
            else:
                msg = detail.splitlines()[-1] if detail else "(no error message)"
                self._test_result_lbl.setText(f"✗ {msg[:120]}")

        # Start first, then take the control. A refused probe means one is
        # already running and owns both the button and the label; disabling
        # ahead of the call and then returning would leave the button dark with
        # nobody scheduled to bring it back.
        if not probe_ssh_async(self, alias, key_path, _done):
            return
        self._test_btn.setEnabled(False)
        suffix = f" (with key {Path(key_path).name})" if key_path else ""
        self._test_result_lbl.setText(f"Testing {alias}{suffix}…")

    # ── geometry persistence ──────────────────────────────────────────────

    def _settings(self) -> QSettings:
        from core.app_settings import app_settings
        return app_settings()

    def _restore_geometry(self) -> None:
        geom = self._settings().value("dialog/remote_form_geometry")
        if geom is not None:
            self.restoreGeometry(geom)

    def closeEvent(self, event) -> None:  # Qt override
        self._settings().setValue("dialog/remote_form_geometry", self.saveGeometry())
        super().closeEvent(event)


# ────────────────────────────────────────────────────────────────────────────
# SSHKeyWizardDialog — generate + install an SSH key (v5.2.1)
# ────────────────────────────────────────────────────────────────────────────


class SSHKeyWizardDialog(QDialog):
    """Walk the user through generating an Ed25519 key and copying it
    to the remote so future runs never need a password.

    Three-step flow:

    1. **Generate** — creates ``~/.ssh/allwell_<alias>`` + ``.pub``
       (idempotent: existing key is reused).
    2. **Copy** — shows the exact shell command (``ssh-copy-id …`` or
       a portable ``cat | ssh`` fallback). User runs it in their own
       terminal and types the password once. We do NOT execute or pipe
       passwords ourselves — the user retains full control and the
       password never enters AllWell's address space.
    3. **Verify** — runs ``ssh -i <key> -o BatchMode=yes <alias> echo
       ok``. Success means the key is installed and AllWell can use it
       for every subsequent ssh / rsync.

    On Accepted, :attr:`installed_key_path` is the path the parent
    dialog should record in ``ssh_key_path``.
    """

    def __init__(self, parent: QWidget | None, *, ssh_alias: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Setup SSH Key — {ssh_alias}")
        self.setModal(True)
        self.resize(640, 520)

        self._ssh_alias = ssh_alias
        self._key_path: Path = ssh_key_setup.default_key_path(ssh_alias)
        self.installed_key_path: Path | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        title = QLabel(f"Set up passwordless SSH for {ssh_alias}", self)
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        intro = QLabel(
            "AllWell never stores SSH passwords. The standard way to "
            "automate ssh is to install a per-machine key on the remote. "
            "This wizard generates that key locally, shows you the exact "
            "command to copy the public half to the remote (you'll be "
            "asked for the password once, in your own terminal), and "
            "verifies the result.",
            self,
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # Step 1: generate key ────────────────────────────────────────────
        step1_title = QLabel("Step 1 — Generate the key locally", self)
        step1_title.setObjectName("SectionTitle")
        layout.addWidget(step1_title)
        self._key_path_lbl = QLabel(f"Key path: {self._key_path}", self)
        self._key_path_lbl.setObjectName("Muted")
        layout.addWidget(self._key_path_lbl)
        self._generate_btn = QPushButton("Generate key", self)
        self._generate_btn.setProperty("variant", "primary")
        self._generate_btn.clicked.connect(self._on_generate)
        layout.addWidget(self._generate_btn)
        self._generate_status_lbl = QLabel("", self)
        self._generate_status_lbl.setObjectName("Muted")
        self._generate_status_lbl.setWordWrap(True)
        layout.addWidget(self._generate_status_lbl)

        # Step 2: copy command ────────────────────────────────────────────
        step2_title = QLabel(
            "Step 2 — Copy the public key to the remote", self,
        )
        step2_title.setObjectName("SectionTitle")
        layout.addWidget(step2_title)
        step2_hint = QLabel(
            "Open your terminal and run this command. You'll be asked "
            "for the remote password once; AllWell never sees it.",
            self,
        )
        step2_hint.setObjectName("Muted")
        step2_hint.setWordWrap(True)
        layout.addWidget(step2_hint)

        cmd_row = QHBoxLayout()
        self._command_edit = QLineEdit(self)
        self._command_edit.setReadOnly(True)
        mono = QFont("Monaco")
        mono.setStyleHint(QFont.TypeWriter)
        self._command_edit.setFont(mono)
        cmd_row.addWidget(self._command_edit, 1)
        self._copy_btn = QPushButton("Copy", self)
        self._copy_btn.setProperty("variant", "secondary")
        self._copy_btn.clicked.connect(self._on_copy_command)
        cmd_row.addWidget(self._copy_btn)
        layout.addLayout(cmd_row)

        # Step 3: verify ──────────────────────────────────────────────────
        step3_title = QLabel("Step 3 — Verify", self)
        step3_title.setObjectName("SectionTitle")
        layout.addWidget(step3_title)
        self._verify_btn = QPushButton("I ran the command — verify now", self)
        self._verify_btn.setProperty("variant", "primary")
        self._verify_btn.clicked.connect(self._on_verify)
        layout.addWidget(self._verify_btn)
        self._verify_status_lbl = QLabel("", self)
        self._verify_status_lbl.setWordWrap(True)
        layout.addWidget(self._verify_status_lbl)

        layout.addStretch(1)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        # Disable the later steps until the earlier ones progress.
        self._copy_btn.setEnabled(False)
        self._command_edit.setEnabled(False)
        self._verify_btn.setEnabled(False)

        # If the key already exists from a previous run, surface its
        # state up front. v5.2.6 distinguishes a passphrase-less key
        # (safe to reuse → populate Step 2 so the user can skip
        # straight to Verify) from a passphrase-protected one
        # (AllWell can't use it under BatchMode=yes → warn).
        if self._key_path.is_file():
            if ssh_key_setup.key_has_passphrase(self._key_path):
                self._generate_status_lbl.setText(
                    f"⚠ Existing key at {self._key_path} has a passphrase. "
                    "Click Generate to replace it with a passphrase-less key."
                )
                # Don't populate Step 2 — the existing key won't pass
                # AllWell's BatchMode=yes verify even if it's copied
                # to the remote.
            else:
                self._generate_status_lbl.setText(
                    f"✓ Key already exists at {self._key_path}. "
                    "Click Generate to re-use it, or jump to Step 2."
                )
                self._populate_step2()

    # ── steps ────────────────────────────────────────────────────────────

    def _on_generate(self) -> None:
        replacing = False
        self._generate_btn.setEnabled(False)
        self._generate_status_lbl.setText("Checking key…")
        QApplication.processEvents()

        # v5.2.6: detect the "existing key has a passphrase" trap. The
        # default behaviour is to reuse an existing key; AllWell runs
        # ssh with BatchMode=yes so a passphrase-protected key
        # silently fails as "Permission denied" at every subsequent
        # Test SSH / Bootstrap step. We catch it here and offer to
        # replace the key with a fresh passphrase-less one.
        if self._key_path.is_file() and ssh_key_setup.key_has_passphrase(self._key_path):
            ans = QMessageBox.question(
                self, "Existing key has a passphrase",
                "An existing SSH key was found, but it's protected by "
                "a passphrase. AllWell runs ssh non-interactively "
                "(BatchMode=yes) so it can't unlock it — every Test "
                "SSH or Bootstrap call would say "
                "\"Permission denied\".\n\n"
                "Replace it with a fresh passphrase-less key?\n\n"
                f"Key path: {self._key_path}",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if ans != QMessageBox.Yes:
                self._generate_btn.setEnabled(True)
                self._generate_status_lbl.setText(
                    "✗ Existing key has a passphrase — wizard cannot proceed. "
                    "Delete or rename the key file manually, then click "
                    "Generate again."
                )
                return
            # User opted in: remove both halves so generate_keypair's
            # idempotency check produces a fresh key. If either unlink fails,
            # that check finds the pair still there, does nothing, and returns
            # "no work done" — which this method then reported as "✓ Key
            # already present — reusing", over the very key the user had just
            # clicked Yes to replace. Verify would go on failing with
            # "Permission denied", the opaque failure this wizard exists to
            # remove. Say what happened instead, and stop.
            pub = self._key_path.with_suffix(self._key_path.suffix + ".pub")
            failed = self._unlink_key_halves(self._key_path, pub)
            if failed:
                self._generate_btn.setEnabled(True)
                self._generate_status_lbl.setText(
                    f"✗ Could not remove the existing key: {failed}. The "
                    "passphrase-protected key is still in place and AllWell "
                    "cannot use it. Delete it yourself, then click Generate "
                    "again."
                )
                return

            replacing = True

        self._generate_status_lbl.setText("Generating…")
        QApplication.processEvents()
        created, err = ssh_key_setup.generate_keypair(self._key_path)
        self._generate_btn.setEnabled(True)
        if err:
            self._generate_status_lbl.setText(f"✗ {err}")
            QMessageBox.critical(
                self, "Key generation failed",
                f"Could not generate {self._key_path}:\n{err}",
            )
            return
        if replacing and not created:
            # Both halves were deleted a moment ago, so "no work done" cannot
            # be true here. Whatever happened, this is not the fresh key the
            # user asked for, and "reusing" would be the wrong word for it.
            self._generate_status_lbl.setText(
                f"✗ {self._key_path} was replaced, but no new key was "
                "generated in its place. Check the path by hand before "
                "trying again."
            )
            return
        msg = f"✓ Key generated at {self._key_path}" if created else \
              f"✓ Key already present at {self._key_path} — reusing."
        self._generate_status_lbl.setText(msg)
        self._populate_step2()

    @staticmethod
    def _unlink_key_halves(*paths: Path) -> str:
        """Delete each of *paths* that exists; return "" or a reason it failed.

        Reported rather than swallowed: the caller's next step is idempotent, so
        a file that survived turns "replace this key" into "reuse it" with no
        outward sign.
        """
        reasons = []
        for path in paths:
            try:
                path.unlink()
            except FileNotFoundError:
                continue                     # already gone is the wanted state
            except OSError as exc:
                reasons.append(f"{path.name}: {exc.strerror or exc}")
        return "; ".join(reasons)

    def _populate_step2(self) -> None:
        cmd = ssh_key_setup.build_copy_command(self._ssh_alias, self._key_path)
        self._command_edit.setText(cmd)
        self._command_edit.setEnabled(True)
        self._copy_btn.setEnabled(True)
        self._verify_btn.setEnabled(not probe_in_flight(self))

    def _on_copy_command(self) -> None:
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(self._command_edit.text())
            self._copy_btn.setText("Copied ✓")
            # Bound to the button, so closing the wizard inside those two
            # seconds cannot fire it at a deleted widget.
            QTimer.singleShot(2000, self._copy_btn,
                              lambda: self._copy_btn.setText("Copy"))

    def _on_verify(self) -> None:
        """Check the remote now accepts the key, without freezing the window.

        The probe is an ssh round trip with a 10-second timeout; run inline it
        held the GUI thread for all of it. The two status colours are theme
        tokens: written as hex inside these f-strings they were invisible to
        ``tests/test_hardcoded_tokens.py``, whose scan matched a whole string
        literal, so they could not follow a theme and nothing said so.
        """
        if not probe_ssh_async(self, self._ssh_alias, str(self._key_path),
                               self._on_verified):
            return          # one already in flight; it owns the button
        self._verify_btn.setEnabled(False)
        self._verify_status_lbl.setText("Verifying…")

    def _on_verified(self, ok: bool, detail: str) -> None:
        self._verify_btn.setEnabled(True)
        if ok:
            self._verify_status_lbl.setText(
                f"<span style='color: {theme.Colors.success};'>✓ Key accepted by "
                f"{self._ssh_alias}. AllWell will use this key going forward.</span>"
            )
            self.installed_key_path = self._key_path
            # Replace the Close button with OK so the user can commit
            # the path back to RemoteFormDialog.
            self._buttons.clear()
            ok_btn = self._buttons.addButton(QDialogButtonBox.Ok)
            ok_btn.clicked.connect(self.accept)
            ok_btn.setDefault(True)
        else:
            self._verify_status_lbl.setText(
                f"<span style='color: {theme.Colors.danger};'>✗ {detail}</span><br>"
                "<small>The key may not be installed yet. Make sure you "
                "ran the Step 2 command and the remote accepted your "
                "password. Then click Verify again.</small>"
            )

    # ── geometry persistence ──────────────────────────────────────────────

    def _settings(self) -> QSettings:
        from core.app_settings import app_settings
        return app_settings()

    def closeEvent(self, event) -> None:  # Qt override
        self._settings().setValue(
            "dialog/ssh_key_wizard_geometry", self.saveGeometry(),
        )
        super().closeEvent(event)


# ────────────────────────────────────────────────────────────────────────────
# BootstrapLogDialog — live stdout from services/remote_bootstrap.py
# ────────────────────────────────────────────────────────────────────────────


class BootstrapLogDialog(QDialog):
    """Streams bootstrap output line by line and lets the user cancel.

    Reuses the v5.0.0 PipelineRunner queue+QTimer pattern. Spawns
    ``python services/remote_bootstrap.py <alias>`` in its own session so
    Cancel sends SIGTERM to the whole group (via the shared
    ``services.subprocess_kill.signal_subprocess`` helper).
    """

    # Same event vocabulary as PipelineRunner so the polling loop reads
    # cleanly. Future refactor could share the queue class outright.
    finished_ok = Signal(int)  # exit code; 0 = success

    def __init__(self, parent: QWidget | None, alias: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Bootstrap {alias}")
        self.setModal(True)
        self.resize(720, 480)

        self._alias = alias
        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._log_q: queue.Queue = queue.Queue(maxsize=10000)
        self._started_at: float | None = None
        self._exit_code: int | None = None
        self._finished_emitted = False
        # Lines the reader could not enqueue, and how many of those the log has
        # already been told about. One writer each — the reader thread owns the
        # first, the GUI thread the second — so neither needs a lock.
        self._dropped = 0
        self._dropped_reported = 0

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
        # Both bound to this dialog, so neither survives it: the dialog is
        # destroyed when it closes, and a free-standing timer chain would go on
        # calling into a deleted C++ object. _poll re-arms itself through
        # ``self._poll_timer`` for the same reason.
        QTimer.singleShot(0, self, self._spawn)
        self._poll_timer = QTimer(self)
        self._poll_timer.setSingleShot(True)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(_POLL_IDLE_MS)

    # ── lifecycle ────────────────────────────────────────────────────────

    def _spawn(self) -> None:
        from services.input_resolution_service import build_bootstrap_cmd
        # The repository root is asked for, never derived from this module's own
        # location: ``__file__.parent`` was the root only while this file sat at
        # the top level, and answered ``analyze/`` the moment it moved into a
        # package — pointing the bootstrap at a path that has never existed.
        # ``remote_runner.repo_root`` is the one definition of it, shared with
        # the bootstrap script's own helpers.
        from services.remote_runner import repo_root

        bootstrap = repo_root() / "services" / "remote_bootstrap.py"
        # Source runs invoke the interpreter on the ``.py`` path, so it must
        # exist; the frozen build re-execs the .app binary via the
        # ``--run-bootstrap`` sentinel and never touches this path.
        if not getattr(sys, "frozen", False) and not bootstrap.is_file():
            self._append(f"[error] bootstrap script not found at {bootstrap}\n")
            self._mark_finished(2)
            return
        cmd = build_bootstrap_cmd(bootstrap, self._alias)
        self._append(f"$ {' '.join(cmd)}\n")
        self._started_at = time.monotonic()
        kwargs: dict = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            # The bootstrap reconfigures its own streams to UTF-8 before its
            # first message, and the remote paths and ✓ / → markers it echoes
            # are UTF-8 throughout. Decoding this pipe with the client's locale
            # would mojibake those on a cp1252 Windows box and raise
            # UnicodeDecodeError out of the reader thread on any byte the
            # codepage has no character for.
            **TEXT_UTF8,
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
                        # Counted, and reported in the log by _poll. Dropping
                        # silently leaves unmarked holes mid-log, which is not
                        # what setMaximumBlockCount does — that trims the head,
                        # and the reader can tell you it did.
                        self._dropped += 1
                self._proc.wait()
                self._log_q.put_nowait(("finished", self._proc.returncode))
            except Exception as exc:  # surface to dialog
                self._log_q.put_nowait(("error", str(exc)))

        self._reader_thread = threading.Thread(target=_reader, daemon=True)
        self._reader_thread.start()

    def _poll(self) -> None:
        """Drain what the bootstrap has said, in bounded slices.

        The twin of ``AnalyzeTab._poll_log`` and, until now, the version that
        never got its fix: this loop ran until the queue reported empty, which
        during a TensorFlow install's output bursts it does not do for seconds
        at a time. While it spins the dialog answers nothing — including Cancel,
        the one control a user needs when a bootstrap is wedged.

        A slice ends on ``_POLL_BUDGET_S`` or ``_POLL_MAX_LINES``, whichever
        comes first, and re-arms immediately when there is more, so throughput
        is unchanged and the event loop gets a turn in between.
        """
        deadline = time.monotonic() + _POLL_BUDGET_S
        drained = 0
        more = True
        try:
            while drained < _POLL_MAX_LINES and time.monotonic() < deadline:
                kind, payload = self._log_q.get_nowait()
                drained += 1
                if kind == "line":
                    self._append(payload)
                elif kind == "error":
                    self._append(f"[error] {payload}\n")
                elif kind == "finished":
                    self._mark_finished(int(payload))
        except queue.Empty:
            more = False
        if self._dropped > self._dropped_reported:
            lost = self._dropped - self._dropped_reported
            self._dropped_reported = self._dropped
            self._append(
                f"[warn] {lost} bootstrap log line(s) dropped — the reader "
                "outran this window. The install itself is unaffected.\n")
        if self._exit_code is None:
            self._poll_timer.start(0 if more else _POLL_IDLE_MS)

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
        from core.app_settings import app_settings
        return app_settings()

    def _restore_geometry(self) -> None:
        geom = self._settings().value("dialog/bootstrap_log_geometry")
        if geom is not None:
            self.restoreGeometry(geom)

    def closeEvent(self, event) -> None:  # Qt override
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

    def __init__(self, parent: QWidget | None = None) -> None:
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

    def _load_remotes_or_report(self) -> dict | None:
        """``load_remotes()``, or ``None`` after telling the user why not.

        ``None`` rather than ``{}`` so a caller can tell "the file says there
        are no remotes" from "the file could not be read". ``_on_edit`` reading
        an empty dict would go on to report the selected host as *deleted from
        the file*, which is a different and untrue thing to say about a file
        nothing could parse.

        Every entry point into this dialog reads that file, and each read is
        reached from a ``clicked`` slot. ``_reload`` caught ``ValueError`` and
        not ``OSError``; ``_on_edit`` and ``_on_test`` caught neither. Under
        PySide6 6.7.3 an exception escaping a slot prints a traceback and lets
        the event loop carry on, so a hand-edited ``remotes.toml`` did not crash
        the app — it made Edit…, Test and Manage… buttons that do nothing at
        all, with the only evidence in a boot log nobody reads. ``analyze.tab``
        has caught the pair around this same call for as long as the combo has
        existed; this carries that knowledge across.
        """
        try:
            return load_remotes()
        except (ValueError, OSError) as exc:
            QMessageBox.critical(
                self, "remotes.toml error",
                f"Could not read your remotes.toml:\n{exc}\n\n"
                "Fix the file (or move it aside) and reopen this dialog. "
                "Until then AllWell has no configured remotes to offer.",
            )
            return None

    def _reload(self) -> None:
        prev = self._selected_name()
        self._list.clear()
        remotes = self._load_remotes_or_report() or {}
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

    def _selected_name(self) -> str | None:
        items = self._list.selectedItems()
        if not items:
            return None
        return items[0].data(Qt.UserRole)

    def _refresh_button_state(self) -> None:
        has_sel = self._selected_name() is not None
        self._edit_btn.setEnabled(has_sel)
        self._delete_btn.setEnabled(has_sel)
        # Test is the one button this can be asked to re-enable while it is in
        # use: the probe runs off the GUI thread, and a selection change is the
        # commonest thing a user does while waiting for one.
        self._test_btn.setEnabled(has_sel and not probe_in_flight(self))
        self._bootstrap_btn.setEnabled(has_sel)

    # ── actions ─────────────────────────────────────────────────────────

    def _existing_names(self) -> set[str]:
        return {
            self._list.item(i).data(Qt.UserRole)
            for i in range(self._list.count())
        }

    def _open_form(self, dlg: RemoteFormDialog) -> None:
        """Run one add/edit form to completion and dispose of it.

        Every dialog opened from here used to stay parented to this one for the
        life of the window, holding its own children with it — five opens
        measured five live dialogs.
        """
        try:
            accepted = dlg.exec() == QDialog.Accepted
        finally:
            dlg.deleteLater()
        if accepted:
            self._reload()
            self.remotes_changed.emit()

    def _on_add(self) -> None:
        self._open_form(RemoteFormDialog(
            self, host=None, existing_names=self._existing_names(),
        ))

    def _on_edit(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        remotes = self._load_remotes_or_report()
        if remotes is None:
            return                      # already reported; the file is unreadable
        host = remotes.get(name)
        if host is None:
            QMessageBox.warning(self, "Gone",
                f"Remote '{name}' has been deleted from the file.")
            self._reload()
            return
        self._open_form(RemoteFormDialog(self, host=host))

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
        """Probe the selected host, off the GUI thread.

        Routed through ``ssh_key_setup.probe_ssh_connection`` (via
        :func:`probe_ssh_async`) so a configured ``ssh_key_path`` is passed as
        ``-i <key>``: without it, a host whose only accepted credential is the
        wizard-generated ``~/.ssh/allwell_<alias>`` key reports "Permission
        denied" here even after the wizard has verified it.

        Called inline, this held the whole application — every window, and a
        running pipeline's log pane — for the probe's 15-second timeout
        whenever the host was unreachable. Its twin one class up has run on a
        worker thread since it was written; both now share that one helper.
        """
        name = self._selected_name()
        if name is None:
            return
        remotes = self._load_remotes_or_report()
        if remotes is None:
            return                      # already reported; the file is unreadable
        host = remotes.get(name)
        if host is None:
            QMessageBox.warning(self, "Gone",
                f"Remote '{name}' has been deleted from the file.")
            self._reload()
            return
        def _done(ok: bool, detail: str) -> None:
            self._test_btn.setText("Test")
            self._refresh_button_state()
            if ok:
                extras = (
                    f" (using key {Path(host.ssh_key_path).name})"
                    if host.ssh_key_path else ""
                )
                QMessageBox.information(
                    self, f"Test passed: {name}",
                    f"{host.ssh_alias} responded{extras}.",
                )
            else:
                QMessageBox.critical(
                    self, f"Test failed: {name}",
                    detail or "(ssh returned non-zero with no message)",
                )

        if not probe_ssh_async(self, host.ssh_alias, host.ssh_key_path, _done):
            return          # one already in flight; it owns the button
        self._test_btn.setEnabled(False)
        self._test_btn.setText("Testing…")

    def _on_bootstrap(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        dlg = BootstrapLogDialog(self, alias=name)

        def _on_finish(code: int) -> None:
            self._reload()
            self.remotes_changed.emit()

        dlg.finished_ok.connect(_on_finish)
        try:
            dlg.exec()
        finally:
            dlg.deleteLater()

    # ── geometry persistence ──────────────────────────────────────────────

    def _settings(self) -> QSettings:
        from core.app_settings import app_settings
        return app_settings()

    def _restore_geometry(self) -> None:
        geom = self._settings().value("dialog/manage_remotes_geometry")
        if geom is not None:
            self.restoreGeometry(geom)

    def closeEvent(self, event) -> None:  # Qt override
        self._settings().setValue(
            "dialog/manage_remotes_geometry", self.saveGeometry(),
        )
        super().closeEvent(event)
