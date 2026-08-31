"""The statistical tests the Statistics tab offers, as pure functions.

Qt-free, ctx-free, and driven entirely by lists of floats, so every rule here
is exercised by the fast lane rather than by pressing *Run test* and reading a
panel. The tab's controller resolves the samples and formats the report; what a
test *is* lives here.

**Every result says when it could not be computed, and why.** A confidence
interval that did not converge, an omnibus with one group, a paired test with
unmatched replicates, an ANOVA with an empty cell — each of those is *undefined*
and none of them is zero. Every dataclass below carries an ``undefined`` string
that is empty exactly when the numbers beside it are real, and the report prints
the reason rather than a blank or a plausible-looking zero. That is CLAUDE.md §7
applied to a panel whose whole output is numbers a reader will quote.

**Nothing here is random, even where the method is.** The bootstrap interval
seeds a generator from a fixed constant, because ``compute_pairwise`` promises
that a saved plot re-run reproduces the exact brackets it was saved with — an
interval that moved between two runs of the same data would break that promise
quietly, in the direction of looking like a real change in the data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

#: The bootstrap's generator seed. Fixed rather than drawn, so the same samples
#: always give the same interval — see the module docstring on why.
_BOOTSTRAP_SEED = 20240517

#: How many resamples the difference-of-means interval takes. Enough that the
#: percentile bounds are stable to the three significant figures the report
#: prints, and small enough that a fifteen-pair run stays interactive.
BOOTSTRAP_RESAMPLES = 4000


def _clean(values) -> np.ndarray:
    """*values* as a float array with every non-finite entry dropped.

    A NaN in a replicate list is a well whose statistic could not be computed,
    which is not a measurement of zero and must not be averaged as one.
    """
    arr = np.asarray(values if values is not None else (), dtype=float).ravel()
    if arr.size == 0:
        return arr
    return arr[np.isfinite(arr)]


# ── H1: effect size and an interval ─────────────────────────────────────────

@dataclass(frozen=True)
class EffectSize:
    """Hedges' *g* and a bootstrap interval on the difference of means.

    Two quantities rather than one because they answer different questions: *g*
    is the separation in pooled standard deviations, which is comparable across
    channels and plates, and the interval is in the data's own units, which is
    what a reader checks against the axis. With n=3 wells a p-value alone says
    very little, and these are the cheapest scientific upgrade available.
    """

    g: float | None = None
    diff: float | None = None
    lo: float | None = None
    hi: float | None = None
    #: The interval's coverage, as a percentage, so the label cannot claim 95%
    #: for an interval computed at another level.
    level: float = 95.0
    #: Why *g* alone is missing while the interval is real. Separate from
    #: :attr:`undefined`, which means *nothing* was computed — a result with a
    #: difference and an interval but no pooled SD is a partial answer, and
    #: collapsing the two would either hide the reason or hide the numbers.
    g_undefined: str = ""
    undefined: str = ""

    @property
    def ok(self) -> bool:
        """Whether the whole result computed. See :attr:`g_undefined` for the
        partial case, which is ``ok`` and still has something to say."""
        return not self.undefined


def hedges_g(a, b) -> float | None:
    """Hedges' *g* for two independent samples, or ``None``.

    Cohen's *d* on the pooled standard deviation, times the small-sample
    correction ``1 - 3/(4*df - 1)``. The correction is not cosmetic at the sizes
    this app sees: at n=3 per group the factor is 0.8, so the uncorrected *d*
    overstates the effect by 25%, and a per-well replicate count of three is
    the normal case here rather than the small-sample corner.

    ``None`` when either sample has fewer than two usable values, or when both
    samples are constant — a pooled standard deviation of zero makes the ratio
    undefined, and returning a large number for it would report the strongest
    possible effect for the one case that carries no information about spread.
    """
    xa, xb = _clean(a), _clean(b)
    if xa.size < 2 or xb.size < 2:
        return None
    na, nb = xa.size, xb.size
    df = na + nb - 2
    va, vb = xa.var(ddof=1), xb.var(ddof=1)
    pooled = math.sqrt(((na - 1) * va + (nb - 1) * vb) / df)
    if not math.isfinite(pooled) or pooled <= 0.0:
        return None
    d = (float(xa.mean()) - float(xb.mean())) / pooled
    correction = 1.0 - (3.0 / (4.0 * df - 1.0))
    g = d * correction
    return float(g) if math.isfinite(g) else None


def effect_size(a, b, *, resamples: int = BOOTSTRAP_RESAMPLES,
                level: float = 0.95) -> EffectSize:
    """*g* plus a percentile bootstrap interval on ``mean(a) - mean(b)``.

    A percentile bootstrap rather than a t interval because the replicate counts
    here are small and the per-well summaries are not reliably normal; it also
    needs no assumption this module would then have to state and nobody would
    check. The generator is seeded from a constant, so the interval is a
    function of the samples alone.

    The interval is on the **difference of means**, in the data's own units, and
    not on *g*. An interval on a standardised effect is the harder one to read
    against the plot beside it, and the difference is the quantity a reader can
    check by eye.
    """
    xa, xb = _clean(a), _clean(b)
    if xa.size < 2 or xb.size < 2:
        return EffectSize(undefined="fewer than 2 usable replicates in a group")
    g = hedges_g(xa, xb)
    diff = float(xa.mean() - xb.mean())
    if resamples < 2:
        return EffectSize(g=g, diff=diff,
                          undefined=f"an interval needs 2 or more resamples, "
                                    f"not {int(resamples)}")
    rng = np.random.default_rng(_BOOTSTRAP_SEED)
    draws_a = rng.choice(xa, size=(resamples, xa.size), replace=True).mean(axis=1)
    draws_b = rng.choice(xb, size=(resamples, xb.size), replace=True).mean(axis=1)
    diffs = draws_a - draws_b
    tail = (1.0 - float(level)) / 2.0
    lo, hi = np.quantile(diffs, [tail, 1.0 - tail])
    level_pct = float(level) * 100.0
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return EffectSize(g=g, diff=diff,
                          undefined="the bootstrap interval did not converge")
    # **A partial result is not an undefined one, and says which half is
    # missing.** Two constant groups have a real difference of means and a real
    # (zero-width) interval, and no pooled SD — so *g* alone is undefined. An
    # earlier version returned that in ``undefined`` beside real numbers, which
    # broke this module's own promise that the field is empty exactly when the
    # numbers beside it are real, and the report then printed the interval with
    # the reason dropped.
    return EffectSize(g=g, diff=diff, lo=float(lo), hi=float(hi),
                      level=level_pct,
                      g_undefined=("" if g is not None else
                                   "both groups are constant, so g has no pooled SD"))


# ── H2: the omnibus gate ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Omnibus:
    """One test asking whether *anything* differs, before the pairwise pass."""

    name: str = ""
    stat_name: str = ""
    stat: float | None = None
    p: float | None = None
    df_between: int | None = None
    df_within: int | None = None
    #: Groups left out for having fewer than two usable values. Named rather
    #: than counted away: an omnibus over three of four groups reads exactly
    #: like one over all four, and the df alone will not tell a reader which.
    dropped: tuple[str, ...] = ()
    undefined: str = ""

    @property
    def ok(self) -> bool:
        return not self.undefined


def omnibus(groups, *, parametric: bool = True) -> Omnibus:
    """One-way ANOVA or Kruskal-Wallis across every group at once.

    Reporting fifteen pairwise tests without first asking whether anything
    differs at all is the practice a Benjamini-Hochberg adjustment only
    partially compensates for. Stating the omnibus is more honest and costs one
    scipy call.

    It deliberately does **not** suppress the pairwise results when it comes
    back non-significant. Which comparisons a reader may look at is a policy
    this app should not impose; saying what the omnibus found, next to them, is.

    *groups* is ``[(name, [values]), ...]``. *parametric* picks the test to
    match the Test combo's own choice, so a run whose pairwise tests are
    rank-based is not gated by an ANOVA.
    """
    named = [(str(n), _clean(v)) for n, v in (groups or ())]
    usable = [s for _n, s in named if s.size >= 2]
    dropped = tuple(n for n, s in named if s.size < 2)
    if len(usable) < 3:
        return Omnibus(dropped=dropped,
                       undefined="an omnibus needs 3 or more groups")
    # **Only when every group is constant at the *same* value.** The first
    # version also refused when each group was internally constant at a
    # *different* value — three groups reading 1, 2 and 3 — which
    # ``kruskal`` answers perfectly well (H = 8.0, p = 0.018) and which
    # count-like statistics reach routinely, so the report printed "no variance
    # to partition" over data with a significant between-group effect.
    if _all_identical(usable):
        return Omnibus(undefined="every group holds the same constant value, "
                                 "so there is no variance to partition")
    import scipy.stats as _st

    try:
        if parametric:
            res = _st.f_oneway(*usable)
            name, stat_name = "One-way ANOVA", "F"
        else:
            res = _st.kruskal(*usable)
            name, stat_name = "Kruskal-Wallis", "H"
        stat, p = float(res.statistic), float(res.pvalue)
    except Exception as exc:
        return Omnibus(dropped=dropped, undefined=str(exc))
    if not (math.isfinite(stat) and math.isfinite(p)):
        return Omnibus(name=name, dropped=dropped,
                       undefined="the test returned no finite result")
    k = len(usable)
    n = int(sum(s.size for s in usable))
    return Omnibus(name=name, stat_name=stat_name, stat=stat, p=p,
                   df_between=k - 1, df_within=n - k, dropped=dropped)


def _all_identical(samples) -> bool:
    """Whether every sample holds the same single constant.

    ``f_oneway`` returns NaN for that input rather than raising, which would
    otherwise reach the report as a blank where a reason belongs.
    """
    first = samples[0]
    return all(np.allclose(s, first[0]) for s in samples) and float(first.std()) == 0.0


# ── H3: Dunnett against a named control ─────────────────────────────────────

@dataclass(frozen=True)
class ControlComparison:
    """One treatment against the control, in a Dunnett family."""

    name: str = ""
    stat: float | None = None
    p: float | None = None
    undefined: str = ""


@dataclass(frozen=True)
class DunnettResult:
    control: str = ""
    comparisons: tuple[ControlComparison, ...] = ()
    undefined: str = ""

    @property
    def ok(self) -> bool:
        return not self.undefined


def dunnett_vs_control(control_name: str, control_values,
                       treatments) -> DunnettResult:
    """Every treatment against one control, with Dunnett's own correction.

    This is the test that matches what the app already models. Comparing k
    treatments to a single control is both the common plate design and strictly
    more powerful than all-pairs with a Benjamini-Hochberg adjustment, because
    it makes k comparisons where all-pairs makes k(k+1)/2 and corrects for the
    family it actually ran.

    *treatments* is ``[(name, [values]), ...]``. The control is named separately
    rather than being picked out of that list by index, so a caller cannot
    silently compare the control to itself.
    """
    ctrl = _clean(control_values)
    if ctrl.size < 2:
        return DunnettResult(control=control_name,
                             undefined="the control has fewer than 2 usable replicates")
    named = [(str(n), _clean(v)) for n, v in (treatments or ())]
    usable = [(n, v) for n, v in named if v.size >= 2]
    if not usable:
        return DunnettResult(control=control_name,
                             undefined="no treatment group has 2 or more usable replicates")
    import scipy.stats as _st

    try:
        res = _st.dunnett(*[v for _n, v in usable], control=ctrl)
        stats = [float(s) for s in np.atleast_1d(res.statistic)]
        ps = [float(p) for p in np.atleast_1d(res.pvalue)]
    except Exception as exc:
        return DunnettResult(control=control_name, undefined=str(exc))
    by_name = {}
    for (name, _vals), stat, p in zip(usable, stats, ps):
        by_name[name] = (ControlComparison(name=name, stat=stat, p=p)
                         if math.isfinite(stat) and math.isfinite(p)
                         else ControlComparison(
                             name=name,
                             undefined="the test returned no finite result"))
    # **A treatment too short to test still gets a row.** It used to be dropped
    # from ``usable`` and never seen again — the guard meant to notice
    # (``if skipped and not out``) could not fire, because an empty ``usable``
    # has already returned above. So a group with one replicate vanished from
    # the table with no line and no reason, which is indistinguishable from a
    # group the user never defined.
    out = [by_name.get(name) or ControlComparison(
               name=name,
               undefined="fewer than 2 usable replicates in this group")
           for name, _v in named]
    return DunnettResult(control=control_name, comparisons=tuple(out))


# ── H4: paired and repeated-measures ────────────────────────────────────────

@dataclass(frozen=True)
class PairedResult:
    """A paired test, plus what it had to drop to pair at all."""

    name: str = ""
    stat_name: str = ""
    stat: float | None = None
    p: float | None = None
    n_pairs: int = 0
    unmatched: tuple[str, ...] = ()
    undefined: str = ""

    @property
    def ok(self) -> bool:
        return not self.undefined


def pair_by_key(a_by_key, b_by_key) -> tuple[list[float], list[float], list[str]]:
    """The values two groups share a key for, plus the keys neither could match.

    **Pairing is by key and never by list order.** Two replicate sets repeated
    across plates or days are a paired design, but which replicate in A goes
    with which in B is a fact about the data, not about the order two lists came
    out in. A paired test that zipped the lists would produce a number with a
    plausible face and no basis, which is the failure mode this whole module's
    ``undefined`` fields exist to prevent — so the unmatched keys come back and
    the report names them.
    """
    keys = [k for k in a_by_key if k in b_by_key]
    xa, xb, used = [], [], []
    for key in keys:
        va, vb = a_by_key[key], b_by_key[key]
        if va is None or vb is None:
            continue
        fa, fb = float(va), float(vb)
        if math.isfinite(fa) and math.isfinite(fb):
            xa.append(fa)
            xb.append(fb)
            used.append(key)
    unmatched = sorted(
        (set(a_by_key) | set(b_by_key)) - set(used), key=str)
    return xa, xb, [str(k) for k in unmatched]


def paired_test(a_by_key, b_by_key, *, parametric: bool = True) -> PairedResult:
    """Paired *t* or Wilcoxon signed-rank over the keys two groups share.

    Both inputs are ``{pairing key: value}``. The keys come from a sample
    variable — a *replicate* or *batch* axis the user declared — which is the
    correspondence the data model has no other way to express.
    """
    xa, xb, unmatched = pair_by_key(a_by_key or {}, b_by_key or {})
    if len(xa) < 2:
        return PairedResult(
            n_pairs=len(xa), unmatched=tuple(unmatched),
            undefined=("fewer than 2 replicates could be paired — "
                       "the two groups share too few values of the pairing variable"))
    import scipy.stats as _st

    try:
        if parametric:
            res = _st.ttest_rel(xa, xb)
            name, stat_name = "Paired t-test", "t"
        else:
            res = _st.wilcoxon(xa, xb)
            name, stat_name = "Wilcoxon signed-rank", "W"
        stat, p = float(res.statistic), float(res.pvalue)
    except Exception as exc:
        return PairedResult(n_pairs=len(xa), unmatched=tuple(unmatched),
                            undefined=str(exc))
    if not (math.isfinite(stat) and math.isfinite(p)):
        # Say *which* degenerate case. The commonest by far is a perfectly
        # constant shift — every pair differing by the same amount, so the
        # differences have no variance and the ratio is undefined — and "the
        # test returned no finite result" tells a user nothing they can act on.
        diffs = [a - b for a, b in zip(xa, xb)]
        spread = max(diffs) - min(diffs) if diffs else 0.0
        reason = ("every pair differs by the same amount, so there is no "
                  "variation between pairs for the test to weigh"
                  if spread == 0.0 else "the test returned no finite result")
        return PairedResult(name=name, n_pairs=len(xa),
                            unmatched=tuple(unmatched), undefined=reason)
    return PairedResult(name=name, stat_name=stat_name, stat=stat, p=p,
                        n_pairs=len(xa), unmatched=tuple(unmatched))


# ── H5: two-way ANOVA ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class AnovaTerm:
    name: str = ""
    ss: float | None = None
    df: int | None = None
    f: float | None = None
    p: float | None = None
    undefined: str = ""


@dataclass(frozen=True)
class TwoWayResult:
    factor_a: str = ""
    factor_b: str = ""
    terms: tuple[AnovaTerm, ...] = ()
    residual_ss: float | None = None
    residual_df: int | None = None
    empty_cells: tuple[str, ...] = ()
    undefined: str = ""

    @property
    def ok(self) -> bool:
        return not self.undefined


def _design(labels_a, labels_b, *, with_interaction: bool):
    """Dummy-coded, drop-first design matrix for the requested terms."""
    levels_a = sorted({str(x) for x in labels_a})
    levels_b = sorted({str(x) for x in labels_b})
    n = len(labels_a)
    cols = [np.ones(n)]
    for lvl in levels_a[1:]:
        cols.append(np.array([1.0 if str(x) == lvl else 0.0 for x in labels_a]))
    for lvl in levels_b[1:]:
        cols.append(np.array([1.0 if str(x) == lvl else 0.0 for x in labels_b]))
    if with_interaction:
        for la in levels_a[1:]:
            for lb in levels_b[1:]:
                cols.append(np.array([
                    1.0 if (str(x) == la and str(y) == lb) else 0.0
                    for x, y in zip(labels_a, labels_b)]))
    return np.column_stack(cols), levels_a, levels_b


def _sse(y: np.ndarray, x: np.ndarray) -> tuple[float, int]:
    """Residual sum of squares and the design's rank, by least squares."""
    beta, _res, rank, _sv = np.linalg.lstsq(x, y, rcond=None)
    resid = y - x @ beta
    return float(resid @ resid), int(rank)


