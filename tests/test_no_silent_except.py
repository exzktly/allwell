"""Guardrail: no silent broad-except handlers in well_viewer/.

A broad ``except Exception:`` (or bare ``except:``) whose entire body is a single
``pass`` or ``continue`` swallows failures with no trace — indistinguishable
from "nothing went wrong". Those were migrated to ``well_viewer.core.safe``'s
``swallowed()`` (which logs a DEBUG breadcrumb). This test fails if any new one
is introduced, nudging authors toward ``swallowed()`` / ``swallow()`` or a
specific exception type.

Precise by design: it only flags the exact anti-pattern that was eliminated
(broad except with a sole ``pass``/``continue`` body), so it has no false
positives on handlers that log, re-raise, or do real recovery work.
"""

import ast
from pathlib import Path

WELL_VIEWER = Path(__file__).resolve().parent.parent / "well_viewer"


def _is_broad(handler: ast.ExceptHandler) -> bool:
    """True for ``except:`` (bare) or ``except Exception:``."""
    t = handler.type
    return t is None or (isinstance(t, ast.Name) and t.id == "Exception")


def _silent_body(handler: ast.ExceptHandler) -> bool:
    return len(handler.body) == 1 and isinstance(handler.body[0], (ast.Pass, ast.Continue))


def _iter_py_files():
    for path in sorted(WELL_VIEWER.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def test_no_silent_broad_except_in_well_viewer():
    offenders = []
    for path in _iter_py_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _is_broad(node) and _silent_body(node):
                kind = type(node.body[0]).__name__.lower()
                offenders.append(f"{path.relative_to(WELL_VIEWER.parent)}:{node.body[0].lineno} (silent {kind})")
    assert not offenders, (
        "Silent broad-except handler(s) found. Use "
        "well_viewer.core.safe.swallowed()/swallow() or catch a specific "
        "exception type instead of a bare pass/continue:\n  "
        + "\n  ".join(offenders)
    )
