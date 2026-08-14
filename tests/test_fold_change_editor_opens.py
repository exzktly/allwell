"""Choosing "Per-sample control…" actually opens the editor.

The combo entry and the ``Edit…`` link are the only two ways in, and both go
through one closure. That closure wrapped the call in ``with swallowed():``,
which raises ``AttributeError: __enter__`` *before* the body — so selecting the
entry switched the plot into per-sample mode and then opened nothing, and the
link did nothing at all.

A lane of ~4,950 tests stayed green over it. Every test that exercised the
editor called ``open_pairing_editor`` directly; none went through the control
that a user has to go through. That is CLAUDE.md §7.4 #1 exactly — assert the
outbound call, not the derivation behind it — so this drives the real combo and
asserts a window came back.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from well_viewer.core import fold_change_controls_view as view
from well_viewer.domain.control_ref import ControlRef

from tests.conftest import MockApp, make_agg

_SETS = [
    {"id": 1, "name": "CTRL", "wells": ["A01"], "source": "rep_set"},
    {"id": 2, "name": "DRUG", "wells": ["B01"], "source": "rep_set"},
]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _StubBus:
    class _Sig:
        @staticmethod
        def emit(*_a):
            return None

    plot_options_changed = _Sig()


class _Registry:
    """A real dict-backed stand-in for ``ctx.widgets``.

    The editor stores its window under a scope-namespaced key and looks it up
    on the next open, so a registry that drops what it is handed would make
    this test pass without the window ever existing.
    """

    def __init__(self):
        self.by_name: dict = {}

    def register(self, name, widget):
        self.by_name[name] = widget

    def get_or_none(self, name):
        return self.by_name.get(name)

    def has(self, name):
        return name in self.by_name


@pytest.fixture
def installed(qapp):
    """The Fold-change controls, built into a real parent widget."""
    app = MockApp(
        per_well_pts={"A01": [make_agg(0.0, 100.0)],
                      "B01": [make_agg(0.0, 200.0)]},
        selections=_SETS,
    )
    ctx = app._ctx
    ctx.bus = _StubBus()
    ctx.widgets = _Registry()
    ctx.window = None
    parent = QWidget()
    layout = QVBoxLayout(parent)
    view.install_fold_change_controls(ctx, parent, layout, scope="bar")
    return ctx, parent


def _control_combo(ctx):
    combo = ctx.widgets.get_or_none("_fc_ctrl_combo_bar")
    assert combo is not None, "the installer did not register the Control combo"
    return combo


def test_choosing_per_sample_control_opens_the_editor(installed):
    ctx, _parent = installed
    combo = _control_combo(ctx)

    idx = next((i for i in range(combo.count())
                if combo.itemData(i) is view.PER_SAMPLE), None)
    assert idx is not None, "the Per-sample control entry is not in the combo"

    combo.setCurrentIndex(idx)          # fires currentIndexChanged → _open_editor

    assert ctx.state.plot.fc_control_mode == "per_member", \
        "the state switched, which is the half that always worked"
    win = ctx.widgets.get_or_none("_fc_pairing_window_bar")
    assert win is not None, (
        "per-sample mode turned on and no editor opened — the user is left in a "
        "mode with no way to configure it"
    )
    assert win.isVisible()


def test_the_edit_link_opens_the_editor(installed):
    """The second way in, and the only one once the combo already reads
    per-sample: re-selecting the item already showing fires no signal."""
    ctx, _parent = installed
    view.set_fold_change_state(
        ctx, vs_control_on=True, control_mode="per_member", initiating_scope="")
    summary = ctx.widgets.get_or_none("_fc_summary_label_bar")
    assert summary is not None, "the installer did not register the summary label"

    summary.linkActivated.emit("#edit")

    win = ctx.widgets.get_or_none("_fc_pairing_window_bar")
    assert win is not None, "the Edit… link opened nothing"


def test_the_editor_lists_the_plotted_members(installed):
    """A window that opened but described the wrong plot would satisfy the two
    tests above."""
    ctx, _parent = installed
    ctx.state.plot.fc_control_map = {"DRUG": ControlRef.for_replicate_set("CTRL")}
    from well_viewer.core.fold_change_pairing_view import open_pairing_editor

    open_pairing_editor(ctx, "bar")
    from well_viewer.core.fold_change_pairing_view import pairing_rows

    rows = pairing_rows(ctx, "bar")
    assert [r["member"] for r in rows] == ["CTRL", "DRUG"]
    assert next(r for r in rows if r["member"] == "DRUG")["resolves_to"] == ["A01"]
