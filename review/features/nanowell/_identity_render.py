"""Draw the Nanowells page's **Identity** view: one well's timepoint against its reference timepoint, in one of four
modes (``identity_compute.IDENTITY_MODES``).

Pure matplotlib onto a figure it is handed; every position, distance and sentence comes from
:mod:`.identity_compute` and is only drawn here. Every text a reader can read is a text artist with a ``gid``, so a
test reads the figure back rather than the model (CLAUDE.md §7.4 #1):

* **Distance map** (:func:`render_distance`) — each compared nanowell at its *reference* position (the reference
  timepoint's common frame, in µm: the measured positions the check compared, not lattice places), filled by how far,
  in pitches, its address lands from itself at the compared timepoint; the failing addresses outlined in the theme's
  danger colour; the compared timepoint's tiles mapped into the reference frame, a tile the layout test moved drawn
  in the danger colour.
* **Blink** (:func:`render_blink`, :func:`show_frame`) — both frames drawn once, one shown at a time: the page's
  timer calls :func:`show_frame`, which only flips visibilities, so a test steps it frame by frame.
* **Side by side** (:func:`render_sides`, :func:`show_hover`) — the two montages each in its own frame, with the
  hovered address lit on both and the registration's prediction for it crossed on the compared side.
* **Diagnostics** — drawn by ``_identity_diagnostics_render``: the square looked for and where it was found, the
  match scores around it, and the verdict block.

The Distance map colours each nanowell either by its distance or by its *status* (``identity_compute.status_map``:
kept, imprecise, wrong name, in a misregistered tile, not compared). Every drawn view names the axes a reader may
zoom and pan (``IdentityDrawn.zoomable``) and which of them move together (``links``); ``_identity_zoom`` does the
zooming.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from types import SimpleNamespace

import numpy as np

from review.features.nanowell import identity_focus_compute as fc
from review.features.nanowell.identity_compute import DISTANCE_TOP_PITCHES, STATUS_CLASSES

from review.features.nanowell.renderer import (
    _FOOTER_BASE, _LINE, _PREF_PT, GRID_TYPE_SCALE, MAP_CMAP, _ink, _inks, render_message,
)

#: Where the axes stop at the top, under the title.
_TOP = 0.9
#: The footer's type, in points.
_FOOTER_PT = 7.5
#: How large a hovered address's ring is against the pitch, on *Side by side*.
_HOVER_RING = 0.45


@dataclass
class IdentityDrawn:
    """What an Identity view put on screen, for the timer (Blink) and the pointer (Side by side) to act on."""
    mode: str
    pair: object
    axes: dict = field(default_factory=dict)          # "map" | "reference" | "compared" | "surface" -> axes
    frames: list = field(default_factory=list)        # Blink: [[artists of frame 0], [artists of frame 1]]
    frame: int = 0
    hover: dict = field(default_factory=dict)         # Side by side: the artists that light up an address
    lit: tuple | None = None                          # the address lit now
    distance: object = None                           # identity_compute.DistanceMap
    status: object = None                             # identity_compute.StatusMap, when coloured by status
    blink: object = None                              # identity_compute.BlinkFrames
    diagnostics: object = None                        # identity_compute.Diagnostics
    crops: object = None                              # identity_compute.Crops (Diagnostics)
    zoomable: tuple = ()                              # the names in ``axes`` a reader zooms and pans
    links: tuple = ()                                 # groups of those names that move together
    home: dict = field(default_factory=dict)          # name -> (xlim, ylim) as drawn: the full view
    focus: object = None                              # identity_focus_compute.FocusMarks: the nanowell gone to
    focus_centres: dict = field(default_factory=dict)  # name -> (x, y) the zoom centres on (``_identity_zoom``)
    laid_out: tuple = ()                              # Diagnostics: the figure size (in) its layout was worked for


def title_text(pair) -> str:
    """``<well> · <timepoint> vs <reference> · <verdict>``."""
    return f"{pair.well} · {pair.timepoint} vs {pair.reference or 'no reference'} · {pair.verdict.replace('_', ' ')}"


def _wrapped(fig, lines: list[str]) -> list[tuple[int, str]]:
    """``(line index, text)`` for each row of the footer: every line broken at the words to the figure's width (an
    average glyph is about half the type size wide), so no row runs off the figure or over the next."""
    width_pt = fig.get_size_inches()[0] * 72.0 * 0.94
    chars = max(40, int(width_pt / (0.5 * _FOOTER_PT)))
    return [(i, row) for i, line in enumerate(lines) for row in (textwrap.wrap(line, chars) or [""])]


def _frame(fig, pair, lines: list[str]) -> float:
    """Clear *fig*, write the title and the footer *lines*; return the figure fraction the axes may start at. A line
    too long for the figure is broken over rows; each row is a text of its own, the first line's in the ink."""
    fig.clear()
    _bg, ink, muted = _inks(SimpleNamespace(figure=fig))      # the plot tokens are the figure's, not an axes'
    _ink(fig.text(0.03, 0.955, title_text(pair), fontsize=9.5, color=ink, gid="nw-identity-title"))
    rows = _wrapped(fig, lines)
    for k, (i, text) in enumerate(rows):
        y = _FOOTER_BASE + (len(rows) - 1 - k) * _LINE
        _ink(fig.text(0.03, y, text, fontsize=_FOOTER_PT, color=ink if i == 0 else muted, gid="nw-identity-footer"),
             muted=i != 0)
    return _FOOTER_BASE + (len(rows) + 0.6) * _LINE


