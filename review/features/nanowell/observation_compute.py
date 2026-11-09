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
from review.features.nanowell.collect import NanowellSchemeError, load_nanowell_data, site_backgrounds, site_channels
from review.features.nanowell import subset_compute as ss
from review.features.nanowell.controller import gate_tally, gate_version, page_subset, size_rule, sync_focus_rule
from review.features.nanowell.distribution_compute import METRICS, metric_label
from review.features.nanowell import size_class_compute
from review.features.nanowell.series_compute import baseline_text, exclusion_reason, metric_kinds, site_metrics
from review.features.nanowell.summary_compute import well_statistic

log = logging.getLogger("review")

#: The memo's size: a plot asks for a handful of well sets at most between two edits.
_MEMO_MAX = 8


def _metric_specs(channels, modes=(), sizes=None) -> tuple[ObservationMetric, ...]:
    """Every metric a plot can draw from the nanowell rows; *modes* (one per well, ``background_compute``) decide what
    a fluorescence metric's label says its background was, and *sizes* (Cell Gating's ``SizeClassRule``) names the
    size classes. A size metric's well value is the mean over the well's nanowells, as on the page
    (``summary_compute.MEAN_METRICS``)."""
    kinds = metric_kinds(channels)
    specs = []
    # A name a user's label would give a size metric that another metric already has ("Cell" → "Cell count") keeps
    # its placeholder name: the Image Table's ``NW: <label>`` tokens resolve by name, and two metrics of one name
    # would resolve to whichever came first.
    taken = {metric_label(k, None, modes).lower() for k in METRICS if k not in size_class_compute.COLUMNS}
    for key in METRICS:
        label, needs_channel = metric_label(key, None, modes, sizes), METRICS[key][1]
        if key in size_class_compute.COLUMNS and label.lower() in taken:
            label = metric_label(key, None, modes)
        if key == "occupancy_class":
            continue                              # a category ("0", "1", "2", "3+"), not a number to plot
        if needs_channel:
            for ch in channels:
                column = f"{key}_{ch}"
                specs.append(ObservationMetric(column, f"{ch} {label}", kinds[column][0], ch))
        else:
            specs.append(ObservationMetric(key, label, kinds[key][0], well_statistic=well_statistic(key)))
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


def nanowell_metric_specs(ctx, wells) -> tuple[ObservationMetric, ...] | None:
    """The metrics :func:`nanowell_observations` would offer for *wells*, without reading or computing any rows
    (``collect.site_channels`` reads the header rows only); ``None`` when the dataset holds no nanowell tables.
    The registry service ``observations.nanowell_metrics``, for a picker that lists the metrics while a plate
    opens."""
    data_dir = getattr(ctx.state.dataset, "data_dir", None)
    if not data_dir:
        return None
    wells = sorted(set(wells or ()))
    channels = site_channels(Path(data_dir), wells)
    return None if channels is None else _metric_specs(channels,
                                                        list(site_backgrounds(Path(data_dir), wells).values()),
                                                        size_rule(ctx))


def _page_scope_key(ctx, st) -> tuple:
    """Everything of the page's that :func:`_apply_page_scope` reads, as a memo key: the Subset with its gate boxes
    bound (``repr`` of the frozen conditions — a moved gate is a different key), the excluded wells, the cohort."""
    return (repr(page_subset(ctx, st)), tuple(sorted(st.excluded_wells or ())), bool(st.cohort))


def _apply_page_scope(ctx, st, metrics: pd.DataFrame, data, baseline: str,
                      wells) -> tuple[pd.DataFrame, tuple, str]:
    """*metrics* with only the rows the page itself would draw, the sentences saying what was left out, and the
    Subset's name (``""`` with none active).

    Excluded wells go first (the page leaves them out of its groups, ``controller.page_groups``), then the Subset over
    the wells that remain, so its count is of the nanowells the plot could have drawn — the order and the note the
    page's own figures use (``controller.draw_view``)."""
    notes = []
    excluded = sorted(set(st.excluded_wells or ()) & set(wells))
    if excluded:
        metrics = metrics[~metrics["well"].isin(excluded)]
        notes.append(f"Left out with Exclude well on the Nanowells page: {', '.join(excluded)}.")
    kept_wells = [w for w in wells if w not in set(excluded)]
    subset = page_subset(ctx, st)
    metrics, done = ss.apply(metrics, subset, baseline, kept_wells, data)
    if done is not None:
        notes.append(f"Nanowells page {ss.note(done)}")
        if not done.kept:
            notes.append(ss.empty_text(done))
    if st.cohort:
        notes.append("Fixed cohort (Nanowells page): only nanowells with both plotted values at every timepoint of "
                     "their well are drawn.")
    return metrics, tuple(notes), (subset.label() if done is not None else "")


