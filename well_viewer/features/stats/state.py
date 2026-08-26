"""Statistics-tab domain state.

The user-defined comparison groups, the active-group cursor, and the transient
plate-drag bookkeeping for the Statistics tab. (The group-statistics result
cache is core, not feature, state — see ``core/stats_cache_state.py``.)

Composes ``well_viewer/batch_models.py`` — a statistics group is a ``BarGroup``.
Qt-free: imports only stdlib and ``batch_models``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from well_viewer.domain.batch_models import BarGroup
from well_viewer.domain.tp_match import tp_close


@dataclass
class StatsState:
    """Statistics-tab state.

    ``groups`` are the comparison groups; ``active_grp`` indexes the one the
    sidebar is editing (-1 when none). ``drag_adding`` / ``drag_visited`` track
    an in-progress plate drag that paints wells into the active group.
    """

    groups: list[BarGroup] = field(default_factory=list)
    active_grp: int = -1
    drag_adding: bool = False
    drag_visited: set = field(default_factory=set)

    # Structured pairwise results, so the Bar plot can draw significance
    # brackets without re-running the tests. Each entry is ``{"a": group_name,
    # "b": group_name, "p": float, "p_adj": float, "test": str, "stat": str,
    # "val_col": str, "target_t": float}``. Populated by ``run_stats``; the text
    # output is unchanged.
    #
    # **Keyed by run context, not a single list.** One list held only the most
    # recent run, so running a comparison on a second channel — or a second
    # statistic, or a second timepoint — destroyed the first. A figure could
    # only ever carry the last test anyone happened to run, and the renderer's
    # context check had to be a *gate* (draw this run, or draw nothing) because
    # there was nothing else to offer. Keyed, several comparisons coexist and
    # each figure looks up its own; the gate becomes a lookup over the same
    # three fields it used to compare.
    #
    # The key is ``(val_col, target_t, stat)`` because those are exactly what
    # decides whether a stored run describes the bars in front of you: the
    # column they are drawn from, the timepoint they are drawn at, and — since
    # a "fraction" test brackets the On-fraction panel while every other
    # statistic brackets the Mean panel — which panel it belongs on. Two runs
    # differing only in statistic are a Mean comparison and a Fraction
    # comparison of the same bars, and both should be on screen at once.
    #
    # Insertion-ordered, so "the most recent run" is the last value.
    runs: dict = field(default_factory=dict)

    # ── stored comparison runs ───────────────────────────────────────────────

    @staticmethod
    def run_key(record) -> tuple | None:
        """The store key for one pairwise record, or ``None`` if it has no context.

        ``target_t`` is rounded before it becomes part of a key: it arrives as a
        float parsed from a combo's text and travels through JSON, and two
        spellings of the same timepoint must not open two drawers.

        A record carrying a ``val_col`` but no ``target_t`` keys with ``None`` in
        the timepoint slot rather than becoming contextless. Older records are
        like that, and the behaviour they had before the store existed was to be
        checked against the channel and *not* against the time — so they must
        still be checked against the channel now. Treating them as matching
        anything drew a CY5 comparison over GFP bars.
        """
        if not isinstance(record, dict):
            return None
        val_col = record.get("val_col")
        if val_col is None:
            return None
        target_t = record.get("target_t")
        tp = None
        if target_t is not None:
            try:
                tp = round(float(target_t), 6)
            except (TypeError, ValueError):
                tp = None
        return (str(val_col), tp, str(record.get("stat", "mean") or "mean"))

    def record_run(self, records) -> tuple | None:
        """Store one run's records under its own context; return the key.

        Re-running the same configuration replaces that key's records rather
        than accumulating them — a second run of one comparison is a correction,
        not a second comparison. "The same configuration" is decided with the
        tolerance the lookup uses, not with float equality: a timepoint that
        round-trips through a combo's ``:.4g`` comes back a few parts in 1e5
        off, so an exact-match replacement would leave both spellings stored,
        both matching, and the figure carrying every bracket twice.

        Records with no context (older saved plots, hand-built fixtures) cannot
        be keyed and are kept under ``None``: dropping them would silently lose
        a comparison that the renderer's pre-key behaviour drew happily.
        """
        records = list(records or [])
        if not records:
            return None
        key = self.run_key(records[0])
        # Re-inserting moves the key to the end, which is what makes "the last
        # value" mean "the most recent run" rather than "the earliest one that
        # has since been re-run".
        for stale in [k for k in self.runs if self._same_run(k, key)]:
            self.runs.pop(stale, None)
        self.runs[key] = records
        return key

    @staticmethod
    def _same_run(a: tuple | None, b: tuple | None) -> bool:
        """Whether two keys name the one configuration, timepoints included."""
        if a is None or b is None:
            return a is b
        if a[0] != b[0] or a[2] != b[2]:
            return False
        if a[1] is None or b[1] is None:
            return a[1] is b[1]
        return tp_close(a[1], b[1])

    def runs_matching(self, val_col: str, target_ts=None) -> list:
        """Every stored run drawable over bars of *val_col* at *target_ts*.

        The lookup that replaced the context gate, and it declines exactly what
        the gate declined. A run with no ``val_col`` at all (key ``None``)
        matches anything — that is how a record with no context drew before
        there was a store to key it in. A run with a ``val_col`` but no
        timepoint is checked against the channel and not against the time, which
        is also what the gate did.
        """
        want_col = str(val_col or "")
        wanted = []
        for t in (target_ts or []):
            try:
                wanted.append(float(t))
            except (TypeError, ValueError):
                continue
        out = []
        for key, records in self.runs.items():
            if key is None:
                out.append(records)
                continue
            col, tp, _stat = key
            if col != want_col:
                continue
            if tp is not None and wanted and not any(
                    tp_close(tp, w) for w in wanted):
                continue
            out.append(records)
        return out

    def latest_run(self) -> list:
        """The most recently recorded run, or ``[]``."""
        for records in reversed(list(self.runs.values())):
            return list(records)
        return []

    @property
    def pairwise(self) -> list:
        """The most recent run, for callers that want exactly one.

        A derived view of :attr:`runs`, not a second copy — the saved-plot
        capture and the older tests read "the current comparison" and there is
        no useful answer to that other than the last one recorded.
        """
        return self.latest_run()

    @pairwise.setter
    def pairwise(self, records) -> None:
        """Record *records* as a run; an empty assignment clears the store."""
        records = list(records or [])
        if not records:
            self.runs.clear()
            return
        self.record_run(records)

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
