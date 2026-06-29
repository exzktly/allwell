"""Lazy import proxies — defer importing a target until first call.

A module-scope ``name = lazy("well_viewer.features.<F>.…", "name")`` binds a
callable proxy that imports its target on first call (then caches it), so the
importing module never imports the feature at import time. That keeps removable
features importable-on-demand only — comment-removable AND deletable — without
hunting every call site to make the import function-local. Enforced by
``tests/test_architecture_contracts.test_no_module_scope_imports_of_removable_features``
(a ``lazy(...)`` call is not an ``import`` statement, so it does not couple the
module at load time).

Wraps **callables** (functions / classes used as constructors). For a non-call
reference, import function-locally instead.
"""

from __future__ import annotations

from typing import Callable

# Every (module_path, attr) a lazy proxy was created for, in creation order.
# Lets the ``--smoke`` self-test resolve all of them and confirm none is
# mis-wired (a typo'd name or a moved target only surfaces on first call in the
# real GUI otherwise). Populated as the defining modules import.
_REGISTRY: "list[tuple[str, str]]" = []


def lazy(module_path: str, attr: str) -> Callable:
    _REGISTRY.append((module_path, attr))
    cache: list = []

    def _proxy(*args, **kwargs):
        if not cache:
            from importlib import import_module
            cache.append(getattr(import_module(module_path), attr))
        return cache[0](*args, **kwargs)

    _proxy.__name__ = f"lazy<{module_path}.{attr}>"
    return _proxy


def lazy_names(module_path: str, *names: str):
    """Tuple of ``lazy`` proxies for several callables from one module."""
    return tuple(lazy(module_path, n) for n in names)


def verify_lazy_imports() -> "list[tuple[str, bool, str | None]]":
    """Resolve every registered lazy proxy once.

    Returns ``(target, ok, error)`` per distinct proxy: ``ok`` is True when the
    target imports and is callable. Covers only proxies whose defining module
    has been imported — the smoke runner builds every tab first so they all are.
    """
    from importlib import import_module
    seen: set = set()
    results: list = []
    for module_path, attr in list(_REGISTRY):
        key = (module_path, attr)
        if key in seen:
            continue
        seen.add(key)
        target = f"{module_path}.{attr}"
        try:
            obj = getattr(import_module(module_path), attr)
            if callable(obj):
                results.append((target, True, None))
            else:
                results.append((target, False, "resolved but not callable"))
        except Exception as exc:  # noqa: BLE001 — report, don't raise
            results.append((target, False, f"{type(exc).__name__}: {exc}"))
    return results
