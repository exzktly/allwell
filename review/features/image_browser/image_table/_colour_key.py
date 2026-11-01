"""The colour key under an Image Table grid of nanowell frames: what each colour means.

A metric map draws every nanowell as a colour, and without a key that colour is only a ranking: a reader could see
that one well is "more yellow" than another and not whether yellow is 3 cells or 30, nor whether the wells share
one scale at all. So for every nanowell metric map and fluorescence montage in the grid, the key draws one row:

* the metric's full name (the row labels carry a short one);
* a strip of its colormap with the window's ends written under it — and for a fold change, "no change" marked in
  the middle, where the pooled window centres it (``centred_window`` below);
* what the window is: one shared by every well shown (Auto, pooled), a fixed one the user typed, or each cell's
  own (**per cell**), which says in so many words that colours are then not comparable;
* for a metric map, the empty ring a nanowell with no value is drawn as.

The key is pixels (``key_rgb``), so the live grid, the exported figure and the PowerPoint deck show the same one;
:func:`image_compose.colour_keys` decides what goes in it. Qt-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ColourKey:
    """One key row."""

    title: str
    cmap: str                       # a LUT name; "" or "Greys" for grey
    lo: float | None                # the window, or None when each cell has its own
    hi: float | None
    log: bool = False
    centre: float | None = None     # "no change", for a fold change
    scope: str = ""                 # what the window is, in a sentence
    undefined: bool = False         # draw the "no value" ring


#: Layout at scale 1, in pixels: the row height, the colour strip's size, the gaps.
_ROW_PX = 56
_STRIP_W, _STRIP_H = 220, 12
_PAD = 8
_FONT_PX = 12


def metric_map_background_rgb() -> tuple[int, int, int]:
    """What a metric map draws where no nanowell is: a neutral dark grey, not black, because every colormap's
    darkest end — viridis' deep purple included — all but vanishes on black, and the lowest values with it. A
    montage keeps black: there it is the absence of light, and a stretch puts nothing below it."""
    from ui import theme

    return _rgb(theme.Colors.panel_elevated)


def centred_window(ctx, pooled: list) -> tuple[float, float]:
    """The pooled ``(lo, hi)``, centred on "no change" for a nanowell fold change.

    A fold change pooled to its own min and max puts 1 wherever the data happen to
    fall, so no colour means "unchanged" and a gain and a loss of the same size
    look different. A ratio (log-scaled on its row) gets ``(1/k, k)``, a fold
    change already on a log scale ``(-m, m)``, so the diverging map's midpoint is
    no change. A zero ratio has no logarithm; it sits at the bottom of the window.
    """
    from review.data.observations import fold_change_scale, nanowell_metric_for_token

    lo, hi, low_pos, chan = pooled
    scale = fold_change_scale(nanowell_metric_for_token(ctx, chan))
    if scale == "log":
        m = max(abs(lo), abs(hi)) or 1.0
        return -m, m
    if scale == "ratio" and low_pos is not None and hi > 0:
        k = max(hi, 1.0 / low_pos, 1.0 + 1e-6)
        return 1.0 / k, k
    return lo, hi


def _ramp(cmap: str, n: int) -> np.ndarray:
    from widgets.lut_cmaps import lut_cmap

    t = np.linspace(0.0, 1.0, max(2, n))
    if not cmap or cmap == "Greys":
        grey = (t * 255).astype(np.uint8)
        return np.stack([grey, grey, grey], axis=-1)
    return (lut_cmap(cmap)(t)[:, :3] * 255).astype(np.uint8)


def _position(value: float, lo: float, hi: float, log: bool) -> float | None:
    """Where *value* falls along the strip, 0 to 1, as the cells are coloured."""
    if log:
        if value <= 0 or lo <= 0 or hi <= 0:
            return None
        lo, hi, value = math.log10(lo), math.log10(hi), math.log10(value)
    if hi <= lo:
        return None
    t = (value - lo) / (hi - lo)
    return t if 0.0 <= t <= 1.0 else None


def key_ticks(key: ColourKey) -> list[tuple[float, str]]:
    """``[(position 0–1 along the strip, text)]``: the window's two ends, and for a fold change "no change" where
    the cells' colouring puts it; ``[]`` when each cell has its own window (the strip then reads low to high)."""
    if key.lo is None or key.hi is None:
        return []
    ticks = [(0.0, number(key.lo)), (1.0, number(key.hi))]
    if key.centre is not None:
        t = _position(key.centre, key.lo, key.hi, key.log)
        if t is not None:
            ticks.append((t, f"{number(key.centre)} = no change"))
    return ticks


