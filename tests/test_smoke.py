"""The ``--smoke`` self-test, as a CI guardrail.

Builds every Review tab and resolves every lazy import proxy. Catches a tab that
stops building and a mis-wired ``core/lazy.py`` proxy (typo'd / moved target) —
the refactor failure classes that only surface on first click in the real GUI.
"""

from __future__ import annotations

import pytest


@pytest.mark.slow
def test_smoke_self_test_passes(qtbot):
    from well_viewer.core.smoke import run_smoke
    from well_viewer.runtime_app import WellViewerApp
    app = WellViewerApp()
    qtbot.addWidget(app)
    assert run_smoke(app) == 0


def test_verify_lazy_imports_detects_broken_proxy():
    """The verifier must actually fail on a bad proxy (not silently pass)."""
    from well_viewer.core import lazy
    lazy.lazy("well_viewer.features.scatter.controller", "nope_not_a_real_symbol")
    bad = [(t, err) for t, ok, err in lazy.verify_lazy_imports() if not ok]
    assert any("nope_not_a_real_symbol" in t for t, _ in bad)
