"""Every font size in a stylesheet string is written from the type tokens.

Phone layout (``ui/phone_shell.py``) scales ``theme.Typography`` at runtime and
then rescales each widget's own stylesheet in place, multiplying every
``font-size`` it finds. That is exact only if every size in a sheet came from a
token at the scale in force when the sheet was written: the pass can then
recover the desk size by dividing by that scale. A literal ``font-size: 11px``
breaks it both ways — a widget built at the desk is scaled correctly, but one
first built *in* phone layout writes 11 px under a 1.75 scale, and leaving
phone layout then divides it down to 6.

So no string in the application may spell a font size as a number. The
Image Table builder shelf carried eighteen, two of them half pixels
(``12.5px``) that a whole-number pattern walks past, which is why the pattern
here takes decimals.

An exemption is a site — file and enclosing function — with its reason, never
a whole file.
"""

from __future__ import annotations

import ast
import re

from tests._repo_scan import parse_source, python_sources, rel

#: ``font-size: 11px``, ``font-size:12.5px``, ``font: bold 9pt "Menlo"``.
_LITERAL_FONT_SIZE = re.compile(
    r"font(?:-size)?\s*:[^;{}]*?(?<![\w{$.])\d+(?:\.\d+)?\s*(?:px|pt)\b")

#: (file, enclosing function) → why a literal size is right there.
EXEMPT = {
    ("allwell.py", "_toggle_help_drawer"):
        "The help manual's QTextDocument default stylesheet, which styles "
        "rich text inside one document rather than a widget. Phone layout's "
        "rescale pass reads widget stylesheets only and never sees it, so a "
        "literal here cannot be mis-scaled.",
}


def _docstring_nodes(tree: ast.Module) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def _functions(tree: ast.Module):
    """(node, enclosing function name) for every string constant."""
    def visit(node, fn):
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                yield child, fn
            yield from visit(child, name)
    yield from visit(tree, "<module>")


def literal_font_sizes(files) -> list[tuple[str, int, str, str]]:
    found = []
    for path in files:
        tree = parse_source(path)
        docs = _docstring_nodes(tree)
        for node, fn in _functions(tree):
            if id(node) in docs:
                continue
            m = _LITERAL_FONT_SIZE.search(node.value)
            if m:
                found.append((rel(path), node.lineno, fn, m.group(0)))
    return found


def test_no_literal_font_size_in_a_stylesheet():
    found = [f for f in literal_font_sizes(python_sources())
             if (f[0], f[2]) not in EXEMPT]
    assert not found, (
        "font sizes written as numbers — write them from theme.Typography so "
        "phone layout's rescale pass can scale them and scale them back:\n  "
        + "\n  ".join(f"{p}:{line} in {fn}(): {text!r}" for p, line, fn, text in found))


def test_every_exemption_still_describes_a_literal():
    """An exemption whose site no longer holds a literal is a hole waiting."""
    sites = {(f[0], f[2]) for f in literal_font_sizes(python_sources())}
    stale = sorted(set(EXEMPT) - sites)
    assert not stale, f"exemptions with no literal font size left at the site: {stale}"
