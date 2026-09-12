"""The Heat Map's Display checkboxes do something when you click them.

CLAUDE.md §7.6 is a whole section about the gap this file closes. A per-sample
control editor once shipped unopenable because eighteen test files drove the
module *underneath* its combo and not one selected an entry in it: asserting a
control is offered is not asserting it works, and the two look identical from
a green suite.

The two toggles this branch adds — **Show ± error** and **Label ticks from
sample map** — were in exactly that state. Their handlers
(``_on_show_error_changed`` / ``_on_sample_labels_changed``) were named by no
test, and both renderer fixtures set ``ctx.state.heatmap.show_error`` /
``.label_from_sample_map`` directly, entering one step past the widget. An
adversarial pass proved it: deleting both ``.toggled.connect(...)`` lines *and*
misspelling a widget key inside a handler left 51 tests green and ruff clean.

So these press the buttons. They build the real tab through the real builder
on a real app — the seam the app itself uses, not the convenient one below it
(§7.8 rule 3) — and assert at the two boundaries that matter: the typed slice
the renderer reads, and the persistence call that makes the choice outlive the
session.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QWidget

from review.features.heatmap.tab_view import build_heatmap_tab


#: Every tab parent this module builds, kept alive for the whole session.
#:
#: ``qtbot.addWidget`` would destroy each one at the end of its test, and the
#: tab builder has already handed that widget's empty-state page to the app.
#: The next toggle then redraws, ``refresh_empty_state_pages`` walks the pages
#: the app still holds, and the destroyed one raises "Internal C++ object
#: (QLabel) already deleted" — a teardown artefact that looks exactly like the
#: handler being broken. Holding the parents costs a few widgets in a lane
#: that already builds whole applications, and the fast lane's widget reaper
#: exempts anything marked ``slow`` (which the ``viewer_app`` fixture is).
_PARENTS: list[QWidget] = []


@pytest.fixture
def heatmap_tab(viewer_app, qtbot):
    """The Heat Map tab, built the way the application builds it."""
    app = viewer_app
    parent = QWidget()
    _PARENTS.append(parent)
    build_heatmap_tab(app._ctx, parent)
    # The builder ends in a queued ``draw_idle``; let it settle while the
    # canvas is alive, for the reason test_heatmap_widget_registry_a8 gives.
    qtbot.wait(1)
    return app


def test_the_error_checkbox_starts_unchecked_and_turns_the_setting_on(heatmap_tab):
    app = heatmap_tab
    cb = app._heatmap_show_error_cb
    assert cb.isChecked() is False
    assert app._ctx.state.heatmap.show_error is False

    cb.setChecked(True)

    assert app._ctx.state.heatmap.show_error is True


def test_the_error_checkbox_turns_the_setting_off_again(heatmap_tab):
    """Both directions: a handler that only ever sets True passes the test above."""
    app = heatmap_tab
    cb = app._heatmap_show_error_cb
    cb.setChecked(True)
    cb.setChecked(False)

    assert app._ctx.state.heatmap.show_error is False


def test_the_sample_map_checkbox_starts_checked_and_turns_the_setting_off(heatmap_tab):
    """It is the one toggle in the popover whose default is on."""
    app = heatmap_tab
    cb = app._heatmap_sample_labels_cb
    assert cb.isChecked() is True
    assert app._ctx.state.heatmap.label_from_sample_map is True

    cb.setChecked(False)

    assert app._ctx.state.heatmap.label_from_sample_map is False


def test_the_sample_map_checkbox_turns_the_setting_back_on(heatmap_tab):
    app = heatmap_tab
    cb = app._heatmap_sample_labels_cb
    cb.setChecked(False)
    cb.setChecked(True)

    assert app._ctx.state.heatmap.label_from_sample_map is True


@pytest.mark.parametrize("handle", ["_heatmap_show_error_cb",
                                    "_heatmap_sample_labels_cb"])
def test_toggling_schedules_the_save_that_makes_the_choice_outlive_the_session(
        heatmap_tab, monkeypatch, handle):
    """A toggle that redraws but never persists comes back wrong on reload."""
    app = heatmap_tab
    saved: list[str] = []
    monkeypatch.setattr(app._ctx.persistence, "schedule_save",
                        lambda name: saved.append(name))

    cb = getattr(app, handle)
    cb.setChecked(not cb.isChecked())

    assert "heatmap_layouts" in saved


def test_the_two_toggles_are_not_the_same_control(heatmap_tab):
    """Guards the four tests above against one handler answering for both.

    Both checkboxes wired to one slot would satisfy every assertion in this
    file that touches only one of them.
    """
    app = heatmap_tab
    app._heatmap_show_error_cb.setChecked(True)

    assert app._ctx.state.heatmap.show_error is True
    assert app._ctx.state.heatmap.label_from_sample_map is True
