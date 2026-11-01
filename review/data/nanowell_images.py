"""A nanowell run's stitched montage, read as a frame any image viewer can show.

A nanowell run's ``<well>_out.zip`` holds no masks, top-hat images or overlays — only one downsampled 8-bit phase
montage per timepoint, ``<well>_<timepoint>_montage.png``: the stitched picture the run actually analysed. The
Image Table offers it as the virtual channel ``MONTAGE`` (``frame_load``); a montage has no field of view, so the
cell's FOV is ignored. Reading it needs no nanowell semantics, which is why it lives here, below every feature, and
works in a build without the Nanowells feature (the montage is an image; what is *in* it is that feature's).

**Nanowell frames.** Three kinds of virtual channel draw a nanowell well as one picture per (well, timepoint), all
in the *same* pixel geometry — the grid record's ``montage_origin_um`` and ``montage_um_per_px``, over the stored
montage's size — so one crop is one region of the well in every row, and a metric row lines up pixel for pixel with
the montage beneath it:

* ``MONTAGE`` — the run's phase montage;
* ``MONTAGE <CH>`` — the same stitch drawn from *CH*'s raw tiles (the run keeps no fluorescence montage);
* ``NW: <metric label>`` — the well's nanowells, each disc filled with its value of that per-nanowell metric; a
  fold change may pin its baseline, ``NW: <label> @<baseline>`` (:func:`metric_token`), and otherwise follows the
  Nanowells page's.

The second and third need the Nanowells feature — where each tile went and what each nanowell's metric is are its
semantics — and are drawn by the registry service ``image_table.nanowell_frame``, which answers a
:class:`NanowellFrame`; this module only names the tokens and holds the type. A build without the feature offers
neither, and draws ``MONTAGE`` from the stored picture with no known scale.

Qt-free.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from domain.tp_match import tp_close

MONTAGE_SUFFIX = "_montage.png"

#: The phase montage's token; a fluorescence montage is ``MONTAGE <CH>``.
PHASE_MONTAGE = "MONTAGE"
#: A per-nanowell metric's token is this prefix and the metric's label (``ObservationMetric.label``).
METRIC_PREFIX = "NW: "
#: A metric map's default colormap: its lowest value is not black, which is what a frame draws where nothing is.
METRIC_MAP_COLORMAP = "viridis"


@dataclass
class NanowellFrame:
    """One nanowell frame, in the montage's geometry.

    *values* is float32, rows × columns, NaN wherever nothing was imaged or no nanowell is. *undefined* marks the
    pixels of nanowells that are there but have no value of the metric (``None`` for a montage) — each one's rim,
    so a nanowell with no value is drawn as an empty ring: never as background, and never as a filled disc that
    could be read as a value. *um_per_px* is the frame's own scale (``None`` when unknown), which
    a scale bar must use rather than the tiles' pixel size. *note* says what the frame leaves out, or why there is
    no frame (then *values* is ``None``)."""
    values: np.ndarray | None
    um_per_px: float | None = None
    undefined: np.ndarray | None = None
    note: str = ""
    #: A metric map's nanowells with no value, by reason (``{"no cells": 34, "occluded": 2}``), and how many
    #: nanowells it drew: what the status line sums across wells rather than repeating per well.
    missing: dict = field(default_factory=dict)
    total: int = 0


def montage_token(channel: str = "") -> str:
    """The token of *channel*'s montage; the phase montage's without one."""
    return f"{PHASE_MONTAGE} {channel.strip().upper()}" if channel.strip() else PHASE_MONTAGE


def montage_channel(token) -> str | None:
    """``""`` for the phase montage's token, the lower-cased channel of ``MONTAGE <CH>``, or ``None``."""
    text = str(token or "").strip()
    if text.upper() == PHASE_MONTAGE:
        return ""
    head = PHASE_MONTAGE + " "
    if text.upper().startswith(head) and text[len(head):].strip():
        return text[len(head):].strip().lower()
    return None


#: Between a metric's label and the baseline its fold change is taken from, in a token: ``NW: Count fold change
#: @tp:0h``. A baseline is the Nanowells page's own code — ``earliest``, ``first_measurable`` or ``tp:<timepoint>`` —
#: so the reader hands it to the page's metrics unchanged.
BASELINE_SEP = " @"
#: The two baselines that are rules rather than timepoints, as the page names them.
BASELINE_RULES = {"earliest": "earliest", "first_measurable": "first measurable"}
_TIMEPOINT_BASELINE = "tp:"


def metric_token(label: str, baseline: str | None = None) -> str:
    """The token of the metric *label*, with its fold change taken from *baseline* when one is given (``None``: the
    Nanowells page's own Baseline, whatever it is when the cell is drawn)."""
    token = f"{METRIC_PREFIX}{label}"
    return f"{token}{BASELINE_SEP}{baseline}" if baseline else token