def number(value: float) -> str:
    """A window end as the key writes it: three significant figures (four, with a thousands separator, from
    1,000), and an exponent only for the very large or very small."""
    v = float(value)
    if v != 0 and (abs(v) >= 1e5 or abs(v) < 1e-3):
        return f"{v:.3g}"
    return f"{v:,.4g}" if abs(v) >= 1000 else f"{v:.3g}"


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    text = str(hex_colour).lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def key_rgb(keys, *, width_px: int, font, scale: float = 1.0, on_screen: bool = True) -> np.ndarray | None:
    """The key for *keys* as an ``(H, W, 3)`` uint8 image *width_px* wide, or ``None`` with no keys.

    *font* is ``font(px)`` → a PIL font (the Image Table's own chrome face). *on_screen* draws it in the app's own
    panel and text colours, under the live grid; off, in the publication
    palette (``CPub``), for a saved figure on a white page."""
    from PIL import Image, ImageDraw

    from ui import theme

    keys = list(keys)
    if not keys:
        return None
    if on_screen:
        bg, ink = _rgb(theme.Colors.surface), _rgb(theme.Colors.text_primary)
    else:
        bg, ink = _rgb(theme.CPub.bg), _rgb(theme.CPub.text)
    undefined_ink = _rgb(theme.Colors.plot_undefined_ink)
    black = metric_map_background_rgb()             # the ring swatch sits on what a metric map's background is
    s = max(0.5, float(scale))
    row, pad, font_px = int(_ROW_PX * s), int(_PAD * s), max(6, int(_FONT_PX * s))
    strip_w, strip_h = int(_STRIP_W * s), int(_STRIP_H * s)
    width = max(int(width_px), strip_w + 2 * pad)
    img = Image.new("RGB", (width, row * len(keys) + pad), tuple(bg))
    draw = ImageDraw.Draw(img)
    font, small = font(font_px), font(max(6, int(font_px * 0.85)))
    for i, key in enumerate(keys):
        y = pad + i * row
        title = key.title + (f" — {key.scope}" if key.scope else "")
        draw.text((pad, y), title, fill=tuple(ink), font=font)
        sy = y + int(font_px * 1.45)
        ramp = _ramp(key.cmap, strip_w)
        strip = np.repeat(ramp[np.newaxis, :, :], strip_h, axis=0)
        img.paste(Image.fromarray(strip), (pad, sy))
        draw.rectangle([pad, sy, pad + strip_w - 1, sy + strip_h - 1], outline=tuple(ink))
        ty = sy + strip_h + max(1, int(2 * s))
        ticks = key_ticks(key)
        if ticks:
            for t, text in ticks:
                x = pad + int(round(t * (strip_w - 1)))
                draw.line([x, sy + strip_h, x, ty], fill=tuple(ink))
                tw = draw.textlength(text, font=small)
                tx = min(max(pad, int(x - tw / 2)), pad + strip_w + int(tw))
                if t == 0.0:
                    tx = pad
                elif t == 1.0:
                    tx = int(pad + strip_w - tw)
                draw.text((tx, ty), text, fill=tuple(ink), font=small)
        else:
            draw.text((pad, ty), "low", fill=tuple(ink), font=small)
            draw.text((pad + strip_w - int(draw.textlength("high", font=small)), ty), "high",
                      fill=tuple(ink), font=small)
        x = pad + strip_w + 2 * pad
        if key.log:
            draw.text((x, sy), "log scale", fill=tuple(ink), font=small)
            x += int(draw.textlength("log scale", font=small)) + 2 * pad
        if key.undefined:
            d = strip_h + int(4 * s)
            draw.rectangle([x, sy - int(2 * s), x + d, sy - int(2 * s) + d], fill=black)
            m = max(1, int(2 * s))
            draw.ellipse([x + m, sy - int(2 * s) + m, x + d - m, sy - int(2 * s) + d - m],
                         outline=tuple(undefined_ink), width=max(1, int(2 * s)))
            draw.text((x + d + pad // 2, sy), "no value (empty ring)", fill=tuple(ink), font=small)
    return np.asarray(img, dtype=np.uint8)