def _image_axes(fig, rect):
    ax = fig.add_axes(rect)
    ax.set_axis_off()
    ax._aw_image_panel = True                       # its own limits and aspect: the Properties pass leaves them
    ax.set_aspect("equal", adjustable="box")
    return ax


def _colourbar(fig, ax, mappable, label: str, *, extend: str = "neither"):
    place = ax.get_position()
    cax = fig.add_axes([place.x1 + 0.015, place.y0 + 0.1 * place.height, 0.014, 0.8 * place.height])
    bar = fig.colorbar(mappable, cax=cax, extend=extend)
    cax._aw_colorbar = True
    cax._aw_type_scale = GRID_TYPE_SCALE
    _bg, ink, muted = _inks(ax)
    _ink(bar.ax.set_ylabel(label, fontsize=_PREF_PT * GRID_TYPE_SCALE, color=ink))
    bar.ax.tick_params(labelsize=_PREF_PT * GRID_TYPE_SCALE, colors=muted)
    return bar


def _limits(ax, xs, ys, pad: float) -> None:
    xs, ys = np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)
    keep = np.isfinite(xs) & np.isfinite(ys)
    if not keep.any():
        return
    ax.set_xlim(float(xs[keep].min()) - pad, float(xs[keep].max()) + pad)
    ax.set_ylim(float(ys[keep].max()) + pad, float(ys[keep].min()) - pad)       # y down, as the montage is


def _discs(ax, x, y, radius: float, **kw):
    from matplotlib.collections import EllipseCollection

    width = np.full(len(x), 2 * radius)
    coll = EllipseCollection(width, width, np.zeros(len(x)), units="xy", offsets=np.column_stack([x, y]),
                             offset_transform=ax.transData, **kw)
    ax.add_collection(coll)
    return coll


# ── the Distance map ─────────────────────────────────────────────────────────
def _status_colours() -> dict:
    """Each status class's colour, from the theme: kept success, imprecise warn, a wrong name or a misregistered tile
    danger (the tile's hatched), not compared the undefined ink."""
    from ui.theme import Colors

    return {"kept": Colors.success, "imprecise": Colors.warn, "wrong_name": Colors.danger,
            "misregistered": Colors.danger, "not_compared": Colors.plot_undefined_ink}


#: The hatching of the nanowells in a misregistered tile, which share the wrong names' colour.
STATUS_HATCH = "////"


