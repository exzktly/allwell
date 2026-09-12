"""Every Datasets tool controller owns its worker through the shared base.

`stop`, `is_busy` and `shutdown` were six copies of the same eight lines,
differing only in which method asks the worker to stop and which label a
swallowed teardown error is logged under. They live on
`datasets.core.tool_controller.DatasetToolController` now, with those two
differences as a hook and a class attribute.

The hook is the load-bearing part, and this file is where its reason is
checked rather than only described: `import_loose`'s worker spends the run
blocked inside a subprocess's stdout read loop and never polls
`isInterruptionRequested()`, so Qt's cooperative flag is a no-op there and the
tracked child process would outlive the app. A base that hard-coded
`requestInterruption()` would have turned five correct implementations and one
deliberate exception into six wrong ones, silently — `shutdown` swallows the
failure it would then hit.

The three copy-into-a-destination tools share a second layer,
`DestinationToolController`, for the run-and-confirm-restart cluster. Its
`_destination_has_marker` is per tool on purpose: a folder holding *this*
tool's interrupted attempt is a resume, and the same folder holding another
tool's leftovers is not.

Deliberately NOT shared, and asserted so below: `_on_queue_requested`,
`_on_finished` and `_on_succeeded`. Measured across the six they have six, five
and two distinct shapes; pulling them up would trade six plain methods for a
base full of flags.
"""

from __future__ import annotations

import importlib
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from datasets.core.tool_controller import (  # noqa: E402
    DatasetToolController, DestinationToolController,
)

#: Tools that copy a dataset into a destination the user names, with the marker
#: their own `apply.execute_plan` writes.
DESTINATION_TOOLS = {
    "subset": ".aw_subset_in_progress",
    "merge_plates": ".aw_merge_in_progress",
    "concatenate": ".aw_concat_in_progress",
}
NOT_SHARED = ("_on_queue_requested", "_on_finished", "_on_succeeded")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeWorker:
    """Records which stop method a controller reaches for."""

    def __init__(self):
        self.calls: list[str] = []

    def isRunning(self) -> bool:
        return True

    def requestInterruption(self) -> None:
        self.calls.append("requestInterruption")

    def request_stop(self) -> None:
        self.calls.append("request_stop")

    def wait(self, ms: int) -> None:
        self.calls.append(f"wait({ms})")


def _controllers() -> dict[str, type]:
    from datasets.tab import _FEATURES

    out = {}
    for feature in _FEATURES:
        if not feature.kind:
            continue
        module = importlib.import_module(f"datasets.{feature.kind}.controller")
        cls = next(obj for name, obj in vars(module).items()
                   if isinstance(obj, type) and name.endswith("Controller")
                   and obj.__module__ == module.__name__)
        out[feature.kind] = cls
    return out


ALL = sorted(_controllers())


def test_the_sweep_found_the_controllers():
    assert len(ALL) >= 6, f"only found {ALL}"


@pytest.mark.parametrize("kind", ALL)
def test_every_controller_uses_the_shared_base(kind):
    assert issubclass(_controllers()[kind], DatasetToolController)


@pytest.mark.parametrize("member", ["stop", "is_busy", "shutdown"])
def test_no_controller_re_hand_rolls_the_lifecycle(member):
    offenders = [k for k, cls in _controllers().items() if member in vars(cls)]
    assert not offenders, (
        f"{member} is re-defined on: {offenders}. Set `_shutdown_label`, or "
        "override `_request_worker_stop`, rather than restating the method."
    )


@pytest.mark.parametrize("kind", ALL)
def test_shutdown_reaches_the_stop_method_that_actually_works(qapp, kind):
    """Five tools stop on Qt's interruption flag; Import Loose cannot."""
    cls = _controllers()[kind]
    expected = "request_stop" if kind == "import_loose" else "requestInterruption"

    inst = cls.__new__(cls)          # lifecycle only; no page is built
    inst._worker = _FakeWorker()
    inst.stop()
    assert inst._worker.calls == [expected]
    assert inst.is_busy() is True

    inst._worker.calls.clear()
    inst.shutdown()
    assert inst._worker.calls == [expected, "wait(2000)"]


@pytest.mark.parametrize("kind", ALL)
def test_an_idle_controller_is_not_busy_and_shuts_down_quietly(qapp, kind):
    cls = _controllers()[kind]
    inst = cls.__new__(cls)
    inst._worker = None
    assert inst.is_busy() is False
    inst.stop()
    inst.shutdown()          # must not raise on a controller that never ran


@pytest.mark.parametrize("kind", sorted(DESTINATION_TOOLS))
def test_a_destination_tool_recognises_only_its_own_marker(qapp, kind):
    cls = _controllers()[kind]
    assert issubclass(cls, DestinationToolController)
    inst = cls.__new__(cls)
    marker = DESTINATION_TOOLS[kind]

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        assert inst._destination_is_restart(str(root)) is False
        (root / marker).write_text("")
        assert inst._destination_is_restart(str(root)) is True
        (root / marker).unlink()
        (root / ".aw_someone_elses_in_progress").write_text("")
        assert inst._destination_is_restart(str(root)) is False


@pytest.mark.parametrize("member", NOT_SHARED)
def test_the_per_tool_handlers_stay_per_tool(member):
    """A regression guard pointing the other way: these differ between tools
    for real reasons, so finding them on the base means someone merged six
    distinct behaviours behind a flag."""
    assert member not in vars(DatasetToolController)
    assert member not in vars(DestinationToolController)


# ── the deadline the shutdown does not wait past ─────────────────────────────


class _StubbornWorker:
    """A worker that ignores the stop request, as a blocked one does.

    `wait(msec)` returning False is Qt's way of saying the thread is still
    running. Nothing read that return value, so a worker that outlived the
    deadline was abandoned without a word — and `AllWellApp.closeEvent` goes
    straight on to tear the widget tree down around it.
    """

    def __init__(self) -> None:
        self.stop_requested = False
        self.waited_ms: list[int] = []

    def isRunning(self) -> bool:
        return True

    def requestInterruption(self) -> None:
        self.stop_requested = True

    def wait(self, msec: int) -> bool:
        self.waited_ms.append(msec)
        return False


class _CompliantWorker(_StubbornWorker):
    def wait(self, msec: int) -> bool:
        self.waited_ms.append(msec)
        return True


def _controller_with(worker):
    ctrl = DatasetToolController.__new__(DatasetToolController)
    ctrl._worker = worker
    ctrl._shutdown_label = "probe tool"
    return ctrl


def test_a_worker_that_outlives_the_deadline_is_named_in_the_log(caplog):
    import logging

    worker = _StubbornWorker()
    ctrl = _controller_with(worker)

    with caplog.at_level(logging.WARNING, logger="review"):
        ctrl.shutdown()

    assert worker.stop_requested, "the worker was never asked to stop"
    assert worker.waited_ms == [2000], "the deadline changed"
    messages = [r.getMessage() for r in caplog.records]
    assert any("probe tool" in m for m in messages), (
        f"nothing named the tool whose worker was still running: {messages}"
    )


def test_a_worker_that_stops_in_time_says_nothing(caplog):
    """A warning on every quit would train the user to ignore this one."""
    import logging

    ctrl = _controller_with(_CompliantWorker())

    with caplog.at_level(logging.WARNING, logger="review"):
        ctrl.shutdown()

    assert [r.getMessage() for r in caplog.records] == []
