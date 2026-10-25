# Nanowells page: axes, sizing and crop controls — remediation tracker

A live tracker (CLAUDE.md §6.1), deleted by the PR that closes its last row. The source is a rendering of
every Nanowells view on a realistic synthetic plate (six wells, five timepoints, 127 nanowells per well) at a
1500×900 and a 1100×720 window, reviewed by a UX reviewer, plus a user report that the **Back to the plot** and
**Clear selection** buttons misbehave after **Enlarge crops**, with a request for a pop-out button above the
crops in their place.

States: `open`, `done` (with the test that pins it), `declined` (with the reason).

| # | Item | State | Evidence / reason |
|---|------|-------|-------------------|
| B1 | Enlarge crops / Back to the plot / Clear selection: the in-page crop mode replaces the view and ignores the view switch; replace it with a **Pop out** button above the crop strip that opens the crops large in their own window | done | `tests/test_nanowell_crop_view.py` (the view switch with the crops popped out draws the new view; the same sequence against the base drew the crop grid instead of Trajectories) |
| R1 | The Properties pass applies 18 pt ticks, labels and titles (sized for one large plot) to every panel of a small-multiples figure; scale the page's typography by panel size | open | |
| R2 | Axes keep matplotlib's white face on the dark screen card: the light occupancy shades and the light legend text vanish | open | |
| R3 | Distribution over time: "0 h · t00" tick labels collide; hours only, one shared "Time (h)" label | open | |
| R4 | Trajectories: tick labels at true hours collide ("t00t06t12"); hour ticks and a "Time (h)" label | open | |
| R5 | Trajectories: occupancy colours have no key | open | |
| R6 | Position: the two panels' x positions do not line up; the legend is unreadable | open | |
| R7 | Fold change axis drawn with an offset ("1e−6+1") | open | |
| R8 | Long y labels clipped | open | |
| R9 | Extremes: row labels and per-crop values overprint the crops | open | |
| R10 | Count axes show fractional ticks (2.5, 7.5) | open | |
| R11 | Site detail: x tick labels repeated on every row, y labels clipped, crop titles run together | open | |
| R12 | Array map colour bar typography out of scale with the rest | open | |
| R13 | Six wells drawn in a 2×4 grid leave two cells empty | open | |
