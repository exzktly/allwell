"""Draw the Identity view's **Diagnostics**: how the identity check registered one timepoint onto the reference, as a
reader who has never seen the code can follow it.

Three pictures side by side, and a verdict block under them:

* **What was looked for** — the reference montage around its central square (the patch the check correlated,
  ``PATCH_PITCHES`` either side), the square outlined;
* **Where it was found** — the compared montage around the match, turned and scaled into the reference's orientation
  through the registration (so a good match looks like the first picture), the match outlined and the square the
  run itself accepted at that timepoint ringed;
* **How clearly** — the match score (the correlation the check computed) over the place it searched, its axes in µm
  from where the square was at the reference, the best match and the next-best place more than half a spacing from
  it marked and named with their scores;
* **the verdict block** — one line per check (``identity_compute.checks``), each with a ✓ / ✗ / ! state in the
  theme's colours and its numbers against their thresholds.

Every position and sentence comes from ``identity_compute``; this module only draws. Every text a reader can read
carries a ``nw-identity-*`` gid. Pure matplotlib.
"""

from __future__ import annotations

import textwrap
from types import SimpleNamespace

import numpy as np

from review.features.nanowell._identity_render import (
    _TOP, IdentityDrawn, _colourbar, _frame, _image_axes, _ink, _inks,
)
from review.features.nanowell.renderer import MAP_CMAP

#: The verdict block's type, in points, and the height of one of its rows as a fraction of a 6.4-inch figure.
_CHECK_PT = 8.0
_CHECK_ROW = 0.034
#: The glyph each check state is drawn with.
STATE_GLYPHS = {"pass": "✓", "fail": "✗", "warn": "!", "info": "•", "skip": "–"}
#: The panel's tick and label type, in points.
_TICK_PT = 6.5
#: The side of the outline drawn round the central square, in spacings. The sidecar records where the square is,
#: not how large; a nanowell is about three-quarters of the spacing wide on the plates measured, so the outline
#: marks the place, not the square's measured edge.
SQUARE_OUTLINE_PITCHES = 0.75


def state_colour(state: str, muted: str) -> str:
    """The theme colour a check state is drawn in: passed success, failed danger, a warning warn, the rest muted."""
    from ui.theme import Colors

    return {"pass": Colors.success, "fail": Colors.danger, "warn": Colors.warn}.get(state, muted)


def _rows(fig, checks, left: float) -> list[tuple[int, str]]:
    """``(check index, row)`` for each row of the verdict block: each check's sentence broken at the words to the
    room right of *left*."""
    width_pt = fig.get_size_inches()[0] * 72.0 * (0.98 - left)
    chars = max(40, int(width_pt / (0.5 * _CHECK_PT)))
    return [(i, row) for i, check in enumerate(checks) for row in (textwrap.wrap(check.text, chars) or [""])]


def _draw_checks(fig, checks, bottom: float) -> float:
    """The verdict block, its last row at *bottom* (figure fraction); returns the fraction above its first row."""
    _bg, ink, muted = _inks(SimpleNamespace(figure=fig))
    rows = _rows(fig, checks, 0.06)
    row = _CHECK_ROW * 6.4 / max(fig.get_size_inches()[1], 1.0)
    for k, (i, text) in enumerate(rows):
        y = bottom + (len(rows) - 1 - k) * row
        check = checks[i]
        if k == 0 or rows[k - 1][0] != i:
            fig.text(0.03, y, STATE_GLYPHS.get(check.state, "•"), fontsize=_CHECK_PT + 1.5, fontweight="bold",
                     color=state_colour(check.state, muted), gid=f"nw-identity-state-{check.key}")
        weight = "semibold" if check.key == "verdict" else "normal"
        quiet = check.state == "skip"
        _ink(fig.text(0.06, y, text, fontsize=_CHECK_PT, fontweight=weight, color=muted if quiet else ink,
                      gid=f"nw-identity-check-{check.key}"), muted=quiet)
    return bottom + len(rows) * row


