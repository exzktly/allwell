"""The opening placeholder through a real load (full app, slow lane).

The fast-lane file proves the pieces; this one drives ``ReviewApp._load_path``
— the call every route into Review makes — and reads what Home's placeholder
says at each moment the load repaints the window, which is each
``_opening_set_phase`` pump. Before the fix every one of those read
"Load a directory." with the *Open a dataset* button showing.
"""

from __future__ import annotations

from review.core import load_controller as lc
from review.core.ui_helpers import NO_DATASET_TITLE


def _home_placeholder(app):
    stack = app._ctx.widgets.get_or_none("_home_empty_stack")
    return stack, stack._empty_placeholder


def test_home_says_opening_at_every_repaint_of_a_load(viewer_app, mini_dataset,
                                                      monkeypatch):
    stack, ph = _home_placeholder(viewer_app)
    seen = []
    real = lc._opening_set_phase

    def spy(ctx, text, **kw):
        seen.append((text, stack.currentIndex(), ph._text.text(),
                     ph._action.isVisibleTo(ph), ph._entries.isVisibleTo(ph)))
        return real(ctx, text, **kw)

    monkeypatch.setattr(lc, "_opening_set_phase", spy)
    viewer_app._load_path(mini_dataset)

    assert len(seen) > 5, seen
    want = f"Opening {mini_dataset.name}…"
    wrong = [s for s in seen if s[1:] != (0, want, False, False)]
    assert not wrong, wrong
    # And the load's end hands the page to the dataset.
    assert stack.currentIndex() == 1
    assert viewer_app._app_state.dataset.opening is None


def test_a_folder_with_nothing_to_load_puts_the_open_button_back(viewer_app,
                                                                  tmp_path,
                                                                  monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    (tmp_path / "empty").mkdir()
    viewer_app._load_path(tmp_path / "empty")

    stack, ph = _home_placeholder(viewer_app)
    assert stack.currentIndex() == 0
    assert ph._text.text() == NO_DATASET_TITLE
    assert ph._action.isVisibleTo(ph)
    assert viewer_app._app_state.dataset.opening is None
