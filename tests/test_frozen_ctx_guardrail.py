"""Guardrail: catch the ``ctx``-migration regression class statically.

``ViewerContext`` is a ``frozen=True`` dataclass with a *closed* public surface
(``state`` / ``bus`` / ``widgets`` / ``runtime`` / ``app`` + the named service
accessors). Two mistakes slipped through the strangler migration repeatedly and
were invisible to the fast lane because they only bite the ``loaded_app`` (slow)
paths:

1. Treating ``ctx`` as a scratch bag — ``setattr(ctx, "_x", …)`` (raises
   ``FrozenInstanceError``) or ``getattr/hasattr(ctx, "_x")`` / ``ctx._x`` (always
   the miss branch on a frozen instance). Real fallout this caught: the
   fold-change combo stash crashed every Bar/Line tab build; ``redraw_bars``
   guarded on ``hasattr(ctx, "_ax_bar_mean")`` and so *never redrew*.

2. Calling a ctx-native function with the raw god-object — ``foo(app)`` /
   ``foo(self)`` where ``foo``'s first parameter is ``ctx``. Real fallout: the
   multivariate channel hook and the FOV-replicates toggle both handed ``app``
   to a ctx function and blew up on ``app.state`` / ``app.runtime``. The
   module-qualified spelling ``sc.foo(app)`` counts too, whenever ``sc`` is a
   first-party *module* alias bound in the same file — it resolves to the free
   function just as unambiguously as the bare name. ``test_cell_browser_plate_
   sync_split`` wrote both of its raw-app calls that way, so the qualified form
   is not a corner case; one of them failed loudly, and the other was swallowed
   by the callee's own ``try`` and left the test asserting nothing.

These AST checks fail CI on either pattern so a future ctx conversion can't
silently break a plot path again. Comments/docstrings are ignored (AST, not
regex). Widget/state attributes reached *through* a public accessor
(``ctx.window._notebook``, ``ctx.state.plot._x``) are fine — only ``_x`` reached
*directly* on ``ctx`` is banned. Bridging with ``app._ctx`` is the sanctioned
form and is never flagged (it is an attribute access, not a bare ``app``).
"""
from __future__ import annotations

import ast
from pathlib import Path

_PKG = Path(__file__).resolve().parent.parent / "well_viewer"
_TESTS = Path(__file__).resolve().parent
_DYNATTR = {"getattr", "setattr", "hasattr"}


def _py_files():
    return sorted(_PKG.rglob("*.py"))


def _call_site_files():
    """Files scanned for *call sites*: the package plus the test suite.

    The suite has to be in scope. ``tests/test_qc_reopen_apply.py`` called
    ``save_to_data_dir(app)`` from the day that function moved to ``ctx``, and
    because ``ctx.window`` on a real ``WellViewerApp`` resolves to Qt's own
    ``QWidget.window`` method rather than raising, the failure surfaced as an
    ``AttributeError`` two frames later instead of at the boundary. The test
    uses the ``loaded_app`` fixture, so it is marked ``slow`` and never ran in
    CI — it sat red for months. This check is cheap and static, so it catches
    that class in the fast lane no matter which lane the test itself lives in.

    Function *definitions* still come from ``_py_files()`` only: a helper
    defined inside a test module is local to it and says nothing about the
    package's ctx contract.
    """
    return sorted(_PKG.rglob("*.py")) + sorted(_TESTS.rglob("*.py"))


def _is_ctx_name(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "ctx"


def test_no_frozen_ctx_underscore_access():
    """No ``ctx._x`` or ``getattr/setattr/hasattr(ctx, "_x")`` — the frozen
    context has no underscore attributes; such access silently misses or
    (for setattr) crashes."""
    violations = []
    for path in _py_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            # ctx._x   (direct underscore attribute on the ctx name)
            if (isinstance(node, ast.Attribute) and _is_ctx_name(node.value)
                    and node.attr.startswith("_")):
                violations.append(f"{path.relative_to(_PKG.parent)}:{node.lineno}  ctx.{node.attr}")
            # getattr/setattr/hasattr(ctx, "_x", ...)
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in _DYNATTR and len(node.args) >= 2
                    and _is_ctx_name(node.args[0])
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)
                    and node.args[1].value.startswith("_")):
                violations.append(
                    f"{path.relative_to(_PKG.parent)}:{node.lineno}  "
                    f"{node.func.id}(ctx, {node.args[1].value!r})")
    assert not violations, (
        "Frozen-ctx underscore access (use ctx.widgets / ctx.runtime / ctx.state,"
        " or reach through a public accessor like ctx.window._x):\n  "
        + "\n  ".join(violations))


