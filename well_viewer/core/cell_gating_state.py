"""Cell-gating threshold values — typed state (A8 teardown, R6).

The per-channel **FluorGating** inclusion thresholds and **ThreshFracOn**
fraction-on cutoffs used to live *on Qt widgets*: the Cell Gating tab stashed
``{channel_key → QLineEdit}`` maps on the god-object and every consumer — the
core channel layer (``get_all_fluor_gates`` / ``get_thresh_frac_on``), the CDF
plot, persistence, saved-plots capture — read values via ``edit.text()``.

This slice makes the *values* typed state and demotes the widgets to an
input/view:

- every path that changes an edit's text funnels through the two
  ``editingFinished`` handlers or the persistence loader, and each of those now
  writes this slice (see ``cell_gating_tab_view._cell_gating_sync_gate_state``);
- the accessors read this slice, so gate values exist independent of whether
  the (lazily-built) Cell Gating tab exists;
- persistence serialises this slice, not widget text.

Keys are the Cell Gating row keys: bare fluor-channel names (``"gfp"``) and
full ratio keys (``"ratio:<name>"``). An absent key means the historical
defaults (FluorGating 0.0 = gate off; ThreshFracOn 50.0) — encoded in the
accessors, not here, so "never set" stays distinguishable from "set to the
default value".

This is a **core** slice (a field on ``AppState``), like ``QcState``: the gate
values feed the core channel/threshold layer consumed by every plot, stat, and
export, regardless of which UI features are enabled.

Qt-free: stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class CellGatingState:
    """Per-channel gating values keyed by Cell Gating row key.

    ``fluor_gates`` — FluorGating inclusion thresholds (0.0 = gate off).
    ``thresh_frac_on`` — ThreshFracOn fraction-on cutoffs (default 50.0).
    """

    fluor_gates: Dict[str, float] = field(default_factory=dict)
    thresh_frac_on: Dict[str, float] = field(default_factory=dict)
