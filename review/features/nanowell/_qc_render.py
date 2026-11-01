"""The QC view's figure (pure matplotlib): the plate's wells down, its timepoints across, each cell filled and hatched
by its state (``qc_compute``), with the same hatches the line plots mark unconfirmed timepoints with
(``_identity_marks.HATCH``), so a state reads the same on every view.

Every state that needs a look has its own texture as well as its own ink — solid, horizontal, vertical, ``x``, the
two diagonals, dots — and cells tall enough to hold one carry a glyph too, so the matrix reads without colour.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from review.features.nanowell import qc_compute as qc
from review.features.nanowell._identity_marks import HATCH as _LINE_HATCH
from review.features.nanowell.renderer import _ink, _inks, _mark_colours, _theme_axes, render_message

#: Each state's hatch: the line plots' for the identity verdicts, and one of its own for each grid state.
HATCH = {qc.NOT_ADDRESSED: "", qc.ANCHOR_WAITING: "----", qc.NOT_LOCATED: "||||",
         qc.CONTRADICTED: _LINE_HATCH[qc.CONTRADICTED], qc.INCONCLUSIVE: _LINE_HATCH[qc.INCONCLUSIVE],
         qc.PARTIAL: _LINE_HATCH[qc.PARTIAL], qc.NOT_CHECKED: _LINE_HATCH[qc.NOT_CHECKED],
         qc.REFERENCE: "", qc.CONFIRMED: ""}
#: Each state's glyph, written in cells tall enough to hold it; a partial cell writes its left-out count instead.
GLYPH = {qc.NOT_ADDRESSED: "!", qc.ANCHOR_WAITING: "A", qc.NOT_LOCATED: "–", qc.CONTRADICTED: "X",
         qc.INCONCLUSIVE: "?", qc.REFERENCE: "R"}
#: Each state's tint behind its hatch; *not addressed*, the one an anchor fixes, is filled solid.
_ALPHA = {qc.NOT_ADDRESSED: 1.0, qc.CONFIRMED: 0.10}
_TINT = 0.18
#: The tallest a cell is drawn, and the shortest that holds a glyph, in pixels.
MAX_CELL_PX, GLYPH_PX = 28.0, 14.0
#: Shorter rows than this are too close to name each well: each plate row's band is named by its letter instead.
_NAME_EACH_PX = 9.0
#: Over this many rows, a gap opens between plate rows (where the row letter changes), so 96 wells scan as 8 bands.
_GAP_ROWS, _GAP = 12, 0.35
_LEFT, _RIGHT, _TOP, _BOTTOM = 0.12, 0.97, 0.80, 0.14


def colours(muted: str) -> dict:
    """Each state's ink: the line plots' for the verdicts, and the theme's for the rest."""
    from ui.theme import Colors

    out = dict(_mark_colours(muted))
    out.update({qc.NOT_ADDRESSED: Colors.warn, qc.ANCHOR_WAITING: Colors.accent, qc.NOT_LOCATED: muted,
                qc.REFERENCE: Colors.success, qc.CONFIRMED: Colors.success})
    return out


@dataclass
class QcDrawn:
    """What the last draw put on screen: which cell lies under a point of :attr:`ax`."""

    ax: object = None
    wells: list = field(default_factory=list)
    timepoints: list = field(default_factory=list)
    row_y: list = field(default_factory=list)       # each shown well's top edge, in data units
    cells: dict = field(default_factory=dict)
    #: Puts the well and timepoint labels back at the matrix's own sizes: the page's export style, applied after
    #: every draw, sets every tick label to the Properties size, and 96 wells named at that size are a smear.
    relabel: object = None

    def cell_at(self, x, y) -> qc.QcCell | None:
        """The :class:`qc_compute.QcCell` at data point ``(x, y)``, or ``None`` (outside, a gap, no record)."""
        if x is None or y is None or not self.timepoints:
            return None
        col = math.floor(x)
        if not 0 <= col < len(self.timepoints):
            return None
        for well, top in zip(self.wells, self.row_y):
            if top <= y < top + 1:
                return self.cells.get((well, self.timepoints[col]))
        return None


def _row_tops(wells) -> list:
    gaps = len(wells) > _GAP_ROWS
    tops, y, previous = [], 0.0, None
    for well in wells:
        if gaps and previous is not None and well[:1] != previous[:1]:
            y += _GAP
        tops.append(y)
        y += 1.0
        previous = well
    return tops


