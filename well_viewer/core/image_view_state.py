"""Cross-view image-display state.

Shared by the image viewers (Segmentation Review Image, Image Table) so a
channel's display brightness is tuned once and reused as the default
everywhere it's shown — instead of each view keeping its own copy (S4).

Currently this holds only the per-channel display LUT. The Image Table's
*per-row* LUT edits remain an explicit override layered on top of these
defaults; the Cell Browser auto-normalizes each crop and doesn't participate.

Qt-free: imports only stdlib (state-package import contract, ARCHITECTURE §5.9).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@dataclass
class ImageViewState:
    """Display state shared across the image viewers.

    ``lut_by_channel`` maps a lower-cased channel token (e.g. ``"gfp"``) to its
    ``(lo, hi)`` display window. Tuning a channel's brightness in one viewer
    writes here; other viewers read it as the default for that channel.
    """

    lut_by_channel: Dict[str, Tuple[float, float]] = field(default_factory=dict)

    def get_lut(self, channel: str) -> Optional[Tuple[float, float]]:
        """Return the shared ``(lo, hi)`` for *channel*, or ``None`` if unset."""
        if not channel:
            return None
        return self.lut_by_channel.get(str(channel).strip().lower())

    def set_lut(self, channel: str, lo: float, hi: float) -> None:
        """Record the shared display window for *channel*."""
        if channel:
            self.lut_by_channel[str(channel).strip().lower()] = (float(lo), float(hi))
