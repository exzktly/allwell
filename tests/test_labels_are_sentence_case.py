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
from tests._repo_scan import python_sources, rel

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
GLYPHS = set("✎✕▶⇄■⏸＋")
_WORD = re.compile(r"[A-Za-zŠ][A-Za-z'’\-Šš]*")


def breaches(text: str) -> list[str]:
    """What is wrong with *text* as a label: an empty list when nothing is."""
    if not any(ch.isalpha() for ch in text):
        return []
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
    words = list(_WORD.finditer(stripped))
    for prev, word in zip(words, words[1:]):
        w = word.group()
        between = stripped[prev.end():word.start()]
        stem = re.sub(r"['’]s$", "", w)
        if (len(stem) > 1 and stem[0].isupper() and not stem.isupper() and stem not in PROPER
                # A capital past the first letter is an acronym or a product's own spelling: FOVs, TensorFlow.
                and not any(ch.isupper() for ch in stem[1:])
                and not re.search(r"[.!?]\s*$", between)
                # The first word of a label quoted in prose (in bold, or in quotes) keeps the label's capital.
                and not re.search(r"(<b>|<i>|<tt>|[\"“‘'])\s*$", between)):
            out.append("title case")
            break
    return out


def find(_path: str, tree: ast.Module):
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        at = TEXT_ARG.get(call_name(call))
        if at is None or len(call.args) <= at:
            continue
        for s in strings_in(call.args[at]):
            if breaches(s.value):
                yield s


def _seen():
    return count_sites(find, python_sources(), rel)


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
    for fine in ("Mean intensity", "Open in Review", "StarDist, nuclei", "Browse…", "GFP", "Run pipeline",
                 "No groups yet. Add one to start.", "All FOVs", "the CZI's channel names", "Analyze's tokens",
                 "TensorFlow takes a while", "The <b>Background well</b> field follows it"):
        assert breaches(fine) == [], fine
