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

#: The seam itself is the one place allowed to make the call.
_ALLOWED = {"widgets/menu_exec.py"}


def _offenders() -> list[str]:
    out: list[str] = []
    for path in python_sources():
        where = rel(path)
        if where in _ALLOWED:
            continue
        try:
            tree = ast.parse(path.read_text(), where)
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
                if "menu" in name.lower():
                    out.append(
                        f"{where}:{node.lineno} calls {name}.{fn.attr}() directly — "
                        "use widgets.menu_exec.exec_menu(menu, pos)"
                    )
    return out


def test_no_module_opens_a_context_menu_directly():
    offenders = _offenders()
    assert not offenders, (
        "a raw QMenu.exec hangs the offscreen test run with no attribution, "
        "and the modal-dialog fixture cannot patch it:\n"
        + "\n".join(f"  {o}" for o in offenders)
    )