#: The legend's short words for each status class; the footer says what each means in full.
STATUS_KEY_WORDS = {"kept": "Lands on itself", "imprecise": "Imprecise", "wrong_name": "Wrong name",
                    "misregistered": "In a misregistered tile", "not_compared": "Not compared"}


def _draw_status(fig, ax, status) -> None:
    """Each nanowell filled by its status class, a misregistered tile's hatched, with a legend counting each class."""
    from matplotlib.patches import Patch

    colours = _status_colours()
    _bg, ink, muted = _inks(ax)
    handles = []
    for cls, _label in STATUS_CLASSES:
        keep = np.array([s == cls for s in status.status], dtype=bool)
        hatch = STATUS_HATCH if cls == "misregistered" else None
        if keep.any():
            coll = _discs(ax, status.x[keep], status.y[keep], status.radius_um, facecolors=colours[cls],
                          edgecolors=ink if hatch else "none", linewidths=0.0, zorder=3)
            if hatch:
                coll.set_hatch(hatch)
            coll.set_gid(f"nw-identity-status-{cls}")
        handles.append(Patch(facecolor=colours[cls], edgecolor=ink if hatch else colours[cls], hatch=hatch,
                             label=f"{STATUS_KEY_WORDS[cls]} ({int(keep.sum()):,})"))
    key = ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.02, 1.0), fontsize=7.5, frameon=False,
                    title="Nanowells", title_fontsize=8, borderaxespad=0.0)
    for text in [*key.get_texts(), key.get_title()]:
        text.set_gid("nw-identity-status-key")
        text.set_color(ink)
        _ink(text)


def _status_lines(pair, tolerance: float) -> list[str]:
    from pipeline.nanowell_identity import PARTIAL

    out = [f"Each nanowell at its position at {pair.reference} (µm, the reference's frame), coloured by what the check "
           f"found at {pair.timepoint}: lands on itself (within {tolerance:g} spacing of where the registration puts "
           f"it), imprecise (nearest itself but further), wrong name (it lands on another nanowell, or another lands "
           f"on it), or in a tile placed a lattice step off (hatched)."]
    if pair.verdict == PARTIAL:
        out.append("The wrong names and the misregistered tiles' nanowells are left out of every figure at "
                   f"{pair.timepoint}; the rest are used.")
    return out


def _draw_tiles(ax, dmap, muted) -> tuple[list, list]:
    """The compared timepoint's tiles in the reference's frame: a tile a lattice step off in the danger colour, one
    placed imprecisely in the warning colour, each named with how far; the points they reach."""
    from matplotlib.patches import Polygon

    from ui.theme import Colors

    xs, ys = [], []
    for tile in dmap.tiles:
        if tile.moved_um is not None:
            colour, label, gid, width = Colors.danger, f" moved {tile.moved_um:.0f} µm", "nw-identity-tile-moved", 2.0
        elif tile.imprecise_um is not None:
            colour, label, gid = Colors.warn, f" {tile.imprecise_um:.0f} µm off", "nw-identity-tile-imprecise"
            width = 1.6
        else:
            colour, label, gid, width = muted, "", "nw-identity-tile", 0.8
        if tile.corners is not None:
            ax.add_patch(Polygon(tile.corners, closed=True, fill=False, edgecolor=colour, linewidth=width, zorder=2,
                                 gid=gid))
            xs += list(tile.corners[:, 0])
            ys += list(tile.corners[:, 1])
        else:
            ax.plot([tile.corner[0]], [tile.corner[1]], marker="+", markersize=9, color=colour, zorder=2, gid=gid)
            xs.append(tile.corner[0])
            ys.append(tile.corner[1])
        text = ax.text(tile.corner[0], tile.corner[1], " " + tile.tile + label, fontsize=6.5, color=colour, va="top",
                       clip_on=True, gid="nw-identity-tile-label")
        if colour == muted:
            _ink(text, muted=True)
    return xs, ys


