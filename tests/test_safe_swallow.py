"""Tests for core.safe — the observable exception-suppression helpers.

Records are captured with a handler attached *directly* to the
``review.swallowed`` logger rather than via ``caplog``. caplog listens at
the root logger and therefore depends on propagation, which the GUI disables at
runtime (``status_view`` sets ``review`` ``propagate = False`` so log lines
don't double-print). A direct handler captures the records regardless, which is
also what the real app relies on: ``review.swallowed`` propagates to the
``review`` logger, where the GUI handler lives.
"""

import logging

import pytest

from core.safe import swallow, swallowed


class _Capture:
    """Context manager that captures records emitted to a named logger."""

    def __init__(self, name="review.swallowed", level=logging.DEBUG):
        self._name = name
        self._level = level
        self.records: list[logging.LogRecord] = []
        self._logger = logging.getLogger(name)
        self._handler = logging.Handler()
        self._handler.emit = self.records.append  # type: ignore[method-assign]
        self._prev_level = self._logger.level

    def __enter__(self):
        self._logger.addHandler(self._handler)
        self._logger.setLevel(self._level)
        return self

    def __exit__(self, *exc):
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._prev_level)
        return False


def test_swallow_suppresses_exception():
    with swallow("unit: boom"):
        raise ValueError("boom")
    # Reaching here means the exception was suppressed.


def test_swallow_logs_once_with_traceback():
    with _Capture() as cap:
        with swallow("unit: ctx-note"):
            raise RuntimeError("kaboom")
    assert len(cap.records) == 1
    rec = cap.records[0]
    assert "ctx-note" in rec.getMessage()
    assert rec.levelno == logging.DEBUG
    assert rec.exc_info is not None and rec.exc_info[0] is RuntimeError


def test_swallow_does_not_run_on_success():
    with _Capture() as cap:
        with swallow("unit: clean"):
            value = 1 + 1
    assert value == 2
    assert cap.records == []


@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit])
def test_swallow_does_not_suppress_baseexception(exc):
    with pytest.raises(exc):
        with swallow("unit: must-propagate"):
            raise exc()


def test_swallowed_logs_active_exception():
    with _Capture() as cap:
        try:
            raise KeyError("missing")
        except Exception:
            swallowed("unit: handler-note")
    assert len(cap.records) == 1
    assert "handler-note" in cap.records[0].getMessage()
    assert cap.records[0].exc_info[0] is KeyError


def test_swallowed_records_call_site():
    with _Capture() as cap:
        try:
            raise ValueError
        except Exception:
            swallowed("loc")
    assert len(cap.records) == 1
    # Location prefix points back at this test module, not at safe.py.
    assert "test_safe_swallow" in cap.records[0].getMessage()


def test_custom_level_and_logger():
    with _Capture(name="unit.custom.swallow", level=logging.WARNING) as cap:
        custom = logging.getLogger("unit.custom.swallow")
        with swallow("unit: warn", level=logging.WARNING, logger=custom):
            raise OSError("disk")
    assert len(cap.records) == 1
    assert cap.records[0].levelno == logging.WARNING
