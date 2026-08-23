"""Save / load Cell Gating parameters inside ``pipeline_info.json``.

The pipeline writes pipeline_info.json as part of every analysis run. This
module overlays user-curated cell gating thresholds onto that file without
disturbing the pipeline-owned keys: it reads the existing JSON, sets /
replaces the ``cell_gating`` block (or removes it when every value is back
at its default), and writes the file back.

Schema written under the ``cell_gating`` key:

    {
        "fluor_gates":      {"gfp": 50.0, "mcherry": 0.0},
        "thresh_frac_on":   {"gfp": 60.0, "mcherry": 50.0},
        "thresh_count_on":  {"w3cy5": 4.0},
        "use_scatter_gates": true,
        "scatter_gates":    [{"x0": .., "y0": .., "x1": .., "y1": ..,
                              "x_col": "gfp_mean_intensity",
                              "y_col": "mcherry_mean_intensity"}],
    }

``thresh_frac_on`` is the ON cutoff for the fluorescence-intensity metrics;
``thresh_count_on`` is the separate one for the integer count metrics (smFISH
spot tallies). They are different rows because they are answers to questions on
different scales — "how bright before a cell is ON" against "how many spots
before a cell is ON" — and a single value cannot serve both.

Only non-default values are persisted; channels at default are omitted. The
``scatter_gates`` / ``use_scatter_gates`` pair is written only when the
scatter-gate inclusion mode is on and at least one gate exists.
When every parameter is at default, the block is removed entirely so the
sidecar stays tidy.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

from well_viewer.persistence._io import atomic_write_json
from well_viewer.domain.viewer_state import resolve_pipeline_info_path


_logger = logging.getLogger("well_viewer.domain.gating_state")

PIPELINE_INFO_FILENAME = "pipeline_info.json"
CELL_GATING_KEY = "cell_gating"


def pipeline_info_available(out_dir: Path) -> bool:
    """``True`` when *out_dir* has a writable pipeline_info.json sidecar."""
    return _resolve_pipeline_info_path(out_dir).exists()

DEFAULT_FLUOR_GATE = 0.0
DEFAULT_THRESH_FRAC_ON = 50.0
DEFAULT_THRESH_FRAC_ON_NUCLEAR = 0.0
DEFAULT_THRESH_COUNT_ON = -1.0

_EPS = 1e-9


def default_thresh_frac_on(channel: str, seg_channel_token: str = "") -> float:
    """The ON/OFF intensity cutoff *channel* starts at when nothing is saved.

    A readout channel starts at :data:`DEFAULT_THRESH_FRAC_ON`: "how bright must
    a cell be before it counts as ON" is a question about a fluorescence
    measurement, and a non-zero starting cut is the useful answer.

    The nuclear channel — the segmentation channel recorded as ``nuclear_token``
    in ``pipeline_info.json``, the one nuclei are *found* with — starts at
    :data:`DEFAULT_THRESH_FRAC_ON_NUCLEAR` (0.0, i.e. no cut). An ON/OFF
    brightness cut carries no meaning there: every segmented cell is by
    construction a detection in that channel, so the readout default only served
    to hide most nuclei from the Image Table's threshold / binary view.

    A value the user has actually set is unaffected — this is consulted only
    when a channel has no stored cutoff. Matching is case-insensitive on the
    normalised channel token; ratio keys (``ratio:<name>``) never match.
    """
    seg = str(seg_channel_token or "").strip().lower()
    chan = str(channel or "").strip().lower()
    if seg and chan == seg:
        return DEFAULT_THRESH_FRAC_ON_NUCLEAR
    return DEFAULT_THRESH_FRAC_ON


def default_thresh_count_on(channel: str = "") -> float:
    """The ON cutoff a *count* metric (an smFISH spot tally) starts at.

    :data:`DEFAULT_THRESH_COUNT_ON` sits below the minimum a tally can take. A
    cell is "on" when ``count > cutoff``, so -1.0 admits every cell including the
    zero-spot ones. Zero spots is a measurement — the cell was imaged, segmented,
    and found to carry no transcripts — and dropping those cells raises every
    reported mean and shrinks every N.

    This is the same convention the shape/morphology features use, and for the
    same reason: neither is a fluorescence intensity, so
    :data:`DEFAULT_THRESH_FRAC_ON` is not merely the wrong number for them, it is
    above the entire range the data can occupy and empties the plot.

    Unlike :func:`default_thresh_frac_on` the answer does not depend on which
    channel is asking — no channel's spot tally wants an intensity-scaled floor,
    and the segmentation channel carries no count column at all. The parameter is
    kept so the two defaults are interchangeable in the diff-against-default
    loops that decide what reaches the sidecar.

    A value the user has actually set is unaffected: this is consulted only when
    a channel has no stored count cutoff, and "the fraction of cells carrying more
    than N spots" stays a real question the Cell Gating tab can be asked.
    """
    return DEFAULT_THRESH_COUNT_ON


# The sidecar lives at the dataset root even when the data dir is its
# ``out/`` child, so save and load have to agree on the fallback.
_resolve_pipeline_info_path = resolve_pipeline_info_path


def _is_default(value: float, default: float) -> bool:
    try:
        return math.isclose(float(value), float(default), abs_tol=_EPS)
    except (TypeError, ValueError):
        return True


def build_gating_block(
    fluor_gates: dict[str, float],
    thresh_frac_on: dict[str, float],
    scatter_gates: list | None = None,
    use_scatter_gates: bool = False,
    scatter_gate_choices_gating: list | None = None,
    scatter_gate_choices_thresh: list | None = None,
    use_scatter_thresh: bool = False,
    seg_channel_token: str = "",
    thresh_count_on: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Snapshot non-default gating values as a JSON-friendly dict.

    Returns an empty dict when every value is at its default; callers can
    use that to decide whether to remove the block from the sidecar.

    ``scatter_gates`` (the rectangular gates drawn on the Scatter Plot, each a
    ``{x0,y0,x1,y1,x_col,y_col}`` dict) and ``use_scatter_gates`` are persisted
    only when the inclusion mode is on *and* gates exist — unlike the scalar
    thresholds there is no "default" value to diff against, so the on-mode flag
    is the gate that keeps default state out of the sidecar.

    ``seg_channel_token`` is the dataset's nuclear/segmentation channel, whose
    ThreshFracOn default differs (see :func:`default_thresh_frac_on`). It has to
    be known here or the diff-against-default would strip the wrong values:
    a nuclear cutoff the user deliberately raised to the readout default would
    be dropped, and the zero it starts at would be written out as if it were a
    deliberate choice.

    ``thresh_count_on`` carries the same shape for the *count* metrics, and needs
    no such channel knowledge: :func:`default_thresh_count_on` answers the same
    for every channel.
    """
    block: dict[str, Any] = {}
    fg_out: dict[str, float] = {}
    for ch, val in (fluor_gates or {}).items():
        if not _is_default(val, DEFAULT_FLUOR_GATE):
            fg_out[str(ch)] = float(val)
    if fg_out:
        block["fluor_gates"] = fg_out

    tfo_out: dict[str, float] = {}
    for ch, val in (thresh_frac_on or {}).items():
        if not _is_default(val, default_thresh_frac_on(ch, seg_channel_token)):
            tfo_out[str(ch)] = float(val)
    if tfo_out:
        block["thresh_frac_on"] = tfo_out

    tco_out: dict[str, float] = {}
    for ch, val in (thresh_count_on or {}).items():
        if not _is_default(val, default_thresh_count_on(ch)):
            tco_out[str(ch)] = float(val)
    if tco_out:
        block["thresh_count_on"] = tco_out

    # Persist the gates when EITHER scatter-gate layer (inclusion / fraction-on
    # thresholding) is on — both consume the same selected gate(s).
    if (use_scatter_gates or use_scatter_thresh) and scatter_gates:
        block["scatter_gates"] = [dict(g) for g in scatter_gates]
        # Per-layer selected gates (by name); omitted ⇒ all gates (union). An
        # explicit (possibly empty) list selects exactly those names.
        if scatter_gate_choices_gating is not None:
            block["scatter_gate_choices_gating"] = [str(n) for n in scatter_gate_choices_gating]
        if scatter_gate_choices_thresh is not None:
            block["scatter_gate_choices_thresh"] = [str(n) for n in scatter_gate_choices_thresh]
        if use_scatter_gates:
            block["use_scatter_gates"] = True
        if use_scatter_thresh:
            block["use_scatter_thresh"] = True

    return block