def render_distance(fig, pair, dmap, lines: list[str], status=None) -> IdentityDrawn:
    """Each compared address at its reference position, filled by its distance in pitches with the failing ones
    outlined — or, given *status* (``identity_compute.StatusMap``), by its status class with a legend counting each
    — and the compared timepoint's tiles mapped into the reference frame."""
    from matplotlib.colors import Normalize

    from ui.theme import Colors

    tolerance = float((pair.payload or {}).get("tolerance_pitches") or 0.25)
    if status is not None:
        lines = list(lines) + _status_lines(pair, tolerance)
    else:
        lines = list(lines) + [f"Each nanowell at its position at {pair.reference} (µm, the reference's frame), "
                               f"filled by how far its address lands from itself at {pair.timepoint}, in pitches; "
                               "outlined in red where it fails."]
    lines.append(f"Tiles of {pair.timepoint} mapped into {pair.reference}'s frame: a tile a lattice step off in red, "
                 "one placed imprecisely in amber.")
    bottom = _frame(fig, pair, lines)
    ax = _image_axes(fig, [0.03, bottom, 0.70 if status is not None else 0.82, _TOP - bottom])
    drawn = IdentityDrawn("distance", pair, {"map": ax}, distance=dmap, status=status, zoomable=("map",))
    _bg, ink, muted = _inks(ax)
    xs, ys = list(dmap.x), list(dmap.y)
    if status is not None:
        _draw_status(fig, ax, status)
        xs, ys = xs + list(status.x), ys + list(status.y)
    else:
        has = np.isfinite(dmap.distance)
        norm = Normalize(0.0, DISTANCE_TOP_PITCHES, clip=False)
        if has.any():
            fill = _discs(ax, dmap.x[has], dmap.y[has], dmap.radius_um, cmap=MAP_CMAP, norm=norm, edgecolors="none",
                          zorder=3)
            fill.set_array(dmap.distance[has])
            fill.set_gid("nw-identity-fill")
            bar = _colourbar(fig, ax, fill, f"Distance (pitches) · red line: the tolerance, {tolerance:g}",
                             extend="max")
            bar.ax.axhline(tolerance, color=Colors.danger, lw=1.4, gid="nw-identity-tolerance")
        if (~has).any():                    # an address only the reference holds: nothing to fill, still failing
            _discs(ax, dmap.x[~has], dmap.y[~has], dmap.radius_um, facecolors="none",
                   edgecolors=Colors.plot_undefined_ink, linewidths=0.8, zorder=3)
        failing = np.array([a in dmap.failing for a in dmap.addresses], dtype=bool)
        if failing.any():
            _discs(ax, dmap.x[failing], dmap.y[failing], dmap.radius_um * 1.15, facecolors="none",
                   edgecolors=Colors.danger, linewidths=1.4, zorder=4).set_gid("nw-identity-failing")
    tx, ty = _draw_tiles(ax, dmap, muted)
    _limits(ax, xs + tx, ys + ty, dmap.pitch_um or 1.0)
    return drawn


# ── Blink ────────────────────────────────────────────────────────────────────
#: Blink's address labels, in points, bold and haloed: they sit on a greyscale montage of any brightness, so each is
#: stroked in the app's darkest surface and drawn in a bright colour that differs between the two frames.
BLINK_LABEL_PT = 9.0
#: Each frame's label colour, as a theme lookup (the theme can change while the page is open): the reference's
#: green, the compared timepoint's amber — both bright on dark and on light grey, and told apart as the frames flip.
BLINK_LABEL_COLOURS = (lambda: _theme().success, lambda: _theme().warn)


def _theme():
    from ui.theme import Colors

    return Colors


def _halo() -> list:
    """A dark outline round a text, so it reads on a bright pixel as well as a dark one."""
    from matplotlib import patheffects

    return [patheffects.withStroke(linewidth=2.6, foreground=_theme().surface)]


