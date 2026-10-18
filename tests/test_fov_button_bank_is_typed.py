"""Only a ReservedWidthButton can join the per-FOV toggle bank.

``plot_toggles.refresh_fov_btn_state`` restyles every button on
``ctx.runtime.fov.btns`` and reserves room for the wider checked label
("FOV ✓"), so the ✓ never clips inside a popover sized at first show. It used to
do that only ``if hasattr(btn, "reserve")``: a plain ``QPushButton`` on the bank
would quietly get no reservation and bring the clipping back, with nothing to
say so. The bank now has one door, ``plot_toggles.register_fov_button``, which
refuses anything that is not a ``ReservedWidthButton``, and the restyle calls
``reserve`` unconditionally.

* a plain button is refused at registration, with a ``TypeError`` naming it;
* the two real registrants — the plot toolbar's band controls and the batch
  export panels — go through that door, never ``fov.btns.append`` directly (an
  AST scan, so a third registrant cannot route around it);
* a button registered through the real band-controls builder has its width
  reserved for "FOV ✓".
"""

from __future__ import annotations

import ast
from types import SimpleNamespace

import pytest

from tests._repo_scan import parse_source, python_sources, rel

OWNER = "review/core/plot_toggles.py"


def _ctx():
    from review.core.app_runtime import AppRuntime
    from review.core.app_state import AppState

    state = AppState()
    return SimpleNamespace(state=state, runtime=AppRuntime())


def test_a_plain_button_is_refused(qtbot):
    from PySide6.QtWidgets import QPushButton

    from review.core.plot_toggles import register_fov_button

    ctx = _ctx()
    btn = QPushButton("FOV")
    qtbot.addWidget(btn)
    with pytest.raises(TypeError, match="ReservedWidthButton"):
        register_fov_button(ctx, btn)
    assert not ctx.runtime.fov.btns, "the refused button still joined the bank"


def test_the_band_controls_button_reserves_room_for_the_checked_label(qtbot):
    from PySide6.QtWidgets import QPushButton, QWidget

    from review.core.ui_helpers import make_band_controls
    from widgets.reserved_width_button import ReservedWidthButton

    ctx = _ctx()
    parent = QWidget()
    qtbot.addWidget(parent)
    make_band_controls(ctx, parent, with_fov=True)
    (btn,) = ctx.runtime.fov.btns
    assert isinstance(btn, ReservedWidthButton)
    probe = QPushButton("FOV ✓")
    probe.setFont(btn.font())
    qtbot.addWidget(probe)
    assert btn.minimumWidth() >= probe.fontMetrics().horizontalAdvance("FOV ✓"), (
        "the FOV toggle reserved no room for its checked label")


def _direct_appends(path, tree) -> list[str]:
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"append", "extend", "insert"}):
            continue
        target = node.func.value
        if (isinstance(target, ast.Attribute) and target.attr == "btns"
                and isinstance(target.value, ast.Attribute) and target.value.attr == "fov"):
            out.append(f"{rel(path)}:{node.lineno}")
    return out


def test_nothing_outside_plot_toggles_appends_to_the_fov_bank():
    offenders = []
    for path in python_sources():
        if rel(path) == OWNER:
            continue
        offenders.extend(_direct_appends(path, parse_source(path)))
    assert not offenders, (
        "Register a per-FOV toggle through plot_toggles.register_fov_button, which "
        "refuses a button that cannot reserve room for 'FOV ✓':\n  "
        + "\n  ".join(offenders)
    )
