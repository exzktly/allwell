"""Scoring the per-field metrics a run recorded — and refusing to, where it must.

``pipeline/field_quality.py`` measures. This decides what is anomalous, and the
decision is split in two, because one rule cannot cover both halves of what was
measured.

**Robust-z-against-the-plate encodes "unlike the rest of this plate implies
artifact".** That is valid only for a quantity that *should* be constant across
the plate by design. Of what a field record carries, three qualify — focus,
saturation and dynamic range describe how the picture was taken. The rest
describe what was in the well, and vary by design:

* a cytotoxic compound drops nucleus count ten- to twentyfold, which is
  ``|z|`` far past any threshold worth setting;
* cell-cycle arrest enlarges nuclei;
* treatment-induced autofluorescence raises background.

Scoring those against the plate median flags **the wells where the experiment
worked**, and — with an exclusion toggle on — would discard them, biasing every
dose-response toward null. Not "different means and wider error bars": a
*directional* bias that removes the signal. So they are scored **within a well,
across its own fields**, where the fields are technical replicates of one
biology and disagreement between them genuinely is a technical fault; and their
plate-wide values are reported, never flagged.

**Timepoints group, they do not pool.** ``available_timepoints`` is first-class
in this app, and a t=0 field and a t=72h field of the same well are not
replicates of each other. Scoring is within a timepoint.

Numpy-free: robust statistics over tens to thousands of scalars are a sort and
an arithmetic mean, and this module is imported by the Review side where an
extra dependency buys nothing. Qt-free and imports nothing above ``domain``.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _field

#: Below this many comparable fields, there is no dispersion to measure. A MAD
#: over three values is not an estimate of spread, and a verdict drawn from one
#: is worse than no verdict.
MIN_FIELDS_FOR_SCORING = 8

#: 1 / Phi^-1(0.75). Scales MAD onto the standard deviation for a normal
#: sample, so a "robust z" is comparable in size to an ordinary one.
_MAD_TO_SIGMA = 0.67448975

#: Default flag threshold. Deliberately not 3: the metrics are not normal, a
#: plate carries hundreds of fields, and three acquisition metrics are each
#: tested, so the family-wise rate is three times the per-metric one. This is a
#: starting point the user can move against the distribution the surface shows
#: them — not a calibrated cut, and nothing here pretends otherwise.
DEFAULT_Z_THRESHOLD = 5.0

#: Metrics whose healthy value is zero for most fields, so their MAD is zero on
#: exactly the plates where they work. Scored against an absolute bound instead:
#: a relative rule switches itself off precisely when one saturated field is
#: the thing worth finding.
_ABSOLUTE_BOUNDS = {
    # A field with more than one pixel in five hundred pinned at the sensor
    # maximum has lost quantification in those pixels, whatever the rest of the
    # plate looks like.
    "sat_frac": 0.002,
}


@dataclass(frozen=True)
class MetricVerdict:
    """One metric's reading for one field, and what could be said about it."""

    metric: str
    value: float | None = None
    z: float | None = None
    flagged: bool = False
    #: Why no ``z`` was computed. Empty when one was.
    declined: str = ""


@dataclass(frozen=True)
class FieldVerdict:
    """Every metric's verdict for one field, plus the one that drove the flag."""

    key: tuple
    verdicts: tuple[MetricVerdict, ...] = ()
    #: The flagged metric with the largest ``|z|``, or ``None`` when clean.
    driver: MetricVerdict | None = None

    @property
    def flagged(self) -> bool:
        return self.driver is not None

    @property
    def scored_metrics(self) -> tuple[str, ...]:
        return tuple(v.metric for v in self.verdicts if v.z is not None)

    def describe(self) -> str:
        if self.driver is None:
            return ""
        d = self.driver
        if d.z is None:
            return f"{d.metric} = {d.value:g}"
        direction = "below" if d.z < 0 else "above"
        return (f"{d.metric} {abs(d.z):.1f} MADs {direction} the median "
                f"({d.value:g})")


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def robust_z(values: list[float]) -> tuple[list[float | None], str]:
    """``(per-value robust z, reason they could not be computed)``.

    Two refusals, and the second is the one most easily mistaken for rigour:

    * **too few values** — no dispersion to estimate;
    * **a MAD of zero** — every value identical, so no value is any number of
      MADs from the median. Returning ``inf`` for the one that differs, or
      ``0.0`` for all of them, would both be inventions.

    A *near*-zero MAD is not refused and is not special-cased, because there is
    no principled cut between "small spread" and "no spread"; what stops it
    producing thousands is that the surface shows the distribution, so a column
    of ``|z| = 4000`` reads as the degenerate distribution it is rather than as
    four thousand anomalies.
    """
    vals = [float(v) for v in values if v is not None]
    if len(vals) < MIN_FIELDS_FOR_SCORING:
        return [None] * len(values), (
            f"fewer than {MIN_FIELDS_FOR_SCORING} comparable fields")
    med = _median(vals)
    mad = _median([abs(v - med) for v in vals])
    if mad <= 0.0:
        return [None] * len(values), "every field reported the same value"
    scale = mad / _MAD_TO_SIGMA
    return [None if v is None else (float(v) - med) / scale
            for v in values], ""


