"""The scans behind the surface guards (``SURFACES_TRACKER.md`` §5.2).

One scan per rule, each over the shared parsed trees (``tests/_repo_scan``), each
returning sites as ``(file, function, line, what)``. A guard holds its sites
against a roster keyed by *file and function* whose value names the tracker row
that removes the site; an entry that no longer matches a site fails, so every
roster only shrinks (CLAUDE.md §7.5 #10).

The functions take the trees as an argument so ``tests/test_guards_bite.py`` can
hand each a synthetic file containing the violation it exists to catch.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._repo_scan import REPO_ROOT, parsed_sources

Site = tuple[str, str, int, str]


def app_trees() -> list[tuple[str, ast.Module]]:
    """Every application source file but ``scripts/`` (tools, not the app)."""
    out = []
    for path, tree in parsed_sources():
        rel = Path(path).resolve().relative_to(REPO_ROOT).as_posix()
        if not rel.startswith("scripts/"):
            out.append((rel, tree))
    return out


class _Scopes(ast.NodeVisitor):
    """Walks a tree keeping the dotted name of the enclosing function."""

    def __init__(self) -> None:
        self.stack: list[str] = []
        self.loops = 0

    def where(self) -> str:
        return ".".join(self.stack) or "<module>"

    def _scoped(self, node) -> None:
        self.stack.append(node.name)
        outer, self.loops = self.loops, 0
        self.generic_visit(node)
        self.loops = outer
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scoped

    def _loop(self, node) -> None:
        self.loops += 1
        self.generic_visit(node)
        self.loops -= 1

    visit_For = visit_While = _loop


def _callee(node: ast.Call) -> str:
    """``QMenu`` for ``QMenu(...)`` and for ``QtWidgets.QMenu(...)``."""
    f = node.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def _aliases(tree: ast.Module, module: str, name: str) -> set[str]:
    """Every local name *name* of *module* is imported under (an alias too)."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            out |= {a.asname or a.name for a in node.names if a.name == name}
    return out


def _qt_aliases(tree: ast.Module, name: str) -> set[str]:
    out = {name}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("PySide6"):
            out |= {a.asname or a.name for a in node.names if a.name == name}
    return out


# ── 1. a widget in a menu ─────────────────────────────────────────────────────

def widget_actions(trees) -> list[Site]:
    """Every construction of a ``QWidgetAction``: a menu hosting a widget is a
    popover by another name."""
    sites: list[Site] = []
    for rel, tree in trees:
        names = _qt_aliases(tree, "QWidgetAction")

        class V(_Scopes):
            def visit_Call(self, node):
                if _callee(node) in names:
                    sites.append((rel, self.where(), node.lineno, "QWidgetAction"))
                self.generic_visit(node)
        V().visit(tree)
    return sites


# ── 2. a popover ──────────────────────────────────────────────────────────────

POPOVER_HOME = "widgets/popover.py"


def popovers(trees) -> list[Site]:
    """Every construction of ``widgets.popover.Popover`` outside its module —
    under whatever name it was imported, and through the module too
    (``popover.Popover(...)``)."""
    sites: list[Site] = []
    for rel, tree in trees:
        if rel == POPOVER_HOME:
            continue
        names = _aliases(tree, "widgets.popover", "Popover")
        modules = _aliases(tree, "widgets", "popover")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {a.asname or a.name for a in node.names if a.name == "widgets.popover"}

        class V(_Scopes):
            def visit_Call(self, node):
                f = node.func
                direct = isinstance(f, ast.Name) and f.id in names
                via = (isinstance(f, ast.Attribute) and f.attr == "Popover"
                       and ast.unparse(f.value) in modules)
                if direct or via:
                    sites.append((rel, self.where(), node.lineno, "Popover"))
                self.generic_visit(node)
        V().visit(tree)
    return sites


# ── 3. a menu holds commands only ─────────────────────────────────────────────

def _is_true(node) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def checkable_menu_actions(trees) -> list[Site]:
    """Every ``QAction`` made checkable: ``a.setCheckable(True)`` on a name
    bound from ``QAction(...)`` / ``menu.addAction(...)`` /
    ``add_menu_action(...)`` in the same function, the chained
    ``menu.addAction(...).setCheckable(True)``, and ``QAction(..., checkable=True)``.
    A tick in a menu is a setting, and a setting belongs on an inspector page."""
    sites: list[Site] = []
    makers = {"QAction", "addAction", "add_menu_action", "addSection", "owned_action"}
    for rel, tree in trees:
        class V(_Scopes):
            def __init__(self):
                super().__init__()
                self.actions: list[set[str]] = [set()]

            def _scoped(self, node):
                self.actions.append(set())
                super()._scoped(node)
                self.actions.pop()

            visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scoped

            def visit_Assign(self, node):
                if isinstance(node.value, ast.Call) and _callee(node.value) in makers:
                    for t in node.targets:
                        self.actions[-1].add(ast.unparse(t))
                self.generic_visit(node)

            def visit_Call(self, node):
                f = node.func
                if _callee(node) == "QAction" and any(
                        k.arg == "checkable" and _is_true(k.value) for k in node.keywords):
                    sites.append((rel, self.where(), node.lineno, "QAction(checkable=True)"))
                if (isinstance(f, ast.Attribute) and f.attr == "setCheckable"
                        and node.args and _is_true(node.args[0])):
                    recv = f.value
                    chained = isinstance(recv, ast.Call) and _callee(recv) in makers
                    if chained or ast.unparse(recv) in self.actions[-1]:
                        sites.append((rel, self.where(), node.lineno, "checkable action"))
                self.generic_visit(node)
        V().visit(tree)
    return sites


