"""A ``logging.Handler`` never reaches the GUI through a timer (ARCHITECTURE §5).

``logging.Handler.emit`` runs on whichever thread called ``logger.info(...)``.
In this app that includes every background thread there is: the remote runner's
batch loop and its fetcher, the smFISH and cell-browser loaders, the gating
``QThread``s, and every ``swallowed()`` breadcrumb any of them writes. A handler
that appends to a widget therefore has to cross a thread boundary on the way,
and ``QTimer.singleShot(0, …)`` is not the way across even though it reads like
it. Measured, under a real ``QCoreApplication``:

    a QTimer built on a worker thread has affinity to that thread: True
    main QThread is a different object                          : True
    the singleShot callback ever ran                            : False

The timer is created on the *calling* thread. From a worker with no event loop
the callback never fires — so the line is silently lost — and the stray
``QObject`` is left owned by a thread that is about to end, which is what emits
``QObject::killTimer: Timers cannot be stopped from another thread`` when the
thread is torn down. Clicking Stop during a remote run tears several down at
once.

Two handlers had this, with the same wrong comment on the same line: the shell's
``_RingHandler`` (``allwell.py``) and Review's ``_GUILogHandler``
(``review/core/status_view.py``). One was written from the other, which is
why this is a guard rather than a fix — the shape is easy to reintroduce and
statically obvious. The route that works is
``core.worker_bridge.GuiLogRelay``: a ``QObject`` built on the GUI
thread whose queued signal may be emitted from anywhere.

Timer *construction* is flagged alongside ``singleShot``: ``QTimer(...)`` and
``startTimer(...)`` inside ``emit`` bind a timer to the emitting thread in
exactly the same way, and a guard that only knew the one spelling it was written
for would be half a rule.
"""

from __future__ import annotations

import ast

from tests._repo_scan import python_sources, rel
from tests._repo_scan import parse_source

#: Attribute/function names that create or arm a Qt timer.
_TIMER_CALLS = frozenset({"singleShot", "startTimer", "QTimer"})


def _is_logging_handler(node: ast.ClassDef) -> bool:
    """Whether *node* subclasses ``logging.Handler``, under any import spelling.

    Matches ``Handler``, ``logging.Handler`` and the ``import logging as
    _logging`` spelling the shell uses, plus the app's own handler subclasses by
    the ``…Handler`` naming convention — a class named ``_RingHandler`` that
    derives from another handler in this repo is still a handler.
    """
    for base in node.bases:
        name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
        if name == "Handler" or name.endswith("Handler"):
            return True
    return False


def _emit_methods(node: ast.ClassDef) -> list[ast.FunctionDef]:
    return [n for n in node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "emit"]


def _timer_calls(scope: ast.AST) -> list[tuple[int, str]]:
    """Every timer call anywhere under *scope*, including nested defs/lambdas."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(scope):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        # ``QTimer`` under an alias (``_QTimer.singleShot``) matches on the
        # attribute, so the owner's spelling never has to be enumerated.
        if name in _TIMER_CALLS or name.endswith("QTimer"):
            try:
                found.append((node.lineno, ast.unparse(func)))
            except Exception:
                found.append((node.lineno, name))
    return found


def test_no_timer_inside_a_logging_handler_emit():
    offenders: list[str] = []
    for path in python_sources():
        rel_path = rel(path)
        tree = parse_source(path)
        # Handler classes are not always at module scope — the shell defines
        # ``_RingHandler`` inside the method that installs it — so walk the
        # whole tree rather than reading ``tree.body``.
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not _is_logging_handler(node):
                continue
            for method in _emit_methods(node):
                for lineno, call in _timer_calls(method):
                    offenders.append(
                        f"{rel_path}:{lineno} — {node.name}.emit calls {call}(...)")

    offenders = sorted(set(offenders))
    assert not offenders, (
        "A logging.Handler.emit creates or arms a Qt timer. emit() runs on "
        "whichever thread logged the record, so the timer is bound to that "
        "thread: the callback never fires (the log line is lost) and tearing "
        "the thread down raises 'QObject::killTimer: Timers cannot be stopped "
        "from another thread'. Marshal with "
        "core.worker_bridge.GuiLogRelay instead:\n  "
        + "\n  ".join(offenders)
    )
