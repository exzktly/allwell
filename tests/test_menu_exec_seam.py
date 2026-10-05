"""No production module calls ``QMenu.exec`` directly.

Opening a context menu blocks in Qt's C++ event loop, and under the offscreen
QPA nothing delivers the click that closes it — pytest's signal-based
``--timeout`` cannot interrupt a C++ loop, so the run hangs with no attribution.
``tests/conftest.py::_no_modal_dialogs`` turns that hang into a named failure
for every dialog opener, and it cannot do the same for ``QMenu``: Shiboken
exposes ``exec`` as a static/instance overload, so ``monkeypatch.setattr(QMenu,
"exec", …)`` writes a class attribute that instance lookup then ignores. Adding
``QMenu`` to that list looked like coverage and measured nothing.

So the call goes through ``widgets/menu_exec.exec_menu``, which is ordinary
Python and therefore patchable, and this guard keeps it that way.

It also rejects ``from widgets.menu_exec import exec_menu``: that binds the
function into the importing module's globals, where a patch applied to
``widgets.menu_exec`` can no longer reach it — the seam would be there and
still unguardable. Import the module, call through it.
"""
from __future__ import annotations

import ast

from tests._repo_scan import python_sources, rel
from tests._repo_scan import parse_source

#: The seam itself is the one place allowed to make the call.
_ALLOWED = {"widgets/menu_exec.py"}


def _offenders() -> list[str]:
    out: list[str] = []
    for path in python_sources():
        where = rel(path)
        if where in _ALLOWED:
            continue
        try:
            tree = parse_source(path)
        except SyntaxError:  # pragma: no cover - a file we cannot parse
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "widgets.menu_exec":
                for alias in node.names:
                    if alias.name == "exec_menu":
                        out.append(
                            f"{where}:{node.lineno} imports exec_menu by name — "
                            "`from widgets import menu_exec` and call through it"
                        )
                continue
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr in ("exec", "exec_"):
                target = fn.value
                name = getattr(target, "id", "") or getattr(target, "attr", "")
                if "menu" in name.lower() or name in _menus_built_in(tree):
                    out.append(
                        f"{where}:{node.lineno} calls {name}.{fn.attr}() directly — "
                        "use widgets.menu_exec.exec_menu(menu, pos)"
                    )
    return out


def _built_by_a_call(scope) -> dict[str, int]:
    """Names assigned from a call anywhere in *scope*, with the line."""
    return {t.id: n.lineno for n in ast.walk(scope) if isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Call)
            for t in n.targets if isinstance(t, ast.Name)}


def _menus_built_in(tree) -> set[str]:
    """Names bound to a ``*Menu(...)`` construction anywhere in the module.

    The name test above only sees a variable called something with "menu" in
    it; ``m = QMenu(b); m.exec(pos)`` walked past it.
    """
    out = set()
    for n in ast.walk(tree):
        if (isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)):
            f = n.value.func
            called = getattr(f, "id", "") or getattr(f, "attr", "")
            if called.endswith("Menu"):
                out |= {t.id for t in n.targets if isinstance(t, ast.Name)}
    return out


def per_open_menus_left_alive(tree) -> list[int]:
    """Lines of ``menu_exec.exec_menu(m, …)`` where ``m`` was built in the same
    function: a menu built for one opening must be disposed of after it."""
    lines = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        built = _built_by_a_call(fn)
        for call in ast.walk(fn):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "exec_menu" and call.args
                    and isinstance(call.args[0], ast.Name)
                    and call.args[0].id in built):
                lines.append(call.lineno)
    return sorted(set(lines))


def test_a_menu_built_to_be_opened_once_is_disposed_of():
    """Built per opening and parented to a long-lived widget, a menu lives as
    long as that widget unless something deletes it; five openings of a
    job-queue row's menu left ten alive. ``exec_transient_menu`` deletes it."""
    sites = [f"{rel(p)}:{line}" for p in python_sources() if rel(p) not in _ALLOWED
             for line in per_open_menus_left_alive(parse_source(p))]
    assert not sites, (
        "These open a menu they just built with exec_menu, so it is never freed; "
        "use widgets.menu_exec.exec_transient_menu(menu, pos):\n  "
        + "\n  ".join(sites))


def test_no_module_opens_a_context_menu_directly():
    offenders = _offenders()
    assert not offenders, (
        "a raw QMenu.exec hangs the offscreen test run with no attribution, "
        "and the modal-dialog fixture cannot patch it:\n"
        + "\n".join(f"  {o}" for o in offenders)
    )
