"""The in-app manual is checked as it *renders*, not as it is written.

``tests/test_help_topics.py`` matches every help topic against the headings in
``README_WellViewer.md``. That is the right check for the topic map and the
wrong one for the drawer: the drawer shows what ``QTextDocument.setMarkdown``
produced, and Qt's markdown parser reads a bare ``<date>`` as an unclosed HTML
tag and silently discards everything after it. Two of those sat in the manual,
so the render stopped a third of the way through, ``_render_manual_html``
injected anchors for the headings that survived, and help opened from
twenty-nine of the forty-one pages landed nowhere — on a manual that also
stopped at "Home".

Nothing could see it, because every existing check reads the markdown source.
"""

from __future__ import annotations

import os
import pathlib
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MANUAL = REPO_ROOT / "_Docs" / "Markdowns" / "README_WellViewer.md"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def rendered(qapp):
    import allwell
    return allwell._render_manual_html(MANUAL.read_text(encoding="utf-8"))


def _markdown_headings() -> list[str]:
    text = MANUAL.read_text(encoding="utf-8")
    # Skip fenced code blocks: a `#` inside one is a comment, not a heading.
    out, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            m = re.match(r"^#{1,6}\s+(.*)$", line)
            if m:
                out.append(m.group(1).strip())
    return out


def _anchors(html: str) -> set[str]:
    return set(re.findall(r'<a name="([^"]+)"', html))


def test_every_heading_survives_the_render(rendered):
    """A heading missing from the render is a chapter of the manual the user
    cannot reach, and the cause is always something earlier in the file that
    Qt's parser stopped on."""
    import allwell

    headings = _markdown_headings()
    anchors = _anchors(rendered)
    missing = [h for h in headings
               if allwell._gh_slug(re.sub(r"[*`_]", "", h)) not in anchors]
    assert not missing, (
        f"{len(missing)} of {len(headings)} headings are absent from the "
        "rendered manual — everything after the first one is being discarded "
        f"by QTextDocument.setMarkdown:\n  " + "\n  ".join(missing[:10]))


def test_every_help_target_has_an_anchor_to_scroll_to(rendered):
    """``_scroll_help_to_current_section`` calls ``scrollToAnchor(_gh_slug(target))``.
    An anchor that is not in the document is not an error — the browser simply
    does not move — so a broken target looks exactly like help that opened at
    the top on purpose."""
    import allwell
    from ui import help_topics

    anchors = _anchors(rendered)
    targets: set[str] = set()
    for name in ("ANALYZE_SECTION_HELP", "DATASETS_PAGE_HELP",
                 "MODE_OVERVIEW", "SECTION_BY_PAGE"):
        table = getattr(help_topics, name)
        if isinstance(table, dict):
            targets.update(str(v) for v in table.values())
    # The anchor. Without it, emptying the four tables — or renaming them to
    # something ``getattr`` still finds — would make this test pass on zero
    # targets, which is how a scan-based guard stops measuring anything without
    # ever going red. (A *rename* is already loud: ``getattr`` has no default.)
    assert len(targets) >= 30, (
        f"only {len(targets)} help targets found across the four topic tables "
        "— this test is checking almost nothing")
    assert anchors, "the rendered manual carries no anchors at all"
    missing = sorted(t for t in targets if allwell._gh_slug(t) not in anchors)
    assert not missing, (
        f"{len(missing)} of {len(targets)} help topics point at a heading with "
        "no anchor in the rendered manual, so help opened from those pages "
        f"leaves the drawer wherever it was:\n  " + "\n  ".join(missing[:10]))


def test_the_render_keeps_most_of_the_manual(qapp):
    """The blunt version of the two above, and the one that states the symptom
    a user reports: the manual just stops.

    Measured as a *proportion of the source*, not by looking for the last
    heading's text. The first draft did the latter and passed against the
    truncated manual, because "Where to read next" is also a line in the table
    of contents near the top — the heading was gone and the substring was not.
    Markdown-to-plain-text drops syntax, so the ratio is not 100%: measured at
    94.3% for a manual that renders in full, against 28.5% for one that stops
    a third of the way down.
    """
    from PySide6.QtGui import QTextDocument

    source = MANUAL.read_text(encoding="utf-8")
    doc = QTextDocument()
    doc.setMarkdown(source)
    ratio = len(doc.toPlainText()) / len(source)
    assert ratio > 0.85, (
        f"the rendered manual is {ratio:.1%} of its source — something in it "
        "is stopping QTextDocument.setMarkdown, and every chapter after that "
        "point is missing from the help drawer")


def test_no_bare_angle_bracket_placeholder_in_the_manual():
    """The specific shape, guarded specifically, because the tests above can
    only say that *something* truncated the render.

    ``<date>``, ``<name>``, ``<path>`` and their kin read as HTML tags. Escaped
    (``\\<date\\>``) or in a code span they render as themselves, in the drawer
    and on GitHub alike.
    """
    text = MANUAL.read_text(encoding="utf-8")
    out, fenced = [], False
    for n, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        # Strip inline code spans, where the brackets are already literal.
        stripped = re.sub(r"`[^`]*`", "", line)
        for m in re.finditer(r"(?<!\\)<([a-zA-Z][a-zA-Z0-9_-]*)>", stripped):
            out.append(f"line {n}: <{m.group(1)}>")
    assert not out, (
        "these read as HTML tags to QTextDocument.setMarkdown, which discards "
        "the rest of the document — escape them as \\\\<x\\\\> or wrap them in "
        "backticks:\n  " + "\n  ".join(out))