def draw_qc(fig, matrix, *, which: str, out_dir, message: str = "", highlight=frozenset(),
            excluded=(), error: str = "") -> QcDrawn | None:
    """Draw *matrix*, the wells *which* (``qc_compute.WELL_FILTERS``) chooses as rows. *message* is the last anchor
    action's result, written under the title; *highlight* rings those ``(well, timepoint)`` cells; *excluded* wells
    are labelled so (the page's choice, not the run's — they keep their row)."""
    if matrix is None and error:
        render_message(fig, f"The plate's grid records and identity checks could not be read: {error}",
                       gid="nw-qc-failed")
        return None
    if matrix is None:
        render_message(fig, "Reading the plate's grid records and identity checks…", gid="nw-qc-reading")
        return None
    if not matrix.cells:
        render_message(fig, "This run wrote no grid records (nanowells/<well>_grid.json), so there is nothing to "
                            "check.", gid="nw-qc-empty")
        return None
    wells, tps = matrix.shown_wells(which), list(matrix.timepoints)
    fig.clear()
    ax = fig.add_axes((_LEFT, _BOTTOM, _RIGHT - _LEFT, _TOP - _BOTTOM))
    _theme_axes(ax)
    ground, ink, muted = _inks(ax)
    _title(fig, matrix, which, out_dir, message, ink, muted)
    if not wells:
        ax.set_axis_off()
        _ink(ax.text(0.5, 0.5, "Every timepoint is addressed and confirmed.", ha="center", va="center",
                     transform=ax.transAxes, color=muted, fontsize=9, gid="nw-qc-all-good"), muted=True)
        _legend(fig, matrix, muted)
        return QcDrawn(ax=ax)
    tops = _row_tops(wells)
    height_px = ax.get_position().height * fig.get_figheight() * fig.dpi
    span = tops[-1] + 1.0
    rows_px = height_px / span
    # Few rows would each fill a fifth of the figure: cap the cell height and leave the rest of the axes empty.
    ax.set_ylim(max(span, height_px / MAX_CELL_PX), 0)
    ax.set_xlim(0, len(tps))
    _cells(ax, matrix, wells, tps, tops, muted)
    if min(rows_px, MAX_CELL_PX) >= GLYPH_PX:
        _glyphs(ax, matrix, wells, tps, tops, ground, muted)
    _highlight(ax, highlight, wells, tps, tops)
    every = min(rows_px, MAX_CELL_PX) >= _NAME_EACH_PX

    def relabel() -> None:
        _axes_labels(ax, wells, tps, tops, set(excluded), ink, muted, every=every)

    relabel()
    _legend(fig, matrix, muted)
    drawn = QcDrawn(ax=ax, wells=wells, timepoints=tps, row_y=tops, cells=matrix.cells, relabel=relabel)
    ax._aw_hover_label = lambda x, y: (qc.short_text(c) if (c := drawn.cell_at(x, y)) is not None else None)
    return drawn


def _title(fig, matrix, which, out_dir, message, ink, muted) -> None:
    _ink(fig.text(_LEFT, 0.965, qc.summary(matrix), ha="left", va="top", fontsize=10, fontweight="bold", color=ink,
                  gid="nw-qc-summary"))
    y = 0.925
    action = qc.action_line(matrix)
    lines = ([(message, ink)] if message else []) + ([(action, ink)] if action and not message else []) \
        + [(n, muted) for n in qc.notes(matrix, which, out_dir)]
    for text, colour in lines:
        _ink(fig.text(_LEFT, y, text, ha="left", va="top", fontsize=7.5, color=colour, wrap=True,
                      gid="nw-qc-message" if colour is ink else "nw-qc-note"), muted=colour is muted)
        y -= 0.032


