"""The 96 well-rank colours, and nothing else.

A selection's colour is *baked from the rank of its lowest well* and then
written into the ``color`` field of the ``sample_definitions`` block on disk.
Review bakes it when a person makes a selection; Datasets re-bakes it when
``merge_plates`` moves a condition to a different part of the plate. The two
panes therefore have to hold the same 96 values or the same well writes two
different colours into the same file, so this is shared subject matter rather
than screen chrome — which is why it sits here, below every GUI layer, instead
of in :mod:`ui.theme`.

:mod:`ui.theme` re-exports it under the same name, so every reader that has
always said ``theme.WELL_COLORS_TUPLE`` still does. This module imports
nothing.

Generated via a greedy max-min selection in OKLab space (min pairwise distance
≈ 0.09). The first colour (light blue) is positioned in the most-used slot —
the original first colour was a dark red that read as too heavy at a
single-well selection.

Three consumers reference it:

* :data:`ui.theme.Colors.trace` / :data:`ui.theme.CPub.trace` — what
  ``widgets/plot_card.plot_palette()`` feeds into matplotlib's
  ``axes.prop_cycle`` (screen and publication modes alike).
* ``review.domain.plate_layout.WELL_COLORS`` — a plain ``list()`` copy, read by
  every plot renderer that needs a per-well colour by rank.
* :func:`domain.selections_format.well_colors` — the palette ``rank_color``
  bakes from.

NOTE — the length must be **96** (one colour per 96-well plate position), not a
submultiple of it. Well colour is ``WELL_COLORS[well_rank(token) % len]`` and
ranks step by 12 (the column count) going *down* a plate column, so any length
that divides a multiple of 12 within one column (e.g. 48 = 4x12) makes the
colours visibly repeat down each column — rows E-H mirror A-D in "sets of
four". 96 distinct entries give every well its own colour and remove the wrap.
"""

from __future__ import annotations

WELL_COLORS_TUPLE: tuple = (
    "#6AB2FB", "#06F906", "#0606F9", "#DF2020",
    "#138613", "#D406F9", "#FAAC38", "#6E2277",
    "#6AFBE5", "#2E719E", "#EC79B8", "#FAFA38",
    "#774C22", "#131386", "#05C783", "#806AFB",
    "#B37D19", "#C7058D", "#5C05C7", "#2E9E9E",
    "#950437", "#FA7238", "#AAC705", "#224877",
    "#9C39C6", "#D4DD88", "#AA88DD", "#779E2E",
    "#F906BC", "#0553C7", "#068CF9", "#20D6DF",
    "#E56AFB", "#FA387C", "#DD9D88", "#777222",
    "#79EC7F", "#227762", "#6938FA", "#05C705",
    "#B34F19", "#D16167", "#C7A005", "#471386",
    "#B0F906", "#950495", "#0667F9", "#1929B3",
    # ── ranks 48–95 (rows E–H): perceptually spread to the same separation
    #    (min ΔE*ab ≈ 10.8) as the ranks 0–47 block above ──────────────────
    "#7F4660", "#7F0000", "#007F43", "#33337F",
    "#EAC081", "#7F0C51", "#70ADCC", "#EAE15D",
    "#EA965D", "#A55B9B", "#7ACC33", "#7F3333",
    "#70CCA1", "#5D7F46", "#EA3A52", "#A5875B",
    "#DC81EA", "#46747F", "#CC14CC", "#9BA55B",
    "#EACB00", "#8ECC70", "#5B60A5", "#A56A5B",
    "#EA7D00", "#EA5DC5", "#00EA6D", "#CCB351",
    "#CC337A", "#CC708E", "#BBEA5D", "#A5A510",
    "#6A42A5", "#6DEA00", "#00A579", "#EA795D",
    "#21A500", "#1482CC", "#7F3B00", "#00EABB",
    "#A5425C", "#6C467F", "#42A54F", "#A43AEA",
    "#81EAA4", "#3A46EA", "#17C0EA", "#EA5D83",
)
