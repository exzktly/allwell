"""Changing one row's control rebuilds the table once, after the signal returns.

Two defects sat in the per-sample pairing editor, both reachable only by
actually operating the combo a user operates.

``_write`` called ``_populate()`` after ``set_fold_change_state``. That sync
walks every scope whose name is not the ``initiating_scope`` and ``_write``
passes the empty string, so no scope is skipped and the sync already reaches
``refresh_pairing_window`` — which calls this window's own populate. Every
single-combo edit therefore rebuilt the whole table twice.

Worse, the rebuild ran inside the combo's own ``currentIndexChanged`` emission,
and ``_fill_row`` calls ``setCellWidget`` for the row being rebuilt. That
destroys the very combo whose signal is on the stack: Qt deletes the C++ object
under the Python wrapper while the emission is still running.

These drive the real widget rather than calling the closure underneath it — the
seam under the control is where every callback bug in this app has hidden
(CLAUDE.md section 7.6).
"""

from __future__ import annotations

import types

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QComboBox, QTableWidget

from well_viewer.core import fold_change_pairing_view as V
from well_viewer.domain.fold_change_pairing import ControlOutcome

pytestmark = pytest.mark.qt

_SCOPE = "bar"


def _row(member, status="ok"):
    return {
        "member": member,
        "display": member,
        "control": None,
        "status": status,
        "inherited_from": "",
        "outcome": ControlOutcome.OK,
        "choices": [("— none —", None), ("A01", ("well", "A01"))],
        "saved": None,
        "resolves_to": [],
    }


@pytest.fixture
def editor(qtbot, monkeypatch):
    """The real window, over stubbed data and a recording state writer."""
    writes: list[dict] = []
    populates: list[int] = []

    monkeypatch.setattr(V, "pairing_rows", lambda _c, _s: [_row("S1"), _row("S2")])
    monkeypatch.setattr(V, "unused_rows", lambda _c, _s: [])

    ctx = types.SimpleNamespace(
        window=None,
        state=types.SimpleNamespace(
            plot=types.SimpleNamespace(fc_control_map={}, fc_control_mode="per_sample"),
            selection=types.SimpleNamespace(selections=[], selected_wells=set()),
            dataset=types.SimpleNamespace(well_paths={}),
        ),
        widgets=types.SimpleNamespace(get_or_none=lambda _n: None),
        runtime=types.SimpleNamespace(
            barplot=types.SimpleNamespace(current_keys=lambda: []),
        ),
    )

    import well_viewer.core.fold_change_controls_view as CV

    def _set_state(_ctx, *, control_map=None, initiating_scope=None, **_kw):
        writes.append(dict(control_map or {}))
        # What the real sync does for a scope it does not skip: repopulate.
        assert initiating_scope == "", (
            "the sync skips the initiating scope, so passing a real scope name "
            "here would stop the window from being refreshed at all"
        )
        win._aw_populate()

    monkeypatch.setattr(CV, "set_fold_change_state", _set_state)

    win = V.build_pairing_window(ctx, _SCOPE)
    assert win is not None
    qtbot.addWidget(win)

    real_populate = win._aw_populate

    def _counting():
        populates.append(1)
        real_populate()

    win._aw_populate = _counting
    return win, writes, populates


def _combo(win, row) -> QComboBox:
    table = win.findChild(QTableWidget)
    return table.cellWidget(row, V.COL_CONTROL)


def test_one_edit_writes_once_and_rebuilds_once(editor, qtbot):
    win, writes, populates = editor
    _combo(win, 0).setCurrentIndex(1)
    # The handler defers by one event-loop turn, so nothing has happened yet.
    assert writes == []
    QCoreApplication.processEvents()

    assert len(writes) == 1, "the row's control was written more than once"
    assert len(populates) == 1, (
        f"the table was rebuilt {len(populates)} times for one combo change"
    )


def test_the_combo_is_not_destroyed_inside_its_own_signal(editor, qtbot):
    """The crash shape: the rebuild must not run during the emission."""
    win, _writes, _populates = editor
    combo = _combo(win, 0)
    seen: list[bool] = []

    def _after(_i):
        # Still inside the emission. If the rebuild ran synchronously it would
        # already have replaced this cell widget with a new object.
        seen.append(_combo(win, 0) is combo)

    combo.currentIndexChanged.connect(_after)
    combo.setCurrentIndex(1)

    assert seen == [True], "the combo was replaced while its signal was running"
    QCoreApplication.processEvents()
