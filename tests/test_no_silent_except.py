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

REPO_ROOT = Path(__file__).resolve().parent.parent
WELL_VIEWER = REPO_ROOT / "well_viewer"

# The reusable widget library. It may not import ``well_viewer`` (that is the
# whole point of the package), so it uses ``util.aw_safe`` — the same helper the
# Qt-free pipeline side uses — rather than ``well_viewer.core.safe``. It was the
# last un-guarded body of GUI code in the repo, and it is where a swallowed
# exception is hardest to notice: a widget that quietly fails to paint or to
# recolour just looks like a widget that was never asked to.
WIDGETS = REPO_ROOT / "widgets"

# The Datasets pane. Seven tool pages, their Qt-free planners and their
# QThread workers — several thousand lines of code that reports what it did by
# writing to a log the user can open, which is exactly the kind that can fail
# quietly and look idle. It was clean when this guard was added and had no
# guard keeping it that way, which is the only interesting state for a rule
# like this to be in.
DATASETS = REPO_ROOT / "datasets"

# Pipeline / services side (Qt-free; uses aw_safe.swallowed instead of
# well_viewer.core.safe). Guarded so these don't silently regress either.
PIPELINE_DIRS = [REPO_ROOT / "services"]
PIPELINE_FILES = [
    REPO_ROOT / "process_microscopy.py",
    REPO_ROOT / "pipeline/background.py",
    REPO_ROOT / "pipeline/cellpose.py",
    REPO_ROOT / "pipeline/stardist.py",
    REPO_ROOT / "pipeline/io.py",
    # Code-review G4: close the coverage gap — these Qt-free pipeline modules
    # were audited and are currently free of broad silent-pass handlers, so
    # guard them too. (Extending the check to tuple/typed ``pass`` and broad
    # ``return None`` needs a frozen offender baseline first — G4's remaining
    # scope.)
    REPO_ROOT / "pipeline/auto_threshold.py",
    REPO_ROOT / "pipeline/flatfield.py",
    REPO_ROOT / "pipeline/shape_features.py",
]


def _is_broad(handler: ast.ExceptHandler) -> bool:
    """True for ``except:`` (bare) or ``except Exception:``."""
    t = handler.type
    return t is None or (isinstance(t, ast.Name) and t.id == "Exception")


def _silent_body(handler: ast.ExceptHandler) -> bool:
    return len(handler.body) == 1 and isinstance(handler.body[0], (ast.Pass, ast.Continue))


def _offenders(paths):
    out = []
    for path in paths:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _is_broad(node) and _silent_body(node):
                kind = type(node.body[0]).__name__.lower()
                out.append(f"{path.relative_to(REPO_ROOT)}:{node.body[0].lineno} (silent {kind})")
    return out


def _walk(root):
    return [p for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts]


def test_no_silent_broad_except_in_well_viewer():
    offenders = _offenders(_walk(WELL_VIEWER))
    assert not offenders, (
        "Silent broad-except handler(s) found. Use "
        "well_viewer.core.safe.swallowed()/swallow() or catch a specific "
        "exception type instead of a bare pass/continue:\n  "
        + "\n  ".join(offenders)
    )


def test_no_silent_broad_except_in_widgets():
    offenders = _offenders(_walk(WIDGETS))
    assert not offenders, (
        "Silent broad-except handler(s) found in widgets/. Use "
        "util.aw_safe.swallowed()/swallow() — widgets/ cannot import "
        "well_viewer.core.safe — or catch a specific exception type instead of "
        "a bare pass/continue:\n  " + "\n  ".join(offenders)
    )


def test_no_silent_broad_except_in_datasets():
    """The Datasets pane mixes both helpers: its Qt-free halves (`_common/`,
    the `*_compute` planners, `apply.py`, the workers) use `util.aw_safe`,
    while the controllers and views may use `well_viewer.core.safe`. Either is
    fine; a bare pass is not."""
    offenders = _offenders(_walk(DATASETS))
    assert not offenders, (
        "Silent broad-except handler(s) found in datasets/. Use "
        "well_viewer.core.safe.swallowed()/swallow() on the Qt side, "
        "util.aw_safe's equivalents in the Qt-free halves, or catch a specific "
        "exception type instead of a bare pass/continue:\n  " + "\n  ".join(offenders)
    )


def test_no_silent_broad_except_in_pipeline():
    paths = [p for d in PIPELINE_DIRS for p in _walk(d)]
    paths += [p for p in PIPELINE_FILES if p.exists()]
    offenders = _offenders(paths)
    assert not offenders, (
        "Silent broad-except handler(s) found in the pipeline/services side. Use "
        "aw_safe.swallowed()/swallow() or catch a specific exception type "
        "instead of a bare pass/continue:\n  " + "\n  ".join(offenders)
    )
