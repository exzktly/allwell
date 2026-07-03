"""smFISH tab domain state.

Per-feature scalar state for the smFISH tab — the filename token separator, the
overlay-visibility toggle, and the one-shot "fit view on next redraw" latch that
used to live on the god-object as ``app._smfish_separator`` /
``_smfish_show_overlays`` / ``_smfish_fit_on_next_redraw`` (A8 state migration).

Only the flat scalars move here. The runtime objects — worker bridge, extractor,
classifier, token/zip maps, current label/image arrays, matplotlib figures and
CDF-popup handles — stay on the app; they are not typed-state candidates.

Qt-free: imports only stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SmfishState:
    """smFISH feature-local scalar state.

    ``separator`` — the character splitting FOV/timepoint tokens in filenames.
    ``show_overlays`` — draw the segmentation overlays on the preview.
    ``fit_on_next_redraw`` — one-shot latch: refit the view extent on the next
    redraw (set after a load, cleared once consumed).
    """

    separator: str = "_"
    show_overlays: bool = True
    fit_on_next_redraw: bool = True
