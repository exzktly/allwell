"""Lazy import proxies — defer importing a target until first call.

A module-scope ``name = lazy("review.features.<F>.…", "name")`` binds a
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

from collections.abc import Callable


def lazy(module_path: str, attr: str) -> Callable:
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
