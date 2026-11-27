"""Only a switch can join the per-FOV spread bank.

``plot_toggles.refresh_fov_btn_state`` sets every member of ``ctx.runtime.fov.btns``
from the shared state. The setting acts at once, so it is a switch (design language
§2.5) — the bank once held push buttons that wrote their state into their label
("FOV" / "FOV ✓"), which is what the design language retires. The bank has one door,
``plot_toggles.register_fov_button``, which refuses anything that is not a
``ToggleSwitch``.

* a plain button is refused at registration, with a ``TypeError`` naming it;
* the two real registrants — the plot toolbar's band controls and the batch
  export panels — go through that door, never ``fov.btns.append`` directly (an
  AST scan, so a third registrant cannot route around it);
* the band-controls builder registers a switch whose state follows the shared one
  and whose label never changes.
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
    with pytest.raises(TypeError, match="ToggleSwitch"):
        register_fov_button(ctx, btn)
    assert not ctx.runtime.fov.btns, "the refused button still joined the bank"


def test_the_band_controls_switch_follows_the_shared_state(qtbot):
    from PySide6.QtWidgets import QWidget

    from review.core.plot_toggles import refresh_fov_btn_state
    from review.core.ui_helpers import make_band_controls
    from widgets.toggle_switch import ToggleSwitch

    ctx = _ctx()
    parent = QWidget()
    qtbot.addWidget(parent)
    make_band_controls(ctx, parent, with_fov=True)
    (switch,) = ctx.runtime.fov.btns
    assert isinstance(switch, ToggleSwitch)
    label = switch.text()
    for on in (True, False, True):
        ctx.state.plot.use_fov_replicates = on
        refresh_fov_btn_state(ctx)
        assert switch.isChecked() is on
    assert switch.text() == label, "the switch wrote its state into its label"


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
