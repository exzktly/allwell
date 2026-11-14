"""Rare-responder analysis: its report as text and as files.

:func:`report_lines` is the result read aloud — per treatment, the three tests, their corrected p-values, the
interpretation, and every warning — and :func:`write` puts every table of a ``rare_report_compute.RareResult`` in a
folder, with the parameter log beside them, so a result can always be traced to the choices that made it:

==========================  =======================================================================
``rare_per_treatment.csv``  the per-treatment table: exceedance OR / CI / p, HC and A²_R with their
                            permutation p, BH q-values, control wells in the null, smallest p
``rare_sensitivity.csv``    Test A at every sensitivity percentile
``rare_per_well.csv``       every well's count, HC, A²_R and empirical p-values
``rare_responders.csv``     every flagged nanowell, with its crop path
``rare_wells.csv``          per-well QC summary: kept, excluded, loading distribution, median FC
``rare_exclusions.csv``     every excluded nanowell with its reason
``rare_validation_*.csv``   the self-check's false-positive shares and power by spike count
``rare_parameters.json``    every parameter, the strata, exclusions by reason, thresholds, the seed
==========================  =======================================================================

Qt-free and matplotlib-free.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from review.features.nanowell import rare_hc_compute as hc
from review.features.nanowell.rare_exceedance_compute import BB_LABEL, FISHER_LABEL
from review.features.nanowell.rare_null_compute import pool
from review.features.nanowell.rare_report_compute import significant
from review.features.nanowell.rare_ad_compute import SCHOLZ_LABEL
from util.json_write import write_json_atomic


def _num(value, spec: str = ".3g") -> str:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "—"
    if math.isnan(value):
        return "—"
    if math.isinf(value):
        return "∞" if value > 0 else "−∞"
    return format(value, spec)


def _or_text(r) -> str:
    if not math.isfinite(r["exceedance_or"]) and not math.isinf(r["exceedance_or"]):
        return "not estimable"
    if math.isinf(r["exceedance_or"]):
        return (f"unbounded — no control nanowell was past the cutoff (95% interval {_num(r['exceedance_or_ci_low'])} "
                "to ∞)")
    return (f"{_num(r['exceedance_or'])} (95% interval {_num(r['exceedance_or_ci_low'])} to "
            f"{_num(r['exceedance_or_ci_high'])})")


def _perm_text(p, q, n, exhaustive, floor, undefined: str) -> str:
    if not math.isfinite(p):
        return undefined
    at_floor = " — the lowest possible" if math.isfinite(floor) and p <= floor * (1 + 1e-9) else ""
    return (f"p {_num(p)}{at_floor}, q {_num(q)} ({n} relabelings of the wells, "
            f"{'every one' if exhaustive else 'a random sample'}; lowest possible p {_num(floor)})")


def _unit_lines(result, unit, r) -> list[str]:
    kt, nt, kc, nc = counts_of(unit)
    plates = "all plates" if r["plate"] == "all" else f"plate {r['plate']}"
    lines = [f"{r['treatment']} ({plates}): {r['n_treatment_wells']} {r['treatment']} vs {r['n_control_wells']} "
             f"control wells — {r['interpretation']}"]
    if not r["group_inference"]:
        lines.append(f"  not tested as a group: {r['refusal']}")
    if kt + kc == 0:
        lines.append(f"  {NAME_A}: not tested — no nanowell in any well was past the cutoff "
                     f"(the control's {r['percentile']:g}th percentile).")
    else:
        method = "beta-binomial regression" if r["exceedance_method"].startswith("beta-binomial") else r["exceedance_method"]
        lines.append(f"  {NAME_A} at the control's {r['percentile']:g}th percentile ({method}): {kt} of {nt} "
                     f"{r['treatment']} vs {kc} of {nc} control nanowells past the cutoff; odds of being past it "
                     f"{_or_text(r)}; spread between wells ρ {_num(r['rho'])}; p {_num(r['exceedance_p'])}, "
                     f"q {_num(r['exceedance_q'])}")
        lines.append(f"    for comparison, {FISHER_LABEL}: p {_num(r['fisher_p_anticonservative'])}")
    # A permutation that never ran (too few wells for a group test) is said as such, not as an undefined statistic.
    lines.append(f"  {NAME_B}: mean score {_num(r['hc_mean'])} over the {r['treatment']} wells; "
                 + _perm_text(r["hc_p"], r["hc_q"], r["hc_permutations"], r["hc_exhaustive"], r["hc_min_p"],
                              NOT_RUN if unit.hc_perm is None else "not computable"))
    lines.append(f"  {NAME_C}: " + (f"mean {_num(r['a2r_mean'])}; " if math.isfinite(r["a2r_mean"]) else "")
                 + _perm_text(r["a2r_p"], r["a2r_q"], r["a2r_permutations"], r["hc_exhaustive"], r["a2r_min_p"],
                              NOT_RUN if unit.a2_perm is None else C_UNDEFINED))
    stat, sp, note = unit.scholz
    capped = note.startswith("p capped")
    p_text = (f"p ≥ {_num(sp)} (scipy caps it)" if capped and math.isfinite(sp) and sp >= 0.25 else
              f"p ≤ {_num(sp)} (scipy caps it)" if capped and math.isfinite(sp) else f"p {_num(sp)}")
    lines.append(f"    for comparison, {SCHOLZ_LABEL}: standardised statistic {_num(stat)}, {p_text}"
                 + (f" ({note})" if note and not capped else ""))
    floor = 1.0 / (len(unit.controls) + 1) if unit.controls else math.nan
    for c in (c for c in unit.primary.counts if c.treated):
        well = c.key.split(":", 1)[-1]
        at = " — the lowest possible" if math.isfinite(c.empirical_p) and c.empirical_p <= floor * (1 + 1e-9) else ""
        lines.append(f"  {well}: {c.k} of {c.n} nanowells past the cutoff ({_pct(c.k, c.n)}); against the "
                     f"{len(unit.controls)} control wells, p {_num(c.empirical_p)}{at}")
    if r["notes"]:
        lines.append(f"  notes: {r['notes']}")
    return lines


def report_lines(result) -> list[str]:
    """The full report, in plain words, as the page's Details shows it and ``rare_report.txt`` holds it: what each test
    asks, then per group its verdict, every test's statistic, p and q, the comparison tests, each well's count, and
    every warning; the self-check when it ran."""
    lines = [f"Rare-responder analysis of {result.prepared.value_label}: {result.log['nanowells_kept']} nanowells kept, "
             f"{result.log['nanowells_excluded']} left out.",
             *[f"{name} asks: {what}." for name, what in ASKS.items()],
             f"q is each p corrected across the {len(result.units)} group(s) tested (Benjamini–Hochberg), separately "
             f"for each test; only the {NAME_A.lower()} decides the verdict."]
    for unit, (_i, r) in zip(result.units, result.table.iterrows()):
        lines += _unit_lines(result, unit, r)
    lines += [f"WARNING: {w}" for w in result.warnings]
    if result.validation is not None:
        lines.append("Self-check, control vs control (share of p < 0.05 should be ≤ ~5%):")
        for _i, r in result.validation.false_positive.iterrows():
            lines.append(f"  {r['test']}: {_num(r['share_below_0.05'], '.0%')} of {r['splits']} splits")
        if len(result.validation.power):
            table = result.validation.power.pivot(index="k", columns="test", values="power")
            lines.append("Self-check, power by spiked responders per well (k): "
                         + "; ".join(f"k={k}: " + ", ".join(f"{t} {_num(v, '.0%')}" for t, v in row.items())
                                     for k, row in table.iterrows()))
        lines += [f"  {n}" for n in result.validation.notes]
    return lines


def write(result, folder) -> list[Path]:
    """Every table and the parameter log into *folder* (created); the paths written."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    tables = {"rare_per_treatment.csv": result.table, "rare_sensitivity.csv": result.sensitivity,
              "rare_per_well.csv": result.per_well, "rare_responders.csv": result.responders,
              "rare_wells.csv": result.prepared.wells, "rare_exclusions.csv": result.prepared.exclusions}
    if result.validation is not None:
        tables["rare_validation_false_positive.csv"] = result.validation.false_positive
        tables["rare_validation_power.csv"] = result.validation.power
    written = []
    for name, frame in tables.items():
        path = folder / name
        frame.to_csv(path, index=False)
        written.append(path)
    log = folder / "rare_parameters.json"
    # Through ``default=str`` first: the log holds numpy scalars, which the JSON encoder does not take.
    write_json_atomic(log, json.loads(json.dumps(result.log, default=str)))
    report = folder / "rare_report.txt"
    report.write_text("\n".join(report_lines(result)) + "\n", encoding="utf-8")
    return written + [log, report]


