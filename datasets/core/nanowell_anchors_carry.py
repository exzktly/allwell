"""Carry the nanowell anchors set by hand (``in/nanowell_anchors.json``) into a dataset a tool writes.

Subset, Merge Plates and Concatenate each write a new ``in/`` folder. An anchor is a person's statement of where a
well's central square is, made because the run could not find it; a tool that dropped the file would hand back a
dataset whose anchored wells come out unaddressed again on the next run, with nothing to say why. So each tool
carries the anchors of the wells and timepoints it carries, under the names it gives them: Subset keeps only what it
keeps, Merge Plates renames dataset 2's wells by its remap, Concatenate renames dataset 2's timepoints by its shift.

An anchor's position does not change with any of these: it is in its own well's frame at its own timepoint, and no
tool moves a tile. Qt-free.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from util.nanowell_anchors import Anchor, AnchorsFileError, anchors_path, read_anchors, write_anchors


@dataclass(frozen=True)
class AnchorSource:
    """One source dataset's ``in/`` folder and how its wells and timepoints are named in the destination.

    *well* and *timepoint* map a source name to the destination's (``None`` drops the anchor: the tool did not
    carry that well or timepoint)."""
    in_dir: Path
    well: Callable[[str], str | None] = lambda w: w
    timepoint: Callable[[str], str | None] = lambda t: t


def carry_anchors(sources, dest_in_dir: Path, emit: Callable[[str], None]) -> int:
    """Write the anchors of every source, renamed, to *dest_in_dir*; the number carried.

    Raises ``ValueError`` when a source's file cannot be used or two sources anchor the same destination well at
    the same timepoint: either could be the one meant, so neither is picked. The destination file is written only
    when there is something to write; with nothing carried an existing file is left alone, so an in-place
    Concatenate whose second dataset has no anchors keeps the first's as they are.
    """
    carried: dict[tuple[str, str], Anchor] = {}
    dropped = 0
    for source in sources:
        try:
            anchors = read_anchors(anchors_path(source.in_dir))
        except AnchorsFileError as exc:
            raise ValueError(f"The nanowell anchors cannot be carried: {exc}") from None
        for (well, tp), anchor in anchors.items():
            new_well, new_tp = source.well(well), source.timepoint(tp)
            if new_well is None or new_tp is None:
                dropped += 1
                continue
            key = (new_well, new_tp)
            if key in carried and carried[key].position() != anchor.position():
                raise ValueError(f"The nanowell anchors cannot be carried: both datasets anchor {new_well} at "
                                 f"{new_tp}, at different positions")
            carried[key] = Anchor(new_well, new_tp, anchor.x_um, anchor.y_um)
    if carried:
        write_anchors(anchors_path(dest_in_dir), carried)
        emit(f"Carried {len(carried)} nanowell anchor(s) set by hand"
             + (f"; {dropped} of wells or timepoints not carried were left behind." if dropped else "."))
    elif dropped:
        emit(f"None of the {dropped} nanowell anchor(s) set by hand is of a well and timepoint this dataset keeps.")
    return len(carried)
