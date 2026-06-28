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
    # Optional per-channel display colour (the additive tint the Cell Browser
    # paints a channel with). Absent ⇒ the viewer's default look (the Cell
    # Browser's fixed per-index tint). Maps a lower-cased channel token to an
    # ``(r, g, b)`` 0–255 triple.
    tint_by_channel: Dict[str, Tuple[int, int, int]] = field(default_factory=dict)

    def get_lut(self, channel: str) -> Optional[Tuple[float, float]]:
        """Return the shared ``(lo, hi)`` for *channel*, or ``None`` if unset."""
        if not channel:
            return None
        return self.lut_by_channel.get(str(channel).strip().lower())

    def set_lut(self, channel: str, lo: float, hi: float) -> None:
        """Record the shared display window for *channel*."""
        if channel:
            self.lut_by_channel[str(channel).strip().lower()] = (float(lo), float(hi))

    def get_tint(self, channel: str) -> Optional[Tuple[int, int, int]]:
        """Return the per-channel display colour, or ``None`` if unset."""
        if not channel:
            return None
        return self.tint_by_channel.get(str(channel).strip().lower())

    def set_tint(self, channel: str, rgb: Optional[Tuple[int, int, int]]) -> None:
        """Record (or clear, when *rgb* is ``None``) the per-channel colour."""
        if not channel:
            return
        ch = str(channel).strip().lower()
        if rgb is None:
            self.tint_by_channel.pop(ch, None)
        else:
            self.tint_by_channel[ch] = tuple(max(0, min(255, int(c))) for c in rgb)
