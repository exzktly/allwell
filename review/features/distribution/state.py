"""Distribution tab domain state.

Per-feature state for the Distribution tab — the histogram bin count and the
two log-axis toggles that used to live on the god-object as
``app._distribution_bins`` / ``_distribution_log_x`` / ``_distribution_log_y``
(A8 state migration). These are persisted per view by
``persistence/view_state.py``.

Qt-free: imports only stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DistributionState:
    """Distribution feature-local display state.

    ``bins`` — number of histogram bins. ``log_x`` / ``log_y`` — put the
    respective axis on a log scale.
    """

    bins: int = 40
    log_x: bool = False
    log_y: bool = False
