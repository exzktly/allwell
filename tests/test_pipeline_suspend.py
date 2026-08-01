"""A suspended run winds down; it does not die.

Stop sends SIGTERM and, five seconds later, SIGKILL. Whatever each worker was
computing is lost. Suspend sends the same SIGTERM, but the pipeline catches it:
it stops taking on new FOV-groups, lets the ones in flight finish, and returns
through the normal end-of-well path so the well writes its CSV and its
``<well>_out.zip``. Those artifacts are the entire resume mechanism —
``--incremental`` reads them back as the done-set — so what these tests pin is
that the flag is set, honoured, and never mistakes an unattempted group for a
failed one.
"""

from __future__ import annotations

import signal

import pytest

import pipeline.suspend as ps


@pytest.fixture(autouse=True)
def _fresh_flag():
    """The module flag is process-global and never cleared in production."""
    ps._SUSPENDED.clear()
    saved = {s: signal.getsignal(s) for s in ps._SUSPEND_SIGNALS}
    yield
    ps._SUSPENDED.clear()
    for sig, handler in saved.items():
        signal.signal(sig, handler)


def test_a_fresh_process_is_not_suspended():
    assert ps.is_suspended() is False


def test_request_suspend_sets_the_flag():
    ps.request_suspend()
    assert ps.is_suspended() is True


def test_the_signal_sets_the_flag_instead_of_killing():
    ps.install_handlers()
    signal.raise_signal(signal.SIGTERM)      # would terminate us by default
    assert ps.is_suspended() is True


def test_the_second_signal_is_a_hard_stop():
    """A suspend that hangs is worse than one that loses a FOV-group.

    The handler restores whatever disposition it replaced before returning, so
    a user asking twice — or a supervisor escalating — gets the default action
    back rather than another polite note.
    """
    hits = []
    signal.signal(signal.SIGTERM, lambda *a: hits.append("previous"))
    ps.install_handlers()
    signal.raise_signal(signal.SIGTERM)
    assert ps.is_suspended() is True
    assert hits == []                        # first one was ours
    signal.raise_signal(signal.SIGTERM)
    assert hits == ["previous"]              # second went back to the old one


def test_the_first_signal_says_what_is_happening():
    said = []

    class _Log:
        def warning(self, msg, *args):
            said.append(msg % args if args else msg)

    ps.install_handlers(log=_Log())
    signal.raise_signal(signal.SIGTERM)
    assert len(said) == 1
    assert "SUSPEND requested" in said[0]
    assert "--incremental" in said[0], "the message must say how to continue"


def test_installing_handlers_never_raises_on_an_unsupported_signal(monkeypatch):
    """A run must not fail to start because it could not be interrupted politely."""
    def _boom(*_a, **_k):
        raise OSError("no such signal here")

    monkeypatch.setattr(signal, "signal", _boom)
    ps.install_handlers()                    # must not raise
    assert ps.is_suspended() is False
    # Put the real ``signal.signal`` back before the autouse fixture's teardown
    # tries to use it — fixture finalisers run outer-first, so monkeypatch's own
    # undo would come too late.
    monkeypatch.undo()


def test_the_worker_initializer_takes_no_arguments():
    """``multiprocessing.Pool(initializer=...)`` calls it bare."""
    ps.worker_initializer()
    signal.raise_signal(signal.SIGTERM)
    assert ps.is_suspended() is True


def test_an_unattempted_fov_group_is_not_a_failed_one():
    """The distinction the whole feature rests on.

    A group the run never got to is missing from the output — which the
    completeness record already reports as a gap between the input's group count
    and the CSV's. Counting it as *failed* would put it in the PARTIAL warning
    and the per-well ``failed`` list, telling the user their data is broken when
    it is merely unfinished.
    """
    import inspect

    import process_microscopy as pm

    src = inspect.getsource(pm._process_groups_in_worker)
    assert "_suspend.is_suspended()" in src
    # The single-threaded loop breaks out rather than appending to `failed`.
    body = src.split("if fov_threads == 1 or len(groups) == 1:", 1)[1]
    first = body.split("if aborted_reason:", 1)[0]
    assert "break" in first
    assert "failed.append" not in first