def score_fields(records, *, z_threshold: float = DEFAULT_Z_THRESHOLD,
                 acquisition_metrics=None, biological_metrics=None,
                 ) -> list[FieldVerdict]:
    """Score every field in *records*. One :class:`FieldVerdict` per record.

    *records* is a sequence of mappings, each carrying a ``key`` (whatever
    addresses the field — well, fov, timepoint) plus the metric values.

    Acquisition metrics are compared **within a timepoint, across the plate**;
    biological ones **within a well, across its own fields**. Zero-inflated
    metrics take an absolute bound instead of either.

    Every flag is per metric. There is deliberately no combined score: a
    max-of-``|z|`` over a varying number of metrics is not comparable between
    fields — a field scored on three metrics has a larger maximum than one
    scored on one, for no reason to do with the pictures.
    """
    from pipeline.field_quality import (
        ACQUISITION_METRICS, BIOLOGICAL_METRICS,
    )

    acq = tuple(acquisition_metrics or ACQUISITION_METRICS)
    bio = tuple(biological_metrics or BIOLOGICAL_METRICS)
    rows = list(records)
    n = len(rows)
    per_field: list[dict[str, MetricVerdict]] = [{} for _ in range(n)]

    def _group(indices: list[int], metric: str) -> None:
        values = [rows[i].get(metric) for i in indices]
        zs, why = robust_z(values)
        for slot, i in enumerate(indices):
            val = values[slot]
            if val is None:
                per_field[i][metric] = MetricVerdict(
                    metric, None, None, False, "not recorded for this field")
                continue
            z = zs[slot]
            per_field[i][metric] = MetricVerdict(
                metric, float(val), z,
                bool(z is not None and abs(z) >= z_threshold), why)

    def _absolute(metric: str, bound: float) -> None:
        for i, row in enumerate(rows):
            val = row.get(metric)
            if val is None:
                per_field[i][metric] = MetricVerdict(
                    metric, None, None, False, "not recorded for this field")
            else:
                per_field[i][metric] = MetricVerdict(
                    metric, float(val), None, float(val) > bound,
                    f"judged against an absolute bound of {bound:g}")

    by_tp: dict[object, list[int]] = {}
    by_well: dict[object, list[int]] = {}
    for i, row in enumerate(rows):
        by_tp.setdefault(row.get("timepoint"), []).append(i)
        by_well.setdefault((row.get("well"), row.get("timepoint")), []).append(i)

    for metric in acq:
        if metric in _ABSOLUTE_BOUNDS:
            _absolute(metric, _ABSOLUTE_BOUNDS[metric])
            continue
        for indices in by_tp.values():
            _group(indices, metric)
    for metric in bio:
        if metric in _ABSOLUTE_BOUNDS:
            _absolute(metric, _ABSOLUTE_BOUNDS[metric])
            continue
        for indices in by_well.values():
            _group(indices, metric)

    out: list[FieldVerdict] = []
    for i, row in enumerate(rows):
        verdicts = tuple(per_field[i][m] for m in (*acq, *bio)
                         if m in per_field[i])
        flagged = [v for v in verdicts if v.flagged]
        driver = max(flagged, key=lambda v: abs(v.z) if v.z is not None
                     else float("inf"), default=None)
        out.append(FieldVerdict(row.get("key"), verdicts, driver))
    return out


@dataclass(frozen=True)
class PlateQualitySummary:
    """What to say about a whole plate's field quality, including "nothing"."""

    scored: int = 0
    flagged: int = 0
    verdicts: tuple[FieldVerdict, ...] = ()
    declined: str = ""
    drivers: dict = _field(default_factory=dict)

    def describe(self) -> str:
        if self.declined:
            return self.declined
        if not self.scored:
            return "No fields were scored."
        if not self.flagged:
            return f"{self.scored:,} fields, none flagged."
        top = ", ".join(f"{m} ({c})" for m, c in
                        sorted(self.drivers.items(), key=lambda kv: -kv[1]))
        return f"{self.flagged:,} of {self.scored:,} fields flagged — {top}."


def summarise(records, **kwargs) -> PlateQualitySummary:
    """Score *records* and reduce them to what a status line can say.

    A dataset analysed before field quality was recorded carries no records at
    all, and that is reported as what it is rather than as "0 fields flagged" —
    a plate nobody measured and a plate measured and found clean must not read
    alike.
    """
    rows = list(records)
    if not rows:
        return PlateQualitySummary(
            declined="This dataset was analysed before field quality was "
                     "recorded, so there is nothing to score.")
    if any(r.get("partial_plate") for r in rows):
        # A resumed or incremental run measures only the fields it
        # re-processed, so the plate median is drawn from a biased subset —
        # and every field's robust z against it is wrong. Scoring anyway and
        # reporting the number would be the silent substitution this whole
        # subsystem exists to prevent; the run that produced the block is the
        # only thing that can fix it.
        wells = sorted({str(r.get("well")) for r in rows
                        if r.get("partial_plate")})
        shown = ", ".join(wells[:4]) + ("…" if len(wells) > 4 else "")
        return PlateQualitySummary(
            declined=(f"Field quality was measured for only part of this plate "
                      f"({len(wells)} well(s) partly measured: {shown}), so a "
                      f"plate-wide comparison would be drawn from a biased "
                      f"sample. Re-run those wells with Force to score them."))
    verdicts = score_fields(rows, **kwargs)
    flagged = [v for v in verdicts if v.flagged]
    drivers: dict[str, int] = {}
    for v in flagged:
        drivers[v.driver.metric] = drivers.get(v.driver.metric, 0) + 1
    return PlateQualitySummary(len(verdicts), len(flagged), tuple(verdicts),
                               "", drivers)