def render_blink(fig, pair, frames, lines: list[str]) -> IdentityDrawn:
    """The reference montage and the compared one resampled into its pixel grid, one shown at a time, each with its
    own addresses' labels. Frame 0 (the reference) is shown; :func:`show_frame` flips to the other."""
    lines = list(lines) + [f"Blink: {pair.reference} and {pair.timepoint} mapped into {pair.reference}'s pixels through "
                           "the registration, alternating. A correct mapping keeps the lattice still; each frame "
                           "labels its own addresses (every third along each axis), so addresses that name other "
                           "nanowells jump."]
    if frames.covered < 0.5:
        lines.append(f"The compared montage covers {frames.covered:.0%} of the reference's frame.")
    bottom = _frame(fig, pair, lines)
    ax = _image_axes(fig, [0.03, bottom, 0.94, _TOP - bottom])
    drawn = IdentityDrawn("blink", pair, {"map": ax}, blink=frames, zoomable=("map",))
    _bg, ink, _muted = _inks(ax)
    from ui.theme import Colors

    sets = []
    halo = _halo()
    for k, (image, labels, name) in enumerate(((frames.reference, frames.ref_labels, f"{pair.reference} (reference)"),
                                              (frames.compared, frames.compared_labels,
                                               f"{pair.timepoint}, mapped into {pair.reference}'s frame"))):
        artists = [ax.imshow(np.ma.masked_invalid(image), cmap="gray", vmin=0, vmax=255, extent=frames.extent,
                             interpolation="nearest", zorder=0, gid=f"nw-identity-blink-{k}")]
        colour = BLINK_LABEL_COLOURS[k]()
        for (q, r), x, y in labels:
            artists.append(ax.text(x, y, f"{q},{r}", fontsize=BLINK_LABEL_PT, fontweight="bold", color=colour,
                                   ha="center", va="center", clip_on=True, zorder=3, path_effects=halo,
                                   gid=f"nw-identity-label-{k}"))
        artists.append(ax.text(0.01, 0.99, name, transform=ax.transAxes, fontsize=8.5, fontweight="bold",
                               color=colour, va="top", path_effects=halo, gid="nw-identity-frame",
                               bbox={"boxstyle": "round,pad=0.3", "facecolor": Colors.surface, "alpha": 0.8,
                                     "edgecolor": colour}))
        sets.append(artists)
    drawn.frames = sets
    x0, x1, y1, y0 = frames.extent
    ax.set_xlim(x0, x1)
    ax.set_ylim(y1, y0)
    show_frame(drawn, 0)
    return drawn


def show_frame(drawn: IdentityDrawn, k: int) -> int:
    """Show Blink frame *k* (0 the reference, 1 the compared timepoint; wrapped) and hide the other; returns it."""
    if not drawn.frames:
        return 0
    k = int(k) % len(drawn.frames)
    for i, artists in enumerate(drawn.frames):
        for artist in artists:
            artist.set_visible(i == k)
    drawn.frame = k
    return k


# ── Side by side ─────────────────────────────────────────────────────────────
def _montage_side(ax, montage, sites: dict, colour) -> list:
    xs, ys = [], []
    if montage is not None:
        ax.imshow(montage.image, cmap="gray", vmin=0, vmax=255, extent=montage.extent(), interpolation="nearest",
                  zorder=0)
        left, right, bottom, top = montage.extent()
        xs, ys = [left, right], [top, bottom]
    if sites:
        xy = np.array(list(sites.values()), dtype=float)
        ax.scatter(xy[:, 0], xy[:, 1], s=2.0, color=colour, linewidths=0, zorder=2)
        xs += list(xy[:, 0])
        ys += list(xy[:, 1])
    return [xs, ys]