def _panel(fig, rect, title: str, note: str, name: str):
    ax = _image_axes(fig, rect)
    ax.set_anchor("N")                               # the three panels' titles on one line, whatever their shapes
    ax.set_axis_on()
    _bg, ink, muted = _inks(ax)
    ax.tick_params(labelsize=_TICK_PT, colors=muted, length=2)
    for spine in ax.spines.values():
        spine.set_color(muted)
        spine.set_linewidth(0.6)
    _ink(ax.text(0.0, 1.13, title, transform=ax.transAxes, fontsize=8.5, fontweight="semibold", color=ink,
                 va="bottom", gid=f"nw-identity-panel-title-{name}"))
    _ink(ax.text(0.0, 1.02, note, transform=ax.transAxes, fontsize=7, color=muted, va="bottom",
                 gid=f"nw-identity-panel-note-{name}"), muted=True)
    return ax


def _say(ax, text: str, gid: str = "nw-identity-panel-message") -> None:
    _bg, _ink_, muted = _inks(ax)
    _ink(ax.text(0.5, 0.5, "\n".join(textwrap.wrap(text, 34)), transform=ax.transAxes, ha="center", va="center",
                 fontsize=7.5, color=muted, gid=gid), muted=True)


def _square(ax, centre, side: float, colour, *, dashed: bool, gid: str):
    from matplotlib.patches import Rectangle

    return ax.add_patch(Rectangle((centre[0] - side / 2, centre[1] - side / 2), side, side, fill=False,
                                  edgecolor=colour, linewidth=1.6, linestyle="--" if dashed else "-", zorder=4,
                                  gid=gid))


def _crop_panels(fig, rects, pair, diag, crops, why: str) -> dict:
    """*What was looked for* and *Where it was found*, on one grid measured from the square's centre."""
    from ui.theme import Colors

    square = pair.entry.get("square") or {}
    turn = float(square.get("rotation_deg") or 0.0)
    found_note = (f"{pair.timepoint}, turned {turn:+.1f}° into {pair.reference}'s orientation" if crops is not None
                  and crops.turned else f"{pair.timepoint} as imaged, where the square was expected")
    looked = _panel(fig, rects[0], "What was looked for", f"{pair.reference} around its central square", "looked")
    found = _panel(fig, rects[1], "Where it was found", found_note, "found")
    axes = {"looked": looked, "found": found}
    for ax in axes.values():
        ax.set_xlabel("µm from the square's centre", fontsize=_TICK_PT, color=_inks(ax)[2])
    side = SQUARE_OUTLINE_PITCHES * float(getattr(pair.ref, "pitch", 0) or diag.pitch_um or 0)
    if crops is None:
        for ax in axes.values():
            _say(ax, why or "The reference's square is not known, so there is nothing to crop.")
        return axes
    left, right, bottom, top = crops.extent
    ok = bool(square.get("ok"))
    match_colour = Colors.success if ok else Colors.danger
    for name, image, outline in (("looked", crops.looked, Colors.accent), ("found", crops.found, match_colour)):
        ax = axes[name]
        ax.set_xlim(left, right)
        ax.set_ylim(bottom, top)
        if image is None:
            _say(ax, why or "The montage is not read.")
            continue
        ax.imshow(np.ma.masked_invalid(image), cmap="gray", vmin=0, vmax=255, extent=crops.extent,
                  interpolation="nearest", zorder=0, gid=f"nw-identity-crop-{name}")
        if name == "looked" or crops.turned:
            _square(ax, (0.0, 0.0), side, outline, dashed=name == "looked", gid=f"nw-identity-square-{name}")
    # The key, under the pictures in the colours it names: a legend over a montage is unreadable on its dark ground.
    keys = [(looked, "▢ dashed: the central square", Colors.accent)] if crops.looked is not None else []
    if crops.turned and crops.found is not None:
        keys.append((found, "▢ the match" if ok else "▢ the best match (not accepted)", match_colour))
    if crops.accepted is not None and crops.found is not None:
        found.plot([crops.accepted[0]], [crops.accepted[1]], marker="o", markersize=16, markerfacecolor="none",
                   markeredgecolor=Colors.accent2, mew=1.8, linestyle="none", zorder=5, gid="nw-identity-accepted")
        keys.append((found, f"○ the square the run picked at {pair.timepoint}", Colors.accent2))
    rows: dict = {}
    for ax, text, colour in keys:
        k = rows.get(id(ax), 0)
        rows[id(ax)] = k + 1
        ax.text(0.0, -0.2 - 0.075 * k, text, transform=ax.transAxes, fontsize=7, color=colour, va="top",
                gid="nw-identity-crop-key")
    return axes


