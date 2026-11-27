"""What a user reads on a control is written one way: sentence case, no colon, a real ellipsis, no text glyphs.

The design language's seventh rule (``_Docs/Markdowns/DESIGN_LANGUAGE_TRACKER.md`` §2.2, §3.6): sentence-style
capitals (Microsoft's Windows guidance, which the user chose over Apple's title style), no colon after a label
(the position says it labels the field), "…" rather than "...", icons from the drawn set rather than ✎ ✕ ▶ ⇄ ■ ⏸ ＋,
and no "+ " or "▶ " in front of a command. Measured: 135 labels in sentence case, 24 in Title Case and 15 mixed.

A site is a string literal handed as the text of a button, checkbox, radio button, label, group box, menu action,
band scope or form row, that breaks any of those. A word counts as a capital in the middle of a label unless it is
an acronym or on :data:`PROPER` — product, file-format and statistical-method names, and the three panes, which
are names. The roster (``tests/_design_rosters.SENTENCE_CASE``) holds today's sites per file and only shrinks; row
D13 empties it.
"""

from __future__ import annotations

import ast
import re

from tests._design_rosters import SENTENCE_CASE as ROSTER
from tests._design_scan import call_name, count_sites, over_roster, stale_roster, strings_in
from tests._repo_scan import parse_source, python_sources, rel

#: Call → which positional argument is the text a user reads.
TEXT_ARG = {
    "QPushButton": 0, "QCheckBox": 0, "QRadioButton": 0, "QLabel": 0, "QGroupBox": 0, "QAction": 0,
    "setText": 0, "setTitle": 0, "addAction": 0, "setPlaceholderText": None,
    "btn_primary": 1, "btn_secondary": 1, "btn_card": 1, "btn_danger": 1,
    "button": 0, "primary": 0, "default": 0, "quiet": 0, "destructive": 0,
    "page_title": 0, "section_heading": 0, "field_label": 0, "add_scope": 0, "add_row": 0, "LabelledField": 0,
}

PROPER = {
    "Review", "Analyze", "Datasets", "All-Well", "StarDist", "Cellpose", "OME-TIFF", "TIFF", "PowerPoint",
    "Excel", "NIS-Elements", "Nikon", "Zeiss", "ImageJ", "Fiji", "Slurm", "Python", "Windows", "macOS", "Mac",
    "Linux", "Gaussian", "Otsu", "Poisson", "Welch", "Mann-Whitney", "Kruskal-Wallis", "Dunn", "Tukey",
    "Bonferroni", "Holm", "Benjamini-Hochberg", "Wilcoxon", "Pearson", "Spearman", "Hill", "Ripley", "Moran",
    "Student", "Šidák", "Ctrl", "Shift", "Cmd", "Esc", "Enter", "Tab",
    # The app's single-word sections, which the sidebar names: a name, wherever a sentence mentions it.
    "Home", "Plotting", "Statistics", "Multivariate", "Spatial", "Nanowells", "smFISH",
}
GLYPHS = set("✎✕▶⇄■⏸＋↻✓✗⚠ℹ")


def _page_names() -> tuple[str, ...]:
    """The names of the app's pages and tools, read from the two registries that declare them: a sentence that
    sends a reader to "the Scatter Plot tab" or "Merge Plates" names a place, so it keeps the place's spelling.
    Those names are keys (restore state, help topics, manual headings) and are not this rule's to change."""
    from tests._repo_scan import REPO_ROOT, parse_source
    names = {"Job Queue", "Recipes", "Runs"}  # the Automation page's own tabs, built in datasets/tab.py
    for path, call in (("review/features/registry.py", "FeatureDescriptor"), ("datasets/tab.py", "_Feature")):
        for node in ast.walk(parse_source(REPO_ROOT / path)):
            if isinstance(node, ast.Call) and call_name(node) == call:
                for kw in node.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant) and str(kw.value.value)[:1].isupper():
                        names.add(kw.value.value)
    return tuple(sorted(names, key=len, reverse=True))


PAGE_NAMES = _page_names()
_WORD = re.compile(r"[A-Za-zŠ][A-Za-z'’\-Šš]*")