def nanowell_observations(ctx, wells, baseline: str | None = None, *,
                          scatter_gates: bool = True, page_scope: bool = False) -> ObservationTable | None:
    """The nanowell rows of *wells* (an iterable of well labels), or ``None`` when the dataset holds no nanowell
    tables. Reads on the calling thread; a well already read by the page or an earlier call is not read again.

    *baseline* (a ``series_compute`` baseline code) takes the fold changes from it instead of the page's own Baseline:
    the Image Table's metric maps pin one per channel, so two baselines can be drawn side by side. It is part of the
    memo key, as the page's is.

    ``scatter_gates=False`` counts every cell the scatter gates leave out too (``controller.gate_tally``): the values
    a gate drawn on a nanowell metric is evaluated against when Cell Gating applies it to cells, so that inclusion
    never depends on a count it changes. Part of the memo key.

    ``page_scope=True`` draws the page's own choice of *which* nanowells as well: the wells left out with
    **Exclude well** are dropped, only the nanowells inside the page's **Subset** are kept (``subset_compute.apply``,
    the call every view of the page makes, with its scatter-gate conditions bound to the gates as they stand now),
    and the page's **Fixed cohort** is carried on the table for the plot to apply over its own metrics
    (:attr:`ObservationTable.fixed_cohort`). Each says what it did in :attr:`ObservationTable.notes`, in the page's
    own sentences, so a scoped plot never reads as the whole plate. The Scatter Plot's nanowell scatter asks for this:
    one Subset, built on the page, decides the nanowells of both. The default stays unscoped, because Cell Gating
    evaluates a gate drawn on a nanowell metric against these rows (``scatter_gates=False``), and a Subset that
    names that gate would otherwise decide its own inputs."""
    st = ctx.state.feature_slice("nanowell")
    data_dir = getattr(ctx.state.dataset, "data_dir", None)
    if st is None or not data_dir:
        return None
    wells = tuple(sorted(set(wells or ())))
    # The rule in force now (Cell Gating's QC parameters), not whatever the page last copied: a plot may be drawn
    # before the page was ever opened, or after the rule changed while it was hidden.
    sync_focus_rule(ctx, st)
    focus = st.rim_focus_rule()
    baseline = st.baseline if baseline is None else baseline
    scope = _page_scope_key(ctx, st) if page_scope else None
    key = (str(data_dir), wells, baseline, focus.key(), gate_version(ctx), bool(scatter_gates), scope)
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
                                 _metric_specs(data.channels, list(data.fluor_backgrounds.values()), size_rule(ctx)))
    else:
        sizes = size_rule(ctx)
        metrics = site_metrics(data, baseline, gate_tally(ctx, data, scatter_gates=scatter_gates, sizes=sizes), focus)
        scope_notes: tuple[str, ...] = ()
        subset_label = ""
        if page_scope:
            metrics, scope_notes, subset_label = _apply_page_scope(ctx, st, metrics, data, baseline, wells)
            # A condition on a size metric names its field generically ("Size class 1 count"); say the user's label.
            scope_notes, subset_label = tuple(sizes.name(n) for n in scope_notes), sizes.name(subset_label)
        frame = _frame(metrics)
        channels = list(data.channels)

        def why_missing(metric: str) -> pd.Series:
            if metric == "occupied":
                return exclusion_reason(frame, "n", channels)
            return exclusion_reason(frame, metric, channels)

        from review.domain.nanowell_rim_focus import note as focus_note

        table = ObservationTable(NANOWELLS, frame,
                                 _metric_specs(channels, list(data.fluor_backgrounds.values()), sizes),
                                 why_missing,
                                 notes=tuple(filter(None, [focus_note(metrics, focus), *scope_notes])),
                                 fixed_cohort=bool(page_scope and st.cohort),
                                 baseline=baseline_text(baseline), subset=subset_label)
    if len(memo) >= _MEMO_MAX:
        memo.pop(next(iter(memo)))
    memo[key] = table
    return table