def _surface_panel(fig, rect, pair, diag):
    """*How clearly*: the match score around where the square was expected, the best and next-best places named."""
    from ui.theme import Colors

    ax = _panel(fig, rect, "How clearly", f"match score at each place in {pair.timepoint}", "surface")
    _bg, ink, muted = _inks(ax)
    ax.set_xlabel(f"µm from where the square was at {pair.reference}", fontsize=_TICK_PT, color=muted)
    if diag.surface is None or diag.extent is None:
        _say(ax, "No match scores were recorded for this timepoint.", "nw-identity-no-surface")
        return ax
    image = ax.imshow(np.ma.masked_invalid(diag.surface), cmap=MAP_CMAP, extent=diag.extent, interpolation="nearest",
                      zorder=0, gid="nw-identity-surface")
    _colourbar(fig, ax, image, "match score (1: identical)")
    square = pair.entry.get("square") or {}
    ok = bool(square.get("ok"))
    marks = ((diag.relative(diag.expected_um), "+", ink, f"at {pair.reference}", "nw-identity-expected", (-8, -12),
              "right"),
             (diag.relative(diag.found_um), "o", Colors.success if ok else Colors.danger,
              f"best {float(square['peak']):.2f}" if square.get("peak") is not None else "best",
              "nw-identity-peak", (9, 7), "left"),
             (diag.relative(diag.runner_up_um), "o", Colors.warn,
              f"next {float(square['runner_up']):.2f}" if square.get("runner_up") is not None else "next",
              "nw-identity-runner-up", (9, -13), "left"))
    for at, marker, colour, label, gid, offset, side in marks:
        if at is None:
            continue
        ax.plot([at[0]], [at[1]], marker=marker, markersize=12 if marker == "o" else 10, mew=1.8, linestyle="none",
                markerfacecolor="none", color=colour, zorder=3, gid=gid)
        text = ax.annotate(label, at, xytext=offset, textcoords="offset points", fontsize=7, color=colour, ha=side,
                           fontweight="semibold", zorder=4, annotation_clip=True, gid="nw-identity-mark-label")
        if colour == ink:
            _ink(text)
    left, right, bottom, top = diag.extent
    ax.set_xlim(left, right)
    ax.set_ylim(bottom, top)
    return ax


def footer_lines(diag) -> list[str]:
    """What the Diagnostics' footer says the picture's units and scores are."""
    spacing = (f"Distances in nanowell spacings (1 spacing = {diag.pitch_um:.0f} µm, centre to centre)."
               if diag.pitch_um else "Distances in nanowell spacings, centre to centre.")
    return [spacing + " Match score: how alike the reference's patch around its square and each place of the compared "
            "montage are once the regular lattice is subtracted (1 identical, 0 unrelated)."]


def render_diagnostics(fig, pair, diag, crops, lines: list[str], *, why: str = "") -> IdentityDrawn:
    """The three pictures and the verdict block (module docstring). *crops* is ``identity_compute.Crops`` (``None``
    when there is nothing to crop or the montages are not read yet, which *why* says); *lines* go in the footer."""
    bottom = _frame(fig, pair, list(lines) + footer_lines(diag))
    top = _draw_checks(fig, diag.checks, bottom + 0.01)
    panel_bottom = top + 0.13                        # room for the axes' labels and the crops' key under them
    height = max(0.12, _TOP - 0.07 - panel_bottom)
    rects = ([0.06, panel_bottom, 0.25, height], [0.37, panel_bottom, 0.25, height],
             [0.68, panel_bottom, 0.23, height])
    axes = _crop_panels(fig, rects[:2], pair, diag, crops, why)
    axes["surface"] = _surface_panel(fig, rects[2], pair, diag)
    return IdentityDrawn("diagnostics", pair, axes, diagnostics=diag, crops=crops,
                         zoomable=("looked", "found", "surface"), links=(("looked", "found"),))
