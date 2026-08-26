"""Guardrail: a test must not name a ``WellViewerApp`` attribute that no longer
exists anywhere in the app.

The `ctx` migration keeps moving loose ``app._foo`` attributes onto typed state
(``ctx.state.*`` / ``ctx.runtime.*``) or into the widget registry. Production
call sites move with them — they have to, or the app breaks — but a test that
names the old attribute keeps parsing fine and fails only when it runs. Since
every such test uses a real-app fixture, it lives in the `slow` lane, so "when
it runs" used to mean "never". A full slow-lane sweep found three:
``app._open_export_style_panel`` and ``app._review_image_source_seg`` (both read,
both `AttributeError`), and ``app._tab_last_tab_name`` (a *write*, which is
worse — it raises nothing, it just parks a value nobody reads, quietly making
the test's setup inert while it keeps passing).

The check is deliberately coarse: an attribute counts as real if its name appears
*anywhere* in the app source, in any form. That covers plain attributes, class
constants, ``ctx.widgets.register("_lbl_grid", …)`` string keys, and names
reached through ``getattr``. It cannot catch an attribute that moved to a
different object while keeping its name — but it catches the whole
deleted-in-a-migration class, and it does it in the fast lane instead of a
nightly run.

Writes are checked as well as reads, precisely because that is the silent case.

A test sometimes needs to park something of its own on the app — most often a
parent widget kept alive so Qt does not collect the children under test. Name
those ``app._test_<something>``; the ``_test_`` prefix is exempt here and says
plainly that the app never reads it.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from tests._repo_scan import parse_source

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent

# The fixtures that yield a real ``WellViewerApp``. Mirrors
# ``tests/test_frozen_ctx_guardrail.py::_REAL_APP_FIXTURES`` — a test that
# receives one of these is talking to the real object, so its attribute names
# have to be real too. A test building its own SimpleNamespace mock may invent
# whatever attributes it likes.
_REAL_APP_FIXTURES = {
    "viewer_app", "loaded_app", "image_table_app",
    "loaded_app_shape", "loaded_app_multifov",
}

# Any underscore-leading identifier-ish token in the app source counts as known.
_TOKEN_RE = re.compile(r"_[A-Za-z][A-Za-z0-9_]*")


def _app_source_tokens() -> set:
    """Every ``_name`` token appearing in the application source.

    Repo-root modules are included because the shell (``allwell.py``) and the
    Analyze pane define app attributes too.
    """
    text = []
    for path in sorted((REPO_ROOT / "well_viewer").rglob("*.py")):
        text.append(path.read_text())
    for path in sorted(REPO_ROOT.glob("*.py")):
        text.append(path.read_text())
    for pkg in ("widgets", "services", "ui", "util", "pipeline"):
        for path in sorted((REPO_ROOT / pkg).rglob("*.py")):
            text.append(path.read_text())
    return set(_TOKEN_RE.findall("\n".join(text)))


def _real_app_names(fn_node: ast.AST) -> set:
    """Local names that refer to the real app inside *fn_node*.

    The fixture parameters themselves, plus anything assigned straight from one
    (``app = loaded_app``), which is what almost every test does.
    """
    args = fn_node.args
    params = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    names = params & _REAL_APP_FIXTURES
    if not names:
        return set()
    for node in ast.walk(fn_node):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Name)
                and node.value.id in names):
            names.add(node.targets[0].id)
    return names


def test_tests_do_not_name_removed_app_attributes():
    known = _app_source_tokens()
    violations = []
    for path in sorted(TESTS.rglob("*.py")):
        tree = parse_source(path)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            holders = _real_app_names(fn)
            if not holders:
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Attribute)
                        and isinstance(node.value, ast.Name)
                        and node.value.id in holders
                        and node.attr.startswith("_")
                        and not node.attr.startswith("_test_")
                        and node.attr not in known):
                    how = "writes" if isinstance(node.ctx, ast.Store) else "reads"
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno}  "
                        f"{how} {node.value.id}.{node.attr} — no such attribute "
                        "anywhere in the app")
    assert not violations, (
        "Test(s) naming a WellViewerApp attribute that no longer exists. It "
        "moved to typed state or the widget registry — find its current home "
        "and use that:\n  " + "\n  ".join(violations))
