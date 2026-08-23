"""Audit U4, U5, U9, U10, U11, U14, U15, U16 — the Manage Remotes dialogs.

Eight defects that shipped together in ``analyze_remote_dialog.py``, all of them
in code reached from a ``clicked`` slot and none of them visible from a test
that only asks whether a control exists:

* **U4** — ``ManageRemotesDialog._on_test`` called ``probe_ssh_connection``
  inline on the GUI thread. A remote behind a VPN the user is not on froze the
  whole application for the probe's 15-second timeout, log pane and all. Its
  twin one class up has used a worker thread since it was written.
* **U5** — ``_reload`` caught ``ValueError`` and not ``OSError``; ``_on_edit``
  and ``_on_test`` called ``load_remotes()`` bare. Under PySide6 6.7.3 an
  exception escaping a slot prints a traceback and lets the event loop carry on,
  so a hand-edited ``remotes.toml`` made Edit…, Test and Manage… buttons that
  did nothing at all.
* **U9** — two ``except OSError: pass`` handlers around the unlinks that clear
  the way for a replacement key. If either failed, ``generate_keypair``'s
  idempotency check found the pair still there and did nothing, and the wizard
  reported "✓ Key already present — reusing" over the key the user had just
  clicked Yes to replace.
* **U10** — ``BootstrapLogDialog._poll`` drained until the queue was empty,
  which during a TensorFlow install's output bursts it is not for seconds at a
  time; Cancel, the one control that matters then, was unclickable. A full queue
  dropped lines with a bare ``pass``.
* **U11** — no dialog was ever destroyed. Five opens left five live dialogs
  parented to the tab, each holding its connections and its own children.
* **U14** — ``result_host`` re-spelled three ``RemoteHost`` defaults as string
  literals, 130 lines below the placeholders that read the same values off the
  dataclass.
* **U15** — "On a node it schedules" with an empty scheduler command saved as
  ``compute_launcher=""``, which means *run on this machine*.
* **U16** — the Test SSH success line re-read the alias field at poll time.

Every test drives the control a user drives.
"""
from __future__ import annotations

import queue
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QDialog, QMessageBox     # noqa: E402


def _Host(name="hpc", ssh_alias="hpc.example", ssh_key_path=""):
    """A real ``RemoteHost`` — the edit form reads a dozen of its fields, so a
    stand-in object would only test the stand-in."""
    from services.remote_config import RemoteHost

    return RemoteHost(name=name, ssh_alias=ssh_alias, ssh_key_path=ssh_key_path)


@pytest.fixture
def boxes(monkeypatch):
    """Record every message box instead of opening one (they are modal)."""
    seen: list[tuple[str, str, str]] = []
    for kind in ("critical", "warning", "information", "question"):
        monkeypatch.setattr(
            QMessageBox, kind,
            lambda _p, title, text, *a, _k=kind, **kw: (
                seen.append((_k, title, text)) or QMessageBox.Yes),
        )
    return seen


