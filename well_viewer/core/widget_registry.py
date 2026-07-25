"""Name-keyed widget handle registry.

Part of the ``runtime_app`` god-object teardown. Roughly half of the ~116 loose
``self._*`` attributes on ``WellViewerApp`` are not state at all — they are Qt
widget handles (figures, canvases, axes, combo boxes, cards) that a tab builder
stashes on the app so other code can reach them later.

``WidgetRegistry`` is the name-keyed store those handles move into. A tab builder
calls ``registry.register("_line_fig", fig)`` instead of ``app._line_fig = fig``;
``register`` also stamps the handle onto the bound app as ``app._line_fig`` so
legacy ``app._<widget>`` readers resolve via ordinary attribute lookup — no
``__getattr__`` forwarder required.

The backing ``name -> widget`` dict can be **shared with ``AppRuntime``** (pass
``store=``): the real app wires one attribute-store dict into both so a handle
registered here as ``_<feature>_<name>`` is also reachable through
``ctx.runtime.<feature>.<name>`` (and vice-versa), preserving the cross-reads
that used to resolve because both stamped one ``app.__dict__``. See
``core/app_runtime.py``.

Deliberately Qt-free — it only holds references.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Iterator


class WidgetRegistry:
    """A ``name -> widget`` store for Qt widget handles.

    Names keep their legacy leading underscore (``"_line_fig"``) so the
    legacy ``app._line_fig`` reader continues to resolve via plain attribute
    lookup on the bound app.
    """

    def __init__(self, app: Any = None, store: dict | None = None) -> None:
        # Backing store; may be shared with AppRuntime (pass ``store=``) so the
        # two namespaces resolve one another's entries. Keys are the legacy
        # underscored widget names (``"_line_fig"``).
        self._widgets: dict[str, Any] = store if store is not None else {}
        self._app = app

    def register(self, name: str, widget: Any) -> Any:
        """Store *widget* under *name*, also stamping it onto the bound app
        as ``app.<name>`` so legacy attribute readers resolve directly.
        Returns *widget* so a builder can write
        ``ax = registry.register("_line_ax", fig.add_subplot(...))``."""
        self._widgets[name] = widget
        if self._app is not None:
            setattr(self._app, name, widget)
        return widget

    def get(self, name: str) -> Any:
        """Return the widget registered under *name*; ``KeyError`` if absent."""
        return self._widgets[name]

    def get_or_none(self, name: str) -> Any | None:
        """Return the widget registered under *name*, or ``None`` if absent."""
        return self._widgets.get(name)

    def has(self, name: str) -> bool:
        return name in self._widgets

    def __contains__(self, name: object) -> bool:
        return name in self._widgets

    def __len__(self) -> int:
        return len(self._widgets)

    def __iter__(self) -> Iterator[str]:
        return iter(self._widgets)

    def names(self) -> list[str]:
        return list(self._widgets)

    def clear(self) -> None:
        self._widgets.clear()
