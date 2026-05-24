"""Statistics-tab domain state.

The user-defined comparison groups, the active-group cursor, the result cache,
and the transient plate-drag bookkeeping for the Statistics tab.

Composes ``well_viewer/batch_models.py`` — a statistics group is a ``BarGroup``.
Qt-free: imports only stdlib and ``batch_models``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from well_viewer.domain.batch_models import BarGroup


@dataclass
class StatsState:
    """Statistics-tab state.

    ``groups`` are the comparison groups; ``active_grp`` indexes the one the
    sidebar is editing (-1 when none). ``drag_adding`` / ``drag_visited`` track
    an in-progress plate drag that paints wells into the active group.
    """

    groups: list[BarGroup] = field(default_factory=list)
    active_grp: int = -1
    cache: dict = field(default_factory=dict)
    drag_adding: bool = False
    drag_visited: set = field(default_factory=set)

    # ── group management ─────────────────────────────────────────────────────

    def active_group(self) -> BarGroup | None:
        """The group ``active_grp`` points at — None when the index is unset
        or out of range."""
        if 0 <= self.active_grp < len(self.groups):
            return self.groups[self.active_grp]
        return None

    def select_group(self, idx: int) -> None:
        self.active_grp = idx

    def add_group(self) -> BarGroup:
        """Append a fresh ``Group N`` and make it the active group; return it."""
        group = BarGroup(f"Group {len(self.groups) + 1}")
        self.groups.append(group)
        self.active_grp = len(self.groups) - 1
        return group

    def delete_group(self, idx: int) -> bool:
        """Remove group *idx*, clamping the active cursor into range. Returns
        False when *idx* is out of range."""
        if not (0 <= idx < len(self.groups)):
            return False
        self.groups.pop(idx)
        self.active_grp = max(0, min(self.active_grp, len(self.groups) - 1))
        return True

    def rename_group(self, idx: int, name: str) -> bool:
        """Rename group *idx*. Returns False when *idx* is out of range."""
        if not (0 <= idx < len(self.groups)):
            return False
        self.groups[idx].name = name
        return True

    def clear_groups(self) -> None:
        self.groups.clear()
        self.active_grp = -1

    def set_groups(self, groups) -> None:
        """Replace the group list and reset the active cursor to the first
        group (or -1 when the new list is empty)."""
        self.groups = list(groups)
        self.active_grp = 0 if self.groups else -1

    # ── result cache ─────────────────────────────────────────────────────────

    def invalidate_cache(self) -> None:
        self.cache.clear()
