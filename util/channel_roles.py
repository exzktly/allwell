"""Shared channel-role heuristic: map channel names to analysis roles.

A microscopy dataset names its channels (``DAPI``, ``GFP``, ``NIR`` …) but the
pipeline needs to know each one's *role* — which is the nuclear/segmentation
channel, which are the fluorescent readouts, which is a phase/brightfield frame.
Two entry points guess that mapping so the user doesn't hand-type every token:
the OME-TIFF converter (from the file's channel metadata) and the Analyze page
(from filenames scanned off the input folder). Both call this module so the guess
is identical either way.

Stdlib-only, so it stays importable from ``util`` without pulling in Qt or the
feature packages (the same reason ``util.well_token`` / ``util.timepoint`` live
here). Listed in ``services.remote_runner._REQUIRED_REMOTE_FILES`` so the manifest
mirrors every ``util/`` file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CHANNEL_ROLES = ("nuclear", "fluor", "phase", "skip")

# Names that read as the nuclear/segmentation channel. Two tiers: a *strong*
# nuclear stain (DAPI/Hoechst/…) always wins as the segmentation channel, and a
# *secondary* channel (NIR / RFP) is the best guess only when no strong stain is
# present — common when segmentation runs on a far-red or membrane marker.
_NUCLEAR_STRONG_RE = re.compile(r"dapi|hoechst|nuclear|nucleus|nuc|dna|h33342|405",
                                re.IGNORECASE)
_NUCLEAR_SECONDARY_RE = re.compile(r"nir|rfp", re.IGNORECASE)
_PHASE_HINT_RE = re.compile(r"phase|brightfield|bright.?field|trans|dic|\bbf\b",
                            re.IGNORECASE)


def _is_nuclear_name(name: str) -> bool:
    name = name or ""
    return bool(_NUCLEAR_STRONG_RE.search(name) or _NUCLEAR_SECONDARY_RE.search(name))


@dataclass
class ChannelTokens:
    """The channel role assignment, in Analyze's ``pipeline_info`` vocabulary."""

    nuclear_token: str = ""
    fluor_tokens: list[str] = field(default_factory=list)
    phase_token: str = ""

    def as_pipeline_info(self) -> dict:
        """A minimal ``pipeline_info``-shaped dict for ``_apply_pipeline_info``."""
        d: dict = {"fluor_tokens": list(self.fluor_tokens)}
        if self.nuclear_token:
            d["nuclear_token"] = self.nuclear_token
        if self.phase_token:
            d["phase_token"] = self.phase_token
        return d


def guess_channel_role(name: str) -> str:
    """The best-guess role for a single channel *name* (``nuclear``/``phase``/``fluor``)."""
    if _is_nuclear_name(name):
        return "nuclear"
    if _PHASE_HINT_RE.search(name or ""):
        return "phase"
    return "fluor"


def _pick_nuclear(names: list[str]) -> str | None:
    """The one channel to assign the nuclear/segmentation role, or None.

    A strong nuclear stain wins over a secondary (NIR/RFP) channel regardless of
    order; with neither, the first channel is used so segmentation always has one.
    """
    strong = [n for n in names if _NUCLEAR_STRONG_RE.search(n or "")]
    if strong:
        return strong[0]
    secondary = [n for n in names if _NUCLEAR_SECONDARY_RE.search(n or "")]
    if secondary:
        return secondary[0]
    return names[0] if names else None


def default_channel_roles(names) -> dict[str, str]:
    """Heuristic role per channel token, keyed by name, with exactly one nuclear.

    Segmentation needs a nuclear channel; :func:`_pick_nuclear` chooses it
    (strongest nuclear name, else first channel) and every other channel takes its
    per-name guess (never nuclear). The caller can override any of it.
    """
    names = list(names)
    chosen = _pick_nuclear(names)
    roles: dict[str, str] = {}
    for n in names:
        if n == chosen:
            roles[n] = "nuclear"
        else:
            guess = guess_channel_role(n)
            roles[n] = "fluor" if guess == "nuclear" else guess
    return roles


def channel_tokens_from_roles(role_pairs) -> ChannelTokens:
    """Aggregate an ordered ``[(name, role), …]`` list into a :class:`ChannelTokens`.

    Takes the first ``nuclear`` and first ``phase`` (extra picks fall to
    fluorescent); ``skip`` channels are dropped from every role.
    """
    pairs = list(role_pairs)
    nuclear = next((n for n, r in pairs if r == "nuclear"), "")
    phase = next((n for n, r in pairs if r == "phase"), "")
    used_singletons = {nuclear, phase} - {""}
    fluor = [
        n for n, r in pairs
        if n not in used_singletons and r in ("fluor", "nuclear", "phase")
    ]
    return ChannelTokens(nuclear_token=nuclear, fluor_tokens=fluor, phase_token=phase)
