"""A logger-shaped object over the remote runner's ``log(text)`` line sink.

``pipeline.completeness.record_run_check`` writes through ``logging``-style calls because it is normally driven
from inside the pipeline; when the client checks a fetched run, the destination is the GUI's line stream instead,
which takes whole lines with a trailing newline. Kept apart from ``services/remote_fetch.py``, whose line budget
(``tests/test_monolith_budgets.py``) asks for exactly this: a self-contained piece moved out rather than the
budget raised.
"""

from __future__ import annotations


class LogFnLogger:
    """Adapts the remote runner's ``log(text)`` sink to a logger-shaped object.

    ``record_run_check`` writes through ``logging``-style calls because it is
    normally driven from inside the pipeline; here the destination is the GUI's
    line stream, which takes whole lines with a trailing newline.
    """

    def __init__(self, log_fn):
        self._log = log_fn

    def _emit(self, prefix, msg, args):
        text = (msg % args) if args else str(msg)
        self._log(f"{prefix}{text}\n")

    def info(self, msg, *args):
        self._emit("[remote] ", msg, args)

    def warning(self, msg, *args):
        self._emit("[remote] ", msg, args)

    def error(self, msg, *args):
        self._emit("[remote] ", msg, args)
