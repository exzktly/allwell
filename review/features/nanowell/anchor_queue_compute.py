"""The anchor queue: every nanowell timepoint on the plate still waiting for an anchor set by hand, in plate order.

After a nanowell run a set of wells and timepoints is usually left ``unaddressed`` — the run could not find the
central square — and each needs a person to click the square on its montage (``util.nanowell_anchors``). Opening
them one at a time from QC Triage or Home costs a round trip per timepoint. The Array view's **Anchor queue** walks
them instead: it shows the first, and each click that sets an anchor moves on to the next (``_anchor_queue``).

What the queue holds is read from the grid records the run wrote (``<out>/nanowells/<well>_grid.json``), which name
every timepoint's status whether or not the page has loaded that well's tables, and from the anchors file. A
timepoint is waiting when its status is ``unaddressed`` and either no anchor is set for it, or the anchor set is the
one the last run already tried and still could not address it with (the grid record's ``manual_anchor_um``): that
anchor was refused, so it needs setting again. An anchor set and not yet used by a run is not waiting: the remedy
for it is a re-run, not another click.

Qt-free.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from util.well_token import canonical_well_label

#: The one grid status an anchor set by hand addresses (QC Triage's ``ANCHORABLE_STATUS`` names the same).
WAITING_STATUS = "unaddressed"


@dataclass(frozen=True)
class QueueItem:
    well: str
    timepoint: str
    reason: str = ""            # the grid record's reason for leaving it unaddressed
    refused: bool = False       # an anchor is set, and the last run tried it and still could not address it


@dataclass
class AnchorQueue:
    """The queue the Array view is walking: its items, which one is shown, and what has happened to each."""

    items: list
    index: int = 0
    #: The plate selection when the queue started: each step shows its well beside it and Stop restores it, so
    #: walking thirty wells does not leave thirty wells selected on every other tab.
    selection_before: frozenset = frozenset()
    anchored: set = field(default_factory=set)      # (well, timepoint) anchored while the queue ran
    #: Timepoints left out because an anchor is set and waits for a re-run, ``(well, timepoint)``: counted, so the
    #: messages can say so, and their wells need no click of their own with :attr:`per_well` on.
    pending_rerun: frozenset = frozenset()
    data_dir: str = ""                              # the dataset it was built for; another one drops it
    #: Every waiting timepoint. :attr:`items` is this, or with :attr:`per_well` only each well's first.
    all_items: list = field(default_factory=list)
    per_well: bool = False

    def __post_init__(self) -> None:
        if not self.all_items:
            self.all_items = list(self.items)

    def set_per_well(self, on: bool) -> None:
        """Walk every waiting timepoint, or only each well's first (:func:`one_per_well`). The item shown stays shown
        when the new list holds it; otherwise the walk goes on at the first item after it, so switching never sends
        the walk back over wells already passed."""
        current = self.current
        at = self.all_items.index(current) if current in self.all_items else len(self.all_items)
        self.per_well = bool(on)
        done = {well for well, _tp in self.pending_rerun}
        self.items = one_per_well(self.all_items, done) if self.per_well else list(self.all_items)
        self.index = next((i for i, item in enumerate(self.items) if self.all_items.index(item) >= at),
                          len(self.items))

    @property
    def current(self) -> QueueItem | None:
        return self.items[self.index] if 0 <= self.index < len(self.items) else None

    @property
    def finished(self) -> bool:
        return self.index >= len(self.items)

    def skipped(self) -> int:
        """Items passed over without an anchor (those behind the shown one, plus every one once finished)."""
        upto = len(self.items) if self.finished else self.index
        return sum(1 for item in self.items[:upto] if (item.well, item.timepoint) not in self.anchored)

    def position_text(self) -> str:
        """``"3 of 17 · B04 at 24h"`` — where the walk is, for the band."""
        item = self.current
        if item is None:
            return f"Done · {len(self.anchored)} of {len(self.items)} anchored"
        return f"{self.index + 1} of {len(self.items)} · {item.well} at {item.timepoint}"

    def summary(self) -> str:
        """What the walk did, said on the figure when it ends."""
        parts = [f"Anchor queue finished: {len(self.anchored)} of {len(self.items)} timepoint(s) anchored"]
        skipped = self.skipped()
        if skipped:
            parts.append(f"{skipped} skipped")
        text = ", ".join(parts) + "."
        if self.anchored or self.pending_rerun:
            text += " Re-run anchored wells… to address them from the anchors."
        return text


def _same(a, b) -> bool:
    try:
        return math.dist((float(a[0]), float(a[1])), (float(b[0]), float(b[1]))) < 1e-3
    except (TypeError, ValueError, IndexError):
        return False


def grid_statuses(nanowell_dir: Path | None) -> dict[str, dict[str, dict]]:
    """``well -> {timepoint -> grid block}`` from every readable grid record under *nanowell_dir*. A record that
    cannot be read contributes nothing: QC Triage names it, and a queue built around it would only skip it."""
    out: dict[str, dict[str, dict]] = {}
    if nanowell_dir is None:
        return out
    for path in sorted(Path(nanowell_dir).glob("*_grid.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        well = canonical_well_label(str(payload.get("well") or path.name[:-len("_grid.json")]))
        blocks = payload.get("timepoints")
        if well is None or not isinstance(blocks, dict):
            continue
        out[well] = {str(tp): b for tp, b in blocks.items() if isinstance(b, dict)}
    return out


def waiting_items(grids: dict, anchors: dict, order=None) -> tuple[list[QueueItem], frozenset]:
    """The timepoints waiting for an anchor, in plate order (row by row, then column) and each well's timepoints in
    *order* (a callable sorting labels; the record's own order when ``None``), and the ``(well, timepoint)`` of each
    unaddressed timepoint left out because an anchor is set and waits for a re-run.

    *grids* is :func:`grid_statuses`; *anchors* is ``util.nanowell_anchors.read_anchors``'s
    ``(well, timepoint) -> Anchor``."""
    items: list[QueueItem] = []
    pending = set()
    for well in sorted(grids):
        blocks = grids[well]
        tps = [tp for tp in blocks if blocks[tp].get("status") == WAITING_STATUS]
        for tp in (order(tps) if order is not None else tps):
            block = blocks[tp]
            anchor = anchors.get((well, tp))
            tried = block.get("manual_anchor_um")
            refused = anchor is not None and tried is not None and _same(anchor.position(), tried)
            if anchor is not None and not refused:
                pending.add((well, tp))
                continue
            items.append(QueueItem(well, tp, str(block.get("reason") or "").strip(), refused))
    return items, frozenset(pending)


def one_per_well(items: list, done=()) -> list:
    """Each well's first waiting timepoint (in the queue's order), the rest left out, and nothing of a well in
    *done* (one with an anchor already set and waiting for a re-run). The manual's rule: one anchor per well is
    enough when the rest of the plate is anchored, because the end of the run addresses the well's other timepoints
    from it through the plate's other wells; a timepoint it still cannot address says so and takes a click of its
    own on the next walk."""
    seen: set = set(done)
    out = []
    for item in items:
        if item.well not in seen:
            seen.add(item.well)
            out.append(item)
    return out
