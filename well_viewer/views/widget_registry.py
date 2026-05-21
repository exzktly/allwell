"""Name-keyed widget handle registry.

Part of the ``runtime_app`` god-object teardown. Roughly half of the ~116 loose
``self._*`` attributes on ``WellViewerApp`` are not state at all — they are Qt
widget handles (figures, canvases, axes, combo boxes, cards) that a tab builder
stashes on the app so other code can reach them later.

``WidgetRegistry`` is the name-keyed store those handles move into. A tab builder
calls ``registry.register("_line_fig", fig)`` instead of ``app._line_fig = fig``;
``WellViewerApp.__getattr__`` forwards a legacy ``app._line_fig`` read to the
registry, so reader call sites are untouched during the migration.

Deliberately Qt-free and dependency-free — it only holds references.
"""

from __future__ import annotations

from typing import Any, Iterator


class WidgetRegistry:
    """A ``name -> widget`` store for Qt widget handles.

    Names keep their legacy leading underscore (``"_line_fig"``) so the
    transitional ``WellViewerApp.__getattr__`` forwarder can match an attribute
    name against the registry directly.
    """

    def __init__(self) -> None:
        self._widgets: dict[str, Any] = {}

    def register(self, name: str, widget: Any) -> Any:
        """Store *widget* under *name* and return it (so a builder can write
        ``ax = registry.register("_line_ax", fig.add_subplot(...))``)."""
        self._widgets[name] = widget
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
