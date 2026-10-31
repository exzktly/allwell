"""Draw the Identity view's **Diagnostics**: how the identity check registered one timepoint onto the reference, as a
reader who has never seen the code can follow it.

Read top to bottom, in the order a reader needs it:

* **the verdict** — the title, then the verdict and why in the largest type on the figure, after its ✓ / ✗ / ! glyph;
* **the pictures**, the figure's main content, each a square image as large as the figure allows:

  * **What was looked for** — the reference montage around its central square (the patch the check correlated,
    ``PATCH_PITCHES`` either side), the square outlined dashed in the accent;
  * **Where it was found** — the compared montage around the match, turned and scaled into the reference's
    orientation through the registration (so a good match looks like the first picture), the match outlined solid
    and the square the run itself accepted at that timepoint ringed round it, wider than the outline so both read;
  * **How clearly** — the match score (the correlation the check computed) over the place it searched, its axes in
    µm from where the square was at the reference, with three marks of three shapes: a cross where the square was,
    a circle on the best match, a diamond on the next-best place more than half a spacing from it.

  Each panel names its marks in a key under it — the mark itself, drawn as on the picture, then the words in the
  ink, with the scores — rather than writing labels over the picture, where they piled onto one another;
* **the checks** — one compact entry per remaining check (``identity_compute.checks``), each with its glyph in the
  theme's colours and its numbers against their thresholds;
* **the footer** — the units and the zoom hint, in the smallest type.

Where each of those goes is worked out from the figure's own size (``_identity_diagnostics_layout``): the plot card
has no fixed size, so nothing here is a fixed fraction. A change of size is laid out afresh (the page redraws when
its canvas settles at a new size, ``controller.on_canvas_resized``); ``IdentityDrawn.laid_out`` says for which size
this drawing was made.

Every position and sentence comes from ``identity_compute``; this module only draws. Every text a reader can read
carries a ``nw-identity-*`` gid. Pure matplotlib.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np

from review.features.nanowell import _identity_diagnostics_layout as dl
from review.features.nanowell._identity_render import IdentityDrawn, _image_axes, _ink, _inks, title_text
from review.features.nanowell.renderer import _PREF_PT, MAP_CMAP

#: The glyph each check state is drawn with.
STATE_GLYPHS = {"pass": "✓", "fail": "✗", "warn": "!", "info": "•", "skip": "–"}
#: The side of the outline drawn round the central square, in spacings. The sidecar records where the square is,
#: not how large; a nanowell is about three-quarters of the spacing wide on the plates measured, so the outline
#: marks the place, not the square's measured edge.
SQUARE_OUTLINE_PITCHES = 0.75
#: The radius of the ring round the square the run accepted, in spacings: wider than the outline's half-diagonal
#: (0.53 spacing), so a ring on an agreeing square circles the match's outline rather than sitting inside it.
ACCEPTED_RING_PITCHES = 0.62
#: The colour bar's label, and the shorter one it takes when the bar is shorter than the label.
BAR_LABEL, BAR_LABEL_SHORT = "match score", "score"


def state_colour(state: str, muted: str) -> str:
    """The theme colour a check state is drawn in: passed success, failed danger, a warning warn, the rest muted."""
    from ui.theme import Colors

    return {"pass": Colors.success, "fail": Colors.danger, "warn": Colors.warn}.get(state, muted)


def footer_lines(diag) -> list[str]:
    """What the Diagnostics' footer says the units and the score are."""
    spacing = (f"The checks measure in nanowell spacings (1 spacing = {diag.pitch_um:.0f} µm, centre to centre); "
               "the pictures' axes are in µm." if diag.pitch_um else
               "The checks measure in nanowell spacings, centre to centre; the pictures' axes are in µm.")
    return [spacing + " Match score: how alike the reference's patch and each place searched are once the regular "
            "lattice is subtracted (1 identical, 0 unrelated)."]


# ── what each panel writes ───────────────────────────────────────────────────
def _tick_text(*values) -> str:
    """The widest y tick label an axis over *values* draws: a minus and as many digits as the largest."""
    top = max((abs(float(v)) for v in values if v is not None and math.isfinite(float(v))), default=0.0)
    return "−" + "0" * max(1, len(str(int(top))))


def _match_ok(pair) -> bool:
    return bool((pair.entry.get("square") or {}).get("ok"))