#: Characters standing in for an icon when they are the whole of a label: ✕ for remove, − + for a step, ◀ ▲ ▼ for
#: a direction, ⋯ for more. A ▾ or ▸ alone is a disclosure caret, which §7.12 of CLAUDE.md allows.
ICON_GLYPHS = GLYPHS | set("×◀▲▼⋯−+")


def breaches(text: str) -> list[str]:
    """What is wrong with *text* as a label: an empty list when nothing is."""
    if not any(ch.isalpha() for ch in text):
        return ["glyph"] if text.strip() and ICON_GLYPHS & set(text) else []
    out = []
    stripped = text.strip()
    if stripped.endswith(":"):
        out.append("colon")
    if "..." in text:
        out.append("three dots")
    if GLYPHS & set(text):
        out.append("glyph")
    if stripped.startswith(("+ ", "▶ ")):
        out.append("leading mark")
    # Title Case: a capitalised word after the first, unless it starts a sentence or is a name. Markup is read the
    # way a reader sees it: an opening bold, italic or code tag quotes what follows, any other tag is a space.
    stripped = re.sub(r"<(b|i|tt)>", '"', stripped)
    stripped = re.sub(r"<[^>]*>", " ", stripped)
    for name in PAGE_NAMES:
        stripped = re.sub(rf"\b{re.escape(name)}\b", "Review", stripped)
    words = list(_WORD.finditer(stripped))
    for prev, word in zip(words, words[1:]):
        w = word.group()
        between = stripped[prev.end():word.start()]
        stem = re.sub(r"['’]s$", "", w)
        if stem.split("-")[0] in PROPER:  # Shift-click, Mann-Whitney's
            continue
        if (len(stem) > 1 and stem[0].isupper() and not stem.isupper() and stem not in PROPER
                # A capital past the first letter is an acronym or a product's own spelling: FOVs, TensorFlow.
                and not any(ch.isupper() for ch in stem[1:])
                and not re.search(r"[.!?]\s*$", between)
                # A breadcrumb — Analyze → Advanced — names the place each arrow leads to.
                and not re.search(r"[→▸]\s*$", between)
                # The first word of a label quoted in prose (in bold, or in quotes) keeps the label's capital.
                and not re.search(r"(<b>|<i>|<tt>|[\"“‘'])\s*$", between)):
            out.append("title case")
            break
    return out


def text_helpers(trees) -> dict[str, tuple[int, str]]:
    """The app's own helpers that hand one of their parameters on as a label — ``_add_bold_label(row, text)``,
    ``self._row(section, label)`` — by name, with that parameter's position at the call (``self`` not counted).
    A name defined more than once with the text in different positions is left out rather than guessed at."""
    seen: dict[str, set[tuple[int, str]]] = {}
    for tree in trees:
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef) or fn.name in TEXT_ARG:
                continue
            params = [a.arg for a in fn.args.args]
            shift = 1 if params[:1] in (["self"], ["cls"]) else 0
            for call in ast.walk(fn):
                at = TEXT_ARG.get(call_name(call)) if isinstance(call, ast.Call) else None
                if (at is not None and len(call.args) > at and isinstance(call.args[at], ast.Name)
                        and call.args[at].id in params):
                    seen.setdefault(fn.name, set()).add((params.index(call.args[at].id) - shift, call_name(call)))
    return {name: next(iter(at)) for name, at in seen.items() if len(at) == 1 and next(iter(at))[0] >= 0}


#: Filled by :func:`_seen` from the files it reads, so a synthetic file read alone carries its own helpers: each
#: name's text position and the call it hands the text to.
HELPERS: dict[str, tuple[int, str]] = {}

#: Where a lone sign is text and not a stand-in for an icon: a label's "×" between two fields, an equation's "+".
_TEXT_TAKERS = {"QLabel", "field_label", "help_text", "body", "number"}


