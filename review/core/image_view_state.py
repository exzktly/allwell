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


@dataclass
class ImageViewState:
    """Display state shared across the image viewers.

    ``lut_by_channel`` maps a lower-cased channel token (e.g. ``"gfp"``) to its
    ``(lo, hi)`` display window. Tuning a channel's brightness in one viewer
    writes here; other viewers read it as the default for that channel.
    """

    lut_by_channel: dict[str, tuple[float, float]] = field(default_factory=dict)
    # Optional per-channel display colour (the additive tint the Cell Browser
    # paints a channel with). Absent ⇒ the viewer's default look (the Cell
    # Browser's fixed per-index tint). Maps a lower-cased channel token to an
    # ``(r, g, b)`` 0–255 triple.
    tint_by_channel: dict[str, tuple[int, int, int]] = field(default_factory=dict)

    def get_lut(self, channel: str) -> tuple[float, float] | None:
        """Return the shared ``(lo, hi)`` for *channel*, or ``None`` if unset."""
        if not channel:
            return None
        return self.lut_by_channel.get(str(channel).strip().lower())

    def set_lut(self, channel: str, lo: float, hi: float) -> None:
        """Record the shared display window for *channel*."""
        if channel:
            self.lut_by_channel[str(channel).strip().lower()] = (float(lo), float(hi))

    def get_tint(self, channel: str) -> tuple[int, int, int] | None:
        """Return the per-channel display colour, or ``None`` if unset."""
        if not channel:
            return None
        return self.tint_by_channel.get(str(channel).strip().lower())

    def set_tint(self, channel: str, rgb: tuple[int, int, int] | None) -> None:
        """Record (or clear, when *rgb* is ``None``) the per-channel colour."""
        if not channel:
            return
        ch = str(channel).strip().lower()
        if rgb is None:
            self.tint_by_channel.pop(ch, None)
        else:
            self.tint_by_channel[ch] = tuple(max(0, min(255, int(c))) for c in rgb)

    def reset_for_new_dataset(self) -> None:
        """Forget the per-channel display windows and tints.

        A LUT is a pair of intensity values, and intensities are a property of
        the plate that produced them. Carrying a bright plate's window onto a
        dim one renders the new plate black, and carrying a dim plate's window
        onto a bright one blows it out — in both cases with the histogram
        controls showing the numbers, so it reads as the data rather than as a
        stale setting. Neither dict is persisted, so nothing else clears them.
        """
        self.lut_by_channel = {}
        self.tint_by_channel = {}