def _crop_texts(pair, crops) -> dict:
    from ui.theme import Colors

    turn = float((pair.entry.get("square") or {}).get("rotation_deg") or 0.0)
    found_note = (f"{pair.timepoint}, turned {turn:+.1f}° into {pair.reference}'s orientation" if crops is not None
                  and crops.turned else f"{pair.timepoint} as imaged, where the square was expected")
    ticks = _tick_text(*(crops.extent if crops is not None else ()))
    looked = dl.PanelText("What was looked for", f"{pair.reference} around its central square",
                          "µm from the square's centre", tick_text=ticks)
    # The two crops are one grid, zoomed and panned together: the second's y ticks would repeat the first's.
    found = dl.PanelText("Where it was found", found_note, "µm from the square's centre", tick_text="")
    if crops is None:
        return {"looked": looked, "found": found}
    if crops.looked is not None:
        looked.keys.append(dl.KeyEntry("dashed-square", Colors.accent, "the central square", "nw-identity-crop-key"))
    if crops.turned and crops.found is not None:
        ok = _match_ok(pair)
        found.keys.append(dl.KeyEntry("square", Colors.success if ok else Colors.danger,
                                      "the match" if ok else "the best match (not accepted)", "nw-identity-crop-key"))
    if crops.accepted is not None and crops.found is not None:
        found.keys.append(dl.KeyEntry("ring", Colors.accent2, f"the square the run picked at {pair.timepoint}",
                                      "nw-identity-crop-key"))
    return {"looked": looked, "found": found}


def _surface_marks(pair, diag) -> list[tuple]:
    """``(where, swatch, colour, words, gid)`` for each mark on the match-score surface that has a place."""
    from ui.theme import Colors

    square = pair.entry.get("square") or {}
    marks = ((diag.relative(diag.expected_um), "plus", Colors.accent, f"at {pair.reference}", "nw-identity-expected"),
             (diag.relative(diag.found_um), "circle", Colors.success if _match_ok(pair) else Colors.danger,
              f"best {float(square['peak']):.2f}" if square.get("peak") is not None else "best", "nw-identity-peak"),
             (diag.relative(diag.runner_up_um), "diamond", Colors.warn,
              f"next {float(square['runner_up']):.2f}" if square.get("runner_up") is not None else "next",
              "nw-identity-runner-up"))
    return [m for m in marks if m[0] is not None]


def _surface_text(pair, diag) -> dl.PanelText:
    has = diag.surface is not None and diag.extent is not None
    text = dl.PanelText("How clearly", f"match score at each place in {pair.timepoint}",
                        f"µm from where the square was at {pair.reference}",
                        tick_text=_tick_text(*(diag.extent if has else ())), bar_label=BAR_LABEL if has else "")
    if has:
        text.keys = [dl.KeyEntry(swatch, colour, words, "nw-identity-mark-label")
                     for _at, swatch, colour, words, _gid in _surface_marks(pair, diag)]
    return text


# ── drawing ──────────────────────────────────────────────────────────────────
class _Pen:
    """Writes at points from the figure's lower left corner, in the plot's inks."""

    def __init__(self, fig, lay: dl.Layout, ts: dl.Typesetter):
        self.fig, self.w, self.h, self.ts = fig, lay.width, lay.height, ts
        self.ground, self.ink, self.muted = _inks(SimpleNamespace(figure=fig))

    def frac(self, x: float, y: float) -> tuple[float, float]:
        return x / self.w, y / self.h

    def text(self, x: float, y: float, text: str, size: float, gid: str, *, colour=None, weight: str = "normal",
             muted: bool = False, inked: bool = True, va: str = "top"):
        colour = colour or (self.muted if muted else self.ink)
        artist = self.fig.text(*self.frac(x, y), text, fontsize=size, fontweight=weight, color=colour, va=va,
                               gid=gid)
        return _ink(artist, muted=muted) if inked else artist

    def rect(self, x: float, y: float, w: float, h: float) -> list[float]:
        return [x / self.w, y / self.h, w / self.w, h / self.h]