#: One name per test, everywhere the result is read — the figure, Details and the export.
NAME_A = "Exceedance test"
NAME_B = "Higher Criticism"
NAME_C = "Upper-tail shape test"
#: What each test asks, said once where the tests are introduced (Details).
ASKS = {NAME_A: "more nanowells past the control cutoff than in control (decides the verdict)",
        NAME_B: "a few extreme nanowells, with no fixed cutoff",
        NAME_C: "any difference in the top of the distribution (Anderson–Darling)"}
#: The reason an Upper-tail shape statistic is undefined, said in place of a p.
C_UNDEFINED = "not computable (too few distinct fold changes above the control median)"
#: A group permutation that was not run: the design has too few wells on a side to test the group at all.
NOT_RUN = "not run (too few wells to test the group)"


def _pct(k: int, n: int) -> str:
    return f"{k / n:.1%}" if n else "—"


def counts_of(unit) -> tuple[int, int, int, int]:
    """``(treated past the cutoff, treated, control past the cutoff, control)`` nanowells for *unit*'s cutoff."""
    counts = unit.primary.counts
    return (sum(c.k for c in counts if c.treated), sum(c.n for c in counts if c.treated),
            sum(c.k for c in counts if not c.treated), sum(c.n for c in counts if not c.treated))


def cutoff_name(params) -> str:
    """How the cutoff is named: the control percentile it was set at (its top or its bottom, by the direction), or
    the fixed value the user gave."""
    if params.fixed_threshold is not None:
        return f"the fixed cutoff ({params.fixed_threshold:g})"
    end = "bottom" if params.direction == "lower" else "top"
    return f"the control cutoff (its {end} {100 - params.percentile:g}%)"


