"""The mode bar is the header's pill (window style W1, design language §3.1), so it no longer follows the rail.

Review / Analyze / Datasets sat at the head of the left column, pinned to that column's width and hidden when the
rail collapsed. In W1 it is a pill beside the application's name in the one top bar: shown whatever the rail does,
in every mode, at the width its three segments need. ``AllWellApp._sync_mode_bar`` owns that, exercised here
against a stub rather than a live window (building the real one is a slow-lane affair).
"""

from __future__ import annotations

import types

import pytest

pytest.importorskip("PySide6")

from allwell import AllWellApp


def _shell(qapp, *, collapsed: bool, mode_index: int = 0):
    """A stand-in carrying only what ``_sync_mode_bar`` reads."""
    from PySide6.QtWidgets import QWidget

    from widgets.mode_bar import ModeBar

    host = QWidget()
    bar = ModeBar(host)
    for text, data in (("Review", "review"), ("Analyze", "analyze"),
                       ("Datasets", "datasets")):
        bar.addSegment(text, data=data)
    bar.setCurrentIndex(mode_index)
    bar.setFixedWidth(600)                       # a width left over from somewhere else
    host.show()
    shell = types.SimpleNamespace(
        _mode_seg=bar, _review=types.SimpleNamespace(_sidebar_collapsed=collapsed))
    return shell, host, bar


@pytest.mark.parametrize("collapsed", [False, True])
@pytest.mark.parametrize("mode_index", [0, 1, 2])
def test_the_pill_is_shown_at_its_own_width_whatever_the_rail_does(qapp, collapsed, mode_index):
    shell, host, bar = _shell(qapp, collapsed=collapsed, mode_index=mode_index)
    AllWellApp._sync_mode_bar(shell)
    assert bar.isVisibleTo(host)
    assert bar.width() == bar.sizeHint().width() < 600


def test_missing_bar_is_safe(qapp):
    shell, _host, _bar = _shell(qapp, collapsed=False)
    shell._mode_seg = None
    AllWellApp._sync_mode_bar(shell)  # must not raise
