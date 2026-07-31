"""Guardrail: a tab that has adopted the shared control band never goes back.

The Review pane's defining problem was that every tab invented its own control
layout — Scatter stacked four rows including one of permanently-greyed controls,
Heat Map used three plus a separator, Statistics replaced the row with a
five-row form. Tabs are being moved onto ``widgets/control_band.ControlBand``
one at a time, and the failure mode is not a bad band; it is a later change
quietly reintroducing a bespoke row beside it.

This is an AST scan, deliberately, so it runs in the **fast lane**. Anything
that builds a real tab needs the ``viewer_app`` fixture, which ``conftest.py``
auto-marks ``slow``, and ``scripts/test.sh`` runs ``-m "not slow"`` — a test
that constructs a tab to check its layout would never run in CI at all.

``ADOPTED`` is a ratchet: modules are added as they adopt the band and never
removed. ``test_adoption_list_only_grows`` fails if an entry goes stale, which
is what would happen if a tab were reverted to its own row.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Modules that build their tab's controls through the shared band. Add a module
#: here in the same commit that adopts the band in it; never remove one.
ADOPTED = (
    "well_viewer/core/centre_view.py",          # Plotting — all five sub-tabs
)


def _tree(rel: str) -> ast.AST:
    return ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))


def _calls(tree: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def _call_names(tree: ast.AST) -> list[str]:
    """Every call's callee spelled as written — ``Foo`` or ``mod.Foo``."""
    out = []
    for call in _calls(tree):
        fn = call.func
        if isinstance(fn, ast.Name):
            out.append(fn.id)
        elif isinstance(fn, ast.Attribute):
            out.append(fn.attr)
    return out


def test_adopted_modules_build_exactly_one_band():
    """One band per module — a second one is a bespoke row wearing the name."""
    for rel in ADOPTED:
        names = _call_names(_tree(rel))
        count = names.count("ControlBand")
        assert count == 1, (
            f"{rel} constructs ControlBand {count} times; a tab has one control "
            "band, and a second row of controls beside it is the layout drift "
            "the band exists to stop."
        )


def test_adopted_modules_have_no_bespoke_control_row():
    """No hand-rolled flow row left over beside the band.

    ``FlowLayout`` is how the pre-band control rows wrapped, and it is what the
    band uses internally. A direct construction in an adopted module means a
    second control row was built by hand rather than through ``add_scope``.
    """
    for rel in ADOPTED:
        names = _call_names(_tree(rel))
        assert "FlowLayout" not in names, (
            f"{rel} builds a FlowLayout directly. The band owns the wrapping "
            "control row — add controls with ControlBand.add_scope() instead."
        )


def test_adopted_modules_set_no_plot_card_headers():
    """The plot-card header is created and then hidden in eight tabs. A module
    on the band has no reason to build one."""
    for rel in ADOPTED:
        for call in _calls(_tree(rel)):
            fn = call.func
            if isinstance(fn, ast.Attribute) and fn.attr == "setHeaderVisible":
                arg = call.args[0] if call.args else None
                is_false = isinstance(arg, ast.Constant) and arg.value is False
                assert not is_false, (
                    f"{rel}:{call.lineno} builds a plot-card header only to hide "
                    "it; drop the header instead."
                )


def test_adoption_list_only_grows():
    """Every ADOPTED entry still exists and still uses the band.

    Without this the ratchet is decorative: a tab could be reverted to its own
    control row and the tests above would pass vacuously on a module that no
    longer mentions ControlBand.
    """
    for rel in ADOPTED:
        path = REPO_ROOT / rel
        assert path.is_file(), (
            f"{rel} is in ADOPTED but does not exist. If the module moved, "
            "update the entry; do not delete it."
        )
        assert "ControlBand" in _call_names(_tree(rel)), (
            f"{rel} is listed as having adopted the shared control band but no "
            "longer constructs one."
        )
