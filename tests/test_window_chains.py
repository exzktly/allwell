"""A window opens at most one window above the main one.

Five kinds of surface each have one job; a window opened from a window opened
from the main window is three windows deep, and the one under it can no longer
be reached until the top one is dealt with. Manage Remote Servers → the host
form → the SSH key wizard (or the scheduler test) was exactly that, and both
are pages of the host form now.

This reads the syntax tree: every class that is a ``QDialog`` (directly, or
through another such class) and every construction of one inside another's
methods. Each such pair is a window opened from a window — two deep — and must
be on the roster below with its reason. A chain of three fails whatever the
roster says. Alerts (``QMessageBox``) and file choosers are not windows of the
application's and are not counted.
"""

from __future__ import annotations

import ast

from tests import _surface_guards as scan

#: ``(opener, opened)`` -> why a window opens a window here.
SANCTIONED: dict[tuple[str, str], str] = {
    ("ManageRemotesDialog", "RemoteFormDialog"):
        "kept: adding or editing one host, from the list of hosts",
    ("ManageRemotesDialog", "BootstrapLogDialog"):
        "kept: the live log of setting up a host, from the list of hosts",
    ("ManageRemotesDialog", "RemoteStorageDialog"):
        "kept: one host's storage, from the list of hosts",
    ("GrantedNodePanel", "SchedulerTestDialog"):
        "kept: testing a granted node, from the panel inside Manage Remote Servers",
    ("GrantedNodePanel", "GrantedNodeLogDialog"):
        "kept: a granted node's log, from the panel inside Manage Remote Servers",
}

#: Widgets that are not dialogs but live only inside one, so a window they open
#: is opened from that dialog.
LIVES_IN = {"GrantedNodePanel": "ManageRemotesDialog"}

#: The method that shows a dialog's content as a page of the window it is
#: called on (``RemoteFormDialog.show_page``): what it is handed is not a window.
PAGE_METHOD = "show_page"

_NOT_WINDOWS = {"QMessageBox", "QFileDialog", "QInputDialog", "QColorDialog", "QProgressDialog"}


def _dialog_classes(trees) -> set[str]:
    bases: dict[str, set[str]] = {}
    for _rel, tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases[node.name] = {b.id if isinstance(b, ast.Name) else getattr(b, "attr", "")
                                    for b in node.bases}
    dialogs = {"QDialog"}
    changed = True
    while changed:
        changed = False
        for name, parents in bases.items():
            if name not in dialogs and parents & dialogs:
                dialogs.add(name)
                changed = True
    return dialogs - {"QDialog"} - _NOT_WINDOWS


def window_pairs(trees=None) -> set[tuple[str, str]]:
    """``(opener class, opened dialog class)`` for every dialog constructed in a
    method of a dialog, or of a widget that lives only inside one."""
    trees = scan.app_trees() if trees is None else trees
    dialogs = _dialog_classes(trees)
    pairs = set()
    for _rel, tree in trees:
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            opener = LIVES_IN.get(cls.name, cls.name)
            if opener not in dialogs:
                continue
            for fn in (n for n in ast.walk(cls) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
                # What this method shows as a page: the first argument of each show_page call.
                paged = {ast.unparse(c.args[0]) for c in ast.walk(fn)
                         if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == PAGE_METHOD and c.args}
                assigned = {}
                for a in ast.walk(fn):
                    if isinstance(a, ast.Assign) and isinstance(a.value, ast.Call):
                        for t in a.targets:
                            assigned[id(a.value)] = ast.unparse(t)
                for node in ast.walk(fn):
                    if isinstance(node, ast.Call):
                        f = node.func
                        name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
                        if name in dialogs and name != cls.name and assigned.get(id(node)) not in paged:
                            pairs.add((cls.name, name))
    return pairs


def _depths(pairs) -> dict[str, int]:
    opened = {b for _a, b in pairs}
    depth: dict[str, int] = {}

    def of(name: str, seen=()) -> int:
        if name in seen:
            return 99
        parents = [LIVES_IN.get(a, a) for a, b in pairs if b == name]
        return 1 if not parents else 1 + max(of(p, (*seen, name)) for p in parents)

    for name in opened | {LIVES_IN.get(a, a) for a, _b in pairs}:
        depth[name] = of(name)
    return depth


def test_every_window_opened_from_a_window_is_sanctioned():
    found = sorted(window_pairs())
    unknown = [f"{a} -> {b}" for a, b in found if (a, b) not in SANCTIONED]
    assert not unknown, ("a window opens a window here: make it a page of the first, or roster the pair "
                         "with its reason:\n  " + "\n  ".join(unknown))


def test_no_window_chain_is_three_deep():
    deep = {name: d for name, d in _depths(window_pairs()).items() if d > 2}
    assert not deep, f"windows three or more deep above the main one: {deep}"


def test_the_roster_describes_only_real_pairs():
    gone = sorted(set(SANCTIONED) - window_pairs())
    assert not gone, f"these SANCTIONED pairs no longer exist — delete them: {gone}"


def test_the_guard_sees_a_three_deep_chain():
    """Proof that it bites: the chain this row removed, rebuilt in a fake file."""
    source = '''
from PySide6.QtWidgets import QDialog
class A(QDialog):
    def go(self): B(self).exec()
class B(QDialog):
    def go(self): C(self).exec()
class C(QDialog):
    pass
'''
    trees = [("fake.py", ast.parse(source))]
    pairs = window_pairs(trees)
    assert pairs == {("A", "B"), ("B", "C")}
    assert max(_depths(pairs).values()) == 3
    # The same chain with C shown as a page of B is two deep.
    paged = source.replace("def go(self): C(self).exec()", "def go(self):\n        c = C(self)\n        self.show_page(c)")
    pairs = window_pairs([("fake.py", ast.parse(paged))])
    assert pairs == {("A", "B")}
