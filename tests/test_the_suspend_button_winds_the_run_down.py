"""Pressing Suspend winds the run down, whatever the record does (audit L11).

Suspend does two things in order: it writes a pointer to the output folder so
the app can offer to resume, and it asks the run to stop taking on new
FOV-groups. The order is deliberate — a run that wound down having left nothing
behind to resume it with is the worse failure — but "first" was implemented as
"and only if that worked".

The record write and the wind-down shared one ``try``, so anything escaping the
write returned from the slot. The user pressed the button precisely because they
wanted the work kept, the run carried straight on, and nothing on screen said
so. The escape was not hypothetical: ``suspend_record._write_all`` caught
``OSError`` alone while the record carries a ``spec`` the caller handed in, so
one unserialisable value in it raised a ``TypeError`` from ``json.dump``.

CLAUDE.md §7.6 — these press the button. Nothing in the repo did.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def tab(qtbot, monkeypatch, tmp_path):
    import analyze.form

    monkeypatch.setattr(analyze.form, "load_remotes", lambda: {})
    from analyze.tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)

    # The real runner, put into the state a running run leaves it in, through
    # the attributes its own properties read: a live subprocess handle makes
    # ``is_running`` true, and ``_last_output_dir`` is what the resolver sets.
    # A stand-in would have to reimplement the teardown the tab performs on
    # close, and would then be testing the stand-in.
    class _LiveProc:
        def poll(self):
            return None

    runner = t._runner
    runner._proc = _LiveProc()
    runner._last_output_dir = tmp_path
    runner.suspended = False

    def _suspend() -> None:
        runner.suspended = True

    runner.suspend = _suspend
    t._suspend_btn.setEnabled(True)
    return t


def test_pressing_suspend_asks_the_run_to_wind_down(tab, tmp_path, monkeypatch):
    monkeypatch.setattr("services.suspend_record.record_path",
                        lambda: tmp_path / "state" / "suspended.json")
    tab._suspend_btn.click()
    assert tab._runner.suspended, "the button did not reach the runner at all"


def test_a_record_that_cannot_be_written_still_winds_the_run_down(
        tab, monkeypatch):
    """The reported shape: the write fails and the run must still stop."""
    import analyze.suspend

    monkeypatch.setattr(analyze.suspend, "save",
                        lambda run: (_ for _ in ()).throw(OSError("disk gone")))
    tab._suspend_btn.click()
    assert tab._runner.suspended, (
        "an exception from the resume record returned out of the slot, so the "
        "run kept going after the user pressed Suspend and nothing said so"
    )


def test_a_form_that_cannot_be_collected_still_winds_the_run_down(tab, monkeypatch):
    """The other way in: the spec is built before the record is ever written."""
    monkeypatch.setattr(type(tab), "_collect_run_spec",
                        lambda self: (_ for _ in ()).throw(ValueError("bad form")))
    tab._suspend_btn.click()
    assert tab._runner.suspended


def test_a_run_that_has_not_resolved_its_output_folder_is_refused(tab, monkeypatch):
    """...but a run with nowhere to resume from is told, not suspended."""
    from PySide6.QtWidgets import QMessageBox

    warned: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda _p, _t, text, *a, **k: warned.append(text)))
    tab._runner._last_output_dir = None
    tab._suspend_btn.click()
    assert not tab._runner.suspended
    assert warned, "the refusal was silent"


def test_a_run_that_is_not_going_is_left_alone(tab):
    tab._runner._proc = None                    # is_running now answers False
    tab._suspend_btn.click()
    assert not tab._runner.suspended
