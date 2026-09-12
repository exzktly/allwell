"""Audit U17 — the OME-TIFF input guard must not answer "carry on" when it
could not look.

``_ome_input_hint`` blocks a run whose input is a packed OME-TIFF, redirecting
the user to the Datasets converter that de-interleaves it. It wrapped the
detection in ``except Exception: return None`` — and ``None`` is the one answer
that means *this input is fine, start the run*. A permission error part-way
through the walk, or a truncated file, therefore turned the guard off silently
and let the run go ahead against an input Analyze cannot process. The handler
carried ``# pragma: no cover``, so nothing ever executed it.

Degraded output must not be indistinguishable from good output: a check that did
not complete now says so, and the run stops.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def tab(qtbot, monkeypatch):
    import analyze.form

    monkeypatch.setattr(analyze.form, "load_remotes", lambda: {})
    from analyze.tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)
    return t


@pytest.mark.parametrize("raised", [
    PermissionError(13, "Permission denied"),
    OSError(5, "Input/output error"),
    ValueError("embedded null byte"),
])
def test_a_check_that_could_not_run_blocks_the_run(tab, monkeypatch, raised):
    import analyze.domain.input

    def _boom(_path):
        raise raised

    monkeypatch.setattr(analyze.domain.input, "detect_ome_input", _boom)
    tab._input_edit.setText("/some/plate")

    hint = tab._ome_input_hint()
    assert hint is not None, (
        f"{type(raised).__name__} from the OME check was read as 'this input is "
        "fine' and the run would have started")
    assert type(raised).__name__ in hint, "the message should name what happened"
    assert "/some/plate" in hint, "the message should name the input it could not read"


def test_a_readable_ordinary_folder_still_starts(tab, monkeypatch, tmp_path):
    """The guard must not become a blanket refusal."""
    import analyze.domain.input

    monkeypatch.setattr(analyze.domain.input, "detect_ome_input", lambda _p: None)
    tab._input_edit.setText(str(tmp_path))
    assert tab._ome_input_hint() is None


def test_an_empty_input_field_is_not_the_guard_s_business(tab):
    assert tab._ome_input_hint() is None
