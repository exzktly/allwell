"""Guard: the leaked-thread sweep knows every thread a spawned run starts.

`conftest._live_run_threads` is what stops a remote run's background threads
when a test forgets to, and what fails the test that forgot. It finds them by
walking `threading.enumerate()` and matching the owning object's type name, so
its reach is exactly the roster in `_RUN_THREAD_OWNERS` — a thread whose owner
is not in that roster is invisible to it, and invisible in the direction that
produces no failure.

That is not hypothetical. The roster held `Fetcher` alone for a long time while
`RemoteRunner.spawn` also started an `_UploadHeartbeat` beside it, so a test
driving the real `spawn` left a thread touching the remote over ssh on a timer.
It surfaced only as a `PytestUnhandledThreadExceptionWarning` inside an
unrelated test minutes later, because the `ssh` call was on the leaked thread's
stack and not on any test's.

So this pins the two things that would let it narrow again without a failure:
every type the roster names still exists and still has the members the roster
uses, and every threaded background worker a spawned run owns is in the roster.
"""
from __future__ import annotations

import ast

import pytest

from tests._repo_scan import REPO_ROOT, parse_source
from tests.conftest import _RUN_THREAD_OWNERS, _live_run_threads


def test_every_rostered_owner_still_exists_and_answers_the_roster():
    """A renamed class or member turns an entry into a silent no-op.

    The roster matches on `type(owner).__name__`, so a rename does not raise —
    it simply stops matching, and the sweep goes quiet about that thread.
    """
    import importlib

    modules = [importlib.import_module(f"services.{m}") for m in _REMOTE_CLIENT_MODULES]
    for name, (asked_to_stop, stop) in _RUN_THREAD_OWNERS.items():
        cls = next((getattr(m, name) for m in modules if hasattr(m, name)), None)
        assert cls is not None, (
            f"_RUN_THREAD_OWNERS names {name!r}, which no longer exists in any "
            f"of {_REMOTE_CLIENT_MODULES}. The entry now matches nothing and the "
            "sweep is silent about that thread.")
        # The stop callable is named on the class; the predicate reads private
        # state, so it is checked against a real instance below where cheap.
        assert callable(asked_to_stop) and callable(stop)


def test_the_heartbeat_is_swept_and_a_stopped_one_is_not_a_leak():
    """The two halves of the predicate, on the type that was missing from it."""
    from services.remote_runner import _UploadHeartbeat

    class _Cache:
        def _ssh_run(self, *_a, **_k):
            return None

    hb = _UploadHeartbeat(_Cache(), "/scratch/x/.aw_upload_alive",
                          interval_s=30)
    hb.start()
    try:
        found = [o for _t, o, _s in _live_run_threads() if o is hb]
        assert found, (
            "a live upload heartbeat was not seen by the sweep, so a test "
            "leaking one would neither be stopped nor told")
    finally:
        hb.stop()

    # Asked to stop is not abandoned. Judging on liveness alone fails every
    # test that cleaned up correctly, since the fixture that stops them runs
    # immediately before the guard that checks.
    assert not [o for _t, o, _s in _live_run_threads() if o is hb]


#: The remote client's modules: the orchestrator and the owners extracted from
#: it. A thread-starting class moved into a new one is found only if it is here.
_REMOTE_CLIENT_MODULES = ("remote_runner", "remote_fetch")


def _threaded_worker_classes() -> set[str]:
    """Classes in the remote client that start a named daemon thread of their own."""
    owners: set[str] = set()
    classes = [cls for m in _REMOTE_CLIENT_MODULES
               for cls in ast.walk(parse_source(REPO_ROOT / "services" / f"{m}.py"))]
    for cls in classes:
        if not isinstance(cls, ast.ClassDef):
            continue
        for node in ast.walk(cls):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "Thread"):
                continue
            # A worker this sweep can reach is one whose thread runs a bound
            # method of the class — that is how the owner is recovered.
            for kw in node.keywords:
                if kw.arg != "target":
                    continue
                if (isinstance(kw.value, ast.Attribute)
                        and isinstance(kw.value.value, ast.Name)
                        and kw.value.value.id == "self"):
                    owners.add(cls.name)
    return owners


def test_no_run_owned_thread_is_missing_from_the_roster():
    """The sweep's reach is the roster, so the roster has to be complete.

    A new background worker beside the ones already here inherits none of this
    protection: nothing fails, the thread simply keeps running into later
    tests. Add it to `_RUN_THREAD_OWNERS` with how to tell a stopping one from
    an abandoned one, or excuse it here with the reason.
    """
    #: Threaded workers that are not a leak this sweep should chase.
    excused = {
        # Tees the run's ssh stdout into the fetcher; it ends when the pipe
        # closes rather than on a timer, and it reaches nothing on its own.
        "_RemoteProcess": "the stdout tee ends with the pipe, not on a timer",
        "_BatchedRemoteProcess": "same tee, batched",
    }
    missing = sorted(_threaded_worker_classes()
                     - set(_RUN_THREAD_OWNERS) - set(excused))
    assert not missing, (
        "these classes in the remote client start a thread running "
        "one of their own methods, but conftest's _RUN_THREAD_OWNERS does not "
        "know how to stop one. A test that leaks it is neither stopped nor "
        f"failed: {', '.join(missing)}")


@pytest.mark.parametrize("name", sorted(_RUN_THREAD_OWNERS))
def test_every_rostered_owner_actually_starts_a_thread(name):
    """The mirror image: an entry for a class that no longer runs a thread.

    It costs nothing at run time and is worse than nothing to read, since it
    states a rule about a worker that has gone.
    """
    assert name in _threaded_worker_classes(), (
        f"_RUN_THREAD_OWNERS names {name!r}, which no longer starts a thread "
        "running one of its own methods. Drop the entry.")
