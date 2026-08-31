"""How a statistical result is written into the Statistics tab's report.

Pure text assembly: every function here takes a dataclass from
``domain/stats_tests`` and returns lines. No Qt, no ctx, no app state, so the
wording of every block is fast-lane work — which matters more here than in most
places, because these lines are the whole output of the tab and a reader quotes
them into a paper.

**A block that could not be computed prints its reason, in the same place the
number would have gone.** Not a blank, not a dash, and never a zero: an omnibus
that declined and an omnibus that found nothing are different facts, and the
panel is the only place either of them can be said.

The p-values go through ``domain/significance`` like every other p in the app,
so a number here and a bracket on the Bar plot beside it cannot be spelled two
ways.
"""

from __future__ import annotations

from well_viewer.domain.significance import p_phrase, significance_stars


def _num(value, spec: str = ".4g") -> str:
    """A number, or ``—`` when there isn't one.

    The em dash is only ever reached for a field of a result that already said
    why it is undefined elsewhere in its own block; it is not a substitute for
    that reason.
    """
    return "—" if value is None else format(float(value), spec)


def effect_size_text(effect) -> str:
    """``g = -1.83   Δ = -3.10 [95% CI -4.02, -2.21]`` — or why not.

    Both quantities on one line beside the p-value, because the p is the part a
    reader over-weights and these are the two that say how big the difference
    actually is. With n=3 wells the p-value alone says very little.
    """
    if effect is None:
        return ""
    if not effect.ok:
        return f"effect size: {effect.undefined}"
    parts = []
    if effect.g is not None:
        parts.append(f"g = {_num(effect.g, '.3g')}")
    if effect.diff is not None:
        if effect.lo is None or effect.hi is None:
            parts.append(f"Δ = {_num(effect.diff, '.3g')} [no interval]")
        else:
            # The level comes from the result, not from this string. Hard-coded
            # it read `95% CI` over an interval computed at whatever level the
            # caller asked for.
            parts.append(
                f"Δ = {_num(effect.diff, '.3g')} "
                f"[{effect.level:g}% CI {_num(effect.lo, '.3g')}, "
                f"{_num(effect.hi, '.3g')}]")
    # **A partial result says which half is missing.** Two constant groups have
    # a real difference and a real (zero-width) interval and no pooled SD, so
    # *g* alone is undefined — printing the interval and dropping the reason
    # leaves a reader with a zero-width CI, no g, and no explanation for either.
    if effect.g_undefined:
        parts.append(f"(no g: {effect.g_undefined})")
    return "   ".join(parts)


def omnibus_lines(result) -> list[str]:
    """The omnibus block, printed *above* the pairwise results.

    It never suppresses them. Which comparisons a reader may look at is a
    policy this app should not impose — but reporting fifteen pairwise tests
    without first saying whether anything differs at all is the practice a
    Benjamini-Hochberg adjustment only partially compensates for, so the answer
    goes at the top where it is read first.
    """
    if result is None:
        return []
    if not result.ok:
        # **Silent for the two-group case, which is most runs.** "an omnibus
        # needs 3 or more groups" over every two-group comparison — and over
        # every KS run, which is two groups by definition — is a refusal of
        # something the user did not ask for, printed on the commonest path.
        # Every other reason is a real shortfall and is said.
        if "3 or more groups" in result.undefined:
            return []
        return [f"Omnibus:    not run — {result.undefined}", ""]
    line = (f"Omnibus:    {result.name}   "
            f"{result.stat_name}({result.df_between}, {result.df_within}) = "
            f"{_num(result.stat, '.4g')}   {p_phrase(result.p)}   "
            f"{significance_stars(result.p)}")
    lines = [line]
    if result.p is not None and result.p >= 0.05:
        lines.append(
            "            No overall difference at the 5% level. The pairwise "
            "results below are still shown, and still adjusted.")
    if result.dropped:
        # Named, not counted away: an omnibus over three of four groups reads
        # exactly like one over all four, and the df will not tell a reader
        # which groups it actually weighed.
        lines.append(f"            Left out ({len(result.dropped)} group(s) "
                     f"with fewer than 2 replicates): "
                     f"{', '.join(result.dropped)}.")
    lines.append("")
    return lines


def dunnett_lines(result) -> list[str]:
    """Every treatment against the named control, with Dunnett's correction."""
    if result is None:
        return []
    if not result.ok:
        return [f"Dunnett vs '{result.control}': not run — {result.undefined}", ""]
    lines = [
        f"Dunnett's test — every group against control '{result.control}'.",
        "Adjusted for the k comparisons actually made, which is a smaller "
        "family than all-pairs and so a more powerful test.",
        "",
    ]
    for comp in result.comparisons:
        lines.append(f"── {comp.name}  vs  {result.control} ──")
        if comp.undefined:
            lines.append(f"  Not computed: {comp.undefined}")
        else:
            lines.append(f"  t = {_num(comp.stat, '.4f')}   "
                         f"{p_phrase(comp.p)}   {significance_stars(comp.p)}")
        lines.append("")
    return lines


def paired_lines(name_a: str, name_b: str, result) -> list[str]:
    """One paired comparison, and what it could not pair.

    The unmatched replicates are **named**, not counted away. A paired test run
    on three of five replicates looks exactly like one run on all five, and the
    reader has nothing on screen to tell them apart.
    """
    if result is None:
        return []
    lines = [f"── {name_a}  vs  {name_b} ──"]
    if not result.ok:
        # **The pair count goes on the declined line too.** "no variation
        # between pairs" and "nothing could be paired" are different problems
        # with different fixes, and the count is what separates them — a
        # declined test that did not say how many pairs it had left the reader
        # unable to tell whether the pairing worked at all.
        lines.append(f"  Not computed (n = {result.n_pairs} pairs): "
                     f"{result.undefined}")
    else:
        lines.append(f"  {result.stat_name} = {_num(result.stat, '.4f')}   "
                     f"{p_phrase(result.p)}   {significance_stars(result.p)}   "
                     f"(n = {result.n_pairs} pairs)")
    if result.unmatched:
        shown = ", ".join(result.unmatched[:6])
        more = (f" and {len(result.unmatched) - 6} more"
                if len(result.unmatched) > 6 else "")
        lines.append(f"  Unpaired: {len(result.unmatched)} replicate(s) "
                     f"present in only one group — {shown}{more}.")
    lines.append("")
    return lines


def two_way_lines(result) -> list[str]:
    """The two-way table, one row per term, with its residual line."""
    if result is None:
        return []
    if not result.ok:
        return [f"Two-way ANOVA: not run — {result.undefined}", ""]
    lines = [
        f"Two-way ANOVA — {result.factor_a} x {result.factor_b}, "
        "Type II sums of squares.",
        "",
        f"{'Term':<28}{'SS':>12}{'df':>5}{'F':>12}   p",
    ]
    for term in result.terms:
        if term.undefined:
            lines.append(f"{term.name:<28}{'—':>12}{'—':>5}{'—':>12}   "
                         f"not estimable")
            lines.append(f"{'':<28}{term.undefined}")
            continue
        lines.append(
            f"{term.name:<28}{_num(term.ss):>12}{term.df:>5}{_num(term.f):>12}"
            f"   {p_phrase(term.p)}   {significance_stars(term.p)}")
    lines.append(f"{'Residual':<28}{_num(result.residual_ss):>12}"
                 f"{result.residual_df:>5}")
    lines.append("")
    return lines