def past(params) -> str:
    """How a nanowell beyond the cutoff is said to be, in the direction looked for."""
    return "fell below" if params.direction == "lower" else "rose above"


def _in_sentence(label: str) -> str:
    """*label* as it reads mid-sentence: its first letter lowered, unless it starts an acronym (``GFP brightness``)."""
    return label[:1].lower() + label[1:] if label[1:2].islower() else label


def headline(unit, params, value_label: str = "") -> str:
    """The answer for *unit*, first and in counts: the verdict, then how many nanowells of each condition were past
    the cutoff on the value tested, and the wells behind them. Every number is a count a reader can check against the
    panels."""
    kt, nt, kc, nc = counts_of(unit)
    wells = f"{len(unit.treated)} {unit.treatment} vs {len(unit.controls)} control wells"
    on = f" on {_in_sentence(value_label)}" if value_label else ""
    # Control wells are scored leave-one-out, so one control well is scored against an empty pool: "0 of 0" would
    # read as a count, and it is the absence of one.
    against = (f"against {kc} of {nc} control nanowells ({_pct(kc, nc)})" if len(unit.controls) >= 2 else
               "with no control null to set it against (a single control well has no other control wells to be "
               "scored against)")
    return (f"{unit.label}: {unit.interpretation}. {kt} of {nt} {unit.treatment} nanowells ({_pct(kt, nt)}) "
            f"{past(params)} {cutoff_name(params)}{on}, {against} · {wells}.")


def _floor_note(perm) -> str:
    if perm is None or not math.isfinite(perm.p) or not math.isfinite(perm.min_p) or perm.p > perm.min_p * (1 + 1e-9):
        return ""
    return " (the lowest possible with these wells)"


def needs(unit, params) -> str:
    """What the deciding test's regression needs that this design lacks, in one sentence."""
    short = []
    if len(unit.controls) < params.min_controls_bb:
        short.append(f"{params.min_controls_bb} control wells (this design has {len(unit.controls)})")
    if len(unit.treated) < params.min_treated_bb:
        short.append(f"{params.min_treated_bb} {unit.treatment} wells (this design has {len(unit.treated)})")
    return (f"it needs at least {' and '.join(short)}." if short
            else "the regression could not be fitted.")


def deciding_line(unit, n_units: int, params) -> str:
    """The exceedance test's result: its p (and q when several groups were tested), or why it has none."""
    kt, _nt, kc, _nc = counts_of(unit)
    a = unit.primary
    if not unit.group_allowed:
        return f"Deciding: {NAME_A.lower()} not run — {unit.refusal}."
    if kt + kc == 0:
        return f"Deciding: {NAME_A.lower()} not tested — no nanowell in any well was past the cutoff."
    if not math.isfinite(a.p):
        return (f"Deciding: {NAME_A.lower()} has no group p — {needs(unit, params)} With fewer, it gives each well's "
                "own p only (Details).")
    q = unit.q.get("exceedance", math.nan)
    corrected = f" (q = {_num(q)} across the {n_units} groups tested)" if n_units > 1 else ""
    return f"Deciding: {NAME_A.lower()} p = {_num(a.p)}{corrected}."


