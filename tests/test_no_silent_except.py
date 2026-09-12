"""Guardrail: no silent broad-except handlers in review/.

A broad ``except Exception:`` (or bare ``except:``) whose entire body is a single
``pass`` or ``continue`` swallows failures with no trace — indistinguishable
from "nothing went wrong". Those were migrated to ``core.safe``'s
``swallowed()`` (which logs a DEBUG breadcrumb). This test fails if any new one
is introduced, nudging authors toward ``swallowed()`` / ``swallow()`` or a
specific exception type.

Precise by design: it only flags the exact anti-pattern that was eliminated
(broad except with a sole ``pass``/``continue`` body), so it has no false
positives on handlers that log, re-raise, or do real recovery work.
"""

import ast
from pathlib import Path
from tests._repo_scan import parse_source

REPO_ROOT = Path(__file__).resolve().parent.parent
REVIEW = REPO_ROOT / "review"

# The reusable widget library. It may not import ``review`` (that is the
# whole point of the package), so it uses ``util.aw_safe`` — the same helper the
# Qt-free pipeline side uses — rather than ``core.safe``. It was the
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
# core.safe). Guarded so these don't silently regress either.
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
        tree = parse_source(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _is_broad(node) and _silent_body(node):
                kind = type(node.body[0]).__name__.lower()
                out.append(f"{path.relative_to(REPO_ROOT)}:{node.body[0].lineno} (silent {kind})")
    return out


def _walk(root):
    return [p for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts]


def test_no_silent_broad_except_in_review():
    offenders = _offenders(_walk(REVIEW))
    assert not offenders, (
        "Silent broad-except handler(s) found. Use "
        "core.safe.swallowed()/swallow() or catch a specific "
        "exception type instead of a bare pass/continue:\n  "
        + "\n  ".join(offenders)
    )


def test_no_silent_broad_except_in_widgets():
    offenders = _offenders(_walk(WIDGETS))
    assert not offenders, (
        "Silent broad-except handler(s) found in widgets/. Use "
        "util.aw_safe.swallowed()/swallow() — widgets/ cannot import "
        "core.safe — or catch a specific exception type instead of "
        "a bare pass/continue:\n  " + "\n  ".join(offenders)
    )


def test_no_silent_broad_except_in_datasets():
    """The Datasets pane mixes both helpers: its Qt-free halves (`_common/`,
    the `*_compute` planners, `apply.py`, the workers) use `util.aw_safe`,
    while the controllers and views may use `core.safe`. Either is
    fine; a bare pass is not."""
    offenders = _offenders(_walk(DATASETS))
    assert not offenders, (
        "Silent broad-except handler(s) found in datasets/. Use "
        "core.safe.swallowed()/swallow() on the Qt side, "
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


# ── the whole application surface ────────────────────────────────────────────
#
# The four tests above name the areas the rule was introduced for, one at a
# time, and each says which helper that area may use. Between them they left
# the root modules and `scripts/` uncovered, which is where the remaining
# handlers were: this module built its own scan surface (`_walk` over four
# hand-listed roots) rather than the shared one, so widening `_repo_scan` did
# not widen this. That is the same defect as the areas themselves — a rule
# applied to most of its domain and not the rest — so the rule now runs over
# everything `_repo_scan` calls application code.

#: Sites where swallowing really is the correct behaviour, keyed on
#: ``(file, line-relative-to-nothing)`` — a *site*, named by file and by the
#: function containing it, not a whole file. A file-level exemption would
#: excuse every future handler added to the same file.
#: ``test_silent_except_allowlist_is_live`` fails when an entry stops naming a
#: real handler.
SILENT_EXCEPT_ALLOWLIST = {
    # The crash reporter. Everything here runs *while* the app is already
    # failing — the last-resort log writer, the faulthandler arming, and
    # `sys.excepthook` itself. An exception escaping any of them loses the
    # crash report, which is the one artifact that makes a native crash
    # diagnosable (ARCHITECTURE §12.8). There is nowhere left to report to.
    ("startup/boot_diag.py", "boot"),
    ("startup/boot_diag.py", "arm_faulthandler"),
    ("startup/boot_diag.py", "_hook"),
    ("startup/boot_diag.py", "install_excepthook"),
    # The bootstrap's own last-resort note. `_quietly` reports a failed log
    # write to the real stderr; when *that* write fails too there is no
    # remaining channel to report on, which is the one honest case for a bare
    # pass. Everything else in this module now goes through `_quietly`.
    ("scripts/_bootstrap_lib.py", "_quietly"),
    # Rebuilding matplotlib's font cache. The handler's own comment states the
    # trade: "a functioning (if unlimited) font manager beats a crash", and the
    # fallback is a working app with the system font list rather than the
    # bundled one.
    ("startup/mpl_bootstrap.py", "configure"),
}


def _silent_sites():
    """``(file, innermost enclosing function, line)`` per silent broad handler.

    *Innermost* matters: ``startup.boot_diag.install_excepthook`` defines the ``_hook``
    it installs, and attributing ``_hook``'s handlers to the outer function
    would let one exemption cover two different functions' worth of swallowing.
    A handler outside any function is reported under ``"<module>"``.
    """
    from tests._repo_scan import python_sources, rel

    found = []
    for path in python_sources():
        try:
            tree = parse_source(path)
        except SyntaxError:  # pragma: no cover - the tree parses
            continue
        stack: list[str] = []

        def visit(node, stack=stack, where=rel(path)):
            is_fn = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            if is_fn:
                stack.append(node.name)
            if isinstance(node, ast.ExceptHandler) and _is_broad(node) \
                    and _silent_body(node):
                found.append((where, stack[-1] if stack else "<module>",
                              node.body[0].lineno))
            for child in ast.iter_child_nodes(node):
                visit(child)
            if is_fn:
                stack.pop()

        visit(tree)
    return found


def test_no_silent_broad_except_anywhere():
    offenders = [
        f"{file}:{line} (in {func})"
        for file, func, line in _silent_sites()
        if (file, func) not in SILENT_EXCEPT_ALLOWLIST
    ]
    assert not offenders, (
        "Silent broad-except handler(s) found. Use the swallow()/swallowed() "
        "pair for the area (core.safe on the GUI side, "
        "util.aw_safe elsewhere), or catch a specific exception type:\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_silent_except_allowlist_is_live():
    """Every exemption still names a real handler.

    An entry that has stopped describing anything reads as a reviewed decision
    while covering nothing, and goes on covering whatever lands next under the
    same name.
    """
    real = {(file, func) for file, func, _line in _silent_sites()}
    stale = sorted(SILENT_EXCEPT_ALLOWLIST - real)
    assert not stale, (
        "These allowlist entries no longer name a silent broad-except handler "
        "— delete them:\n  " + "\n  ".join(f"{file}::{func}" for file, func in stale)
    )
