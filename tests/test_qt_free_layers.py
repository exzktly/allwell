"""The layers ARCHITECTURE calls Qt-free are checked, not just described.

`domain/` had held a 501-line Qt widget builder — a `QComboBox` subclass pair and
an `install_…_controls` function — while §5's layer diagram called the package
"Qt-free models + pure logic". Four things had to line up for that to survive:

1. **No guard covered `domain/` for Qt, and none ever had.** The three Qt-free
   contracts were scoped by hand — `test_pipeline_is_self_sufficient` walked a
   frozen file list, `test_services_have_no_qt_imports` walked `services/`, and
   `test_widgets_do_not_import_the_app` guards a *direction* rather than Qt.
   `tests/test_layering.py` does cover `domain/`, but only against imports of
   `well_viewer.features` — a different axis entirely.
2. **The naming guard cannot see outside `features/`.** Under `features/`, a Qt
   builder without a `_view` suffix fails `test_feature_module_naming`; that is
   the one automated signal saying "this file builds widgets", and it was scoped
   away from the directory the file landed in.
3. **§3's "known exceptions" note reads as permission**, since it records that
   `domain/` mixes suffixed and bare module names.
4. **§16 listed the rule neither as enforced nor as unenforced**, and a rule
   absent from both lists reads as covered.

The file itself carried the tell: it re-exported from `domain/fold_change_scopes`
with the comment "They live there so the unit tests can import them without
pulling in PySide6". The Qt was known about and the pure half was deliberately
split out; nothing said the Qt half had to leave `domain/` too.

`widgets` is forbidden alongside Qt itself: importing a widget pulls Qt
transitively, the same hole `test_services_do_not_import_well_viewer` plugs for
`services/`.

`persistence/` is deliberately **not** listed. ARCHITECTURE claims Qt-freeness
for `pipeline`, `util`, `services` and `domain` only, and
`persistence/bar_groups.py`, `registry.py` and `sample_definitions.py`
legitimately use `QFileDialog` / `QMessageBox` / `QTimer`.
"""

from __future__ import annotations

import ast
from pathlib import Path
from tests._repo_scan import parse_source

REPO_ROOT = Path(__file__).resolve().parent.parent
QT_ROOTS = {"PySide6", "PyQt5", "PyQt6", "shiboken6"}
FORBIDDEN = QT_ROOTS | {"widgets"}

#: Packages whose whole contents must be Qt-free, with the claim each one rests on.
QT_FREE_ROOTS = {
    "pipeline": "§3 — the analysis-pipeline package, run headless on a worker",
    "util": "§3 — general Qt-free helpers the pipeline imports",
    "services": "§13 — CLI-reusable, so no Qt and no well_viewer",
    "well_viewer/domain": "§5 — Qt-free models + pure logic",
    "well_viewer/data": "§5 — IO + decode",
}

#: The one file `well_viewer/data` is still allowed to build widgets in.
#: `crop_tool.py` is a rubber-band drag interaction (`QRubberBand`, `QLabel`,
#: `QPushButton`) that hid behind §5's old "Qt-free where practical" hedge —
#: untestable phrasing, which is why the hedge is gone and this is a number
#: instead. Whether it should move to `features/crop/` beside `region.py` and
#: `controller.py` is a separate decision this list makes visible rather than
#: silent. Shrink-only: never add an entry without writing down why.
ALLOWLIST = {
    "well_viewer/data/crop_tool.py",
}


