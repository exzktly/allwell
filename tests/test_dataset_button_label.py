"""The rail-head "Open dataset" button's label (review/core/app_shell_view.py).

Regression guard: the standard Analyze-pipeline layout loads
``<dataset_root>/out`` as ``dataset.data_dir``, so a naive
``Path(data_dir).name`` always reads the literal string "out" -- useless for
telling two datasets apart in the sidebar. ``refresh_dataset_button`` must
climb one level to the dataset root's own name for that layout, while a
genuinely flat dataset (no sibling ``in/`` folder) keeps showing its own
folder name unchanged.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from review.core.app_shell_view import refresh_dataset_button  # noqa: E402
from review.core.app_state import AppState  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeWin:
    def __init__(self, data_dir):
        self._dataset_btn = QPushButton()
        self._app_state = AppState()
        self._app_state.dataset.data_dir = data_dir


def test_out_layout_shows_the_dataset_root_name_one_level_up(qapp, tmp_path):
    root = tmp_path / "Experiment 1"
    (root / "in").mkdir(parents=True)
    out_dir = root / "out"
    out_dir.mkdir()

    win = _FakeWin(out_dir)
    refresh_dataset_button(win)

    assert win._dataset_btn.text() == "Experiment 1"
    assert win._dataset_btn.toolTip() == str(out_dir)


def test_flat_dataset_named_out_keeps_its_own_name(qapp, tmp_path):
    # No sibling "in/" folder -- this "out" is the dataset root itself, not
    # the output half of an in/out split, so it must not climb a level.
    flat_out = tmp_path / "out"
    flat_out.mkdir()

    win = _FakeWin(flat_out)
    refresh_dataset_button(win)

    assert win._dataset_btn.text() == "out"


def test_non_out_folder_name_is_unaffected(qapp, tmp_path):
    plain = tmp_path / "some_dataset"
    plain.mkdir()

    win = _FakeWin(plain)
    refresh_dataset_button(win)

    assert win._dataset_btn.text() == "some_dataset"


def test_no_dataset_loaded_shows_placeholder(qapp):
    win = _FakeWin(None)
    refresh_dataset_button(win)

    assert win._dataset_btn.text() == "No dataset — Open"