def render_sides(fig, pair, ref_montage, other_montage, lines: list[str]) -> IdentityDrawn:
    """The two montages side by side, each in its own frame, with the sites of each timepoint dotted. Hovering lights
    an address on both and crosses where the registration predicts the reference's site lands (:func:`show_hover`)."""
    from matplotlib.patches import Circle

    from ui.theme import Colors

    lines = list(lines) + ["Side by side: hover near a nanowell on either image to light its address on both; the "
                           f"cross on {pair.timepoint} is where the registration predicts {pair.reference}'s nanowell "
                           "of that address lands."]
    bottom = _frame(fig, pair, lines)
    left = _image_axes(fig, [0.03, bottom, 0.455, _TOP - bottom])
    right = _image_axes(fig, [0.515, bottom, 0.455, _TOP - bottom])
    drawn = IdentityDrawn("sides", pair, {"reference": left, "compared": right}, zoomable=("reference", "compared"),
                          links=(("reference", "compared"),))
    _bg, ink, muted = _inks(left)
    pitch = getattr(pair.ref, "pitch", None) or 1.0
    xs, ys = [], []
    for ax, montage, own, name in ((left, ref_montage, pair.ref, f"{pair.reference} (reference)"),
                                   (right, other_montage, pair.other, pair.timepoint)):
        more_x, more_y = _montage_side(ax, montage, getattr(own, "sites", {}) or {}, muted)
        xs, ys = xs + more_x, ys + more_y
        _ink(ax.text(0.01, 0.99, name, transform=ax.transAxes, fontsize=8, color=ink, va="top",
                     gid="nw-identity-side"))
    # Both in the acquisition's common frame, so one set of limits shows the same place on each: they zoom and pan
    # together (``_identity_zoom``).
    for ax in (left, right):
        _limits(ax, xs, ys, 0.0)
    drawn.hover = {
        "reference": left.add_patch(Circle((0, 0), _HOVER_RING * pitch, fill=False, edgecolor=Colors.heatmap_selection,
                                           linewidth=1.8, zorder=5, visible=False, gid="nw-identity-hover-ref")),
        "compared": right.add_patch(Circle((0, 0), _HOVER_RING * pitch, fill=False,
                                           edgecolor=Colors.heatmap_selection, linewidth=1.8, zorder=5,
                                           visible=False, gid="nw-identity-hover-cmp")),
        "predicted": right.plot([0], [0], marker="x", markersize=10, mew=2.0, color=Colors.accent, zorder=6,
                                visible=False, gid="nw-identity-predicted")[0],
        "label": _ink(fig.text(0.97, 0.955, "", fontsize=8.5, ha="right", color=ink, gid="nw-identity-hover")),
    }
    return drawn


def show_hover(drawn: IdentityDrawn, target) -> bool:
    """Light *target* (``identity_compute.HoverTarget``; ``None`` clears) on both sides; whether anything changed."""
    lit = None if target is None else tuple(target.address)
    if lit == drawn.lit or not drawn.hover:
        return False
    drawn.lit = lit
    hover = drawn.hover
    ref_xy = None if target is None else target.ref_xy
    other_xy = None if target is None else target.other_xy
    predicted = None if target is None else target.predicted_xy
    for key, xy in (("reference", ref_xy), ("compared", other_xy)):
        hover[key].set_visible(xy is not None)
        if xy is not None:
            hover[key].center = tuple(xy)
    hover["predicted"].set_visible(predicted is not None)
    if predicted is not None:
        hover["predicted"].set_data([predicted[0]], [predicted[1]])
    if target is None:
        hover["label"].set_text("")
    else:
        said = f"{target.address[0]},{target.address[1]}"
        if other_xy is not None and predicted is not None:
            gap = float(np.hypot(other_xy[0] - predicted[0], other_xy[1] - predicted[1]))
            pitch = getattr(drawn.pair.other, "pitch", None)
            said += f" · lands {gap / pitch:.2f} pitch from itself" if pitch else f" · lands {gap:.1f} µm from itself"
        elif other_xy is None:
            said += f" · not addressed at {drawn.pair.timepoint}"
        elif ref_xy is None:
            said += f" · not addressed at {drawn.pair.reference}"
        hover["label"].set_text(said)
    return True


# ── going to one nanowell ────────────────────────────────────────────────────
#: How large the ring around a nanowell gone to is against the pitch: wider than the hover's, so both read at once.
_FOCUS_RING = 0.6