def _imports(tree: ast.Module):
    """Every import at ANY nesting — a function-local one still fails on a
    headless worker, and still drags the graph into a feature-subset build."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node, alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node, node.module.split(".")[0]


def test_qt_free_layers_import_no_qt():
    offenders = []
    for root, why in sorted(QT_FREE_ROOTS.items()):
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in ALLOWLIST:
                continue
            for node, top in _imports(parse_source(path)):
                if top in FORBIDDEN:
                    offenders.append(f"{rel}:{node.lineno} imports {top}  ({why})")
    assert not offenders, (
        "Qt reached a layer ARCHITECTURE describes as Qt-free. A Qt builder "
        "belongs in `well_viewer/core/` (cross-cutting) or the owning "
        "`features/<name>/` package, named `*_view.py`; the pure half it needs "
        "can stay where it is:\n  " + "\n  ".join(offenders)
    )


def test_the_allowlist_is_live():
    """An allowlist entry for a file that no longer breaks the rule — or no
    longer exists — is a ratchet that has quietly stopped biting."""
    stale = []
    for rel in sorted(ALLOWLIST):
        path = REPO_ROOT / rel
        if not path.exists():
            stale.append(f"{rel} (no such file)")
            continue
        tops = {top for _, top in _imports(parse_source(path))}
        if not (tops & FORBIDDEN):
            stale.append(f"{rel} (imports no Qt any more — drop it from ALLOWLIST)")
    assert not stale, "stale Qt-free allowlist entries:\n  " + "\n  ".join(stale)


# ── the transitive half ──────────────────────────────────────────────────────

#: Module-scope imports only. A *function-local* import of a Qt-touching module
#: is exactly how a layer stays importable while still reaching Qt on demand —
#: it is the fix for this class of defect, not an instance of it — so following
#: one here would report every correct solution as a violation.
#:
#: "Module scope" is not "``tree.body``": an import inside a module-level ``if``
#: or ``try`` still runs at import time, and a first draft of this that walked
#: only the top-level statement list was evaded by
#: ``if os.environ.get(...) != "1": from well_viewer.core.ui_helpers import …``.
#: So the whole tree is walked and only imports *inside a function or class
#: body* are skipped.
def _module_scope_imports(tree: ast.Module) -> set[str]:
    deferred: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            deferred.update(id(inner) for inner in ast.walk(node) if inner is not node)
    out: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in deferred:
            continue
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module)
    return out


def _module_file(dotted: str) -> Path | None:
    base = REPO_ROOT / dotted.replace(".", "/")
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _qt_path_from(start: str) -> list[str] | None:
    """The shortest module-scope import chain from *start* to Qt, or None."""
    from collections import deque

    queue = deque([(start, [start])])
    seen = {start}
    while queue:
        dotted, path = queue.popleft()
        f = _module_file(dotted)
        if f is None:
            continue
        try:
            tree = parse_source(f)
        except SyntaxError:
            continue
        for imported in sorted(_module_scope_imports(tree)):
            top = imported.split(".")[0]
            # ``FORBIDDEN``, not ``QT_ROOTS``: importing a widget pulls Qt in
            # transitively, which is why the direct check forbids ``widgets``
            # too. Terminating only on Qt itself let a hop whose endpoint was
            # ``widgets`` walk straight through — the shape of the bug this
            # test was written for, one module further out.
            if top in FORBIDDEN:
                return path + [imported]
            if top == "well_viewer" and imported not in seen:
                seen.add(imported)
                queue.append((imported, path + [imported]))
    return None


def test_a_qt_free_layer_stays_importable_without_qt():
    """Reaching Qt one hop away is the same failure as importing it directly.

    ``test_qt_free_layers_import_no_qt`` reads each file's own import list, so a
    detour through ``core/`` is invisible to it — and every docstring in these
    packages cites that test as the guarantee. ``domain/stats_compute`` imported
    two pure ``ctx`` readers from ``core.channel_state_controller``, which
    imported ``core.ui_helpers``, which imports ``PySide6.QtCore``: ``import
    well_viewer.domain.fold_change`` failed with ``ImportError:
    libQt6Core.so.6`` on a machine with no Qt libraries.

    The chain is reported in full, because the fix is almost never in the file
    that fails — it is at the hop where a pure module reached for a Qt-touching
    one at module scope.
    """
    offenders: list[str] = []
    for root, why in sorted(QT_FREE_ROOTS.items()):
        if not root.startswith("well_viewer"):
            continue    # the direct check already covers packages outside it
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in ALLOWLIST or path.name == "__init__.py":
                continue
            dotted = rel[:-3].replace("/", ".")
            chain = _qt_path_from(dotted)
            if chain is not None and len(chain) > 1:
                offenders.append(f"{why}\n      " + "\n      → ".join(chain))
    assert not offenders, (
        "these modules reach Qt through another module's import, so importing "
        "them on a machine without Qt raises. Make the offending hop a "
        "function-local import, or move the pure names the caller needs into "
        "the pure layer:\n  " + "\n  ".join(offenders))
