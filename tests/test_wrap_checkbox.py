"""WrapCheckBox — a checkbox whose label wraps. Regression guard for the
collapse-in-an-HBox bug (Ignored label policy → composite width ~0, text gone,
wrapped height ballooned)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QSizePolicy  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


_LABEL = "Dry-run cleanup (show what would be deleted, don't delete) — a long label"


def test_wrap_checkbox_does_not_collapse(qapp):
    from widgets.wrap_checkbox import WrapCheckBox
    cb = WrapCheckBox(_LABEL)
    # The fix: the label uses Preferred (not Ignored) horizontal policy, so the
    # composite advertises a real preferred width and can't collapse to ~0 in a
    # horizontal row (which made the text vanish and the wrapped height balloon).
    assert cb._lbl.sizePolicy().horizontalPolicy() == QSizePolicy.Preferred
    assert cb.sizeHint().width() > 100      # label contributes width (was ~30)
    assert cb._lbl.wordWrap() is True
    assert cb._lbl.text() == _LABEL
    # word-wrap minimum stays small, so it still doesn't pin a narrow sidebar
    assert cb.minimumSizeHint().width() < 250


def test_wrap_checkbox_api(qapp):
    from widgets.wrap_checkbox import WrapCheckBox
    seen = []
    cb = WrapCheckBox("x")
    cb.toggled.connect(seen.append)
    cb.setChecked(True)
    assert cb.isChecked() and seen == [True]
    cb.setChecked(False)
    assert not cb.isChecked()