def draw_focus(fig, drawn: IdentityDrawn, marks, centres: dict) -> None:
    """Mark the nanowell the reader went to (``identity_focus_compute.FocusMarks``) on *drawn* and say what was found
    at the top right. A ring where each timepoint puts the address on its own frame — the reference's on the Distance
    map and Blink, each side's own on *Side by side* — and a cross where the other timepoint's address of that name
    lands: on the Distance map and Blink the compared site mapped into the reference's frame, on *Side by side* the
    registration's prediction. The two coincide when the address names the same nanowell."""
    from matplotlib.patches import Circle

    from ui.theme import Colors

    drawn.focus, drawn.focus_centres = marks, dict(centres)
    if drawn.mode not in fc.FOCUS_MODES:
        # Diagnostics draws the central square, not the array, and lays its whole figure out from its size: a
        # sentence pinned at the top right would sit on its title and verdict. The page greys the box there and
        # says why on it.
        return
    _bg, ink, _muted = _inks(SimpleNamespace(figure=fig))
    _ink(fig.text(0.97, 0.925, marks.text, fontsize=8, ha="right", va="center", color=ink, gid="nw-identity-focus"))
    radius = _FOCUS_RING * float(marks.pitch or 1.0)
    if drawn.mode in ("distance", "blink"):
        rings, crosses = [("map", marks.ref_xy)], [("map", marks.other_in_ref)]
    elif drawn.mode == "sides":
        rings, crosses = [("reference", marks.ref_xy), ("compared", marks.other_xy)], [("compared", marks.predicted_xy)]
    else:
        return
    for name, xy in rings:
        ax = drawn.axes.get(name)
        if ax is not None and xy is not None:
            ax.add_patch(Circle(xy, radius, fill=False, edgecolor=Colors.heatmap_selection, linewidth=2.2, zorder=7,
                                gid="nw-identity-focus-ring"))
    for name, xy in crosses:
        ax = drawn.axes.get(name)
        if ax is not None and xy is not None:
            ax.plot([xy[0]], [xy[1]], marker="x", markersize=12, mew=2.2, color=Colors.accent, zorder=8,
                    gid="nw-identity-focus-cross")


def attach_hover_labels(drawn: IdentityDrawn) -> None:
    """Name the nanowell under the pointer in the canvas's readout (``axes._aw_hover_label``, read by
    ``widgets.mpl_toolbar``): on the Distance map the reference's addresses; on Blink the shown frame's own — the
    compared timepoint's mapped into the reference's frame, so a wrong address reads as its wrong name; on *Side by
    side* each montage's own timepoint's. Diagnostics draws no nanowells and gets none."""
    pair = drawn.pair
    ref, other = pair.ref, pair.other
    kind = getattr(ref, "kind", None) or "hex"
    pitch = getattr(ref, "pitch", None) or getattr(other, "pitch", None)
    ref_sites, other_sites = getattr(ref, "sites", None) or {}, getattr(other, "sites", None) or {}
    if drawn.mode == "distance":
        drawn.axes["map"]._aw_hover_label = fc.SiteFinder(ref_sites, pitch, kind, f"{pair.reference} · ")
    elif drawn.mode == "blink":
        finders = (fc.SiteFinder(ref_sites, pitch, kind, f"{pair.reference} · "),
                   fc.SiteFinder(fc.mapped_sites(other_sites, pair.inverse), pitch, kind, f"{pair.timepoint} · "))
        drawn.axes["map"]._aw_hover_label = lambda x, y: finders[drawn.frame % 2](x, y)
    elif drawn.mode == "sides":
        drawn.axes["reference"]._aw_hover_label = fc.SiteFinder(ref_sites, pitch, kind, f"{pair.reference} · ")
        drawn.axes["compared"]._aw_hover_label = fc.SiteFinder(other_sites, getattr(other, "pitch", None) or pitch,
                                                               kind, f"{pair.timepoint} · ")


def render_identity_message(fig, text: str) -> None:
    render_message(fig, text, gid="nw-identity-message")