def _cells(ax, matrix, wells, tps, tops, muted) -> None:
    from matplotlib.collections import PolyCollection
    from matplotlib.colors import to_rgba

    inks = colours(muted)
    boxes: dict = {}
    col = {tp: i for i, tp in enumerate(tps)}
    for well, top in zip(wells, tops):
        for tp in tps:
            cell = matrix.cells.get((well, tp))
            if cell is not None:
                x = col[tp]
                boxes.setdefault(cell.state, []).append([(x, top), (x + 1, top), (x + 1, top + 1), (x, top + 1)])
    for state in qc.QC_STATES:
        if state not in boxes:
            continue
        ink = inks[state]
        tint = PolyCollection(boxes[state], facecolors=[to_rgba(ink, _ALPHA.get(state, _TINT))],
                              edgecolors=[to_rgba(muted, 0.35)], linewidths=0.5, zorder=1, gid=f"nw-qc-{state}")
        tint.aw_state = state
        ax.add_collection(tint)
        if HATCH[state]:
            # The hatch is drawn in the edge colour (matplotlib before 3.10 has no hatch colour of its own).
            hatch = PolyCollection(boxes[state], facecolors="none", edgecolors=[to_rgba(ink, 0.9)], linewidths=0,
                                   hatch=HATCH[state], zorder=2, gid=f"nw-qc-{state}-hatch")
            hatch.aw_state = state
            ax.add_collection(hatch)


def _glyphs(ax, matrix, wells, tps, tops, ground, muted) -> None:
    inks = colours(muted)
    for well, top in zip(wells, tops):
        for i, tp in enumerate(tps):
            cell = matrix.cells.get((well, tp))
            if cell is None:
                continue
            glyph = f"{cell.left_out:,}" if cell.state == qc.PARTIAL and cell.left_out else GLYPH.get(cell.state)
            if not glyph:
                continue
            solid = cell.state == qc.NOT_ADDRESSED
            # On the card's ground over a hatch, so the glyph is not lost in the lines it sits on.
            box = None if solid or not HATCH[cell.state] else {"facecolor": ground, "edgecolor": "none", "pad": 1.0}
            ax.text(i + 0.5, top + 0.5, glyph, ha="center", va="center", fontsize=6.5, fontweight="bold",
                    color=ground if solid else inks[cell.state], zorder=3, gid="nw-qc-glyph", clip_on=True, bbox=box)


def _highlight(ax, highlight, wells, tps, tops) -> None:
    from matplotlib.patches import Rectangle

    from ui.theme import Colors

    row = dict(zip(wells, tops))
    col = {tp: i for i, tp in enumerate(tps)}
    for well, tp in highlight or ():
        if well in row and tp in col:
            ax.add_patch(Rectangle((col[tp] + 0.06, row[well] + 0.06), 0.88, 0.88, fill=False,
                                   edgecolor=Colors.accent, linewidth=1.5, zorder=4, gid="nw-qc-highlight"))


def _axes_labels(ax, wells, tps, tops, excluded, ink, muted, *, every: bool) -> None:
    """Each timepoint above its column; each well beside its row, or — rows too short to name — each plate row's
    letter beside its band (the readout names the well under the pointer)."""
    ax.xaxis.tick_top()
    ax.set_xticks([i + 0.5 for i in range(len(tps))])
    ax.set_xticklabels([str(tp) for tp in tps], rotation=90 if len(tps) > 16 else 0, fontsize=7, color=ink)
    if every:
        ax.set_yticks([t + 0.5 for t in tops])
        ax.set_yticklabels([f"{w} · excluded" if w in excluded else w for w in wells], fontsize=7)
        for label, well in zip(ax.get_yticklabels(), wells):
            label.set_color(muted if well in excluded else ink)
    else:
        bands: dict = {}
        for well, top in zip(wells, tops):
            bands.setdefault(well[:1], []).append(top)
        ax.set_yticks([(min(t) + max(t) + 1) / 2 for t in bands.values()])
        ax.set_yticklabels(list(bands), fontsize=8, fontweight="bold", color=ink)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)


def _legend(fig, matrix, muted) -> None:
    from matplotlib.colors import to_rgba
    from matplotlib.patches import Patch

    inks = colours(muted)
    handles = [Patch(facecolor=to_rgba(inks[s], _ALPHA.get(s, _TINT)), edgecolor=to_rgba(inks[s], 0.9),
                     hatch=HATCH[s] or None, label=f"{qc.WORDS[s]} {n:,}") for s, n in qc.legend_items(matrix)]
    legend = fig.legend(handles=handles, loc="lower center", ncol=min(len(handles), 5), frameon=False, fontsize=7,
                        bbox_to_anchor=(0.5, 0.0), handlelength=1.6)
    legend.set_gid("nw-qc-legend")
    _bg, ink, _muted = _inks(fig.axes[0]) if fig.axes else ("", muted, muted)
    for text in legend.get_texts():
        text.set_color(ink)
        _ink(text)