def _constants(tree: ast.Module) -> dict[str, ast.Constant]:
    """The module's and its classes' string constants by name — ``_RUN_LABEL = "Subset"`` — so a label handed over
    by name is read like one written in place."""
    found: dict[str, ast.Constant] = {}
    bodies = [tree.body] + [n.body for n in tree.body if isinstance(n, ast.ClassDef)]
    for body in bodies:
        for node in body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
                found[node.targets[0].id] = node.value
    return found


def _defaults(tree: ast.Module) -> list[ast.Constant]:
    """A parameter's default string where the parameter is handed on as a label in the same function:
    ``def _build_run_button(self, layout, *, button_text="Run"):`` … ``buttons.primary(button_text)``."""
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        args = fn.args.args[len(fn.args.args) - len(fn.args.defaults):] + fn.args.kwonlyargs
        defaults = list(fn.args.defaults) + list(fn.args.kw_defaults)
        named = {a.arg: d for a, d in zip(args, defaults)
                 if isinstance(d, ast.Constant) and isinstance(d.value, str)}
        if not named:
            continue
        for call in ast.walk(fn):
            at = TEXT_ARG.get(call_name(call)) if isinstance(call, ast.Call) else None
            if at is not None and len(call.args) > at and isinstance(call.args[at], ast.Name):
                if call.args[at].id in named:
                    out.append(named[call.args[at].id])
    return out


def find(_path: str, tree: ast.Module):
    constants = _constants(tree)
    for d in _defaults(tree):
        if breaches(d.value):
            yield d
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        name = call_name(call)
        at, taker = (TEXT_ARG[name], name) if name in TEXT_ARG else HELPERS.get(name, (None, ""))
        if at is None or len(call.args) <= at:
            continue
        named = [constants[n.id] for n in ast.walk(call.args[at]) if isinstance(n, ast.Name) and n.id in constants]
        for s in strings_in(call.args[at]) + named:
            found = breaches(s.value)
            if found and not (taker in _TEXT_TAKERS and not any(ch.isalpha() for ch in s.value)):
                yield s
        # An f-string's words are not known until it runs, but its marks are: "✓ {n} saved" carries a glyph.
        for f in (n for n in ast.walk(call.args[at]) if isinstance(n, ast.JoinedStr)):
            if any(isinstance(v, ast.Constant) and GLYPHS & set(str(v.value)) for v in f.values):
                yield f


def _seen():
    sources = list(python_sources())
    HELPERS.clear()
    HELPERS.update(text_helpers(parse_source(path) for path in sources))
    return count_sites(find, sources, rel)


def test_labels_are_written_in_sentence_case_without_colons_or_glyphs():
    bad = over_roster(_seen(), ROSTER)
    assert not bad, ("a label in Title Case, with a colon, three dots, a text glyph or a leading mark — write it "
                     "in sentence case, without a colon, with … and an icon from widgets/icons.py:\n  "
                     + "\n  ".join(bad))


def test_the_roster_only_describes_sites_that_remain():
    stale = stale_roster(_seen(), ROSTER)
    assert not stale, "lower these roster entries:\n  " + "\n  ".join(stale)


def test_the_rule_reads_labels_the_way_a_reader_does():
    assert breaches("Mean Intensity") == ["title case"]
    assert breaches("Folder:") == ["colon"]
    assert breaches("Browse...") == ["three dots"]
    assert breaches("+ Add group") == ["leading mark"]
    assert breaches("▶ Run Pipeline") == ["glyph", "leading mark", "title case"]
    assert breaches("✓ Saved") == ["glyph"]
    assert breaches("✕") == ["glyph"] and breaches("−") == ["glyph"]
    assert breaches("▾") == [] and breaches("1 / 4") == []
    for fine in ("Mean intensity", "Open in Review", "StarDist, nuclei", "Browse…", "GFP", "Run pipeline",
                 "No groups yet. Add one to start.", "All FOVs", "the CZI's channel names", "Analyze's tokens",
                 "TensorFlow takes a while", "Shift-click to add", "The <b>Background well</b> field follows it",
                 "Draw one on the Scatter Plot tab", "Open it in Merge Plates", "Show in Segmentation",
                 "Define one on Home → Variables"):
        assert breaches(fine) == [], fine
