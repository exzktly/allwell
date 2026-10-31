"""The Array view's outline key: a swatch for each way a nanowell is outlined in the view, drawn on the figure.

The Array view outlines each nanowell by its state (``ui/nanowell_sites.STATE_COLOURS``) or, filled by a metric,
outlines the ones without a value in grey with a hatch or dash that names their state (``renderer._STATE_STYLE``).
Both used to be explained by one sentence in the footer, which named the colours in words a reader then had to match
against the rings by eye — and called every grey ring "not seen", although a nanowell excluded with its field, or
whose rim was out of focus, is grey too. The key draws each swatch the way the nanowells are drawn: the same colour,
the same dash, the same hatch, as a ring. It lists only what the view holds, each with how many of the nanowells
drawn it covers, so a key entry is never a claim about something that is not on screen.

:func:`key_entries` decides what the key says and is pure; :func:`draw_key` puts it on the axes as a standalone
legend (``ax.add_artist``), so the export-style pass sizes and recolours its text but never moves it
(``review/core/plot_style._apply_legend_prefs``).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class KeyEntry:
    """One swatch: a ring drawn in *colour*, with *linestyle* and *hatch*, beside *label*."""
    label: str
    colour: str
    linestyle: str = "solid"
    hatch: str = ""


#: The state words as the key writes them.
_WORDS = {"out_of_focus": "rim out of focus", "not_located": "not located", "identity_contradicted":
          "identity contradicted", "field_excluded": "field excluded"}


#: The order the coloured states are keyed in: the nanowells a result is made of first, then the reasons a
#: nanowell has none.
_ORDER = ("measured", "empty", "occluded", "clipped")


def _row_major(items: list, ncol: int) -> list:
    """*items* reordered so a legend, which fills its columns top to bottom, reads them left to right."""
    rows = -(-len(items) // ncol)
    grid = [items[r * ncol:(r + 1) * ncol] for r in range(rows)]
    return [row[c] for c in range(ncol) for row in grid if c < len(row)]


def state_word(state: str) -> str:
    """*state* as the key writes it."""
    return _WORDS.get(state, str(state).replace("_", " "))


def _count(label: str, n: int) -> str:
    return f"{label} · {n:,}"


def key_entries(sites, *, colour: str, styles: dict) -> list[KeyEntry]:
    """The key for the nanowells in *sites* drawn with *colour* (``"state"`` or ``"metric"``).

    *styles* is the renderer's ``state -> (hatch, linestyle)`` for a nanowell without a value. A fused nanowell is
    drawn dashed in the fused colour whatever else it is, so it is counted there and nowhere else, as it is drawn.
    """
    from ui.nanowell_sites import FUSED_COLOUR, STATE_COLOURS, state_colour
    from ui.theme import Colors

    states = np.array([str(s) for s in sites.state], dtype=object)
    fused = np.asarray(sites.fused, dtype=bool)
    plain = ~fused
    entries: list[KeyEntry] = []
    if colour == "metric":
        has = np.isfinite(np.asarray(sites.values, dtype=float))
        bare = plain & ~has & np.isin(states, ["measured", "empty"])
        if bare.any():
            entries.append(KeyEntry(_count("no value", int(bare.sum())), Colors.plot_undefined_ink))
        for state, (hatch, style) in styles.items():
            n = int((plain & ~has & (states == state)).sum())
            if n:
                entries.append(KeyEntry(_count(f"no value: {state_word(state)}", n), Colors.plot_undefined_ink,
                                        style, hatch))
    else:
        for state in sorted(STATE_COLOURS, key=lambda k: _ORDER.index(k) if k in _ORDER else len(_ORDER)):
            n = int((plain & (states == state)).sum())
            if n:
                entries.append(KeyEntry(_count(state_word(state), n), STATE_COLOURS[state]))
        # Every state without a colour of its own is drawn in the one muted colour, so it is one swatch naming them.
        rest = sorted({s for s, p in zip(states, plain) if p and s not in STATE_COLOURS})
        if rest:
            n = int((plain & np.isin(states, rest)).sum())
            entries.append(KeyEntry(_count(" / ".join(state_word(s) for s in rest), n), state_colour(rest[0])))
    if fused.any():
        entries.append(KeyEntry(_count("fused: on a tile seam", int(fused.sum())), FUSED_COLOUR, "dashed"))
    return entries


def draw_key(ax, entries: list[KeyEntry], *, linewidth: float, fontsize: float) -> object | None:
    """The key as a row of ring swatches along the top edge of *ax*, right-aligned clear of the title; ``None``
    when there is nothing to key."""
    from matplotlib.legend import Legend
    from matplotlib.legend_handler import HandlerPatch
    from matplotlib.patches import Circle, Patch

    from widgets.plot_card import follows_theme

    if not entries:
        return None

    class _Ring(HandlerPatch):
        """A legend swatch drawn as a ring, as the nanowells are, rather than matplotlib's default rectangle."""

        def create_artists(self, legend, orig, xdescent, ydescent, width, height, fontsize, trans):
            radius = 0.42 * min(width, height * 1.6)
            ring = Circle((width / 2 - xdescent, height / 2 - ydescent), radius)
            self.update_prop(ring, orig, legend)
            ring.set_fill(False)
            ring.set_transform(trans)               # after update_prop, which copies the handle's own transform over
            return [ring]

    ncol = min(len(entries), 4)
    entries = _row_major(entries, ncol)
    handles = [Patch(facecolor="none", edgecolor=e.colour, linestyle=e.linestyle, hatch=e.hatch or None,
                     linewidth=linewidth, label=e.label) for e in entries]
    key = Legend(ax, handles, [e.label for e in entries], loc="lower right", bbox_to_anchor=(1.0, 1.0),
                 ncol=ncol, frameon=False, fontsize=fontsize, handlelength=1.6, handleheight=1.6,
                 columnspacing=1.2, handletextpad=0.5, borderaxespad=0.2, borderpad=0.1,
                 handler_map={Patch: _Ring()})
    key.set_gid("nw-array-key")
    for text in key.get_texts():
        follows_theme(text)
    ax.add_artist(key)
    return key
