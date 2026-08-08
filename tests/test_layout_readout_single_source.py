"""The Analyze layout readout quotes the plan; it never writes its own line.

Analyze answers "what will this folder do?" twice — under the input field the
moment a folder is picked, and again at Run — and for a long time those were two
implementations. They disagreed on what counts as well content: the readout
accepted any ``*.zip`` in ``in/``, the resolver required one named for a 96-well
position. A dataset holding ``in/Plate1_A1.zip`` therefore displayed
"✓ Found in/ subfolder — using as input" and then failed the run outright with
"Cannot determine input layout" — a message that listed, as an unmet
requirement, the exact condition the folder satisfied.

``services.input_resolution_service.plan_input_layout`` is now the single
decision, and ``AnalyzeTab._refresh_output`` renders ``plan.message``. That
holds only while the label is written from a value rather than from a literal:
the moment a branch in the GUI composes its own sentence about the layout, the
GUI has an opinion about the layout again, and the two can drift apart exactly
as before.

So the rule is about where the *text* comes from. A name or an attribute is
fine — it was computed elsewhere. A hand-written string is not. The one
exception is the empty string, which clears the label and asserts nothing.
"""

from __future__ import annotations

import ast

from tests._repo_scan import python_sources, rel

#: The label under the Analyze input field, whose text is the layout verdict.
LAYOUT_LABEL = "_layout_lbl"


def _is_quoted_value(node: ast.AST) -> bool:
    """True if *node* is text computed elsewhere rather than composed here.

    ``plan.message`` and a bare name pass. An f-string does not, even one that
    only interpolates: ``f"⚠ {ome_hint}"`` is still a sentence written in the
    GUI, and the ``⚠``/``✓`` prefix is precisely the part that made a promise
    the run then broke.
    """
    if isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)):
        return True
    if isinstance(node, ast.Call):
        # `str(plan.output_dir)` and friends — a conversion, not a composition.
        return True
    if isinstance(node, ast.Constant) and node.value == "":
        return True
    if isinstance(node, ast.IfExp):
        return _is_quoted_value(node.body) and _is_quoted_value(node.orelse)
    return False


def _rendered(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - unparse covers every literal shape
        return "<unrenderable>"


def _layout_label_writes(tree: ast.AST):
    """Yield every ``…._layout_lbl.setText(arg)`` call in *tree*."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "setText"):
            continue
        owner = fn.value
        if isinstance(owner, ast.Attribute) and owner.attr == LAYOUT_LABEL:
            yield node


def test_the_layout_readout_is_not_written_in_the_gui():
    offenders: list[str] = []
    for path in python_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for call in _layout_label_writes(tree):
            arg = call.args[0] if call.args else None
            if arg is None or _is_quoted_value(arg):
                continue
            offenders.append(
                f"{rel(path)}:{call.lineno} — {LAYOUT_LABEL}.setText("
                f"{_rendered(arg)})")

    assert not offenders, (
        "The Analyze layout readout must render the value "
        "`services.input_resolution_service.plan_input_layout` returned, not a "
        "sentence composed here — a second opinion about the layout is how the "
        "readout came to promise a run that then failed. Move the wording into "
        "the plan's `message` and display it.\n  "
        + "\n  ".join(offenders)
    )
