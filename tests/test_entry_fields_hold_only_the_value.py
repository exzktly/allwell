"""An entry field holds only its value: no unit, prefix or placeholder word inside the box.

Qt's ``QSpinBox`` / ``QDoubleSpinBox`` refuse typing and Backspace while the cursor sits inside a suffix or a
prefix, and over a special-value text ("Off", "Wait indefinitely") every keystroke is ignored. A click on the
text lands the cursor there, so the value the user types is silently dropped. The rim focus box on the Nanowells
band (" % of sharp") and Cell Gating's nanowell focus box (" %") both shipped that way and were reported as
"cannot type in the desired percentage".

So the unit, and what a sentinel value means, go in the label beside the field ("Cache budget (GB)",
"Moving average (timepoints, 1 = off)"), and the box shows the number alone. This guard fails on any call to
``setSuffix`` / ``setPrefix`` / ``setSpecialValueText`` with anything but an empty string, and on a ``Stepper``
built with a ``suffix=`` / ``prefix=`` keyword. Clearing an affix (``setSuffix("")``) is allowed: it is how
``review/core/channel_state_controller.py`` keeps its stepper bare.
"""

from __future__ import annotations

import ast

from tests._repo_scan import parse_source, python_sources, rel

AFFIX_SETTERS = frozenset({"setSuffix", "setPrefix", "setSpecialValueText"})
AFFIX_KEYWORDS = frozenset({"suffix", "prefix", "special_value_text"})
STEPPER_NAMES = frozenset({"Stepper", "_Stepper"})


def _is_empty_string(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == ""


def _callee_name(func: ast.AST) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def violations(tree: ast.AST) -> list[tuple[int, str]]:
    """``(line, what)`` for each call that puts text other than the value inside an entry field."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee_name(node.func)
        if name in AFFIX_SETTERS and isinstance(node.func, ast.Attribute):
            if not (len(node.args) == 1 and _is_empty_string(node.args[0])):
                found.append((node.lineno, f"{name}(...)"))
        elif name in STEPPER_NAMES:
            for kw in node.keywords:
                if kw.arg in AFFIX_KEYWORDS and not _is_empty_string(kw.value):
                    found.append((node.lineno, f"{name}({kw.arg}=...)"))
    return found


def test_no_entry_field_carries_a_unit_or_placeholder_word():
    sites = []
    for path in python_sources():
        for line, what in violations(parse_source(path)):
            sites.append(f"{rel(path)}:{line}: {what}")
    assert not sites, (
        "An entry field holds text besides its value, which Qt refuses to type over — put the unit, or what the "
        "sentinel value means, in the label beside it instead:\n  " + "\n  ".join(sites))
