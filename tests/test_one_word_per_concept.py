"""One word per concept: nothing user-facing calls a saved selection a "group".

The rail's drawer under the plate map saves a set of lit wells you can get back.
It pools nothing — loading one lights wells and toggles which groups are shown,
and it never edits a group's membership. Home's **Groups** are replicate sets
and they pool everything: every plot, the Statistics tab, Batch Export and the
saved fold-change control references all key on them.

Those are different objects with different stores, different lifetimes and
different consequences, and both were called "group". The manual had to spend a
paragraph disambiguating them ("Groups are *plotting selections only* — not
replicate sets or stats groups"), which is a name telling you it is wrong. The
drawer is **Saved selections** now.

**The rule stops at the string literals, deliberately.** The symbols and the
on-disk key still say ``plot_group``: ``build_plot_groups_drawer``,
``_wells_for_new_plot_group``, ``plot_group_visibility_plan``, and
``persistence.json::plot_groups`` with its mirror inside the
``sample_definitions`` block. Renaming a persistence section is a migration with
a compatibility window and no user has ever read that key, so the cost is real
and the benefit is zero. What a user reads is what had to change.

Two things this guard is careful about, because both were live in the tree when
it was written:

* **"a plot grouped by Dose" is not this feature.** It is a plot pooled by a
  sample variable, and the phrase appears in
  ``features/home/variables/controller.py`` and two persistence docstrings. The
  pattern therefore ends at a word boundary after ``group``/``groups``, which
  ``grouped`` does not satisfy.
* **Docstrings are not user-facing.** A module docstring explaining the
  ``plot_groups`` section by name is the documentation the exemption above
  exists for. Only non-docstring literals are read.
"""

from __future__ import annotations

import ast
import re

from tests._repo_scan import REPO_ROOT, parse_source, python_sources, rel

#: The feature name as a user would read it. ``groups?`` ends at a word
#: boundary so "a plot grouped by Dose" — a different concept entirely — does
#: not match, and ``\s+`` requires the space so the ``plot_groups`` symbol and
#: persistence key do not either.
_BANNED = re.compile(r"\bplot\s+groups?\b", re.I)

#: The two live documents. `GOD_OBJECT_REPOSITORY_ROADMAP.md` is a ratchet
#: tracker of module sizes and names no user-facing vocabulary.
_DOCS = ("_Docs/Markdowns/README_WellViewer.md",
         "_Docs/Markdowns/ARCHITECTURE.md")

#: ``(file, function)`` sites entitled to the old spelling, each with the reason
#: written here **and** at the site. Empty: the sweep left none, and an entry
#: added to make a failure go away is indistinguishable from one that is
#: entitled, which is why the pair is required rather than a bare file name.
EXEMPT: frozenset[tuple[str, str]] = frozenset()


def _docstrings(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            text = ast.get_docstring(node, clean=False)
            if text:
                out.add(text)
    return out


def _enclosing_functions(tree: ast.Module) -> dict[int, str]:
    """``line -> the innermost function that line is in``, module scope aside."""
    out: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            for line in range(node.lineno, end + 1):
                out[line] = node.name
    return out


def _offending_literals() -> list[str]:
    out: list[str] = []
    for path in python_sources():
        tree = parse_source(path)
        docs = _docstrings(tree)
        where = _enclosing_functions(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)):
                continue
            if node.value in docs or not _BANNED.search(node.value):
                continue
            name = rel(path)
            if (name, where.get(node.lineno, "<module>")) in EXEMPT:
                continue
            out.append(f"{name}:{node.lineno} {node.value[:90]!r}")
    return out


def test_no_user_facing_string_calls_a_saved_selection_a_plot_group():
    offenders = _offending_literals()
    assert not offenders, (
        "these strings still call a saved selection a \"plot group\":\n  "
        + "\n  ".join(offenders)
        + "\nThe drawer under the plate map is \"Saved selections\"; Home's "
          "Groups are replicate sets and pool. Rename the string — the "
          "`plot_group` symbols and the persistence key stay."
    )


def test_the_live_docs_use_one_word_for_each():
    offenders = []
    for name in _DOCS:
        path = REPO_ROOT / name
        if not path.exists():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _BANNED.search(line):
                offenders.append(f"{name}:{number} {line.strip()[:90]!r}")
    assert not offenders, (
        "these documentation lines still say \"plot group\":\n  "
        + "\n  ".join(offenders)
        + "\nSay \"saved selection\" for the rail drawer and keep \"group\" for "
          "Home's replicate sets."
    )


def test_every_exemption_names_a_live_site():
    """An exemption that no longer describes a real violation is a dead rule.

    Empty today. The test exists rather than the roster being trusted, because
    a roster nothing checks is how a file-level exemption came to cover every
    colour literal in a file whose one painter needed one.
    """
    if not EXEMPT:
        return
    live = {tuple(entry.split(":")[0:1]) for entry in _offending_literals()}
    assert live, "exemptions are listed but nothing violates the rule"

#: The four ways a user can say something about a well, and the phrase each
#: entry has to carry so the entry states *what it reaches* rather than only
#: what it is. A definition that does not say whether the thing pools is the
#: definition that left four mechanisms indistinguishable in the first place.
CONCEPTS = (
    ("Group", "pool"),
    ("Sample variable", "pool"),
    ("Well label", "Display only"),
    ("Saved selection", "pools nothing"),
)

#: The README says the same four things in the user's vocabulary rather than
#: the model's, so its table is keyed on the route a user clicks — which is the
#: **layer** of the Sample map tab, not a tab of its own. Written out in full
#: because a guard naming only the layer would go on passing after the merge had
#: left the manual routing readers to three tabs that no longer exist.
README_CONCEPTS = ("Home \u2192 Sample map \u2192 Groups",
                   "Home \u2192 Sample map \u2192 Variables",
                   "Home \u2192 Sample map \u2192 Well Labels",
                   "the rail drawer under the plate map")


def test_the_architecture_glossary_defines_each_concept_and_what_it_reaches():
    text = (REPO_ROOT / "_Docs" / "Markdowns" / "ARCHITECTURE.md").read_text(
        encoding="utf-8")
    rows = {line.split("|")[1].strip().strip("*"): line
            for line in text.splitlines()
            if line.startswith("| **") and line.count("|") >= 3}
    missing = [name for name, _ in CONCEPTS if name not in rows]
    assert not missing, (
        f"the glossary defines no {missing} — a concept a reader meets in the "
        "UI and cannot look up is how four mechanisms became indistinguishable"
    )
    silent = [name for name, effect in CONCEPTS
              if effect.lower() not in rows[name].lower()]
    assert not silent, (
        f"these glossary entries never say what they reach: {silent}. Each has "
        "to state whether it pools, because that is the difference between them."
    )


def test_the_manual_answers_which_one_do_i_use():
    text = (REPO_ROOT / "_Docs" / "Markdowns" / "README_WellViewer.md").read_text(
        encoding="utf-8")
    assert "Which one do I use?" in text, (
        "the manual has no block answering which of the four to use; the tabs "
        "are described one after another with nothing comparing them"
    )
    missing = [name for name in README_CONCEPTS if name not in text]
    assert not missing, (
        f"the manual's comparison omits {missing} \u2014 a table of three of the "
        "four still leaves the reader guessing about the fourth"
    )
