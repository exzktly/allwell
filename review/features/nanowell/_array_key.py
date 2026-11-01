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


def _why(sites, bare, why: dict) -> list[tuple[str, int]]:
    """Why the *bare* nanowells (measured, no value) have none, most common first, at most three and the rest as
    ``other``; a nanowell whose reason is unknown is counted under ``""``: ``[("no cells at baseline", 12), …]``."""
    tally: dict[str, int] = {}
    for k in np.flatnonzero(bare):
        _w, q, r = sites.sites[k]
        reason = why.get((q, r)) or ""
        tally[reason] = tally.get(reason, 0) + 1
    ranked = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
    if len(ranked) > 3:
        ranked = ranked[:3] + [("other", sum(n for _r, n in ranked[3:]))]
    return ranked


#: The short words a no-value nanowell writes inside itself, where its value would be (:func:`reason_tag`): the key
#: lists the same reasons in full, each beside the tag the nanowells carry.
_TAGS = {"no cells at baseline": "0 at base", "empty at baseline": "0 at base", "no count": "no count",
         "fused, no count": "fused", "no confluence": "no conf.", "no cells": "no cells",
         "no fluorescence": "no fluor.", "< 3 timepoints with cells": "< 3 tps", "no count at baseline": "base: no n",
         "no confluence at baseline": "base: no conf.", "no signal at baseline": "base: no sig.",
         "occluded": "occl.", "clipped": "clipped", "rim out of focus": "soft", "no address": "no addr.",
         "not located": "not found", "not in roster": "absent", "never measured": "never",
         "identity contradicted": "id. wrong", "field excluded": "field excl.", "outside the fixed cohort": "cohort"}


def reason_tag(reason: str) -> str:
    """*reason* (a ``distribution_compute.reason_label``) as the few words a nanowell has room for:
    ``no cells at baseline`` → ``0 at base``, ``baseline occluded`` → ``base occl.``; ``""`` for no reason."""
    if not reason:
        return ""
    if reason in _TAGS:
        return _TAGS[reason]
    for head, joined in (("baseline: ", "base: "), ("baseline ", "base ")):
        if reason.startswith(head):
            rest = reason[len(head):]
            return joined + _TAGS.get(rest, rest)
    return reason if len(reason) <= 10 else reason[:9] + "…"


def _no_value_entries(sites, bare, why: dict) -> list[str]:
    """One "no value" label per reason, each ending with its count: ``no value: no cells at baseline (0 at base) · 3``
    — the reason in full, then in brackets the tag the nanowells carry inside them (``reason_tag``) — so the reason
    of any one nanowell on the array can be read off it. Without reasons, one ``no value · N``."""
    out = []
    for reason, n in _why(sites, bare, why):
        if not reason:
            out.append(_count("no value", n))
        elif reason == "other":
            out.append(_count("no value: other reasons", n))
        else:
            tag = reason_tag(reason)
            out.append(_count(f"no value: {reason}" + (f" ({tag})" if tag != reason else ""), n))
    return out


def key_entries(sites, *, colour: str, styles: dict, why: dict | None = None) -> list[KeyEntry]:
    """The key for the nanowells in *sites* drawn with *colour* (``"state"`` or ``"metric"``).

    *styles* is the renderer's ``state -> (hatch, linestyle)`` for a nanowell without a value. A fused nanowell is
    drawn dashed in the fused colour whatever else it is, so it is counted there and nowhere else, as it is drawn.
    *why* (``array_compute.no_value_reasons``) names why a measured nanowell has no value; the key then has one "no
    value" entry per reason, since a fold change is undefined on a nanowell full of cells whenever its baseline had
    none, and the reader must be able to tell which nanowell is which (each writes its reason's tag inside itself).
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
            entries += [KeyEntry(label, Colors.plot_undefined_ink) for label in _no_value_entries(sites, bare, why or {})]
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


def draw_key(ax, entries: list[KeyEntry], *, linewidth: float, fontsize: float, ink: str) -> object | None:
    """The key as a row of ring swatches along the top edge of *ax*, right-aligned clear of the title; ``None``
    when there is nothing to key.

    Its words are written in *ink*, the figure's own (``renderer._inks``). The Array view's axes are an image
    panel, which the export-style pass leaves alone, so nothing recolours the key after this: left at matplotlib's
    default black it was black words on the screen theme's near-black ground."""
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
        text.set_color(ink)
        follows_theme(text)
    ax.add_artist(key)
    return key
