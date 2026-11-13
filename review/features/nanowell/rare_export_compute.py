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

from review.features.nanowell.rare_exceedance_compute import FISHER_LABEL
from review.features.nanowell.rare_ad_compute import SCHOLZ_LABEL


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


def report_lines(result) -> list[str]:
    lines = [f"Rare-responder analysis — value: {result.prepared.value_label}; "
             f"{result.log['nanowells_kept']} nanowells kept, {result.log['nanowells_excluded']} excluded"]
    for _i, r in result.table.iterrows():
        lines.append(f"{r['treatment']} ({r['plate']}): {r['n_treatment_wells']} treatment vs "
                     f"{r['n_control_wells']} control wells — {r['interpretation']}")
        if not r["group_inference"]:
            lines.append(f"  group-level inference refused: {r['refusal']}")
        lines.append(f"  A exceedance @ {r['percentile']:g}th [{r['exceedance_method']}]: OR {_num(r['exceedance_or'])} "
                     f"(95% CI {_num(r['exceedance_or_ci_low'])}–{_num(r['exceedance_or_ci_high'])}), "
                     f"ρ {_num(r['rho'])}, p {_num(r['exceedance_p'])}, q {_num(r['exceedance_q'])}")
        lines.append(f"    {FISHER_LABEL}: p {_num(r['fisher_p_anticonservative'])}")
        lines.append(f"  B HC+ mean {_num(r['hc_mean'])}: permutation p {_num(r['hc_p'])}, q {_num(r['hc_q'])} "
                     f"({r['hc_permutations']} distinct labelings, {'exhaustive' if r['hc_exhaustive'] else 'sampled'};"
                     f" smallest attainable p {_num(r['hc_min_p'])})")
        lines.append(f"  C A²_R mean {_num(r['a2r_mean'])}: permutation p {_num(r['a2r_p'])}, q {_num(r['a2r_q'])} "
                     f"(smallest attainable p {_num(r['a2r_min_p'])})")
        lines.append(f"    {SCHOLZ_LABEL}: A² {_num(r['scholz_stephens_stat_uncalibrated'])}, "
                     f"p {_num(r['scholz_stephens_p_uncalibrated'])}")
        if r["notes"]:
            lines.append(f"  notes: {r['notes']}")
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
    log.write_text(json.dumps(result.log, indent=2, default=str), encoding="utf-8")
    report = folder / "rare_report.txt"
    report.write_text("\n".join(report_lines(result)) + "\n", encoding="utf-8")
    return written + [log, report]
