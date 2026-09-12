"""The floating rail collapse toggle's position (review/core/app_shell_view.py).

Regression guard for two things at once: the toggle must sit vertically
centred on whichever left panel is showing (not pinned level with the rail
head, which read as "stuck at the top" to a user scanning the panel's right
edge for it), while never landing on top of Review's own well-picker plate
(a real, previously-fixed collision — the toggle used to sit on the plate at
common window heights, turning a click-and-drag well selection into an
accidental sidebar collapse).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


def _settle(app) -> None:
    for _ in range(3):
        QApplication.processEvents()


def test_toggle_never_overlaps_the_well_picker_plate(viewer_app):
    from review.core.app_shell_view import position_rail_toggle

    app = viewer_app
    # Drain synchronously rather than leaving deferred QTimer.singleShot(0, …)
    # centre-tab builders pending -- undrained, they fire on whichever later
    # test's processEvents() happens to pump the (session-wide) event loop
    # next, polluting timer-count assertions there (see
    # test_poll_log_resilience.py's rearm counter).
    app._drain_pending_centre_builders()
    app.resize(1640, 980)
    app.show()
    _settle(app)
    # Land on a page that actually shows the rail's picker. Review opens on
    # Home, which deliberately shows no rail frame at all — it carries its own
    # plate, and a second one in the rail would be two plates on one screen — so
    # the collision this test exists to catch is not reachable from there.
    assert app._notebook.setCurrentByName("Plotting")
    _settle(app)
    position_rail_toggle(app)

    btn = app._sidebar_toggle_btn
    plate = app._sidebar_plate
    assert plate.isVisible(), (
        "no rail plate on screen, so this test would pass without measuring "
        "the overlap it exists to measure")

    from PySide6.QtCore import QPoint
    btn_top = btn.y()
    btn_bottom = btn_top + btn.height()
    plate_top = plate.mapTo(app, QPoint(0, 0)).y()
    plate_bottom = plate_top + plate.height()

    overlaps = btn_top < plate_bottom and btn_bottom > plate_top
    assert not overlaps, (
        f"toggle [{btn_top}, {btn_bottom}] overlaps plate [{plate_top}, {plate_bottom}]"
    )


def test_toggle_centres_on_the_panel_when_nothing_to_avoid(viewer_app):
    """With the plate collision out of the way, the toggle must land near
    the panel's true vertical centre -- not pinned near the top the way the
    old dataset-head-level positioning did."""
    from review.core.app_shell_view import position_rail_toggle

    app = viewer_app
    # Drain synchronously rather than leaving deferred QTimer.singleShot(0, …)
    # centre-tab builders pending -- undrained, they fire on whichever later
    # test's processEvents() happens to pump the (session-wide) event loop
    # next, polluting timer-count assertions there (see
    # test_poll_log_resilience.py's rearm counter).
    app._drain_pending_centre_builders()
    app.resize(1640, 980)
    app.show()
    _settle(app)
    app._sidebar_plate.setVisible(False)  # remove the one thing to dodge
    position_rail_toggle(app)

    btn = app._sidebar_toggle_btn
    panel = app._sidebar_root
    from PySide6.QtCore import QPoint
    panel_top = panel.mapTo(app, QPoint(0, 0)).y()
    panel_centre = panel_top + panel.height() // 2
    btn_centre = btn.y() + btn.height() // 2

    assert abs(btn_centre - panel_centre) <= btn.height()
