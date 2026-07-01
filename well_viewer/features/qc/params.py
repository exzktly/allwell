"""Compatibility re-export — the QC verdict model now lives in ``domain/``.

The QC parameter dataclass (:class:`QCParams`) and the pure verdict function
(:func:`compute_qc_pass`) are Qt-free, dependency-light business logic, so they
were relocated to :mod:`well_viewer.domain.qc_params` (the foundational layer).
This keeps the ``persistence`` layer able to (de)serialize ``QCParams`` with a
*downward* import instead of reaching up into ``features`` — see
``tests/test_layering.py``.

Existing ``from well_viewer.features.qc.params import …`` call sites keep
working through this shim.
"""

from well_viewer.domain.qc_params import (  # noqa: F401
    AREA_COLS,
    REGION_AREA_COL,
    SOLIDITY_COL,
    QCParams,
    compute_qc_pass,
)
