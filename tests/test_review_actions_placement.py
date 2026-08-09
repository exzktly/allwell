"""The dataset action cluster lives on the mode band, and only in Review.

Reload, presentation mode, reset saved state, reveal folder, reset view — every
one acts on the loaded dataset or on the plots drawn from it. They used to sit in
the window header, one row above the mode band: a second strip of chrome across
the top of the window, showing the same controls in Analyze and Datasets, where
none of them has anything to act on.

The help button is the exception and is tested here as such. It is not part of
the cluster, it is mounted after it, and it is shown in every mode: the manual it
opens documents Analyze and Datasets too, and gating it away left those two panes
with no discoverable route to the drawer at all.

``AllWellApp._mount_review_actions`` moves both to the band's right end (the
page-tabs host holds the band's only stretch, so they land hard right), and
``_sync_review_actions`` shows the cluster in Review alone. Both are exercised
against a stub rather than a live ``AllWellApp``: building the real window is a
slow-lane affair, and what is under test is these two methods' contract with the
band.
"""

from __future__ import annotations

import types

import pytest

pytest.importorskip("PySide6")

from allwell import AllWellApp


def _band(qapp):
    """A stand-in for the Review mode band: a stretchy filler, then a pin."""
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    band = QWidget()
    layout = QHBoxLayout(band)
    layout.setContentsMargins(0, 0, 0, 0)
    filler = QWidget(band)
    layout.addWidget(filler, 1)
    band.setFixedHeight(26)                      # a tab bar's height
    return band, layout


def _shell(qapp, mode: str = "review"):
    """A stub carrying the two things the band mount touches: the Review-only
    cluster, and the standalone help button that outlives it."""
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    from widgets.icon_button import IconButton

    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    for glyph in ("refresh-cw", "image", "trash-2", "home", "rotate-ccw"):
        row.addWidget(IconButton(glyph, box))
    shell = types.SimpleNamespace(
        _review_actions=box,
        _help_btn=IconButton("info"),
        _review=types.SimpleNamespace(currentMode=lambda: mode),
    )
    # The real method — ``_mount_review_actions`` finishes by calling it.
    shell._sync_review_actions = types.MethodType(
        AllWellApp._sync_review_actions, shell)
    return shell, box


def test_cluster_lands_at_the_bands_right_end(qapp):
    shell, box = _shell(qapp)
    band, layout = _band(qapp)
    AllWellApp._mount_review_actions(shell, band)
    assert layout.itemAt(layout.count() - 2).widget() is box


def test_help_is_the_bands_last_widget(qapp):
    """Help sits outboard of the cluster, so it keeps the same spot in every
    mode instead of sliding right when the cluster hides."""
    shell, _box = _shell(qapp)
    band, layout = _band(qapp)
    AllWellApp._mount_review_actions(shell, band)
    assert layout.itemAt(layout.count() - 1).widget() is shell._help_btn


def test_band_grows_to_fit_the_buttons(qapp):
    """A band pinned to a text row would clip the glyphs; raise the pin."""
    shell, box = _shell(qapp)
    band, _layout = _band(qapp)
    AllWellApp._mount_review_actions(shell, band)
    assert band.minimumHeight() >= box.sizeHint().height()


def test_band_is_not_shrunk_by_a_short_cluster(qapp):
    shell, box = _shell(qapp)
    band, _layout = _band(qapp)
    band.setFixedHeight(80)
    AllWellApp._mount_review_actions(shell, band)
    assert band.minimumHeight() == 80


@pytest.mark.parametrize("mode,shown", [
    ("review", True),
    ("analyze", False),
    ("datasets", False),
])
def test_cluster_shows_in_review_only(qapp, mode, shown):
    shell, box = _shell(qapp, mode)
    band, _layout = _band(qapp)
    AllWellApp._mount_review_actions(shell, band)
    band.show()
    AllWellApp._sync_review_actions(shell)
    assert box.isVisibleTo(band) is shown


@pytest.mark.parametrize("mode", ["review", "analyze", "datasets"])
def test_help_button_shows_in_every_mode(qapp, mode):
    """The reported defect: in Analyze and Datasets the help button went off
    screen with the cluster, leaving no visible way into the drawer."""
    shell, _box = _shell(qapp, mode)
    band, _layout = _band(qapp)
    AllWellApp._mount_review_actions(shell, band)
    band.show()
    AllWellApp._sync_review_actions(shell)
    assert shell._help_btn.isVisibleTo(band) is True


def test_sync_round_trips_across_modes(qapp):
    shell, box = _shell(qapp)
    band, _layout = _band(qapp)
    AllWellApp._mount_review_actions(shell, band)
    band.show()
    for mode in ("analyze", "review", "datasets", "review"):
        shell._review = types.SimpleNamespace(currentMode=lambda m=mode: m)
        AllWellApp._sync_review_actions(shell)
        assert box.isVisibleTo(band) is (mode == "review")
        assert shell._help_btn.isVisibleTo(band) is True


def test_band_fits_the_help_button_when_there_is_no_cluster(qapp):
    """A shell with a help button and no cluster still has to raise the pin.

    Not "the cluster is hidden outside Review" — a hidden widget keeps its
    sizeHint, so with both present the max() reads the same either way and a
    test phrased that way passes with the help button dropped from it entirely.
    The case that bites is the one `test_missing_cluster_is_safe` shows is
    reachable: a build that got one of the two and not the other.
    """
    from widgets.icon_button import IconButton

    band, _layout = _band(qapp)
    shell = types.SimpleNamespace(_help_btn=IconButton("info"))
    shell._sync_review_actions = types.MethodType(
        AllWellApp._sync_review_actions, shell)
    AllWellApp._mount_review_actions(shell, band)
    assert band.minimumHeight() >= shell._help_btn.sizeHint().height()


def test_missing_cluster_is_safe(qapp):
    """Trimmed builds / partial construction must not take the window down."""
    band, _layout = _band(qapp)
    shell = types.SimpleNamespace()
    shell._sync_review_actions = types.MethodType(
        AllWellApp._sync_review_actions, shell)
    AllWellApp._mount_review_actions(shell, band)   # must not raise
    AllWellApp._sync_review_actions(shell)          # must not raise
