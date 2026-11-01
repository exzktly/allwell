"""The Nanowells feature's answer to "give me nanowell observations": the registry service ``observations.nanowell``.

A plot tool outside this feature never imports it (it is removable, and a build without it deletes this package);
it asks ``ctx.features.query(NANOWELL_SERVICE, ctx, wells)`` and gets an :class:`~review.domain.observation.
ObservationTable`, or ``None`` when the feature is not in the build (``review/data/observations``).

**One owner for the settings.** The rows are the page's own ``series_compute.site_metrics``, computed with the
page's Baseline and rim-focus rule (``NanowellState``) and with Cell Gating's tally, so a fold change on a plot and
the same fold change on the page are one number. The page's Fixed cohort and Moving average are *view* transforms
of a figure, not properties of a row, and are not applied here; a plot of nanowell observations draws every row
the metrics define and its footer counts the rest by reason.

**Cost.** Wells are read once into the page's own ``well_parts`` cache (the page reads them the same way), and the
table is memoised on ``(folder, wells, baseline, focus rule, gating version)`` — every input that changes a row —
so a redraw that changes nothing reads and computes nothing.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from review.domain.observation import NANOWELLS, ObservationMetric, ObservationTable
from review.features.nanowell.collect import NanowellSchemeError, load_nanowell_data
from review.features.nanowell.controller import gate_tally, gate_version, sync_focus_rule
from review.features.nanowell.distribution_compute import METRICS
from review.features.nanowell.series_compute import exclusion_reason, metric_kinds, site_metrics

log = logging.getLogger("review")

#: The memo's size: a plot asks for a handful of well sets at most between two edits.
_MEMO_MAX = 8


def _metric_specs(channels) -> tuple[ObservationMetric, ...]:
    kinds = metric_kinds(channels)
    specs = []
    for key, (label, needs_channel) in METRICS.items():
        if key == "occupancy_class":
            continue                              # a category ("0", "1", "2", "3+"), not a number to plot
        if needs_channel:
            for ch in channels:
                column = f"{key}_{ch}"
                specs.append(ObservationMetric(column, f"{ch} {label}", kinds[column][0], ch))
        else:
            specs.append(ObservationMetric(key, label, kinds[key][0]))
    # Occupied — whether the nanowell holds a cell — as a 0/1 value: its well mean is the share of occupied
    # nanowells, a different number from any "fraction above a threshold".
    specs.append(ObservationMetric("occupied", "Occupied (holds ≥ 1 cell)", "value"))
    return tuple(specs)


def _frame(metrics: pd.DataFrame) -> pd.DataFrame:
    from pipeline.nanowell_task import timepoint_hours

    frame = metrics.copy()
    # A run whose filenames carry no timepoint writes a blank label, which the table reader reads as NaN and the
    # cell rows as "" (and as 0 h): one spelling, so the two join and select the same rows.
    frame["timepoint"] = frame["timepoint"].where(frame["timepoint"].notna(), "")
    frame["occupied"] = (pd.to_numeric(frame["n"], errors="coerce") >= 1).astype(float).where(frame["n"].notna())
    # The timepoint as the cell rows' ``timepoint_hours`` reads it, so a plot's timepoint choices (built from the
    # cell rows) select the same rows here. ``time_h`` stays the acquisition record's clock, which may differ.
    frame["timepoint_hours"] = frame["timepoint"].map(timepoint_hours)
    return frame.reset_index(drop=True)


def nanowell_observations(ctx, wells) -> ObservationTable | None:
    """The nanowell rows of *wells* (an iterable of well labels), or ``None`` when the dataset holds no nanowell
    tables. Reads on the calling thread; a well already read by the page or an earlier call is not read again."""
    st = ctx.state.feature_slice("nanowell")
    data_dir = getattr(ctx.state.dataset, "data_dir", None)
    if st is None or not data_dir:
        return None
    wells = tuple(sorted(set(wells or ())))
    # The rule in force now (Cell Gating's QC parameters), not whatever the page last copied: a plot may be drawn
    # before the page was ever opened, or after the rule changed while it was hidden.
    sync_focus_rule(ctx, st)
    focus = st.rim_focus_rule()
    key = (str(data_dir), wells, st.baseline, focus.key(), gate_version(ctx))
    memo = st.observation_memo
    if key in memo:
        return memo[key]
    if st.parts_for != str(data_dir):
        st.well_parts, st.parts_for, st.loaded_wells = {}, str(data_dir), frozenset()
    try:
        data = load_nanowell_data(Path(data_dir), wells=list(wells), parts=st.well_parts)
    except NanowellSchemeError as exc:
        log.warning("Nanowell observations: %s", exc)
        return None
    if data is None:
        return None
    if not len(data.sites):
        table = ObservationTable(NANOWELLS, pd.DataFrame(columns=["well", "timepoint", "nw_q", "nw_r", "state"]),
                                 _metric_specs(data.channels))
    else:
        metrics = site_metrics(data, st.baseline, gate_tally(ctx, data), focus)
        frame = _frame(metrics)
        channels = list(data.channels)

        def why_missing(metric: str) -> pd.Series:
            if metric == "occupied":
                return exclusion_reason(frame, "n", channels)
            return exclusion_reason(frame, metric, channels)

        from review.domain.nanowell_rim_focus import note as focus_note

        table = ObservationTable(NANOWELLS, frame, _metric_specs(channels), why_missing,
                                 notes=tuple(filter(None, [focus_note(metrics, focus)])))
    if len(memo) >= _MEMO_MAX:
        memo.pop(next(iter(memo)))
    memo[key] = table
    return table