def _collect_free_func_first_params():
    """Return (ctx_first, non_ctx_first): names of *top-level* (free) functions
    keyed by whether their first parameter is ``ctx``. Methods (defs nested in a
    class) are excluded — they take ``self``/``cls`` implicitly and are never
    called with an explicit first arg."""
    ctx_first, non_ctx_first = set(), set()
    for path in _py_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in tree.body:  # module top level only → free functions
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args.args
                first = args[0].arg if args else None
                (ctx_first if first == "ctx" else non_ctx_first).add(node.name)
    return ctx_first, non_ctx_first


# The fixtures that yield a real ``WellViewerApp``. A test that receives one of
# these has an ``app`` for which ``app.window`` is Qt's ``QWidget.window`` method,
# so handing it to a ctx function fails. A test that does *not* receive one built
# its own ``SimpleNamespace`` mock, and those deliberately set ``app.window = app``
# so the mock doubles as its own ctx — passing it is correct and must not be
# flagged. That distinction is what keeps this check free of an allowlist.
_REAL_APP_FIXTURES = {
    "viewer_app", "loaded_app", "image_table_app",
    "loaded_app_shape", "loaded_app_multifov",
}


def _uses_real_app_fixture(fn_node: ast.AST) -> bool:
    args = fn_node.args
    names = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    return bool(names & _REAL_APP_FIXTURES)


# Package roots whose modules hold the free functions this check is about. A name
# bound to one of them is a module handle, not an object.
_MODULE_ROOTS = ("well_viewer", "util", "widgets", "services")


def _module_aliases(tree: ast.AST) -> set:
    """Names bound to a first-party *module* in this file.

    ``import well_viewer.core.selection_controller as sc`` and ``from
    well_viewer.core import selection_controller as sc`` both make ``sc`` a
    module handle, so ``sc.foo(app)`` resolves to the free function ``foo`` as
    unambiguously as a bare ``foo(app)`` does. Collecting the aliases per file is
    what lets the call scan below cover the qualified form without mistaking a
    method call for one.

    ``from … import foo`` also lands here (binding the function itself, not a
    module); harmless, since the scan only consults this set for *attribute*
    calls and a function has no first-party sub-attributes to call.
    """
    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith(_MODULE_ROOTS):
                    aliases.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").startswith(_MODULE_ROOTS):
                for a in node.names:
                    aliases.add(a.asname or a.name)
    return aliases


def _called_free_func(node: ast.Call, unambiguous: set, aliases: set) -> str | None:
    """The ctx-first free function *node* calls, or ``None``.

    Two resolvable forms: the bare ``foo(app)``, and the module-qualified
    ``sc.foo(app)`` where ``sc`` is a module alias bound in this file. Every
    other attribute call — ``obj.foo(app)`` on a real object — is a method or a
    stored callable (e.g. ``scope.redraw(app)``, where ``redraw`` is the scope's
    own app-taking wrapper) and must not be conflated with a same-named free
    function, so it stays exempt.
    """
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id if fn.id in unambiguous else None
    if (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
            and fn.value.id in aliases and fn.attr in unambiguous):
        return f"{fn.value.id}.{fn.attr}"
    return None


def _bad_ctx_calls(tree: ast.AST, unambiguous: set, *, only_real_app: bool):
    """Yield ``(lineno, fn_name, arg_name)`` for ``foo(app)`` on a ctx function.

    With *only_real_app*, a call is reported only when the function enclosing it
    receives a real-app fixture — the test-suite rule described above.
    """
    aliases = _module_aliases(tree)
    scopes = [(tree, False)] if not only_real_app else []
    if only_real_app:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scopes.append((node, _uses_real_app_fixture(node)))

    for scope, is_real in scopes:
        for node in ast.walk(scope):
            if only_real_app and not is_real:
                continue
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = _called_free_func(node, unambiguous, aliases)
            if name is None:
                continue
            a0 = node.args[0]
            if isinstance(a0, ast.Name) and a0.id in {"app", "self"}:
                yield node.lineno, name, a0.id


def test_no_ctx_function_called_with_raw_app_or_self():
    """A function whose first parameter is ``ctx`` must be called with a context,
    never the raw ``app``/``self`` god-object. Only names that are *unambiguously*
    ctx-first free functions (no same-named app/self-first free function exists)
    are checked, to avoid cross-module name-collision false positives."""
    ctx_first, non_ctx_first = _collect_free_func_first_params()
    unambiguous = ctx_first - non_ctx_first

    violations = []
    for path in _call_site_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        in_tests = path.is_relative_to(_TESTS)
        for lineno, fn_name, arg in _bad_ctx_calls(
                tree, unambiguous, only_real_app=in_tests):
            violations.append(
                f"{path.relative_to(_PKG.parent)}:{lineno}  "
                f"{fn_name}({arg}) — pass {arg}._ctx")
    assert not violations, (
        "ctx-native function(s) called with the raw app/self handle "
        "(thread the context, e.g. app._ctx / self._ctx):\n  "
        + "\n  ".join(violations))