def _ticks(side: float, per: float):
    """A tick locator giving an axis *side* points long about one tick every *per* points."""
    from matplotlib.ticker import MaxNLocator

    return MaxNLocator(nbins=max(1, int(side // per)), steps=[1, 2, 2.5, 5, 10])


def _halo():
    from matplotlib import patheffects

    from ui.theme import Colors

    return [patheffects.withStroke(linewidth=3.2, foreground=Colors.surface)]


def _draw_top(pen: _Pen, lay: dl.Layout, pair, verdict) -> None:
    """The title, and under it the verdict: its glyph, the lead in the largest type, the reason running on after it."""
    pen.text(dl.MARGIN, lay.title_y, title_text(pair), dl.TITLE_PT, "nw-identity-title")
    if verdict is None:
        return
    head, gid = lay.headline, f"nw-identity-check-{verdict.key}"
    x = dl.MARGIN + dl.HEADLINE_GLYPH_COLUMN
    pen.text(dl.MARGIN, head.y, STATE_GLYPHS.get(verdict.state, "•"), dl.HEADLINE_GLYPH_PT,
             f"nw-identity-state-{verdict.key}", colour=state_colour(verdict.state, pen.muted), weight="bold",
             inked=False, va="baseline")
    if head.lead:
        pen.text(x, head.y, head.lead, dl.HEADLINE_PT, gid, weight="semibold", va="baseline")
    for k, (text, y) in enumerate(zip(head.reason_rows, head.baselines())):
        if text:
            start = x + (head.lead_width + pen.ts.width(" ", dl.REASON_PT) if k == 0 and head.lead else 0.0)
            pen.text(start, y, text, dl.REASON_PT, gid, va="baseline")


def _draw_checks(pen: _Pen, lay: dl.Layout) -> None:
    row = dl.rows_height(1, dl.CHECK_PT)
    for place in lay.checks:
        check = place.check
        pen.text(place.x, place.y + 0.5, STATE_GLYPHS.get(check.state, "•"), dl.CHECK_GLYPH_PT,
                 f"nw-identity-state-{check.key}", colour=state_colour(check.state, pen.muted), weight="bold",
                 inked=False)
        quiet = check.state == "skip"
        for k, text in enumerate(place.rows):
            pen.text(place.x + dl.GLYPH_COLUMN, place.y - k * row, text, dl.CHECK_PT, f"nw-identity-check-{check.key}",
                     muted=quiet)


def _draw_footer(pen: _Pen, lay: dl.Layout) -> None:
    row = dl.rows_height(1, dl.FOOTER_PT)
    for k, text in enumerate(lay.footer_rows):
        pen.text(dl.MARGIN, lay.footer_y - k * row, text, dl.FOOTER_PT, "nw-identity-footer", muted=True)


def _swatch(pen: _Pen, kind: str, colour, x: float, y: float) -> None:
    """The mark a key entry names, centred at (*x*, *y*) points, drawn as it is on the picture."""
    from matplotlib.lines import Line2D
    from matplotlib.patches import Rectangle

    fig, side = pen.fig, 7.0
    if kind in ("dashed-square", "square"):
        artist = Rectangle(pen.frac(x - side / 2, y - side / 2), side / pen.w, side / pen.h, fill=False,
                           edgecolor=colour, linewidth=1.3, linestyle=(0, (2.0, 1.2)) if kind == "dashed-square"
                           else "-", transform=fig.transFigure)
    else:
        marker, size = {"ring": ("o", 7.5), "plus": ("+", 7.5), "circle": ("o", 6.5), "diamond": ("D", 5.0)}[kind]
        fx, fy = pen.frac(x, y)
        artist = Line2D([fx], [fy], marker=marker, markersize=size, markerfacecolor="none", markeredgecolor=colour,
                        mew=1.5, linestyle="none", transform=fig.transFigure)
    artist.set_gid("nw-identity-key-swatch")
    fig.add_artist(artist)


def _draw_keys(pen: _Pen, lay: dl.Layout) -> None:
    """Each key entry: the mark it names, then its words in the ink."""
    row = dl.rows_height(1, dl.KEY_PT)
    for entry, rows, x, y in lay.keys:
        _swatch(pen, entry.swatch, entry.colour, x + 4.0, y - row / 2 + 0.5)
        for k, text in enumerate(rows):
            pen.text(x + dl.KEY_SWATCH, y - k * row, text, dl.KEY_PT, entry.gid)


def _panel(pen: _Pen, ts, place: dl.PanelPlace, text: dl.PanelText, name: str):
    """A panel's axes at its place, with its title and note over it, its ticks and axis label under it."""
    ax = _image_axes(pen.fig, pen.rect(place.x, place.y, place.side, place.side))
    ax.set_axis_on()
    # The card's ground under the image: an axes added after the card was themed is matplotlib's white, which showed
    # round the match score's searched disc (the masked samples) as a white box on the dark card.
    ax.set_facecolor(pen.ground)
    ax._aw_type_scale = dl.TICK_PT / _PREF_PT          # the Properties pass draws its ticks and label at TICK_PT
    ax.tick_params(labelsize=dl.TICK_PT, colors=pen.muted, length=2, pad=2)
    for axis in (ax.xaxis, ax.yaxis):                 # as many ticks as the edge holds without their labels touching
        axis.set_major_locator(_ticks(place.side, 40.0))
    if not text.tick_text:
        ax.tick_params(axis="y", labelleft=False)
    for spine in ax.spines.values():
        spine.set_color(pen.muted)
        spine.set_linewidth(0.6)
    ax.set_xlabel("\n".join(place.xlabel_rows), fontsize=dl.TICK_PT, color=pen.muted, labelpad=2,
                  linespacing=dl.LEADING / 1.2)
    top = place.y + place.side + place.above
    for k, title in enumerate(place.title_rows):
        pen.text(place.x, top - dl.rows_height(k, dl.PANEL_TITLE_PT), title, dl.PANEL_TITLE_PT,
                 f"nw-identity-panel-title-{name}", weight="semibold")
    top -= dl.rows_height(len(place.title_rows), dl.PANEL_TITLE_PT)
    for k, note in enumerate(place.note_rows):
        pen.text(place.x, top - dl.rows_height(k, dl.NOTE_PT), note, dl.NOTE_PT, f"nw-identity-panel-note-{name}",
                 muted=True)
    return ax


def _say(pen: _Pen, ts, ax, side: float, text: str, gid: str = "nw-identity-panel-message") -> None:
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlabel("")
    rows = ts.wrap(text, 7.5, max(side - 10.0, 30.0))
    _ink(ax.text(0.5, 0.5, "\n".join(rows), transform=ax.transAxes, ha="center", va="center", fontsize=7.5,
                 color=pen.muted, gid=gid), muted=True)


def _square(ax, centre, side: float, colour, *, dashed: bool, gid: str):
    from matplotlib.patches import Rectangle

    return ax.add_patch(Rectangle((centre[0] - side / 2, centre[1] - side / 2), side, side, fill=False,
                                  edgecolor=colour, linewidth=1.6, linestyle=(0, (4.0, 2.5)) if dashed else "-",
                                  zorder=4, gid=gid))


def _crop_panels(pen: _Pen, ts, lay, texts, pair, diag, crops, why: str) -> dict:
    """*What was looked for* and *Where it was found*, on one grid measured from the square's centre."""
    from matplotlib.patches import Circle

    from ui.theme import Colors

    axes = {name: _panel(pen, ts, lay.panels[name], texts[name], name) for name in ("looked", "found")}
    pitch = float(getattr(pair.ref, "pitch", 0) or diag.pitch_um or 0)
    if crops is None:
        for name, ax in axes.items():
            _say(pen, ts, ax, lay.panels[name].side, why or "The reference's square is not known, so there is "
                                                            "nothing to crop.")
        return axes
    left, right, bottom, top = crops.extent
    match_colour = Colors.success if _match_ok(pair) else Colors.danger
    for name, image, outline in (("looked", crops.looked, Colors.accent), ("found", crops.found, match_colour)):
        ax = axes[name]
        ax.set_xlim(left, right)
        ax.set_ylim(bottom, top)
        if image is None:
            _say(pen, ts, ax, lay.panels[name].side, why or "The montage is not read.")
            continue
        ax.imshow(np.ma.masked_invalid(image), cmap="gray", vmin=0, vmax=255, extent=crops.extent,
                  interpolation="nearest", zorder=0, gid=f"nw-identity-crop-{name}")
        if name == "looked" or crops.turned:
            _square(ax, (0.0, 0.0), SQUARE_OUTLINE_PITCHES * pitch, outline, dashed=name == "looked",
                    gid=f"nw-identity-square-{name}")
    if crops.accepted is not None and crops.found is not None:
        axes["found"].add_patch(Circle(tuple(crops.accepted), ACCEPTED_RING_PITCHES * pitch, fill=False,
                                       edgecolor=Colors.accent2, linewidth=1.6, zorder=5, gid="nw-identity-accepted"))
    return axes


def _colour_bar(pen: _Pen, ts, place: dl.PanelPlace, image) -> None:
    cax = pen.fig.add_axes(pen.rect(place.x + place.side + dl.BAR_GAP, place.y, dl.BAR_WIDTH, place.side))
    bar = pen.fig.colorbar(image, cax=cax)
    cax._aw_colorbar = True
    cax._aw_type_scale = dl.BAR_PT / _PREF_PT
    label = place.bar_label if ts.width(place.bar_label, dl.BAR_PT) <= place.side - 4 else BAR_LABEL_SHORT
    _ink(bar.ax.set_ylabel(label, fontsize=dl.BAR_PT, color=pen.ink, labelpad=3))
    bar.ax.tick_params(labelsize=dl.BAR_PT, colors=pen.muted, length=2, pad=2)
    bar.locator = _ticks(place.side, 24.0)
    bar.update_ticks()
    bar.outline.set_linewidth(0.6)


def _surface_panel(pen: _Pen, ts, lay, text, pair, diag):
    """*How clearly*: the match score around where the square was expected, the three places marked."""
    place = lay.panels["surface"]
    ax = _panel(pen, ts, place, text, "surface")
    if diag.surface is None or diag.extent is None:
        _say(pen, ts, ax, place.side, "No match scores were recorded for this timepoint.", "nw-identity-no-surface")
        return ax
    image = ax.imshow(np.ma.masked_invalid(diag.surface), cmap=MAP_CMAP, extent=diag.extent, interpolation="nearest",
                      zorder=0, gid="nw-identity-surface")
    _colour_bar(pen, ts, place, image)
    shapes = {"plus": ("+", 8.0), "circle": ("o", 7.5), "diamond": ("D", 5.5)}
    halo = _halo()
    for at, swatch, colour, _words, gid in _surface_marks(pair, diag):
        marker, size = shapes[swatch]
        ax.plot([at[0]], [at[1]], marker=marker, markersize=size, mew=1.4, linestyle="none", markerfacecolor="none",
                color=colour, zorder=3, path_effects=halo, gid=gid)
    left, right, bottom, top = diag.extent
    ax.set_xlim(left, right)
    ax.set_ylim(bottom, top)
    return ax


def render_diagnostics(fig, pair, diag, crops, lines: list[str], *, why: str = "") -> IdentityDrawn:
    """The verdict, the three pictures and the checks (module docstring), laid out for *fig*'s size now. *crops* is
    ``identity_compute.Crops`` (``None`` when there is nothing to crop or the montages are not read yet, which *why*
    says); *lines* go in the footer."""
    fig.clear()
    width, height = (float(v) * 72.0 for v in fig.get_size_inches())
    ts = dl.Typesetter()
    checks = list(diag.checks)
    verdict = checks.pop(0) if checks and checks[0].key == "verdict" else None
    texts = {**_crop_texts(pair, crops), "surface": _surface_text(pair, diag)}
    lay = dl.layout(ts, width, height, title=title_text(pair), headline=verdict.text if verdict else "", texts=texts,
                    checks=checks, footer=list(lines) + footer_lines(diag))
    pen = _Pen(fig, lay, ts)
    _draw_top(pen, lay, pair, verdict)
    axes = _crop_panels(pen, ts, lay, texts, pair, diag, crops, why)
    axes["surface"] = _surface_panel(pen, ts, lay, texts["surface"], pair, diag)
    _draw_keys(pen, lay)
    _draw_checks(pen, lay)
    _draw_footer(pen, lay)
    return IdentityDrawn("diagnostics", pair, axes, diagnostics=diag, crops=crops,
                         zoomable=("looked", "found", "surface"), links=(("looked", "found"),),
                         laid_out=laid_out_size(fig))


def laid_out_size(fig) -> tuple[float, float]:
    """*fig*'s size in inches, to the hundredth: what a Diagnostics drawing records it was laid out for, and what
    the page compares against once its canvas settles at a new size."""
    w, h = fig.get_size_inches()
    return round(float(w), 2), round(float(h), 2)
