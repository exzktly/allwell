"""What a user reads in the application spells it "Color" (design language §3.5, row D13).

The tracker's decision: one spelling in everything the interface shows — a label, a button, a menu, a tooltip, a
status line, a dialog. The code's identifiers, its comments and the documents keep whatever spelling they have;
the rule is about the interface. A site is "colour" in any form (Colour, colours, coloured, colouring) inside a
string handed to a widget's text or tooltip — the calls the sentence-case guard reads, the helpers that pass a
label on, and the tooltip, status, title and dialog calls — or held in a module constant whose name says it is
words for the screen (``…_TIP``, ``…_TOOLTIP``, ``…_HELP``, ``…_TEXT``). A string with no space and an underscore
in it is a key, not words, and is not read.
"""

from __future__ import annotations

import ast
import re

from tests import test_labels_are_sentence_case as _labels
from tests._design_scan import call_name, count_sites
from tests._repo_scan import parse_source, python_sources, rel

_COLOUR = re.compile(r"colour", re.IGNORECASE)
_SHOWN = {"setToolTip", "setStatusTip", "setWhatsThis", "addItem", "addItems", "insertItem", "setItemText",
          "setPlaceholderText", "setWindowTitle", "setTitle", "addTab", "setTabText", "setTabToolTip",
          "information", "warning", "critical", "question", "set_status", "showMessage"}
_WORDS_CONSTANT = re.compile(r"(_TIP|_TOOLTIP|_HELP|_TEXT)S?$")


def _is_words(value: str) -> bool:
    return bool(_COLOUR.search(value)) and not (" " not in value and "_" in value)


def find(_path: str, tree: ast.Module):
    takers = _SHOWN | set(_labels.TEXT_ARG) | set(_labels.HELPERS)
    for node in ast.walk(tree):
        roots = []
        if isinstance(node, ast.Call) and call_name(node) in takers:
            roots = list(node.args) + [k.value for k in node.keywords]
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and _WORDS_CONSTANT.search(t.id) for t in node.targets):
            roots = [node.value]
        for root in roots:
            for n in ast.walk(root):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and _is_words(n.value):
                    yield n


def _seen():
    sources = list(python_sources())
    _labels.HELPERS.clear()
    _labels.HELPERS.update(_labels.text_helpers(parse_source(path) for path in sources))
    return count_sites(find, sources, rel)


def test_the_interface_spells_it_color():
    seen = _seen()
    assert not seen, ('write "color" in what the interface shows (§3.5):\n  '
                      + "\n  ".join(f"{path}: {n}" for path, n in sorted(seen.items())))


def test_a_key_is_not_words():
    assert _is_words("Colour map") and _is_words("the colours") and not _is_words("colour_limits")
