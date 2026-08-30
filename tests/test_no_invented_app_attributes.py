"""A test may not invent an attribute on a real application object.

``monkeypatch.setattr(obj, "name", value, raising=False)`` **creates** ``name``
when the object does not have it. On a module that is fine and common — it is
how a lazily-imported symbol or an optional hook gets stubbed. On a *real
application object* it is a trap, because the thing under test then reads an
attribute the shipped application does not have, and the test passes.

That is not hypothetical. The recent-datasets feature shipped completely dead:
both call sites reached for ``ctx.window._qsettings``, which is a method of the
``AllWellApp`` main window and not of the ``WellViewerApp`` review widget that
``ctx.window`` actually is, with no forwarding between them. Nothing was ever
written and nothing was ever read. The full-app test that should have caught it
said::

    monkeypatch.setattr(loaded_app, "_qsettings", lambda: settings,
                        raising=False)

— which created the attribute and then verified the feature against it. Seventy-
six tests, three files and the whole guard family passed.

``tests/test_stale_app_attrs.py`` cannot see this one: its own docstring says it
"cannot catch an attribute that moved to a different object while keeping its
name", and ``_qsettings`` is a live token in ``allwell.py``.

**The rule.** Against one of the full-app fixtures, ``raising=False`` is
forbidden. If the attribute exists, the flag is unnecessary; if it does not, the
test is measuring something the application cannot do. Patch the module-level
seam the code actually goes through instead — which is also the seam a fast-lane
test can reach, so the pressure runs the right way.

Nothing here restricts ``raising=False`` on a module, a class, ``sys``, an
environment variable, or a stub the test built itself. Those are the cases it is
for.
"""

from __future__ import annotations

import ast

from pathlib import Path

from tests._repo_scan import parse_source

#: The fixtures that hand a test a real ``WellViewerApp``. ``conftest.py``
#: auto-marks anything using one of these ``slow``; they are the only objects in
#: the suite whose attribute surface is the shipped application's.
APP_FIXTURES = frozenset({
    "viewer_app", "loaded_app", "loaded_app_shape", "image_table_app",
})


def _offenders():
    out = []
    for path in sorted(Path(__file__).resolve().parent.glob("test_*.py")):
        for node in ast.walk(parse_source(path)):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "attr", None) != "setattr":
                continue
            if getattr(node.func.value, "id", None) != "monkeypatch":
                continue
            if not node.args or not isinstance(node.args[0], ast.Name):
                continue
            if node.args[0].id not in APP_FIXTURES:
                continue
            for kw in node.keywords:
                if (kw.arg == "raising"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is False):
                    name = (node.args[1].value
                            if len(node.args) > 1
                            and isinstance(node.args[1], ast.Constant)
                            else "?")
                    out.append(f"{path.name}:{node.lineno} — "
                               f"{node.args[0].id}.{name}")
    return out


def test_no_test_invents_an_attribute_on_a_real_app():
    offenders = _offenders()
    assert not offenders, (
        "these create an attribute the application does not have, and then "
        "test the feature against it:\n  " + "\n  ".join(offenders)
        + "\n\nIf the attribute exists, drop `raising=False`. If it does not, "
          "the test is measuring something the app cannot do — patch the "
          "module-level seam the code actually goes through."
    )
