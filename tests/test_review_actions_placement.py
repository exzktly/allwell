"""The top-right icons live on the header row, beside the wordmark and version.

Split View's toggle, the dataset action cluster (reload, presentation mode,
reset analysis, copy analysis, reveal folder, reset view), Storage and Help all
sit at the right end of the window's header row — the row that holds the
All-Well wordmark and the version pill. They used to sit at the right end of the
mode band below it, where every icon took width from the page tabs sharing that
band while the header row above stood empty.

The cluster is Review-only; Help (and Storage) are not and are tested here as
such: the manual Help opens documents Analyze and Datasets too, and gating it
away left those two panes with no discoverable route to the drawer at all.

``AllWellApp._mount_review_actions`` and ``_sync_review_actions`` are exercised
against a stub rather than a live ``AllWellApp``: building the real window is a
slow-lane affair, and what is under test is these two methods' contract with the
header row and the band.
"""

from __future__ import annotations

import types

import pytest

pytest.importorskip("PySide6")

from allwell import AllWellApp


def _band(qapp):
    """A stand-in for the header row: a title, then the stretch it ends in."""
    from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(QLabel("All-Well", row))
    layout.addStretch(1)
    return row, layout


def _review_band(qapp):
    """A stand-in for the Review mode band, holding Split View's controls the
    way ``split_view.install_split_view`` leaves them: after the tab host."""
    from PySide6.QtWidgets import QComboBox, QHBoxLayout, QWidget

    from widgets.icon_button import IconButton

    band = QWidget()
    layout = QHBoxLayout(band)
    layout.setContentsMargins(0, 0, 0, 0)
    tabs = QWidget(band)
    layout.addWidget(tabs, 1)
    btn = IconButton("columns-2")
    combo = QComboBox()
    layout.addWidget(btn)
    layout.addWidget(combo)
    band.setFixedHeight(26)                      # a tab bar's height
    split = types.SimpleNamespace(toggle_btn=btn, pane_combo=combo)
    return band, layout, split


def _shell(qapp, mode: str = "review", split=None):
    """A stub carrying what the mount touches: the Review-only cluster, the
    standalone Storage and Help buttons, and (optionally) Split View's pair."""
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    from widgets.icon_button import IconButton

    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    for glyph in ("refresh-cw", "image", "trash-2", "home", "rotate-ccw"):
        row.addWidget(IconButton(glyph, box))
    review = types.SimpleNamespace(currentMode=lambda: mode)
    if split is not None:
        review._ctx = types.SimpleNamespace(
            runtime=types.SimpleNamespace(split=split))
    shell = types.SimpleNamespace(
        _review_actions=box,
        _storage_btn=IconButton("hard-drive"),
        _help_btn=IconButton("info"),
        _review=review,
    )
    # The real method — ``_mount_review_actions`` finishes by calling it.
    shell._sync_review_actions = types.MethodType(
        AllWellApp._sync_review_actions, shell)
    return shell, box


def _widgets(layout):
    return [layout.itemAt(i).widget() for i in range(layout.count())
            if layout.itemAt(i).widget() is not None]


def test_icons_land_on_the_header_row_in_order(qapp):
    """Split toggle, its picker, the cluster, Storage, then Help — after the
    header's stretch, so hard right on the wordmark's line."""
    band, _bl, split = _review_band(qapp)
    shell, box = _shell(qapp, split=split)
    row, layout = _band(qapp)
    AllWellApp._mount_review_actions(shell, row)
    ws = _widgets(layout)
    assert ws[-5:] == [split.toggle_btn, split.pane_combo, box,
                       shell._storage_btn, shell._help_btn]
    stretch_at = next(i for i in range(layout.count())
                      if layout.itemAt(i).spacerItem() is not None)
    assert layout.indexOf(split.toggle_btn) > stretch_at


def test_split_controls_leave_the_page_tab_band(qapp):
    """The reported defect: the icons competed with the page tabs for the
    band's width. After mounting, the band holds the tab host and nothing else,
    so it neither reserves their width nor needs raising past a tab row."""
    band, band_layout, split = _review_band(qapp)
    shell, _box = _shell(qapp, split=split)
    row, _layout = _band(qapp)
    AllWellApp._mount_review_actions(shell, row)
    assert band_layout.count() == 1
    assert split.toggle_btn.parentWidget() is row
    assert split.pane_combo.parentWidget() is row
    assert band.minimumHeight() == 26


def test_help_is_the_rows_last_widget(qapp):
    """Help sits outboard of the cluster, so it keeps the same spot in every
    mode instead of sliding right when the cluster hides."""
    shell, _box = _shell(qapp)
    row, layout = _band(qapp)
    AllWellApp._mount_review_actions(shell, row)
    assert _widgets(layout)[-1] is shell._help_btn


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


def test_help_mounts_when_there_is_no_cluster(qapp):
    """A build that got Help and not the cluster (a trimmed build, or a partial
    construction) still puts Help on the header row."""
    from widgets.icon_button import IconButton

    band, _layout = _band(qapp)
    shell = types.SimpleNamespace(_help_btn=IconButton("info"))
    shell._sync_review_actions = types.MethodType(
        AllWellApp._sync_review_actions, shell)
    AllWellApp._mount_review_actions(shell, band)
    assert _widgets(_layout)[-1] is shell._help_btn


def test_missing_cluster_is_safe(qapp):
    """Trimmed builds / partial construction must not take the window down."""
    band, _layout = _band(qapp)
    shell = types.SimpleNamespace()
    shell._sync_review_actions = types.MethodType(
        AllWellApp._sync_review_actions, shell)
    AllWellApp._mount_review_actions(shell, band)   # must not raise
    AllWellApp._sync_review_actions(shell)          # must not raise
