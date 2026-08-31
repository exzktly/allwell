"""Audit U3 — a glance at "Local" must not rewrite the remote cleanup toggles.

``_refresh_remote_controls`` did two things beyond showing and hiding: it forced
"Auto-delete remote files when done" back to *checked* whenever "Run on:" was
Local, and it cleared "Dry-run cleanup" whenever that box was disabled. Both
edits happened to a widget that was hidden at the time, and neither was undone
by selecting a remote again.

So a user on a shared cluster who ticked "Dry-run cleanup" to confirm what
auto-delete would remove, flicked to Local to check something, flicked back and
pressed Run had the per-run remote output directory and the cached input
deleted for real. The mirror case re-armed an auto-delete they had deliberately
turned off.

These drive the combo and the checkboxes the way a user does, because that is
the only way the round trip happens at all: the reset ran inside the
``currentIndexChanged`` handler.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def tab(qtbot, monkeypatch):
    import analyze.form

    monkeypatch.setattr(analyze.form, "load_remotes", lambda: {})
    from analyze.tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)
    # A remote the form can select without a remotes.toml on disk, added the
    # same way _reload_remotes_combo adds the real ones.
    t._remote_cb.addItem("bench-box", userData="bench-box")
    return t


def _pick(tab, data) -> None:
    for i in range(tab._remote_cb.count()):
        if tab._remote_cb.itemData(i) == data:
            tab._remote_cb.setCurrentIndex(i)
            return
    raise AssertionError(f"no 'Run on:' item carries {data!r}")


def test_dry_run_cleanup_survives_a_round_trip_through_local(tab):
    """The reported shape, and the one that deletes the user's remote files."""
    _pick(tab, "bench-box")
    tab._dry_run_cleanup_cb.setChecked(True)
    assert tab._dry_run_cleanup_cb.isChecked()

    _pick(tab, None)                    # "let me just check something locally"
    _pick(tab, "bench-box")

    assert tab._dry_run_cleanup_cb.isChecked(), (
        "the dry-run tick was silently cleared by a round trip through Local, "
        "so the next run would have deleted the remote output and cached input "
        "for real")
    assert tab._collect_run_spec().dry_run_cleanup is True


def test_an_unticked_auto_delete_survives_a_round_trip_through_local(tab):
    """The mirror case: a deliberate 'leave my files alone' is re-armed."""
    _pick(tab, "bench-box")
    tab._cleanup_remote_cb.setChecked(False)

    _pick(tab, None)
    _pick(tab, "bench-box")

    assert not tab._cleanup_remote_cb.isChecked(), (
        "auto-delete was re-ticked by a round trip through Local")
    assert tab._collect_run_spec().cleanup_remote == "none"


def test_dry_run_is_disabled_but_not_cleared_when_auto_delete_is_off(tab):
    """The gate stays; only the silent edit goes.

    Dry-run has nothing to describe with auto-delete off, so the box is
    unreachable — but unreachable is not the same as unticked, and turning
    auto-delete back on has to restore what the user set.
    """
    _pick(tab, "bench-box")
    tab._dry_run_cleanup_cb.setChecked(True)

    tab._cleanup_remote_cb.setChecked(False)
    assert not tab._dry_run_cleanup_cb.isEnabled(), "the gate stopped gating"
    assert tab._dry_run_cleanup_cb.isChecked(), "the gate cleared the value"

    tab._cleanup_remote_cb.setChecked(True)
    assert tab._dry_run_cleanup_cb.isEnabled()
    assert tab._dry_run_cleanup_cb.isChecked()


def test_the_remote_group_is_still_hidden_for_a_local_run(tab):
    """The part of the old behaviour that was right stays right."""
    _pick(tab, "bench-box")
    assert not tab._remote_params_widget.isHidden()
    _pick(tab, None)
    assert tab._remote_params_widget.isHidden()
