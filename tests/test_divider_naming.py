"""Every divider frame carries a name that makes it paint (ARCHITECTURE §8).

A ``QFrame`` whose shape is ``HLine`` / ``VLine`` paints Qt's default etched
border unless a stylesheet rule replaces it. ``theme.qss`` supplies that rule —
``background-color: border_subtle`` plus the load-bearing ``border: none`` — but
only for frames carrying one of the sanctioned object names or the
``hline``/``vline`` dynamic properties. An unnamed one keeps Qt's default, which
is derived from the widget palette rather than the theme.

That is not a subtle difference. Measured against this app's dark surfaces, on
the default theme:

    HLine + ``#Separator``   1 px   #1b2331   (Colors.border_subtle)
    HLine + ``#HRule``       1 px   #1b2331
    HLine, unnamed           3 px   #e6e9ef   (near-white, three times as thick)

So an unnamed divider is not merely off-token: it is a pale bar three times the
intended weight. Ten of them were live when this guard was written.

Two shapes of call site the naive version of this scan misses, both of which
exist in the tree:

* ``QFrame`` is sometimes imported under an alias (``_QFrame``), so matching the
  attribute owner by name alone drops those sites.
* Batch Export's split divider chooses its shape at runtime
  (``QFrame.HLine if stacked else QFrame.VLine``), so the argument is an
  ``IfExp`` rather than an attribute. It is also the reason the sanctioned name
  has to be chosen in the same place as the shape — ``#HRule`` pins
  ``max-height: 1px`` and ``#VRule`` pins ``max-width: 1px``, so a fixed name
  would pin the hairline along whichever axis the frame is *not* currently
  drawn on.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Roots that hold Qt widget code. ``tests/`` is excluded: a test may build a
# throwaway frame to measure exactly the behaviour documented above.
SCAN_ROOTS = ("well_viewer", "widgets", "datasets", "ui")
SCAN_FILES = ("allwell.py", "analyze_tab.py", "datasets_tab.py")

# The names ``theme.qss`` styles. ``Separator`` is the Review shell's,
# ``HRule`` / ``VRule`` are the Analyze pane's and the run dashboard's.
SANCTIONED_NAMES = frozenset({"Separator", "HRule", "VRule"})
SANCTIONED_PROPS = frozenset({"hline", "vline"})

# ``widgets/mpl_toolbar.py`` is the documented exception: its separator carries
# its own object name (``MplToolbarSep``) and its own rule in the toolbar's
# per-instance stylesheet, because that widget styles itself rather than reading
# the application QSS. Documented in ARCHITECTURE §8.
ALLOWLIST = frozenset({"widgets/mpl_toolbar.py"})


def _py_files():
    for root in SCAN_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path
    for name in SCAN_FILES:
        path = REPO_ROOT / name
        if path.exists():
            yield path


def _is_line_shape(node: ast.AST) -> bool:
    """True if *node* evaluates to ``QFrame.HLine`` / ``.VLine``, under any
    alias for ``QFrame`` and through a conditional expression."""
    if isinstance(node, ast.Attribute):
        return node.attr in {"HLine", "VLine"}
    if isinstance(node, ast.IfExp):
        return _is_line_shape(node.body) or _is_line_shape(node.orelse)
    return False


def _sanctioned_name_arg(node: ast.AST) -> bool:
    """True if *node* is a sanctioned object name, or a conditional picking
    between sanctioned names (the runtime-reorienting divider)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value in SANCTIONED_NAMES
    if isinstance(node, ast.IfExp):
        return _sanctioned_name_arg(node.body) and _sanctioned_name_arg(node.orelse)
    return False


def _receiver(node: ast.Call) -> str | None:
    """Source text of the object a method is being called on, e.g. ``sep`` or
    ``self._split_sep`` — the key both halves of the pair are matched by."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    try:
        return ast.unparse(func.value)
    except Exception:
        return None


def _named_receivers(scope: ast.AST) -> set[str]:
    """Receivers given a sanctioned name (or property) anywhere in *scope*."""
    named: set[str] = set()
    for node in ast.walk(scope):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        recv = _receiver(node)
        if recv is None or not node.args:
            continue
        if node.func.attr == "setObjectName" and _sanctioned_name_arg(node.args[0]):
            named.add(recv)
        elif (node.func.attr == "setProperty"
              and isinstance(node.args[0], ast.Constant)
              and node.args[0].value in SANCTIONED_PROPS):
            named.add(recv)
    return named


def test_no_unnamed_divider_frames():
    offenders: list[str] = []
    for path in _py_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in ALLOWLIST:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))

        # A receiver is "named" if it is named anywhere in the same enclosing
        # function (or at module level) — the two calls are usually adjacent but
        # need not be, and the reorienting divider names itself in a helper.
        scopes = [tree] + [n for n in ast.walk(tree)
                           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for scope in scopes:
            named = _named_receivers(scope)
            for node in ast.walk(scope):
                if (not isinstance(node, ast.Call)
                        or not isinstance(node.func, ast.Attribute)
                        or node.func.attr != "setFrameShape"
                        or not node.args
                        or not _is_line_shape(node.args[0])):
                    continue
                recv = _receiver(node)
                if recv is not None and recv not in named:
                    offenders.append(f"{rel}:{node.lineno} — {recv}.setFrameShape(...)")

    offenders = sorted(set(offenders))
    assert not offenders, (
        "Divider frame(s) with no sanctioned object name (ARCHITECTURE §8). "
        "Each paints Qt's default etched border — 3 px of near-white on this "
        "app's surfaces — instead of a 1 px border_subtle hairline. Build it "
        "with `ui_helpers.build_hline_separator` / `build_vline_separator`, or "
        "give it one of "
        f"{sorted(SANCTIONED_NAMES)}:\n  " + "\n  ".join(offenders)
    )
