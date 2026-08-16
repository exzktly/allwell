"""The "no dataset is open" sentence is written once, and a button replaces it.

The sentence is said in two media — the ``EmptyState`` placeholder over a tab
body, and matplotlib text on an empty figure so an exported copy states its own
shortfall — and had three spellings, already drifting in punctuation. They now
all read ``well_viewer.core.empty_state_copy``.

Home's placeholder wires an ``Open a dataset`` button, so that page showed both
a button and a sentence pointing at the far corner of the window. The button
takes the hint's place, which has to survive the shared refresher rewriting the
hint on every pass.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The half of the sentence a hand-typed copy would carry.
_SENTENCE = re.compile(r"top of the left rail")

_ALLOWED = {
    Path("well_viewer/core/empty_state_copy.py"),   # the one home
    Path("tests/test_no_dataset_copy.py"),          # this file
}


def test_the_no_dataset_sentence_is_written_in_exactly_one_module():
    from tests._repo_scan import python_sources  # the shared scan surface

    offenders = []
    for path in python_sources():
        rel = path.relative_to(REPO_ROOT)
        if rel in _ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _SENTENCE.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "the no-dataset sentence is spelled out here instead of imported from "
        "well_viewer.core.empty_state_copy:\n  " + "\n  ".join(offenders))


def test_the_hint_names_the_control_as_a_button_and_carries_no_ellipsis():
    """The reported defect: a sentence opening with the bare control name,
    whose trailing ellipsis then read as prose trailing off. The dots are gone
    from the control, and "button" carries what they were there to convey."""
    from well_viewer.core.empty_state_copy import NO_DATASET_HINT
    assert "…" not in NO_DATASET_HINT, (
        "the hint carries an ellipsis again; the Open control does not have one")
    assert not NO_DATASET_HINT.startswith("Open"), (
        "the hint opens with the bare control name, which reads as a verb "
        "addressed to the user rather than as the button's label")
    assert "Open" in NO_DATASET_HINT and "button" in NO_DATASET_HINT, (
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

    es.setAction("Open a dataset", lambda: None)
    assert es._hint.isHidden(), (
        "the placeholder shows both a button and a sentence pointing somewhere "
        "else — two answers to the one question the button already answers")

    es.setHint(NO_DATASET_HINT)   # what the shared refresher does on every pass
    assert es._hint.isHidden(), (
        "a refresh repainted the hint underneath the action button")

    es.clearAction()
    assert not es._hint.isHidden(), (
        "removing the button must bring the hint back — it is the only "
        "instruction left once the button is gone")
    assert es._hint.text() == NO_DATASET_HINT