def _metric_parts(token) -> tuple[str, str | None] | None:
    text = str(token or "").strip()
    if not text.upper().startswith(METRIC_PREFIX.upper()):
        return None
    rest = text[len(METRIC_PREFIX):].strip()
    baseline = None
    if BASELINE_SEP in rest:
        rest, baseline = (part.strip() for part in rest.rsplit(BASELINE_SEP, 1))
    return (rest, baseline or None) if rest else None


def metric_label(token) -> str | None:
    """The metric label a ``NW: <label>`` token names (as written, compared case-insensitively by the reader),
    without the baseline a fold change may carry."""
    parts = _metric_parts(token)
    return None if parts is None else parts[0]


def metric_baseline(token) -> str | None:
    """The baseline code a metric token pins its fold change to, or ``None`` — the page's own Baseline, or not a
    metric token. A code is matched case-insensitively for the rules, as written for a timepoint."""
    parts = _metric_parts(token)
    if parts is None or parts[1] is None:
        return None
    code = parts[1]
    return code.lower() if code.lower() in BASELINE_RULES else code


def baseline_timepoint_code(timepoint: str) -> str:
    """The baseline code that takes every fold change from *timepoint* (the run's own label)."""
    return f"{_TIMEPOINT_BASELINE}{timepoint}"


def baseline_text(code) -> str:
    """How a label names a baseline code: ``earliest``, ``first measurable``, or the timepoint's own name."""
    text = str(code or "")
    if text.lower().startswith(_TIMEPOINT_BASELINE):
        return text[len(_TIMEPOINT_BASELINE):]
    return BASELINE_RULES.get(text.lower(), text)


def is_nanowell_frame(token) -> bool:
    """Whether *token* is one of the three nanowell frames (a montage of any channel, or a metric map)."""
    return montage_channel(token) is not None or metric_label(token) is not None


def is_metric_map(token) -> bool:
    return metric_label(token) is not None


def short_label(label: str) -> str:
    """A metric label without its explanation — ``"GFP Brightness (mean per-cell intensity)"`` →
    ``"GFP Brightness"``, ``"log₂ count fold change, (n + 1) / (n₀ + 1)"`` → ``"log₂ count fold change"`` — for a
    row label or a chip, where the full label (kept for the tooltip and the colour key) took a quarter of a figure's
    width."""
    text = str(label or "").strip()
    cuts = [text.find(stop) for stop in (" (", ", ") if stop in text]
    if cuts:
        text = text[:min(cuts)]
    return text.strip() or str(label or "").strip()


def display_token(token) -> str:
    """How an axis, a chip or a title names a channel token: a nanowell frame by its short, case-kept name (a
    metric's label is prose with symbols in it — ``log₂``, ``n₀`` — that upper-casing breaks), anything else
    upper-cased as the Image Table always has."""
    text = str(token or "").strip()
    label = metric_label(text)
    if label is not None:
        baseline = metric_baseline(text)
        tail = f" vs {baseline_text(baseline)}" if baseline else ""
        return f"{METRIC_PREFIX}{short_label(label)}{tail}"
    channel = montage_channel(text)
    if channel:
        return f"{PHASE_MONTAGE} {channel.upper()}"
    return text.upper()


def _hours(token) -> float:
    from pipeline.nanowell_task import timepoint_hours

    try:
        return timepoint_hours(token)
    except (TypeError, ValueError):
        return float("nan")


def _same_timepoint(label: str, tp) -> bool:
    if str(label) == str(tp):
        return True
    a, b = _hours(label), _hours(tp)
    return a == a and b == b and tp_close(a, b)


def montage_member(names, well: str, tp) -> str | None:
    """The archive member holding *well*'s montage at timepoint *tp* (label or hours), or ``None``."""
    prefix = f"{well}_"
    for name in names:
        base = name.rsplit("/", 1)[-1]
        if base.startswith(prefix) and base.endswith(MONTAGE_SUFFIX):
            label = base[len(prefix):-len(MONTAGE_SUFFIX)]
            if _same_timepoint(label, tp):
                return name
    return None


def read_montage_array(out_dir, well: str, tp) -> tuple[np.ndarray | None, str]:
    """``(8-bit greyscale montage, "")`` for *well* at *tp*, or ``(None, why not)``."""
    from PIL import Image

    archive = Path(out_dir) / f"{well}_out.zip"
    try:
        with zipfile.ZipFile(archive) as zf:
            member = montage_member(zf.namelist(), well, tp)
            if member is None:
                return None, f"{archive.name} holds no montage for {tp}."
            with Image.open(io.BytesIO(zf.read(member))) as im:
                return np.asarray(im.convert("L")), ""
    except FileNotFoundError:
        return None, f"{archive.name} is not in the output folder."
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        return None, f"The montage could not be read ({exc})."