def _pump(qtbot, predicate, timeout=3.0):
    """Spin the event loop until *predicate* holds, or fail."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        qtbot.wait(20)
    raise AssertionError("timed out waiting for the polled result")


# ── U5: a malformed remotes.toml leaves working buttons ─────────────────────

@pytest.mark.parametrize("raised", [
    ValueError("Expected '=' after a key in a key/value pair (line 3)"),
    PermissionError(13, "Permission denied"),
])
def test_manage_remotes_opens_when_remotes_toml_cannot_be_read(qtbot, boxes, monkeypatch, raised):
    import analyze_remote_dialog as ard

    def _boom():
        raise raised

    monkeypatch.setattr(ard, "load_remotes", _boom)

    escaped: list[BaseException] = []
    try:
        dlg = ard.ManageRemotesDialog()
    except BaseException as exc:       # noqa: BLE001 — the thing under test
        escaped.append(exc)
    assert not escaped, (
        f"opening Manage Remotes raised {escaped[0]!r} out of a clicked slot, "
        "which under PySide6 6.7.3 is a button that silently does nothing")
    qtbot.addWidget(dlg)
    assert any(kind == "critical" for kind, _t, _x in boxes), (
        "the user was told nothing about the file that could not be read")


@pytest.mark.parametrize("action", ["_on_edit", "_on_test"])
def test_edit_and_test_report_a_broken_remotes_toml(qtbot, boxes, monkeypatch, action):
    import analyze_remote_dialog as ard

    state = {"broken": False}

    def _load():
        if state["broken"]:
            raise ValueError("Expected '=' after a key in a key/value pair (line 3)")
        return {"hpc": _Host()}

    monkeypatch.setattr(ard, "load_remotes", _load)
    dlg = ard.ManageRemotesDialog()
    qtbot.addWidget(dlg)
    dlg._list.setCurrentRow(0)
    assert dlg._selected_name() == "hpc"

    state["broken"] = True              # the user hand-edits the file
    boxes.clear()
    escaped: list[BaseException] = []
    try:
        getattr(dlg, "_edit_btn" if action == "_on_edit" else "_test_btn").click()
    except BaseException as exc:        # noqa: BLE001 — the thing under test
        escaped.append(exc)
    assert not escaped, (
        f"{action} raised {escaped[0]!r}, so the button did nothing at all")
    assert any(kind == "critical" for kind, _t, _x in boxes), (
        f"{action} failed silently — no message named the unreadable file")


# ── U4: the Test button does not hold the GUI thread ────────────────────────

def test_the_manage_remotes_test_button_does_not_block_the_gui(qtbot, boxes, monkeypatch):
    import analyze_remote_dialog as ard
    from services import ssh_key_setup

    monkeypatch.setattr(ard, "load_remotes", lambda: {"hpc": _Host()})

    probe_seconds = 0.5                 # the real timeout is 15

    def _slow_probe(_alias, _key, timeout=15):
        time.sleep(probe_seconds)
        return True, ""

    monkeypatch.setattr(ssh_key_setup, "probe_ssh_connection", _slow_probe)

    dlg = ard.ManageRemotesDialog()
    qtbot.addWidget(dlg)
    dlg._list.setCurrentRow(0)

    started = time.monotonic()
    dlg._test_btn.click()
    held = time.monotonic() - started
    assert held < probe_seconds / 2, (
        f"the Test handler held the GUI thread for {held:.3f}s of a "
        f"{probe_seconds:.1f}s probe; the real probe waits up to 15s and the "
        "whole application, including a running pipeline's log pane, is frozen "
        "for all of it")

    _pump(qtbot, lambda: any(k == "information" for k, _t, _x in boxes))
    assert dlg._test_btn.isEnabled(), "the Test button was left disabled"
    assert dlg._test_btn.text() == "Test"


def test_a_failing_probe_still_re_enables_the_test_button(qtbot, boxes, monkeypatch):
    import analyze_remote_dialog as ard
    from services import ssh_key_setup

    monkeypatch.setattr(ard, "load_remotes", lambda: {"hpc": _Host()})
    monkeypatch.setattr(ssh_key_setup, "probe_ssh_connection",
                        lambda *_a, **_k: (False, "ssh: connect: no route to host"))
    dlg = ard.ManageRemotesDialog()
    qtbot.addWidget(dlg)
    dlg._list.setCurrentRow(0)
    dlg._test_btn.click()
    _pump(qtbot, lambda: any(k == "critical" for k, _t, _x in boxes))
    assert dlg._test_btn.isEnabled()


# ── U16: the success line names the host that was contacted ─────────────────

def test_the_form_test_result_names_the_host_it_actually_probed(qtbot, monkeypatch):
    import analyze_remote_dialog as ard
    from services import ssh_key_setup

    release = queue.Queue()

    def _held_probe(alias, _key, timeout=15):
        release.get(timeout=5)          # stay in flight until the test says so
        return True, ""

    monkeypatch.setattr(ssh_key_setup, "probe_ssh_connection", _held_probe)

    dlg = ard.RemoteFormDialog(None, host=None)
    qtbot.addWidget(dlg)
    dlg._name_edit.setText("hpc")
    dlg._ssh_alias_edit.setText("old-alias")
    dlg._test_btn.click()

    dlg._ssh_alias_edit.setText("new-alias")    # the user keeps typing
    release.put(None)
    _pump(qtbot, lambda: dlg._test_result_lbl.text().startswith("✓"))

    assert "old-alias" in dlg._test_result_lbl.text(), (
        f"the dialog reported {dlg._test_result_lbl.text()!r} about a host it "
        "never contacted")
    assert "new-alias" not in dlg._test_result_lbl.text()


# ── U14 / U15: the add/edit form ────────────────────────────────────────────

def test_result_host_reads_the_dataclass_defaults(qtbot):
    import analyze_remote_dialog as ard
    from services.remote_config import RemoteHost

    dlg = ard.RemoteFormDialog(None, host=None)
    qtbot.addWidget(dlg)
    dlg._name_edit.setText("hpc")
    dlg._ssh_alias_edit.setText("hpc.example")
    built = dlg.result_host()
    reference = RemoteHost(name="hpc", ssh_alias="hpc.example")
    for field in ("remote_project_dir", "remote_staging_dir", "remote_python"):
        assert getattr(built, field) == getattr(reference, field)


def test_the_form_spells_no_remote_host_default_as_a_literal():
    """Equality above passes whether or not the values are copied, so this asks
    the syntax instead: ``result_host`` must not restate a default the dataclass
    already defines. They diverge the day someone changes ``RemoteHost`` — the
    placeholder beside each field is read off the dataclass, so the hint would
    move while the saved value kept the old path."""
    import ast
    import pathlib

    from services.remote_config import RemoteHost

    reference = RemoteHost(name="_", ssh_alias="_")
    defaults = {
        getattr(reference, f)
        for f in ("remote_project_dir", "remote_staging_dir", "remote_python")
    }
    src = pathlib.Path(
        pathlib.Path(__file__).resolve().parent.parent / "analyze_remote_dialog.py"
    ).read_text()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "result_host")
    offenders = [
        f"line {n.lineno}: {n.value!r}"
        for n in ast.walk(fn)
        if isinstance(n, ast.Constant) and n.value in defaults
    ]
    assert not offenders, (
        "result_host() re-spells RemoteHost defaults as string literals:\n  "
        + "\n  ".join(offenders))


def test_a_scheduled_host_with_no_launcher_is_refused(qtbot, boxes, monkeypatch):
    import analyze_remote_dialog as ard

    written: list = []
    monkeypatch.setattr(ard, "write_remote", lambda host: written.append(host))

    dlg = ard.RemoteFormDialog(None, host=None)
    qtbot.addWidget(dlg)
    dlg._name_edit.setText("lobby")
    dlg._ssh_alias_edit.setText("lobby.example")
    dlg._compute_loc_combo.setCurrentIndex(1)        # "On a node it schedules"
    assert dlg._compute_launcher_edit.text().strip(), "the seeded srun should be there"
    dlg._compute_launcher_edit.setText("")           # the user clears it to paste their own
    dlg._on_ok()

    assert not written, (
        "a scheduled host saved with an empty scheduler command, which means "
        f"'run on the SSH host itself': {written}")
    assert any(kind == "critical" for kind, _t, _x in boxes), (
        "nothing told the user why the form would not save")


def test_a_scheduled_host_with_a_launcher_still_saves(qtbot, boxes, monkeypatch):
    """The refusal must not be a blanket one."""
    import analyze_remote_dialog as ard

    written: list = []
    monkeypatch.setattr(ard, "write_remote", lambda host: written.append(host))
    dlg = ard.RemoteFormDialog(None, host=None)
    qtbot.addWidget(dlg)
    dlg._name_edit.setText("lobby")
    dlg._ssh_alias_edit.setText("lobby.example")
    dlg._compute_loc_combo.setCurrentIndex(1)
    dlg._on_ok()
    assert len(written) == 1
    assert written[0].compute_launcher.startswith("srun")


def test_a_local_compute_host_is_not_asked_for_a_launcher(qtbot, boxes, monkeypatch):
    import analyze_remote_dialog as ard

    written: list = []
    monkeypatch.setattr(ard, "write_remote", lambda host: written.append(host))
    dlg = ard.RemoteFormDialog(None, host=None)
    qtbot.addWidget(dlg)
    dlg._name_edit.setText("box")
    dlg._ssh_alias_edit.setText("box.example")
    dlg._on_ok()
    assert len(written) == 1 and written[0].compute_launcher == ""


# ── U9: a replacement that could not delete the old key is not a reuse ──────

def test_a_key_replace_that_cannot_unlink_says_so(qtbot, boxes, monkeypatch):
    import analyze_remote_dialog as ard
    from services import ssh_key_setup

    key = ard.Path("/tmp/definitely-not-there/allwell_hpc")
    monkeypatch.setattr(ssh_key_setup, "default_key_path", lambda _a: key)
    monkeypatch.setattr(ard.Path, "is_file", lambda _self: True)
    monkeypatch.setattr(ssh_key_setup, "key_has_passphrase", lambda _p: True)

    def _refuse(_self):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(ard.Path, "unlink", _refuse)
    # The idempotency short-circuit the stale key would trigger.
    generated: list = []
    monkeypatch.setattr(ssh_key_setup, "generate_keypair",
                        lambda p, **kw: (generated.append(p), (False, ""))[1])

    dlg = ard.SSHKeyWizardDialog(None, ssh_alias="hpc")
    qtbot.addWidget(dlg)
    dlg._generate_btn.click()           # QMessageBox.question is stubbed to Yes

    status = dlg._generate_status_lbl.text()
    assert "reusing" not in status.lower(), (
        f"the wizard reported {status!r} over a key it failed to replace")
    assert "Permission denied" in status, (
        f"the wizard did not say why the replacement failed: {status!r}")
    assert not generated, "generate_keypair was called over a key still in place"
    assert not dlg._verify_btn.isEnabled(), (
        "Step 2/3 were unlocked for a key the wizard could not replace")


def test_a_replace_that_generated_nothing_is_not_called_a_reuse(qtbot, boxes, monkeypatch, tmp_path):
    """``generate_keypair`` answering "no work done" straight after both halves
    were deleted cannot mean what the word "reusing" means. Whatever it does
    mean, the user does not have the fresh key they asked for."""
    import analyze_remote_dialog as ard
    from services import ssh_key_setup

    key = tmp_path / "allwell_hpc"
    key.write_text("old private key")
    monkeypatch.setattr(ssh_key_setup, "default_key_path", lambda _a: key)
    monkeypatch.setattr(ssh_key_setup, "key_has_passphrase", lambda _p: True)
    monkeypatch.setattr(ssh_key_setup, "generate_keypair", lambda p, **kw: (False, ""))

    dlg = ard.SSHKeyWizardDialog(None, ssh_alias="hpc")
    qtbot.addWidget(dlg)
    dlg._generate_btn.click()

    status = dlg._generate_status_lbl.text()
    assert "reusing" not in status.lower(), status
    assert not dlg._verify_btn.isEnabled()


def test_a_key_replace_that_unlinks_cleanly_generates(qtbot, boxes, monkeypatch, tmp_path):
    """The other half: with the old key gone, the wizard proceeds."""
    import analyze_remote_dialog as ard
    from services import ssh_key_setup

    key = tmp_path / "allwell_hpc"
    key.write_text("old private key")
    (tmp_path / "allwell_hpc.pub").write_text("old public key")
    monkeypatch.setattr(ssh_key_setup, "default_key_path", lambda _a: key)
    monkeypatch.setattr(ssh_key_setup, "key_has_passphrase", lambda _p: True)
    monkeypatch.setattr(ssh_key_setup, "generate_keypair", lambda p, **kw: (True, ""))
    monkeypatch.setattr(ssh_key_setup, "build_copy_command",
                        lambda alias, path: f"ssh-copy-id -i {path} {alias}")

    dlg = ard.SSHKeyWizardDialog(None, ssh_alias="hpc")
    qtbot.addWidget(dlg)
    dlg._generate_btn.click()

    assert not key.exists() and not (tmp_path / "allwell_hpc.pub").exists()
    assert "generated" in dlg._generate_status_lbl.text().lower()
    assert dlg._verify_btn.isEnabled()


# ── U10: the bootstrap log drains in bounded slices, and admits drops ───────

def _bootstrap_dialog(qtbot, monkeypatch):
    """A BootstrapLogDialog that never spawns anything.

    ``__init__`` arms ``_spawn`` on a zero-delay timer; replacing the method
    before construction keeps the subprocess out of the test entirely.
    """
    import analyze_remote_dialog as ard

    monkeypatch.setattr(ard.BootstrapLogDialog, "_spawn", lambda _self: None)
    dlg = ard.BootstrapLogDialog(None, alias="hpc")
    qtbot.addWidget(dlg)
    return dlg


def test_one_poll_slice_does_not_drain_an_unbounded_queue(qtbot, monkeypatch):
    dlg = _bootstrap_dialog(qtbot, monkeypatch)
    for i in range(9000):
        dlg._log_q.put_nowait(("line", f"line {i}\n"))

    started = time.monotonic()
    dlg._poll()
    held = time.monotonic() - started

    assert dlg._log_q.qsize() > 0, (
        "one poll slice drained 9000 queued lines in a single pass; during a "
        "TensorFlow install's output bursts that is how long Cancel stays "
        "unclickable")
    import analyze_remote_dialog as ard
    assert held < ard._POLL_BUDGET_S * 4, f"a slice held the GUI for {held:.3f}s"


def test_the_slices_still_get_through_the_whole_queue(qtbot, monkeypatch):
    """Bounded is not lossy: repeated slices deliver every line."""
    dlg = _bootstrap_dialog(qtbot, monkeypatch)
    for i in range(1500):
        dlg._log_q.put_nowait(("line", f"line {i}\n"))
    for _ in range(200):
        dlg._poll()
        if dlg._log_q.empty():
            break
    assert dlg._log_q.empty()
    text = dlg._log.toPlainText()
    assert "line 0" in text and "line 1499" in text


def test_dropped_lines_are_reported_in_the_log(qtbot, monkeypatch):
    """A full queue used to drop with a bare ``pass``, leaving unmarked holes
    mid-log — which is not what ``setMaximumBlockCount`` does; that trims the
    head, and can say so."""
    dlg = _bootstrap_dialog(qtbot, monkeypatch)
    dlg._log_q.put_nowait(("line", "before the flood\n"))
    dlg._dropped = 42                   # what the reader thread records
    dlg._poll()
    text = dlg._log.toPlainText()
    assert "42" in text and "dropped" in text, (
        f"the log gave no sign that 42 lines were lost:\n{text}")
    dlg._poll()
    assert text.count("dropped") == dlg._log.toPlainText().count("dropped"), (
        "the drop marker was repeated on every later slice")


# ── the lifetime the two fixes above create between them ───────────────────
#
# Destroying a dialog on close (U11) and answering a probe from a timer (U4)
# are safe apart and dangerous together: a free-standing ``QTimer.singleShot``
# chain outlives the widget it was started for, and its callback then touches a
# C++ object that no longer exists. Until these dialogs were destroyed, the leak
# was what hid it. Every poller in the module is parented to its dialog instead,
# so Qt takes it down with the widget.

def test_a_poller_dies_with_the_widget_that_owns_it(qtbot):
    import analyze_remote_dialog as ard
    from PySide6.QtWidgets import QWidget

    owner = QWidget()
    qtbot.addWidget(owner)
    result_q: queue.Queue = queue.Queue()
    calls: list = []
    ard.poll_queue_on(owner, result_q, lambda *a: calls.append(a))

    owner.deleteLater()
    qtbot.wait(50)                      # the deferred delete runs
    result_q.put((True, ""))            # …and only then does the probe answer
    qtbot.wait(300)

    assert not calls, (
        "the poller outlived the widget that owned it and called back into a "
        "deleted dialog")


def test_closing_the_form_mid_probe_does_not_reach_a_deleted_widget(qtbot, capfd, monkeypatch):
    """The same thing end to end, through the Test SSH button.

    PySide6 prints an exception raised inside a slot and lets the event loop
    carry on, so the failure is a line on stderr rather than a raised error —
    which is exactly why it would otherwise go unnoticed.
    """
    import analyze_remote_dialog as ard
    from services import ssh_key_setup

    release: queue.Queue = queue.Queue()
    monkeypatch.setattr(ssh_key_setup, "probe_ssh_connection",
                        lambda *_a, **_k: (release.get(timeout=5), (True, ""))[1])

    dlg = ard.RemoteFormDialog(None, host=None)
    qtbot.addWidget(dlg)
    dlg._ssh_alias_edit.setText("hpc.example")
    dlg._test_btn.click()

    dlg.close()
    dlg.deleteLater()
    qtbot.wait(50)
    capfd.readouterr()                  # ignore anything printed before this
    release.put(None)
    qtbot.wait(400)

    err = capfd.readouterr().err
    assert "already deleted" not in err and "RuntimeError" not in err, (
        f"a probe answered into a destroyed dialog:\n{err}")


# ── U11: dialogs are destroyed when they close ─────────────────────────────

def test_manage_remotes_dialogs_do_not_pile_up_on_the_tab(qtbot, monkeypatch):
    import analyze_remote_dialog as ard
    import analyze_tab_form

    monkeypatch.setattr(analyze_tab_form, "load_remotes", lambda: {})
    monkeypatch.setattr(ard, "load_remotes", lambda: {})
    monkeypatch.setattr(QDialog, "exec", lambda _self: QDialog.Rejected)

    from analyze_tab import AnalyzeTab

    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    for _ in range(5):
        tab._manage_remotes_btn.click()
    qtbot.wait(50)

    alive = tab.findChildren(ard.ManageRemotesDialog)
    assert not alive, (
        f"{len(alive)} ManageRemotesDialog(s) still parented to the tab after "
        "five opens, each holding its remotes_changed connection and every "
        "child dialog opened inside it")


@pytest.mark.parametrize("button, child", [
    ("_add_btn", "RemoteFormDialog"),
    ("_edit_btn", "RemoteFormDialog"),
    ("_bootstrap_btn", "BootstrapLogDialog"),
])
def test_the_child_dialogs_are_destroyed_too(qtbot, monkeypatch, button, child):
    import analyze_remote_dialog as ard

    monkeypatch.setattr(ard, "load_remotes", lambda: {"hpc": _Host()})
    monkeypatch.setattr(ard.BootstrapLogDialog, "_spawn", lambda _self: None)
    monkeypatch.setattr(QDialog, "exec", lambda _self: QDialog.Rejected)

    dlg = ard.ManageRemotesDialog()
    qtbot.addWidget(dlg)
    dlg._list.setCurrentRow(0)
    cls = getattr(ard, child)
    for _ in range(3):
        getattr(dlg, button).click()
    qtbot.wait(50)
    assert not dlg.findChildren(cls), f"{button} leaked its {child}"