def _support_part(name: str, perm, undefined: str) -> str:
    if perm is None:
        return f"{name} not run"
    if not math.isfinite(perm.p):
        return f"{name} {undefined if perm.reason == 'the statistic is undefined for every treatment well' else 'not computable: ' + (perm.reason or 'undefined')}"
    return f"{name} p = {_num(perm.p)}{_floor_note(perm)}"


def supporting_line(unit, params) -> str:
    """Higher Criticism and the upper-tail shape test, which never change the verdict, and — when either finds what
    the exceedance test did not — what that disagreement usually means."""
    if not unit.group_allowed:
        return ""
    line = (f"Supporting, not deciding: {_support_part(NAME_B, unit.hc_perm, 'not computable')} · "
            f"{_support_part('shape test', unit.a2_perm, C_UNDEFINED)}.")
    a_sig = significant(unit, "exceedance", params.fdr)
    if not a_sig and significant(unit, "hc", params.fdr):
        line += (f" {NAME_B} finds an excess the cutoff did not: look at the nanowells just under the cutoff.")
    elif not a_sig and significant(unit, "ad", params.fdr):
        line += " The top of the distribution differs in shape, which by itself is not rare responders."
    return line


def caveats(unit, params, value_label: str = "") -> list[str]:
    """What undermines the verdict, each in one sentence — said beside it, never left in Details: a permutation floor
    that keeps the supporting tests from ever reaching significance, an exceedance regression that could not estimate
    the spread between wells, and nanowells Higher Criticism cannot see."""
    out = []
    if not unit.group_allowed:
        return out
    kt, _nt, kc, _nc = counts_of(unit)
    perm = unit.hc_perm
    if perm is not None and math.isfinite(perm.min_p) and perm.min_p >= params.fdr:
        out.append(f"With {len(unit.controls)} control and {len(unit.treated)} {unit.treatment} wells, {NAME_B} and the "
                   f"shape test cannot go below p = {_num(perm.min_p)}, so they can neither confirm nor rule out "
                   "anything here.")
    a = unit.primary
    if a.method == BB_LABEL and math.isfinite(a.p) and kc == 0 and kt > 0:
        exact = (f" (on wells alone, p ≥ {_num(perm.min_p)})" if perm is not None and perm.exhaustive
                 and math.isfinite(perm.min_p) else "")
        out.append("No control nanowell was past the cutoff, so the spread between wells could not be estimated: "
                   f"the {NAME_A.lower()}'s p treats nanowells as independent and overstates the evidence" + exact + ".")
    full = pool(unit.controls)
    unseen = 0
    for w in unit.treated:
        ps = hc.p_values(w, full)[0]
        unseen += int(np.sum(ps <= 1.0 / len(ps))) if len(ps) else 0
    if _is_level(value_label) and not params.normalize:
        out.append(f"{value_label[:1].upper() + value_label[1:]} is a level, not a change from the Baseline: nanowells "
                   f"that started {'higher' if params.direction != 'lower' else 'lower'} can be past the cutoff "
                   "without responding. The fold-change form of the metric compares each with its own Baseline.")
    if unseen:
        out.append(f"{NAME_B} cannot see the {unseen} most extreme {unit.treatment} nanowell(s) (it leaves out "
                   "p ≤ 1/n), so it says nothing about them.")
    out += [note for note in (getattr(unit, "tie_note", ""), getattr(unit, "loading_note", "")) if note]
    return out


def _is_level(value_label: str) -> bool:
    """Whether *value_label* names a level (a count, a brightness, an area) rather than a change from the Baseline.

    A growth rate is a change over time with no fold-change form, so it is not one; the caveat's advice ("the
    fold-change form of the metric") would point at a metric that does not exist."""
    text = value_label.lower()
    return bool(text) and not any(word in text for word in ("fold change", "change", "robust z", "log₂", "log2",
                                                              "growth rate"))


def unit_text(result, unit) -> dict:
    """Everything the figure says about *unit*: ``headline``, ``lines`` (the deciding test, then the supporting
    ones) and ``caveats``."""
    params = result.prepared.params
    return {"headline": headline(unit, params, result.prepared.value_label),
            "lines": [line for line in (deciding_line(unit, len(result.units), params), supporting_line(unit, params))
                      if line],
            "caveats": caveats(unit, params, result.prepared.value_label)}


def verdict_lines(result, units=None) -> list[str]:
    """The figure's text for each treatment as flat lines: its headline, the tests, then each caveat marked ⚠. The
    full report is :func:`report_lines`, which the page shows under the figure (Details)."""
    chosen = result.units if units is None else result.units[:units]
    lines = []
    for unit in chosen:
        text = unit_text(result, unit)
        lines += [text["headline"], *text["lines"], *[f"⚠ {c}" for c in text["caveats"]]]
    return lines
