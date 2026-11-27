"""The Statistics tab's results as table rows: one row per group, one row per comparison (design language row
D15 — a formatted table, not preformatted text).

Pure: the same values the text report is written from (``controller.run_stats``), turned into cells. The text
report stays beside the tables as the full record, so the cells say exactly what its lines say: the same
``p_phrase`` spelling of a p-value, the same significance stars and the same effect-size text.

**A value that could not be computed says why in its cell**, never a blank or a zero: a pair whose test raised
carries its error in the statistic's cell.
"""

from __future__ import annotations

import statistics as _pystats

from review.domain.significance import p_phrase, significance_stars
from review.features.stats._report import effect_size_text

#: Column headings of the two tables.
GROUP_COLUMNS = ("Group", "n", "Mean", "Median", "SD")
PAIR_COLUMNS = ("Comparison", "Statistic", "p", "Adjusted p", "", "Effect size")


def group_rows(group_vals) -> list[tuple[str, ...]]:
    """One row per non-empty group: name, n, mean, median, SD (``—`` for one value, where SD is undefined)."""
    rows = []
    for name, vals in group_vals:
        if not vals:
            continue
        sd = _pystats.stdev(vals) if len(vals) > 1 else None
        rows.append((str(name), str(len(vals)), f"{sum(vals) / len(vals):.4g}", f"{_pystats.median(vals):.4g}",
                     "—" if sd is None else f"{sd:.4g}"))
    return rows


def pair_rows(pair_results, adjusted: dict, show_adjusted: bool, effects=None) -> list[tuple[str, ...]]:
    """One row per comparison: "A vs B", the statistic, p, the BH-adjusted p (when there is a family), the stars
    of the p that governs, and the effect size."""
    rows = []
    for idx, (name_a, name_b, stat_name, stat_value, p, err) in enumerate(pair_results):
        label = f"{name_a} vs {name_b}"
        effect = effect_size_text(effects[idx]) if effects is not None and idx < len(effects) else ""
        if err is not None:
            rows.append((label, f"Error: {err}", "", "", "", effect))
            continue
        if p is None or stat_value is None:
            rows.append((label, "not computed", "", "", "", effect))
            continue
        ap = adjusted.get(idx) if show_adjusted else None
        stars = significance_stars(ap if ap is not None else p)
        rows.append((label, f"{stat_name} = {stat_value:.4f}", p_phrase(p),
                     p_phrase(ap, adjusted=True) if ap is not None else "", stars, effect))
    return rows