def submenus(trees) -> list[Site]:
    """Every ``addMenu`` call: a menu opened from a menu."""
    sites: list[Site] = []
    for rel, tree in trees:
        class V(_Scopes):
            def visit_Call(self, node):
                if isinstance(node.func, ast.Attribute) and node.func.attr == "addMenu":
                    sites.append((rel, self.where(), node.lineno, "addMenu"))
                self.generic_visit(node)
        V().visit(tree)
    return sites


# ── 4. one way to pick several ────────────────────────────────────────────────

MULTI_SELECT_HOME = "widgets/multi_select.py"
OLD_PICKER = "widgets.checkable_dropdown"


def old_picker_importers(trees) -> list[Site]:
    sites: list[Site] = []
    for rel, tree in trees:
        if rel == OLD_PICKER.replace(".", "/") + ".py":
            continue
        for node in ast.walk(tree):
            hit = (isinstance(node, ast.ImportFrom) and (
                node.module == OLD_PICKER or (node.module == "widgets" and any(
                    a.name == "checkable_dropdown" for a in node.names)))) or (
                isinstance(node, ast.Import) and any(a.name == OLD_PICKER for a in node.names))
            if hit:
                sites.append((rel, "<module>", node.lineno, "imports checkable_dropdown"))
    return sites


def check_list_builds(trees) -> list[Site]:
    """A check-list built by hand: a checkable action or a ``QCheckBox`` made
    *inside a loop*, in a function that also builds a menu, a widget action or
    a popover to put them in. One row per item is what a multi-select is."""
    sites: list[Site] = []
    hosts = {"QMenu", "QWidgetAction", "Popover", "make_popover_button", "addMenu"}
    for rel, tree in trees:
        if rel == MULTI_SELECT_HOME:
            continue

        class V(_Scopes):
            def _scoped(self, node):
                if not isinstance(node, ast.ClassDef):
                    calls = {_callee(n) for n in ast.walk(node) if isinstance(n, ast.Call)}
                    names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                    self.has_host = bool(hosts & (calls | names)) or any(
                        isinstance(n, ast.Attribute) and n.attr in ("menu", "exec_menu")
                        for n in ast.walk(node))
                super()._scoped(node)

            visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scoped
            has_host = False

            def visit_Call(self, node):
                f = node.func
                in_loop = self.loops > 0
                tick = (isinstance(f, ast.Attribute) and f.attr == "setCheckable"
                        and node.args and _is_true(node.args[0]))
                box = _callee(node) == "QCheckBox"
                if in_loop and self.has_host and (tick or box):
                    sites.append((rel, self.where(), node.lineno,
                                  "check-list row" if tick else "QCheckBox in a loop"))
                self.generic_visit(node)
        V().visit(tree)
    # One entry per function is enough to roster it.
    seen, out = set(), []
    for s in sites:
        if (s[0], s[1]) not in seen:
            seen.add((s[0], s[1]))
            out.append(s)
    return out


# ── 5. nothing by gesture alone ───────────────────────────────────────────────

def gesture_sites(trees) -> list[Site]:
    """Where something is reached by a gesture: a custom context menu
    (``customContextMenuRequested``, ``contextMenuEvent``), a double click
    (``mouseDoubleClickEvent``, a ``doubleClicked`` / ``itemDoubleClicked``
    connection) and a control revealed by hovering (an ``enterEvent`` that
    shows something)."""
    sites: list[Site] = []
    for rel, tree in trees:
        class V(_Scopes):
            def visit_FunctionDef(self, node):
                if node.name in ("mouseDoubleClickEvent", "contextMenuEvent"):
                    sites.append((rel, ".".join([*self.stack, node.name]), node.lineno, node.name))
                if node.name == "enterEvent" and any(
                        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr in ("show", "setVisible", "raise_")
                        for n in ast.walk(node)):
                    sites.append((rel, ".".join([*self.stack, node.name]), node.lineno,
                                  "hover reveals a control"))
                self._scoped(node)

            def visit_Attribute(self, node):
                if node.attr in ("customContextMenuRequested", "doubleClicked",
                                 "itemDoubleClicked", "cellDoubleClicked", "dblclick"):
                    sites.append((rel, self.where(), node.lineno, node.attr))
                if node.attr in ("Enter", "HoverEnter") and ast.unparse(node.value).endswith("QEvent"):
                    sites.append((rel, self.where(), node.lineno, "hover reveals a control"))
                self.generic_visit(node)

            def visit_Constant(self, node):
                # ``getattr(event, "dblclick", False)``: the same read, as a string.
                if node.value == "dblclick":
                    sites.append((rel, self.where(), node.lineno, "dblclick"))
        V().visit(tree)
    seen, out = set(), []
    for s in sites:
        if (s[0], s[1], s[3]) not in seen:
            seen.add((s[0], s[1], s[3]))
            out.append(s)
    return out


# ── roster arithmetic, shared ─────────────────────────────────────────────────

def unrostered(sites: list[Site], roster: dict) -> list[str]:
    return [f"{f}:{line} {fn}() — {what}" for f, fn, line, what in sites
            if (f, fn) not in roster]


def stale(sites: list[Site], roster: dict) -> list[str]:
    live = {(f, fn) for f, fn, _l, _w in sites}
    return [f"{f} {fn}()" for (f, fn) in sorted(roster) if (f, fn) not in live]
