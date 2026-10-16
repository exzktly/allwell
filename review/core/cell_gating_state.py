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
  writes this slice (see ``cell_gating.controller._cell_gating_sync_gate_state``);
- the accessors read this slice, so gate values exist independent of whether
  the (lazily-built) Cell Gating tab exists;
- persistence serialises this slice, not widget text.

Keys are the Cell Gating row keys: bare fluor-channel names (``"gfp"``) and
full ratio keys (``"ratio:<name>"``). An absent key means the default
(FluorGating 0.0 = gate off; ThreshFracOn 50.0 for a readout channel and 0.0 for
the nuclear one; ThreshCountOn -1.0) — encoded in the accessors, not here, so
"never set" stays distinguishable from "set to the default value".

This is a **core** slice (a field on ``AppState``), like ``QcState``: the gate
values feed the core channel/threshold layer consumed by every plot, stat, and
export, regardless of which UI features are enabled.

Qt-free: stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CellGatingState:
    """Per-channel gating values + scatter-gate config keyed by Cell Gating row.

    ``fluor_gates`` — FluorGating inclusion thresholds (0.0 = gate off).
    ``thresh_frac_on`` — ThreshFracOn fraction-on cutoffs (default 50.0).
    ``thresh_count_on`` — ThreshCountOn cutoffs for the integer *count* metrics
      (smFISH spot tallies), default -1.0 so every cell including the zero-count
      ones is in the ON population. Separate from ``thresh_frac_on`` because the
      two answer questions on different scales: "how bright before a cell is ON"
      (an intensity in the hundreds) against "how many spots before a cell is ON"
      (an integer, a handful per cell). One value cannot serve both, and the
      intensity cutoff sits above the whole range a tally can occupy.

    Scatter-gate configuration (Epic-cellgating — persisted config that used to
    live as loose ``app._scatter_gates`` / ``app._cell_gating_*`` god-object
    attributes, read via ``ctx.runtime`` stamps):

    ``scatter_gates`` — the rectangular gates drawn on the Scatter Plot, each a
      ``{x0,y0,x1,y1,x_col,y_col,name,...}`` dict. Geometry only; persisted.
    ``use_scatter_gates`` — inclusion mode: the selected gate(s) define the
      Included population instead of the per-channel FluorGating floors.
    ``use_scatter_thresh`` — thresholding mode: gate membership defines the
      fraction-on ("on") population instead of the ThreshFracOn cutoff.
    ``gating_choices`` / ``thresh_choices`` — each layer's own gate selection, a
      set of gate names, or ``None`` ⇒ "all gates" (auto-includes gates drawn
      later). An explicit (possibly empty) set selects exactly those names.
    """

    fluor_gates: dict[str, float] = field(default_factory=dict)
    thresh_frac_on: dict[str, float] = field(default_factory=dict)
    thresh_count_on: dict[str, float] = field(default_factory=dict)
    scatter_gates: list[dict] = field(default_factory=list)
    use_scatter_gates: bool = False
    use_scatter_thresh: bool = False
    gating_choices: set[str] | None = None
    thresh_choices: set[str] | None = None
    #: The configuration the last gating pass applied (``GatingWorker``), as the
    #: keyword arguments of ``gating_compute.apply_fluor_gating``; ``None`` until a
    #: pass has run on this dataset. The row cache is bounded, so a well can be
    #: evicted after the pass wrote its inclusion columns and read again from
    #: disk — the gating projector re-applies exactly this to it, which is what
    #: keeps a reloaded well from silently counting every cell. A snapshot rather
    #: than the live settings, so a reloaded well agrees with the wells the pass
    #: wrote even while an edit's own pass is still pending.
    applied_gating: dict | None = None
