"""QC-layer typed state.

The live QC (debris-gating) thresholds used to sit on the god-object as the loose
``WellViewerApp._qc_params`` attribute — a nullable cache of a
:class:`~well_viewer.domain.qc_params.QCParams`. ``None`` is meaningful: it means
"not seeded for this dataset yet", so the QC accessor lazily seeds it from the
dataset's ``pipeline_info`` (the run's settings) on first read, and the
persistence loader sets it back to ``None`` when a new dataset has no saved edits.

Moving it into a typed slice (A8 teardown, residuals-ledger R4) removes one loose
god-object attribute and — because the thresholds now live in ``AppState`` — lets
the QC accessor and the ``QC_Pass`` projection be read purely from ``ctx.state``,
which is the prerequisite for a ``ctx``-based row-fetch data service (R1).

This is a **core** slice (a field on ``AppState``), not a feature-owned one: the
inclusion chokepoint (``data_loading.df_included_mask``) composes ``QC_Pass`` on
every read, so the QC layer is always present regardless of which UI features are
enabled.

Qt-free: imports only stdlib and the Qt-free domain model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from well_viewer.domain.qc_params import QCParams


@dataclass
class QcState:
    """Live QC thresholds for the open dataset.

    ``params`` is ``None`` until seeded — the QC accessor
    (``features.qc.controller.get_qc_params``) fills it from the dataset's
    ``pipeline_info`` on first read; the persistence loader resets it to ``None``
    when switching to a dataset that has no saved QC edits.
    """

    params: Optional[QCParams] = None
