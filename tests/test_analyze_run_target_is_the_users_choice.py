"""Audit U2 — "Run on:" keeps the target the user picked.

``_reload_remotes_combo`` rebuilt the combo from ``remotes.toml`` after every
Manage-Remotes change and re-selected the previous target by reading
``self._remote_cb.currentData()``. That value is ``None`` for two different
states: "the user chose Local" and "the user has never touched this control".
The re-select loop skipped ``None`` outright, fell into its ``for…else``, and
applied ``default_remote_index``, which returns the first configured remote
whenever one exists.

So a user running locally who opened Manage… to bootstrap a cluster for later
— including a bootstrap that *failed*, which emits ``remotes_changed`` all the
same — came back to a "Run on:" reading ``hpc``. Pressing Run rsync'd the whole
plate to an SSH host instead of running on the machine in front of them. The
rebuild happens under ``blockSignals(True)``, so nothing was emitted and
nothing was logged.

The mirror case is the one the docstring already promised and did not deliver:
deleting the selected remote left the combo on some *other* remote rather than
falling back to Local.

These drive the real combo — ``setCurrentIndex`` fires ``currentIndexChanged``
exactly as a click on the popup does, which is the signal the fix hangs on.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


class _Host:
    """Enough of a ``RemoteHost`` for the combo, which only reads the key."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.ssh_alias = f"{name}.example"


def _remotes(*names: str) -> dict:
    return {n: _Host(n) for n in names}


@pytest.fixture
def tab(qtbot, monkeypatch):
    """A real AnalyzeTab built with no remotes configured on disk."""
    import analyze_tab_form
    import services.remote_config as remote_config

    monkeypatch.setattr(analyze_tab_form, "load_remotes", lambda: {})
    monkeypatch.setattr(remote_config, "load_remotes", lambda: {})
    from analyze_tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)
    return t


def _select(tab, data) -> None:
    """Pick the item carrying *data*, the way a user picks it from the popup."""
    for i in range(tab._remote_cb.count()):
        if tab._remote_cb.itemData(i) == data:
            tab._remote_cb.setCurrentIndex(i)
            return
    raise AssertionError(f"no 'Run on:' item carries {data!r}")


def test_choosing_local_survives_a_manage_remotes_change(tab, monkeypatch):
    """The reported shape: pick Local, add a remote, stay on Local."""
    import services.remote_config as remote_config

    assert tab._remote_cb.currentData() is None, "fixture should start on Local"
    _select(tab, None)                       # the user says Local, explicitly

    monkeypatch.setattr(remote_config, "load_remotes", lambda: _remotes("hpc"))
    tab._reload_remotes_combo()

    assert tab._remote_cb.currentData() is None, (
        "'Run on:' switched to "
        f"{tab._remote_cb.currentText()!r} after a Manage-Remotes change, so a "
        "deliberate local run would have been rsync'd to an SSH host")


def test_a_failed_bootstrap_does_not_move_a_local_run_to_a_remote(tab, monkeypatch):
    """``remotes_changed`` fires even when the bootstrap it followed failed."""
    import services.remote_config as remote_config

    _select(tab, None)
    monkeypatch.setattr(remote_config, "load_remotes", lambda: _remotes("hpc", "box"))
    for _ in range(3):                       # every emission, not just the first
        tab._reload_remotes_combo()
    assert tab._remote_cb.currentData() is None


def test_deleting_the_selected_remote_falls_back_to_local(tab, monkeypatch):
    """The behaviour ``_open_manage_remotes``' docstring has always claimed."""
    import services.remote_config as remote_config

    monkeypatch.setattr(remote_config, "load_remotes", lambda: _remotes("hpc", "box"))
    tab._reload_remotes_combo()
    _select(tab, "box")

    monkeypatch.setattr(remote_config, "load_remotes", lambda: _remotes("hpc"))
    tab._reload_remotes_combo()
    assert tab._remote_cb.currentData() is None, (
        f"deleting the selected remote left 'Run on:' on "
        f"{tab._remote_cb.currentText()!r} rather than Local")


def test_a_chosen_remote_that_still_exists_is_kept(tab, monkeypatch):
    """The other half of the same rule — a live choice is not thrown away."""
    import services.remote_config as remote_config

    monkeypatch.setattr(remote_config, "load_remotes", lambda: _remotes("hpc", "box"))
    tab._reload_remotes_combo()
    _select(tab, "box")
    monkeypatch.setattr(remote_config, "load_remotes",
                        lambda: _remotes("hpc", "box", "third"))
    tab._reload_remotes_combo()
    assert tab._remote_cb.currentData() == "box"


def test_the_first_configured_remote_is_still_the_opening_default(qtbot, monkeypatch):
    """``default_remote_index`` still decides the *opening* state.

    Removing it from the rebuild path must not remove it from the build path:
    a user whose ``remotes.toml`` names a host still finds that host selected
    when the pane appears. What changed is that a rebuild is no longer treated
    as an opening state — see ``_reload_remotes_combo``'s docstring.
    """
    import analyze_tab_form
    import services.remote_config as remote_config

    monkeypatch.setattr(analyze_tab_form, "load_remotes", lambda: _remotes("hpc", "box"))
    monkeypatch.setattr(remote_config, "load_remotes", lambda: _remotes("hpc", "box"))
    from analyze_tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)
    assert t._remote_cb.currentData() == "hpc"
    # …and a rebuild keeps it rather than re-deciding.
    t._reload_remotes_combo()
    assert t._remote_cb.currentData() == "hpc"


def test_a_newly_added_remote_does_not_take_over_a_local_run(tab, monkeypatch):
    """The scenario with no remotes configured at all, which is where the
    default fallback did its damage: nothing to pick from, so nothing the user
    could have picked, and adding the first host redirected the run anyway."""
    import services.remote_config as remote_config

    assert tab._remote_cb.count() == 1, "fixture should offer Local only"
    monkeypatch.setattr(remote_config, "load_remotes", lambda: _remotes("hpc"))
    tab._reload_remotes_combo()
    assert tab._remote_cb.currentData() is None, (
        "adding the first remote moved the run onto it without being asked")
