"""The "no dataset is open" sentence is written once, and a button replaces it.

Two rules, both learned from the same placeholder.

**One spelling.** The sentence is said in two media — the ``EmptyState``
placeholder covering a tab body, and matplotlib text painted onto an empty
figure so an exported copy states its own shortfall — and it had three
independent spellings in the tree, already drifting in punctuation. They now all
read ``well_viewer.core.empty_state_copy``; this file fails on a fourth copy
typed out by hand.

**A button is not an extra hint.** The hint's job is to say where the way out
is. Home's placeholder wires an ``Open a dataset…`` button, so on that page the
way out was both a button and a sentence pointing at the opposite corner of the
window — two answers to one question, with the sentence being the worse of them.
The button now stands in place of the hint rather than under it, which has to
survive the shared refresher rewriting the hint on every refresh.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The distinctive half of the sentence — the part that cannot be re-worded
#: without meaning something else, and so is what a hand-typed copy would carry.
_SENTENCE = re.compile(r"top of the left rail")

#: Where the sentence is allowed to appear as a literal.
_ALLOWED = {
    Path("well_viewer/core/empty_state_copy.py"),   # the one home
    Path("tests/test_no_dataset_copy.py"),          # this file
}


def _python_sources() -> list[Path]:
    """The shared scan surface, so this guard is not blind where others see."""
    from tests._repo_scan import python_sources
    return python_sources()


def test_the_no_dataset_sentence_is_written_in_exactly_one_module():
    offenders = []
    for path in _python_sources():
        rel = path.relative_to(REPO_ROOT)
        if rel in _ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _SENTENCE.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "the no-dataset sentence is spelled out here instead of imported from "
        "well_viewer.core.empty_state_copy:\n  " + "\n  ".join(offenders))


def test_the_hint_names_the_control_as_a_button():
    """The reported defect: a sentence that opens with the bare control name.

    ``Open…`` is the control's real label and the ellipsis is the convention for
    "opens a dialog", but a sentence *beginning* with it reads as prose trailing
    off rather than as a name.
    """
    from well_viewer.core.empty_state_copy import NO_DATASET_HINT
    assert not NO_DATASET_HINT.startswith("Open…"), (
        "the hint opens with the bare control name, which reads as prose "
        "trailing off rather than as the button's label")
    assert "Open…" in NO_DATASET_HINT and "button" in NO_DATASET_HINT, (
        "the hint must still name the control by the label it wears, and say "
        "that it is a button")


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_an_action_button_replaces_the_hint_rather_than_joining_it(qapp):
    from widgets.empty_state import EmptyState
    from well_viewer.core.empty_state_copy import NO_DATASET_HINT, NO_DATASET_TITLE

    es = EmptyState(NO_DATASET_TITLE, hint=NO_DATASET_HINT)
    assert not es._hint.isHidden(), "hint should be on before any action is set"
    assert es._hint.text() == NO_DATASET_HINT

    es.setAction("Open a dataset…", lambda: None)
    assert es._hint.isHidden(), (
        "the placeholder shows both a button and a sentence pointing somewhere "
        "else — two answers to the one question the button already answers")

    # The refresher rewrites the hint on every refresh; the suppression has to
    # outlive that, or the line comes back the first time a dataset is closed.
    es.setHint(NO_DATASET_HINT)
    assert es._hint.isHidden(), (
        "a refresh repainted the hint underneath the action button")

    es.clearAction()
    assert not es._hint.isHidden(), (
        "removing the button must bring the hint back — it is the only "
        "instruction left once the button is gone")
    assert es._hint.text() == NO_DATASET_HINT