def save_gating_to_pipeline_info(
    out_dir: Path,
    gating_block: dict[str, Any],
) -> Path | None:
    """Merge ``gating_block`` into the pipeline_info.json sidecar.

    When ``gating_block`` is empty, removes any existing ``cell_gating``
    key so default-valued state is not persisted.

    Returns the written path, or ``None`` when there is no pipeline_info.json
    to update or the write failed. Callers wanting to distinguish the two
    no-write cases can call :func:`pipeline_info_available` first.
    """
    info_path = _resolve_pipeline_info_path(out_dir)
    if not info_path.exists():
        _logger.warning(
            "pipeline_info.json not found at %s — gating thresholds will not "
            "be persisted across sessions for this dataset.", info_path,
        )
        return None
    try:
        existing = json.loads(info_path.read_text())
        if not isinstance(existing, dict):
            _logger.warning("%s is not a JSON object; not updating.", info_path)
            return None
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("Could not read %s: %s", info_path, exc)
        return None

    if gating_block:
        existing[CELL_GATING_KEY] = gating_block
    else:
        existing.pop(CELL_GATING_KEY, None)

    try:
        atomic_write_json(info_path, existing)
    except OSError as exc:
        _logger.warning("Could not write %s: %s", info_path, exc)
        return None
    return info_path


def read_gating_params(out_dir: Path) -> dict[str, Any] | None:
    """Return the saved cell_gating block, or None when absent."""
    info_path = _resolve_pipeline_info_path(out_dir)
    if not info_path.exists():
        return None
    try:
        data = json.loads(info_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("Could not read %s: %s", info_path, exc)
        return None
    if not isinstance(data, dict):
        return None
    block = data.get(CELL_GATING_KEY)
    if not isinstance(block, dict):
        return None
    return block

#: The value a FluorGating floor takes when it is **off**. ``get_fluor_gate``
#: defaults an unset channel to it, and the Cell Gating tab's own docstring
#: calls it "gate off".
FLUOR_GATE_OFF = 0.0


def active_fluor_gates(fluor_gates) -> dict[str, float]:
    """The subset of *fluor_gates* that is actually gating anything.

    A gate of ``0.0`` means "off", but all six filter sites applied it anyway as
    ``isfinite(v) & (v > gate)`` — so a plate at its default gates still lost
    every cell whose background-subtracted mean intensity was zero or negative,
    and every cell carrying a NaN in any *unrelated* gated channel. Those cells
    are not excluded by any choice the user made, and nothing reported their
    removal; the population simply differed from the one on the CSV.

    A negative floor is left active: background-subtracted channels legitimately
    run below zero, so a user who types one means it. Only the default-off value
    is skipped.
    """
    return {
        str(channel): float(gate)
        for channel, gate in (fluor_gates or {}).items()
        if float(gate) != FLUOR_GATE_OFF
    }