def two_way_anova(rows, *, factor_a: str = "A", factor_b: str = "B") -> TwoWayResult:
    """Two factors and their interaction, by Type II sums of squares.

    *rows* is ``[(level_a, level_b, value), ...]`` — one entry per replicate.

    **Type II, not Type I.** Plate data is rarely balanced: a well drops out, a
    condition is imaged once more than another, and a sequential (Type I)
    decomposition then gives each factor a different answer depending on which
    was entered first. Type II asks for each main effect what it adds *given the
    other main effect*, which is the same number in either order and is what
    ``car::Anova(type=2)`` and ``statsmodels.anova_lm(typ=2)`` report. On a
    balanced design all three types agree, and the tests pin that agreement
    against the closed-form balanced formulas — an independent derivation rather
    than this function checked against itself.

    An **empty cell** — a combination of the two factors that no replicate
    holds — makes the interaction inestimable. It is named and the interaction
    term is refused rather than being computed from a rank-deficient design,
    which would report a number nothing in the data supports.
    """
    cleaned = [(str(a), str(b), float(v)) for a, b, v in (rows or ())
               if v is not None and math.isfinite(float(v))]
    if len(cleaned) < 4:
        return TwoWayResult(factor_a=factor_a, factor_b=factor_b,
                            undefined="fewer than 4 usable replicates")
    la = [r[0] for r in cleaned]
    lb = [r[1] for r in cleaned]
    y = np.array([r[2] for r in cleaned], dtype=float)
    levels_a, levels_b = sorted(set(la)), sorted(set(lb))
    if len(levels_a) < 2 or len(levels_b) < 2:
        return TwoWayResult(
            factor_a=factor_a, factor_b=factor_b,
            undefined=("a two-way ANOVA needs 2 or more values of each variable "
                       f"— {factor_a} has {len(levels_a)}, {factor_b} has {len(levels_b)}"))
    seen = {(a, b) for a, b, _v in cleaned}
    empty = [f"{a} x {b}" for a in levels_a for b in levels_b
             if (a, b) not in seen]

    x_full, _la, _lb = _design(la, lb, with_interaction=not empty)
    x_add, _la, _lb = _design(la, lb, with_interaction=False)
    x_a_only = np.column_stack(
        [np.ones(len(y))] + [np.array([1.0 if a == lvl else 0.0 for a in la])
                             for lvl in levels_a[1:]])
    x_b_only = np.column_stack(
        [np.ones(len(y))] + [np.array([1.0 if b == lvl else 0.0 for b in lb])
                             for lvl in levels_b[1:]])

    sse_full, rank_full = _sse(y, x_full)
    sse_add, rank_add = _sse(y, x_add)
    sse_a, _r = _sse(y, x_a_only)
    sse_b, _r = _sse(y, x_b_only)

    # **Confounded factors produce no main effects, and must not produce
    # zeros.** With an empty cell the additive design can be rank-deficient —
    # in the limit each level of A appears with exactly one level of B, so the
    # two variables are the same partition of the plate under two names. Least
    # squares still solves it, ``sse_b - sse_add`` collapses to 0, and the
    # table reports ``F = 0, p = 1`` for both: *neither factor matters*, on data
    # where the levels differ by nine residual standard deviations. That is a
    # number the data does not support, printed indistinguishably from a real
    # null — this module's own cardinal sin, and it is the ordinary case rather
    # than an exotic one (each cell line given one dose, each dose run on one
    # day). The interaction refusal above does not cover it: it names an
    # interaction problem and says nothing about the main effects.
    expected_add_rank = 1 + (len(levels_a) - 1) + (len(levels_b) - 1)
    if rank_add < expected_add_rank:
        return TwoWayResult(
            factor_a=factor_a, factor_b=factor_b, empty_cells=tuple(empty),
            undefined=(f"{factor_a} and {factor_b} are confounded — no set of "
                       "replicates tells them apart, so neither main effect is "
                       "estimable. Vary one while holding the other."))

    df_resid = len(y) - rank_full
    if df_resid <= 0:
        return TwoWayResult(
            factor_a=factor_a, factor_b=factor_b, empty_cells=tuple(empty),
            undefined=("no residual degrees of freedom — every combination has "
                       "a single replicate, so there is nothing to test against"))
    ms_resid = sse_full / df_resid
    if not math.isfinite(ms_resid) or ms_resid <= 0.0:
        return TwoWayResult(
            factor_a=factor_a, factor_b=factor_b, empty_cells=tuple(empty),
            undefined="the residual variance is zero, so no F ratio is defined")

    import scipy.stats as _st

    def _term(name: str, ss: float, df: int) -> AnovaTerm:
        if df <= 0:
            return AnovaTerm(name=name, ss=float(ss), df=df,
                             undefined="no degrees of freedom")
        f = (ss / df) / ms_resid
        p = float(_st.f.sf(f, df, df_resid))
        return AnovaTerm(name=name, ss=float(ss), df=df, f=float(f), p=p)

    terms = [
        _term(factor_a, sse_b - sse_add, len(levels_a) - 1),
        _term(factor_b, sse_a - sse_add, len(levels_b) - 1),
    ]
    if empty:
        terms.append(AnovaTerm(
            name=f"{factor_a} x {factor_b}",
            undefined=("not estimable — no replicate holds "
                       + ", ".join(empty[:4])
                       + (f" and {len(empty) - 4} more" if len(empty) > 4 else ""))))
    else:
        terms.append(_term(f"{factor_a} x {factor_b}", sse_add - sse_full,
                           (len(levels_a) - 1) * (len(levels_b) - 1)))
    return TwoWayResult(factor_a=factor_a, factor_b=factor_b,
                        terms=tuple(terms), residual_ss=sse_full,
                        residual_df=df_resid, empty_cells=tuple(empty))
