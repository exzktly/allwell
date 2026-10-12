"""A remembered folder on a share that is not answering must not hold the window.

The recent-datasets rows say whether each folder can still be opened, and the
only way to know is a ``stat``. Under a network share whose server has gone away
macOS blocks that ``stat`` for tens of seconds, and the rows were built on the
GUI thread three times while the window was being constructed — the rail head's
dataset menu, Home's card and Home's placeholder, nineteen ``stat`` calls for a
full history — so one dead share held the launch for many minutes with nothing
on screen.

The surface tests here block ``os.stat`` itself for one fake share, rather than
stubbing the probe, so they measure what the shipped code does whichever way it
asks: the synchronous ``Path.is_dir`` it replaced reaches ``os.stat`` too, and
against it the menu test waits out every blocked call and fails on its clock.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import errno  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from review.core.folder_reachability import ReachabilityProbe  # noqa: E402

#: How long a blocked ``stat`` waits for the test to release it before it gives
#: up and reports the folder missing. Bounds the damage when the code under test
#: does wait on it — which is the failure these tests exist to catch — so a
#: regression fails on its clock instead of hanging the lane.
_BLOCK_S = 2.0


class _Gate:
    """An ``isdir`` that blocks for chosen paths until released, and counts."""

    def __init__(self, blocked=(), present=()):
        self.blocked = set(blocked)
        self.present = set(present)
        self.release = threading.Event()
        self.calls: list[str] = []

    def __call__(self, path):
        self.calls.append(path)
        if path in self.blocked:
            self.release.wait(_BLOCK_S)
            return False
        return path in self.present


# ── the probe ────────────────────────────────────────────────────────────────

def test_a_folder_that_answers_at_once_is_decided_at_once():
    """The common case must look exactly as it did when the check was inline:
    a definite answer on the first call, and no repaint after it."""
    gate = _Gate(present={"/here"})
    probe = ReachabilityProbe(gate)
    heard = []
    probe.set_listener(lambda: heard.append(1))

    assert probe.check(["/here", "/gone"]) == [True, False]
    time.sleep(0.05)
    assert heard == [], "an answer given inside the budget still asked for a repaint"


def test_a_folder_that_does_not_answer_costs_the_caller_only_the_budget():
    gate = _Gate(blocked={"/dead/a", "/dead/b", "/dead/c"})
    probe = ReachabilityProbe(gate)
    try:
        started = time.monotonic()
        answers = probe.check(["/dead/a", "/dead/b", "/dead/c"], budget_s=0.1)
        elapsed = time.monotonic() - started
    finally:
        gate.release.set()

    assert answers == [None, None, None], (
        "a folder that has not answered was reported as decided")
    # The budget is shared, not per folder: three blocked folders cost one
    # budget, not three.
    assert elapsed < 0.5, f"the caller waited {elapsed:.2f}s on a blocked stat"


def test_a_late_answer_is_announced_once_and_then_kept():
    """A late answer repaints, and the repaint must not start the cycle again.

    Without the cool-down, the repaint's own ``check`` re-asks the dead share,
    draws the row as being checked, and the next late answer repaints again —
    for as long as the share stays down.
    """
    gate = _Gate(blocked={"/dead"})
    probe = ReachabilityProbe(gate)
    heard = threading.Event()
    count = []
    probe.set_listener(lambda: (count.append(1), heard.set()))

    assert probe.check(["/dead"], budget_s=0.05) == [None]
    gate.release.set()
    assert heard.wait(2.0), "the late answer was never announced"

    assert probe.check(["/dead"], budget_s=0.05) == [False]
    time.sleep(0.05)
    assert gate.calls == ["/dead"], (
        "the repaint re-checked a folder that had just answered late")
    assert count == [1]


def test_a_folder_still_being_checked_is_not_checked_again():
    """One blocked thread per folder, never one per repaint."""
    gate = _Gate(blocked={"/dead"})
    probe = ReachabilityProbe(gate)
    try:
        for _ in range(5):
            probe.check(["/dead"], budget_s=0.01)
    finally:
        gate.release.set()
    assert gate.calls == ["/dead"]


def test_a_late_folder_is_checked_again_once_the_cool_down_has_passed():
    """The share coming back must bring its row back without a restart."""
    gate = _Gate(blocked={"/share"})
    probe = ReachabilityProbe(gate, slow_recheck_s=0.0)
    heard = threading.Event()
    probe.set_listener(heard.set)

    probe.check(["/share"], budget_s=0.01)
    gate.release.set()
    assert heard.wait(2.0)

    gate.blocked.clear()
    gate.present.add("/share")
    assert probe.check(["/share"]) == [True]
    assert gate.calls == ["/share", "/share"]


def test_an_old_answer_is_not_offered_while_its_recheck_hangs():
    """A share that answered earlier and is not answering now is exactly the
    folder a click must not be offered on: the load would block on the same
    ``stat``. So a hanging re-check reads as *not known*, not as the old yes."""
    gate = _Gate(present={"/share"})
    probe = ReachabilityProbe(gate)
    assert probe.check(["/share"]) == [True]

    gate.blocked.add("/share")
    try:
        assert probe.check(["/share"], budget_s=0.05) == [None]
    finally:
        gate.release.set()


# ── the surfaces ─────────────────────────────────────────────────────────────

@pytest.fixture
def dead_share(tmp_path, monkeypatch):
    """A folder prefix whose every ``stat`` blocks, as a vanished SMB server's does.

    Patched at ``os.stat``, below both ``Path.is_dir`` and ``os.path.isdir``, so
    the test measures the shipped code's behaviour rather than a seam of it.
    """
    share = tmp_path / "Volumes" / "LabShare"
    prefix = str(share)
    release = threading.Event()
    real_stat = os.stat

    def stat(path, *args, **kwargs):
        text = os.fsdecode(path) if not isinstance(path, int) else ""
        if text.startswith(prefix):
            release.wait(_BLOCK_S)
            # With its errno, as the OS raises it: ``Path.is_dir`` swallows
            # only the errnos it recognises, and a bare FileNotFoundError(text)
            # carries none, so it would escape on the first call and the menu
            # would stop after one blocked stat instead of waiting out all six.
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), text)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stat)
    yield _Share(share, release)
    release.set()


class _Share:
    """The blocked prefix, and the switch that lets its ``stat`` calls return."""

    def __init__(self, path: Path, release: threading.Event) -> None:
        self.path = path
        self.release = release

    def __truediv__(self, name: str) -> Path:
        return self.path / name


def test_a_dead_share_does_not_hold_up_the_dataset_menu(
        qtbot, dead_share, monkeypatch):
    """Through ``_fill_dataset_menu``, which the shell runs while it is still
    constructing the window — before anything is on screen. Six remembered
    plates on a dead share held it for six blocked ``stat`` calls."""
    from PySide6.QtWidgets import QMenu

    from review.core.app_shell_view import RECENTS_IN_MENU, _fill_dataset_menu

    plates = [dead_share / f"plate{i}" for i in range(RECENTS_IN_MENU)]
    win = _menu_window(monkeypatch, plates)
    menu = QMenu()

    started = time.monotonic()
    _fill_dataset_menu(win, menu)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, (
        f"building the menu waited {elapsed:.1f}s on a share that is not answering")
    rows = [a for a in menu.actions() if a.text().startswith("plate")]
    assert len(rows) == RECENTS_IN_MENU, "a plate on the dead share was dropped"
    assert not any(a.isEnabled() for a in rows), (
        "a plate on a share that has not answered was offered as openable")


def test_a_row_still_being_checked_says_so(dead_share, monkeypatch):
    """Not 'unreachable': the share may simply be slow, and Home's card counts
    the unreachable rows into a sentence telling the user a drive is missing."""
    from review.core.recent_view import (
        CHECKING_NOTE, UNREACHABLE_NOTE, entry_rows, row_is_unreachable,
    )

    ctx = _ctx_with_recents(monkeypatch, [dead_share / "plate0"])
    (row,) = entry_rows(ctx, 5)

    assert row[3] is False and row[2] is None, "a row not yet checked is clickable"
    assert CHECKING_NOTE in row[4] and UNREACHABLE_NOTE not in row[4]
    assert row_is_unreachable(row) is False


def test_a_late_answer_repaints_the_surface_that_drew_it(
        qtbot, dead_share, monkeypatch):
    """The answer arrives on a worker thread and the repaint must run on the
    GUI thread — a worker that touches a widget is a crash with no traceback.
    Then the repainted row says why it is disabled."""
    from review.core.recent_view import UNREACHABLE_NOTE, entry_rows

    ctx = _ctx_with_recents(monkeypatch, [dead_share / "plate0"])
    threads = []
    entry_rows(ctx, 5, on_changed=lambda: threads.append(threading.current_thread()))
    assert threads == []

    dead_share.release.set()
    qtbot.waitUntil(lambda: bool(threads), timeout=3000)
    assert threads == [threading.main_thread()], (
        "the repaint ran off the GUI thread")

    (row,) = entry_rows(ctx, 5)
    assert UNREACHABLE_NOTE in row[4]


# ── helpers ──────────────────────────────────────────────────────────────────

class _Settings:
    """``QSettings``' two methods over a dict, so no test writes to the machine."""

    def __init__(self):
        self._d = {}

    def value(self, key, default=None):
        return self._d.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's spelling
        self._d[key] = value


def _settings_with(monkeypatch, paths):
    """Stored directly rather than through ``record_open``, which resolves each
    path — a ``stat`` the dead share would block, in the test's own setup."""
    from core import app_settings as app_settings_module
    from review.core.recent_datasets import RecentDataset, store

    settings = _Settings()
    store(settings, [RecentDataset(str(path), Path(path).name, float(i))
                     for i, path in enumerate(paths)])
    monkeypatch.setattr(app_settings_module, "app_settings", lambda: settings)
    return settings


def _ctx_with_recents(monkeypatch, paths):
    from types import SimpleNamespace

    _settings_with(monkeypatch, paths)
    return SimpleNamespace(
        window=SimpleNamespace(_load_path=lambda _p: None),
        state=SimpleNamespace(dataset=SimpleNamespace(data_dir=None)),
    )


def _menu_window(monkeypatch, paths):
    from types import SimpleNamespace

    from review.core.app_state import AppState

    _settings_with(monkeypatch, paths)
    state = AppState()
    return SimpleNamespace(
        _app_state=state,
        _browse=lambda: None,
        _ctx=SimpleNamespace(window=SimpleNamespace(_load_path=lambda _p: None),
                             state=state),
    )
