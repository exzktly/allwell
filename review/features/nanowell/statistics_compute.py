"""The Nanowells page's **Statistics** view, as data: which samples are compared, the tests run on them, and every
result with the reason it is missing when it could not be computed.

Modelled on the Statistics tab (``review/features/stats``) and built from the same pure tests it uses
(``review/domain/stats_tests``: the omnibus, Dunnett, the effect size and its interval; ``review/domain/significance``
for every p-value's spelling and stars), so a comparison here and one on the Statistics tab are worded alike and
cannot disagree about whether a result was significant.

**The samples are the Distribution view's own numbers.** :func:`samples_from_model` reads the
``distribution_compute.DistributionModel`` drawn *Along x: Wells* — the metric, the timepoint, the baseline, the
*Subset*, the fixed cohort, the moving average, the excluded wells and every exclusion by reason already applied —
so the Statistics view tests exactly the dots the Distribution view draws, never a second reading of the tables that
could drift from it.

**Two units, chosen by the user and named on the figure** (:data:`UNITS`):

* ``nanowells`` — each **well** is a sample and its nanowells are the values. This answers "do these wells'
  distributions differ?", which is a question about the wells themselves. It is *not* evidence that the conditions
  behind them differ: the nanowells of one well share its medium, seeding and handling, so they are not independent
  replicates of a condition (``summary_compute``'s rule). The figure says so every time this unit is drawn.
* ``wells`` — each **group** (a Sample Definitions replicate set, or one well standing alone) is a sample and its
  wells' medians are the values, one per well: the comparison of conditions, with wells as the replicate unit. A
  group needs two wells with a value to be tested.

**Which comparisons are made.** With no control chosen every pair is compared; with one (the band's *vs*), each
other sample against it — fewer comparisons, and the family a plate design usually means. Dunnett always compares
against the control and corrects for its own family; the other tests are corrected across their family with
Benjamini-Hochberg, stated as ``p_BH`` beside the raw p. More comparisons than :data:`MAX_COMPARISONS` are refused
with a sentence saying how to narrow them, rather than computed into a table nobody can read.

**Nothing undefined is reported as a number** (CLAUDE.md §7). A sample with fewer than two values, two constant
samples, a test scipy cannot answer — each carries its reason in ``undefined`` and is printed as that reason.

Qt-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from review.domain.stats_tests import (
    BOOTSTRAP_RESAMPLES, EffectSize, Omnibus, dunnett_vs_control, effect_size, omnibus,
)

NANOWELLS, WELLS = "nanowells", "wells"
#: What one value of a sample is: a nanowell of one well, or the median of one well of a group.
UNITS = (NANOWELLS, WELLS)
UNIT_LABELS = {NANOWELLS: "Wells (nanowells as values)", WELLS: "Groups (well medians as values)"}

#: Every test the view offers, in the order its *Test* control lists them. ``kind`` is what :func:`compare` branches
#: on; ``parametric`` picks the matching omnibus, so a rank-based run is not gated by an ANOVA.
TESTS: tuple[dict, ...] = (
    {"key": "welch", "label": "t-test (Welch, unequal var)", "kind": "pairwise", "parametric": True, "stat": "t"},
    {"key": "student", "label": "t-test (Student, pooled var)", "kind": "pairwise", "parametric": True, "stat": "t"},
    {"key": "mannwhitney", "label": "Mann-Whitney U", "kind": "pairwise", "parametric": False, "stat": "U"},
    {"key": "ks", "label": "Kolmogorov-Smirnov (distributions)", "kind": "pairwise", "parametric": False,
     "stat": "D"},
    {"key": "dunnett", "label": "Dunnett (vs control)", "kind": "dunnett", "parametric": True, "stat": "t"},
)
TEST_KEYS = tuple(t["key"] for t in TESTS)

#: The most comparisons one figure carries. Six samples make fifteen pairs; a sixteenth row no longer fits a
#: legible table, and a family that large is better narrowed (a control, fewer wells) than corrected into silence.
MAX_COMPARISONS = 15
#: Values (both samples together) times resamples the effect-size bootstrap may draw for one comparison. A well holds
#: hundreds of nanowells, and 4,000 resamples of two such samples on every redraw held the page for seconds; scaled
#: down for large samples, where the interval is already narrow and stable, and never below
#: :data:`MIN_RESAMPLES`. Deterministic either way: the resample count is a function of the sample sizes alone.
BOOTSTRAP_BUDGET = 2_000_000
MIN_RESAMPLES = 500
#: Two samples whose means differ by less than this, relative to their scale, are constant at the same value.
_SAME = 1e-12


def test_spec(key: str) -> dict:
    """The roster entry for *key*; an unknown key (a block saved by another version) falls back to Welch."""
    for entry in TESTS:
        if entry["key"] == key:
            return entry
    return TESTS[0]


@dataclass(frozen=True)
class Sample:
    """One sample: its name (a well, or a group), the wells it is drawn from and its values.

    ``n_roster`` and ``excluded`` (reason label -> count) account for every nanowell behind it: the values are what
    is left of the roster once the exclusions are taken out. ``without_value`` names the member wells that gave no
    value (on the ``wells`` unit: a well with no nanowell measured at the timepoint).
    """
    name: str
    wells: tuple
    values: np.ndarray
    n_roster: int = 0
    excluded: dict = field(default_factory=dict)
    without_value: tuple = ()

    @property
    def n(self) -> int:
        return int(self.values.size)

    def mean(self) -> float:
        return float(self.values.mean()) if self.n else math.nan

    def sd(self) -> float:
        return float(self.values.std(ddof=1)) if self.n >= 2 else math.nan

    def median(self) -> float:
        return float(np.median(self.values)) if self.n else math.nan

    def quartiles(self) -> tuple[float, float]:
        if not self.n:
            return math.nan, math.nan
        q1, q3 = np.quantile(self.values, [0.25, 0.75])
        return float(q1), float(q3)

    def ci95(self) -> tuple[float, float]:
        """A 95% Student-t interval on the mean, or NaNs with fewer than two values."""
        if self.n < 2:
            return math.nan, math.nan
        from scipy import stats

        half = float(stats.t.ppf(0.975, self.n - 1)) * self.sd() / math.sqrt(self.n)
        return self.mean() - half, self.mean() + half


@dataclass(frozen=True)
class Comparison:
    """One sample against another: the statistic, the raw p, the family-corrected p and the effect size.

    ``p_adj`` is Dunnett's own corrected p on a Dunnett run, Benjamini-Hochberg's otherwise; ``adjusted`` says
    whether it is a correction at all, since a family of one comparison has nothing to correct and its ``p_adj``
    is the raw p. ``undefined`` is empty exactly when the numbers are real.
    """
    a: str
    b: str
    stat_name: str = ""
    stat: float | None = None
    p: float | None = None
    p_adj: float | None = None
    adjusted: bool = False
    effect: EffectSize | None = None
    undefined: str = ""

    @property
    def ok(self) -> bool:
        return not self.undefined

    @property
    def p_used(self) -> float | None:
        """The p a reader should quote: the corrected one when there was a family to correct for."""
        return self.p_adj if self.adjusted else self.p


@dataclass
class StatisticsResult:
    """Everything the Statistics view draws. ``undefined`` set means no test was run, and says why."""
    unit: str
    test: dict
    label: str                                         # the metric, as the page labels it
    timepoint: str                                     # "" for a metric with one value per nanowell
    samples: list
    control: str = ""                                  # the sample every other is compared against ("" = all pairs)
    omnibus: Omnibus | None = None
    comparisons: list = field(default_factory=list)
    undefined: str = ""
    notes: list = field(default_factory=list)

    @property
    def family(self) -> str:
        return f"each against {self.control}" if self.control else "every pair"

    @property
    def correction(self) -> str:
        """How ``p_adj`` was corrected, as the figure names it; ``""`` when no p printed was corrected (a family of
        one, or one with a single computed comparison)."""
        if not any(c.adjusted for c in self.comparisons):
            return ""
        return "Dunnett's correction" if self.test["kind"] == "dunnett" else "Benjamini-Hochberg"


# ── the samples ──────────────────────────────────────────────────────────────
def samples_from_model(model, groups: dict, unit: str) -> list[Sample]:
    """The samples of *unit* from a Distribution model drawn *Along x: Wells* (one panel, a swarm per well).

    ``nanowells``: one sample per well with a swarm, in the groups' order, its values the swarm's — every nanowell
    with a value, the hidden dots included (hiding is a drawing matter). ``wells``: one sample per group, its values
    its wells' medians, a well whose swarm has no value named in ``without_value``.
    """
    swarms = {s.well: s for s in model.swarms()}
    if unit == NANOWELLS:
        out = []
        for members in groups.values():
            for well in members:
                s = swarms.get(well)
                if s is not None:
                    out.append(Sample(well, (well,), np.asarray(s.values, dtype=float), s.n_roster, dict(s.excluded)))
        return out
    out = []
    for name, members in groups.items():
        medians, used, without, roster, excluded = [], [], [], 0, {}
        for well in members:
            s = swarms.get(well)
            if s is None or not len(s.values):
                without.append(well)
            else:
                medians.append(float(np.median(s.values)))
                used.append(well)
            if s is not None:
                roster += s.n_roster
                for reason, n in s.excluded.items():
                    excluded[reason] = excluded.get(reason, 0) + n
        out.append(Sample(name, tuple(used), np.asarray(medians, dtype=float), roster, excluded, tuple(without)))
    return out


# ── the tests ────────────────────────────────────────────────────────────────
def bh_adjust(ps) -> tuple[list, bool]:
    """Benjamini-Hochberg adjusted p-values for *ps* (``None`` kept as ``None``), and whether any adjustment was made.

    A family with fewer than two real p-values has nothing to correct: its p-values come back as they went in, and
    the flag says so, so a figure never calls a raw p ``p_BH``."""
    valid = [i for i, p in enumerate(ps) if p is not None]
    out = list(ps)
    if len(valid) < 2:
        return out, False
    m = len(valid)
    ranked = sorted(valid, key=lambda i: ps[i])
    running = 1.0
    for rank in range(m, 0, -1):
        i = ranked[rank - 1]
        running = min(running, ps[i] * m / rank)
        out[i] = min(1.0, running)
    return out, True


def _resamples(na: int, nb: int) -> int:
    return int(max(MIN_RESAMPLES, min(BOOTSTRAP_RESAMPLES, BOOTSTRAP_BUDGET // max(na + nb, 1))))


def _constant_alike(a: np.ndarray, b: np.ndarray) -> bool:
    scale = max(1.0, float(np.abs(np.concatenate([a, b])).max()))
    return float(np.ptp(np.concatenate([a, b]))) <= _SAME * scale


def pair_test(a: Sample, b: Sample, test: dict) -> Comparison:
    """*a* against *b* by *test* (a pairwise roster entry): the statistic and raw p, or why there is none."""
    stat_name = test["stat"]
    if a.n < 2 or b.n < 2:
        short = ", ".join(s.name for s in (a, b) if s.n < 2)
        return Comparison(a.name, b.name, stat_name, undefined=f"fewer than 2 values in {short}")
    if _constant_alike(a.values, b.values):
        return Comparison(a.name, b.name, stat_name,
                          undefined="both samples hold the same constant value, so there is nothing to test")
    import scipy.stats as st

    try:
        if test["key"] in ("welch", "student"):
            res = st.ttest_ind(a.values, b.values, equal_var=test["key"] == "student")
        elif test["key"] == "mannwhitney":
            res = st.mannwhitneyu(a.values, b.values, alternative="two-sided")
        elif test["key"] == "ks":
            res = st.ks_2samp(a.values, b.values)
        else:
            raise ValueError(f"{test['label']} is not a pairwise test")
        stat, p = float(res.statistic), float(res.pvalue)
    except Exception as exc:                            # scipy's own words are the reason
        return Comparison(a.name, b.name, stat_name, undefined=str(exc))
    if not (math.isfinite(stat) and math.isfinite(p)):
        return Comparison(a.name, b.name, stat_name, undefined="the test returned no finite result")
    return Comparison(a.name, b.name, stat_name, stat, p)


def _effect(a: Sample, b: Sample) -> EffectSize:
    return effect_size(a.values, b.values, resamples=_resamples(a.n, b.n))


def _pairs(samples: list, control: str) -> list[tuple]:
    if control:
        ref = next(s for s in samples if s.name == control)
        return [(s, ref) for s in samples if s.name != control]
    return [(samples[i], samples[j]) for i in range(len(samples)) for j in range(i + 1, len(samples))]


def compare(samples: list, test_key: str, *, control: str = "", unit: str = NANOWELLS, label: str = "",
            timepoint: str = "") -> StatisticsResult:
    """Run *test_key* over *samples*: the omnibus (three or more samples), then the comparisons of the family —
    every pair, or each against *control* when one is named (Dunnett always needs one)."""
    test = test_spec(test_key)
    control = control if any(s.name == control for s in samples) else ""
    result = StatisticsResult(unit, test, label, timepoint, list(samples), control=control)
    if len(samples) < 2:
        what = "wells" if unit == NANOWELLS else "groups"
        result.undefined = (f"A test compares two or more {what}; the figure draws {len(samples)}. "
                            + ("Select more wells." if unit == NANOWELLS else
                               "Select more wells, or define replicate sets in Sample Definitions."))
        return result
    if test["kind"] == "dunnett" and not control:
        result.undefined = "Dunnett compares each sample against a control: choose one under vs."
        return result
    pairs = _pairs(samples, control)
    if len(pairs) > MAX_COMPARISONS:
        result.undefined = (f"{len(samples)} samples make {len(pairs)} comparisons, more than the "
                            f"{MAX_COMPARISONS} one figure can carry. "
                            + ("Exclude wells, or select fewer." if control else
                               "Choose a control under vs to compare each sample against it, or exclude wells."))
        return result
    try:
        import scipy.stats  # noqa: F401  (availability probe)
    except ImportError:
        result.undefined = "The statistical tests need scipy, which this installation does not have."
        return result
    named = [(s.name, s.values) for s in samples]
    result.omnibus = omnibus(named, parametric=test["parametric"]) if len(samples) >= 3 else None
    if test["kind"] == "dunnett":
        result.comparisons = _dunnett(samples, control)
    else:
        raw = [pair_test(a, b, test) for a, b in pairs]
        adjusted, applied = bh_adjust([c.p for c in raw])
        result.comparisons = [
            Comparison(c.a, c.b, c.stat_name, c.stat, c.p, adj, applied and c.ok, _effect(a, b) if c.ok else None,
                       c.undefined)
            for c, adj, (a, b) in zip(raw, adjusted, pairs)]
    return result


def _dunnett(samples: list, control: str) -> list[Comparison]:
    ref = next(s for s in samples if s.name == control)
    others = [s for s in samples if s.name != control]
    family = dunnett_vs_control(control, ref.values, [(s.name, s.values) for s in others])
    if not family.ok:
        return [Comparison(s.name, control, "t", undefined=family.undefined) for s in others]
    by_name = {c.name: c for c in family.comparisons}
    out = []
    for s in others:
        c = by_name.get(s.name)
        if c is None or c.undefined:
            out.append(Comparison(s.name, control, "t", undefined=(c.undefined if c is not None else
                                                                    "not compared")))
            continue
        # Dunnett's p is already corrected for its family; it has no raw p of its own to print beside it.
        out.append(Comparison(s.name, control, "t", c.stat, c.p, c.p, len(others) > 1, _effect(s, ref)))
    return out


# ── the report ───────────────────────────────────────────────────────────────
#: The gid each kind of report line carries on the figure, so a test reads the figure back (CLAUDE.md §7.4 #1).
GID_HEADER, GID_OMNIBUS, GID_SAMPLE, GID_PAIR, GID_NOTE = (
    "nw-stats-header", "nw-stats-omnibus", "nw-stats-sample", "nw-stats-pair", "nw-stats-note")


def _num(value, spec: str = ".4g") -> str:
    """A number, or ``—`` where the row has none (its reason is stated in the same row or beside it)."""
    return "—" if value is None or not math.isfinite(float(value)) else format(float(value), spec)


def unit_sentence(unit: str) -> str:
    """What one value is, and what the comparison can and cannot claim — on the figure whenever it is drawn."""
    if unit == WELLS:
        return ("Values: one median per well (n = wells); wells are the replicate unit, so this compares the "
                "conditions behind the groups.")
    return ("Values: each well's nanowells (n = nanowells). This compares these wells, not the conditions behind "
            "them: the nanowells of one well share its medium and handling and are not independent replicates — "
            "compare Groups for that.")


def effect_text(effect: EffectSize | None) -> str:
    """``g = -1.2   Δ = -0.5 [95% CI -0.8, -0.2]`` — or why it is missing."""
    if effect is None:
        return ""
    if not effect.ok:
        return f"effect size: {effect.undefined}"
    parts = [f"g = {_num(effect.g, '.3g')}"] if effect.g is not None else []
    if effect.diff is not None:
        interval = (f" [{effect.level:g}% CI {_num(effect.lo, '.3g')}, {_num(effect.hi, '.3g')}]"
                    if effect.lo is not None and effect.hi is not None else " [no interval]")
        parts.append(f"Δ = {_num(effect.diff, '.3g')}{interval}")
    if effect.g_undefined:
        parts.append(f"(no g: {effect.g_undefined})")
    return "   ".join(parts)


def comparison_text(c: Comparison, *, dunnett: bool) -> str:
    """One comparison as the table prints it: the pair, the statistic, the p-values, the stars, the effect size."""
    from review.domain.significance import format_p_value, p_phrase, significance_stars

    head = f"{c.a} vs {c.b}"
    if not c.ok:
        return f"{head}   not computed: {c.undefined}"
    if dunnett:
        number = format_p_value(c.p)
        ps = ((f"p_Dunnett {number}" if number.startswith("<") else f"p_Dunnett = {number}") if c.adjusted
              else p_phrase(c.p))
    else:
        ps = p_phrase(c.p) + (f"   {p_phrase(c.p_adj, adjusted=True)}" if c.adjusted else "")
    effect = effect_text(c.effect)
    return f"{head}   {c.stat_name} = {_num(c.stat, '.4g')}   {ps}   {significance_stars(c.p_used)}" + (
        f"   {effect}" if effect else "")


def sample_text(s: Sample, unit: str) -> str:
    q1, q3 = s.quartiles()
    text = (f"{s.name}:  n = {s.n}   mean = {_num(s.mean())}   sd = {_num(s.sd())}   median = {_num(s.median())}   "
            f"IQR {_num(q1)}–{_num(q3)}")
    if unit == WELLS:
        lo, hi = s.ci95()
        text += f"   95% CI {_num(lo)}, {_num(hi)}" if s.n >= 2 else "   no CI (fewer than 2 wells)"
        if s.without_value:
            text += f"   no value: {', '.join(s.without_value)}"
    excluded = sum(s.excluded.values())
    if excluded:
        text += f"   ({excluded:,} of {s.n_roster:,} nanowells excluded)"
    return text


def family_text(result: StatisticsResult) -> str:
    """The sentence naming the family and how its p-values were corrected, counted from the comparisons themselves:
    how many there are, how many have a p, and whether any p printed was adjusted. A family of one, or one in which
    only one comparison could be computed, prints that p raw (``bh_adjust``; Dunnett's p for a family of one), so
    the sentence never calls it corrected."""
    total = len(result.comparisons)
    computed = sum(1 for c in result.comparisons if c.ok)
    count = f"{total} comparison{'' if total == 1 else 's'}"
    if computed != total:
        count += f", {'none' if not computed else computed} computed"
    family = f"Compared: {result.family} ({count})"
    if result.correction == "Dunnett's correction":
        return family + "; p_Dunnett is corrected for the family by Dunnett's method."
    if result.correction:
        return family + "; p_BH is the Benjamini-Hochberg FDR-adjusted p across them, and the stars are of p_BH."
    if not computed:
        return family + "; nothing to correct for."
    if total == 1:
        return family + "; one comparison, so nothing to correct for."
    return family + "; only one has a p, so nothing to correct for."


def report_lines(result: StatisticsResult) -> list[tuple[str, str]]:
    """Every line of the view's report, ``(gid, text)``, in the order it is printed: the header, the omnibus, one
    line per sample, one per comparison, and the notes."""
    from review.domain.significance import p_phrase, significance_stars

    at = f" · at {result.timepoint}" if result.timepoint else " · one value per nanowell"
    lines = [(GID_HEADER, f"{result.test['label']} · {result.label}{at}"),
             (GID_HEADER, unit_sentence(result.unit))]
    if result.undefined:
        return lines + [(GID_NOTE, f"Not run: {result.undefined}")]
    om = result.omnibus
    if om is not None:
        if om.ok:
            text = (f"Omnibus: {om.name}   {om.stat_name}({om.df_between}, {om.df_within}) = {_num(om.stat)}   "
                    f"{p_phrase(om.p)}   {significance_stars(om.p)}")
            if om.p is not None and om.p >= 0.05:
                text += "   — no overall difference at the 5% level; the comparisons below are still shown."
            if om.dropped:
                text += f"   Left out (fewer than 2 values): {', '.join(om.dropped)}."
        else:
            text = f"Omnibus: not run — {om.undefined}"
        lines.append((GID_OMNIBUS, text))
    lines += [(GID_SAMPLE, sample_text(s, result.unit)) for s in result.samples]
    dunnett = result.test["kind"] == "dunnett"
    lines += [(GID_PAIR, comparison_text(c, dunnett=dunnett)) for c in result.comparisons]
    lines.append((GID_NOTE, family_text(result)))
    lines.append((GID_NOTE, "g: Hedges' g. Δ: difference of means (first minus second), with a bootstrap interval."))
    lines += [(GID_NOTE, note) for note in result.notes]
    return lines
