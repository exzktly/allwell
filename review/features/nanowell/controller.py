"""Orchestration for the Nanowells data: load a dataset's tables, resolve replicate groups, export (D6, D8).

The page (Section V) calls these; nothing here draws. The compute modules beside it are pure; this module
is where the dataset on disk and the app's Sample Definitions come in.
"""

from __future__ import annotations

import logging
from pathlib import Path

from review.features.nanowell.collect import NanowellSchemeError, load_nanowell_data
from review.features.nanowell.series_compute import long_format, site_metrics

log = logging.getLogger("review")


def load_into(state, data_dir) -> None:
    """Read *data_dir*'s nanowell tables into *state* and compute the metrics at the state's baseline.

    A dataset with no ``nanowells/`` leaves ``state.data`` as ``None``; a scheme this version does not read
    leaves it ``None`` with the reason in ``state.load_error``.
    """
    state.reset_data()
    if not data_dir:
        return
    try:
        data = load_nanowell_data(Path(data_dir))
    except NanowellSchemeError as exc:
        state.load_error = str(exc)
        return
    if data is None:
        return
    for problem in data.problems:
        log.warning("Nanowells: %s", problem)
    state.data = data
    state.metrics = site_metrics(data, state.baseline)


def set_baseline(state, baseline: str) -> None:
    """Change the baseline and recompute every metric that depends on it."""
    state.baseline = baseline
    if state.data is not None:
        state.metrics = site_metrics(state.data, baseline)


def replicate_groups(ctx, wells) -> dict:
    """``group name -> wells`` from Sample Definitions' replicate sets, as every plot tab resolves them.

    Wells in no set stand as their own group, so a plate with no definitions compares well against well.
    Only wells with nanowell data are kept; a set left with none is dropped.
    """
    from review.domain.plot_members import plot_replicate_sets

    have = set(wells)
    groups, placed = {}, set()
    for rset in plot_replicate_sets(ctx) or []:
        members = [w for w in rset.wells if w in have]
        if members:
            groups[rset.name] = members
            placed.update(members)
    for well in sorted(have - placed):
        groups[well] = [well]
    return groups


def export_long_csv(state, path) -> int:
    """Write every site, timepoint and metric as a long-format CSV (tracker D8); returns the rows written.

    Written to a temporary name and moved into place, so a failed write never leaves half a table under the
    name a person will open.
    """
    if state.metrics is None or state.data is None:
        raise ValueError("There are no nanowell tables loaded to export.")
    table = long_format(state.metrics, state.data.channels)
    table.insert(table.columns.get_loc("time_h") + 1, "time_source", state.data.time_source or "")
    table.insert(table.columns.get_loc("baseline_timepoint"), "baseline", state.baseline)
    path = Path(path)
    tmp = path.with_name(path.name + ".part")
    table.to_csv(tmp, index=False)
    tmp.replace(path)
    return len(table)
