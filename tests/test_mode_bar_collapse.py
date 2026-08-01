"""The mode bar is the top of the left column, so it collapses with the rail.

Review / Analyze / Datasets sits directly above whichever panel runs down the
window's left edge, pinned to that panel's width so the two read as one column.
Collapsing the rail (``Ctrl+B``, or the button floating on its edge) used to
leave the bar behind: a rail-width band of chrome hanging above an empty column,
shrunk to a natural width its three labels do not fit inside, with the page tab
strip stranded to the right of it.

``AllWellApp._sync_mode_bar`` owns both halves of that — visibility and width —
and runs off the panels' own show/hide events, so it follows every route into
``tab_controller.apply_sidebar_visibility`` without either side knowing about
the other. Exercised here against a stub rather than a live ``AllWellApp``:
building the real window is a slow-lane affair, and the behaviour under test is
this one method's reading of the collapse flag.
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

    review_rail = QWidget(host)
    review_rail.setFixedWidth(329)
    analyze_rail = QWidget(host)
    analyze_rail.setFixedWidth(329)
    host.show()

    review = types.SimpleNamespace(
        _sidebar_root=review_rail, _sidebar_collapsed=collapsed)
    review_rail.setVisible(not collapsed and mode_index == 0)
    analyze_rail.setVisible(not collapsed and mode_index == 1)
    shell = types.SimpleNamespace(
        _mode_seg=bar,
        _review=review,
        _analyze=types.SimpleNamespace(_left_sidebar=analyze_rail),
        _datasets=types.SimpleNamespace(_left_sidebar=QWidget(host)),
    )
    # The real method — it is what resolves "the current mode's left panel".
    shell._mode_left_panel = types.MethodType(AllWellApp._mode_left_panel, shell)
    return shell, host, bar


def test_expanded_rail_shows_the_bar(qapp):
    shell, host, bar = _shell(qapp, collapsed=False)
    AllWellApp._sync_mode_bar(shell)
    assert bar.isVisibleTo(host) is True


def test_collapsed_rail_hides_the_bar(qapp):
    shell, host, bar = _shell(qapp, collapsed=True)
    AllWellApp._sync_mode_bar(shell)
    assert bar.isVisibleTo(host) is False


def test_collapse_round_trips(qapp):
    """Re-expanding brings the bar back — it is the only mode switch there is."""
    shell, host, bar = _shell(qapp, collapsed=False)
    AllWellApp._sync_mode_bar(shell)
    shell._review._sidebar_collapsed = True
    shell._review._sidebar_root.setVisible(False)
    AllWellApp._sync_mode_bar(shell)
    assert bar.isVisibleTo(host) is False
    shell._review._sidebar_collapsed = False
    shell._review._sidebar_root.setVisible(True)
    AllWellApp._sync_mode_bar(shell)
    assert bar.isVisibleTo(host) is True


def test_collapsed_in_analyze_mode_hides_the_bar_too(qapp):
    """The column collapses the same way whichever mode owns the panel."""
    shell, host, bar = _shell(qapp, collapsed=True, mode_index=1)
    AllWellApp._sync_mode_bar(shell)
    assert bar.isVisibleTo(host) is False


def test_expanded_bar_matches_the_panel_width(qapp):
    shell, _host, bar = _shell(qapp, collapsed=False)
    AllWellApp._sync_mode_bar(shell)
    assert bar.width() == 329


def test_width_never_drops_below_the_bar_s_own_natural_width(qapp):
    """A panel narrower than the three segments elides labels, not the bar."""
    shell, _host, bar = _shell(qapp, collapsed=False)
    shell._review._sidebar_root.setFixedWidth(40)
    AllWellApp._sync_mode_bar(shell)
    assert bar.width() == bar.sizeHint().width() >= 40


def test_missing_bar_is_safe(qapp):
    shell, _host, _bar = _shell(qapp, collapsed=False)
    shell._mode_seg = None
    AllWellApp._sync_mode_bar(shell)  # must not raise
