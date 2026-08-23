"""Shared channel-role heuristic: map channel names to analysis roles.

A microscopy dataset names its channels (``DAPI``, ``GFP``, ``NIR`` …) but the
pipeline needs to know each one's *role* — which is the nuclear/segmentation
channel, which are the fluorescent readouts, which is a phase/brightfield frame.
Two entry points guess that mapping so the user doesn't hand-type every token:
the OME-TIFF converter (from the file's channel metadata) and the Analyze page
(from filenames scanned off the input folder). Both call this module so the guess
is identical either way.

The nuclear and phase roles are mutually exclusive: a channel whose name reads as
phase/brightfield is never handed the segmentation role while another channel is
available, and the phase channel remains a member of ``fluor_tokens`` so that
Analyze has a row on which to carry its Phase checkbox.

Stdlib-only, so it stays importable from ``util`` without pulling in Qt or the
feature packages (the same reason ``util.well_token`` / ``util.timepoint`` live
here). Listed in ``services.remote_runner._REQUIRED_REMOTE_FILES`` so the manifest
mirrors every ``util/`` file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CHANNEL_ROLES = ("nuclear", "fluor", "phase", "skip")

#: The segmentation routines whose input *is* the phase channel. Every other
#: routine segments a fluorescent stain.
LABELFREE_METHODS = ("cellpose_labelfree",)

# Names that read as the nuclear/segmentation channel. Two tiers: a *strong*
# nuclear stain (DAPI/Hoechst/…) always wins as the segmentation channel, and a
# *secondary* channel (NIR / RFP) is the best guess only when no strong stain is
# present — common when segmentation runs on a far-red or membrane marker.
_NUCLEAR_STRONG_RE = re.compile(r"dapi|hoechst|nuclear|nucleus|nuc|dna|h33342|405",
                                re.IGNORECASE)
_NUCLEAR_SECONDARY_RE = re.compile(r"nir|rfp", re.IGNORECASE)
# Boundaries on both sides, and neither of them ``\b``. Channel names are
# written as separate tokens ("NIR_BF", "Cy5_BF"), as prefixed run-ons
# ("w1BF", the ImageXpress spelling this repo's own defaults use), and as
# ordinary words that merely start the same way ("GFP_transfected",
# "GFPtrans"). ``\b`` finds no boundary at an underscore, so ``\bbf\b`` never
# matched the commonest spelling of a brightfield channel; a bare ``trans``
# substring, on the other hand, matched a transfection marker and quietly
# excluded it from flat-field and background correction. So: no *letter* may
# precede the hint (which rejects "GFPtrans" and "BFP"), a digit may (which
# accepts "w1BF"), and nothing alphanumeric may follow it.
_PHASE_HINT_RE = re.compile(
    r"phase"
    r"|brightfield|bright.?field"
    r"|(?<![a-z])trans(?:mitted|mission)?(?![a-z0-9])"
    r"|dic"
    r"|(?<![a-z])bf(?![a-z0-9])",
    re.IGNORECASE,
)


def _is_phase_name(name: str) -> bool:
    return bool(_PHASE_HINT_RE.search(name or ""))


def _is_secondary_nuclear_name(name: str) -> bool:
    """True for a NIR/RFP-style name that is *not* also a phase name.

    The two vocabularies overlap: ``NIR_BF`` reads as both a far-red stain and a
    brightfield frame. The phase reading has to win, because the cost of the two
    mistakes is not symmetric — a phase frame handed the segmentation role runs a
    fluorescence routine over an uncorrected brightfield image, and, worse, the
    guard that refuses exactly that (:func:`phase_role_conflicts`) compares
    against ``phase_token``, which stays empty when the name is claimed as
    nuclear instead. So the guard had nothing to compare and returned no
    conflicts. A *strong* stain (DAPI/Hoechst) still wins outright; only this
    weaker tier defers."""
    name = name or ""
    return bool(_NUCLEAR_SECONDARY_RE.search(name)) and not _is_phase_name(name)


def _is_nuclear_name(name: str) -> bool:
    name = name or ""
    return bool(_NUCLEAR_STRONG_RE.search(name) or _is_secondary_nuclear_name(name))


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

    A phase/brightfield-looking name (``DIC``, ``BF``, ``Phase``…) is never taken
    by the secondary tier or by that positional fallback while any other channel
    exists. Segmenting a
    brightfield frame with a fluorescence routine measures nothing, and the
    channel order this runs on comes from a sorted filename listing, so ``DIC``
    lands first often enough that the fallback would keep choosing it. Only when
    *every* channel reads as phase does one of them take the role, because
    segmentation still needs an input.
    """
    strong = [n for n in names if _NUCLEAR_STRONG_RE.search(n or "")]
    if strong:
        return strong[0]
    secondary = [n for n in names if _is_secondary_nuclear_name(n)]
    if secondary:
        return secondary[0]
    non_phase = [n for n in names if not _PHASE_HINT_RE.search(n or "")]
    if non_phase:
        return non_phase[0]
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


def unique_tokens_preserve_order(tokens) -> list[str]:
    """Stable de-dupe for channel tokens, ignoring case and surrounding whitespace."""
    seen: set[str] = set()
    out: list[str] = []
    for tok in tokens:
        cleaned = str(tok or "").strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            out.append(cleaned)
    return out


def quantified_channel_tokens(
    fluor_tokens,
    *,
    nuclear_token: str,
    segmentation_method: str = "",
    cytoplasm_token: str = "",
) -> list[str]:
    """The channels that end up with quantified intensity columns in the CSVs.

    The segmentation channel is quantified like any other and leads the list;
    the cytoplasm channel joins it under seeded watershed. Either may already be
    a member of *fluor_tokens* — always so after the label-free phase remap — so
    the result is de-duplicated case-insensitively, first occurrence winning.

    One definition for three call sites that have to agree: the run's own channel
    list, the ``pipeline_info.json`` sidecar it writes, and the auto-threshold
    pass a batched remote run re-runs locally.
    """
    ordered = [nuclear_token, *fluor_tokens]
    if segmentation_method == "stardist_seeded_watershed_cell":
        ordered.append(cytoplasm_token)
    return unique_tokens_preserve_order(ordered)


def phase_role_conflicts(
    segmentation_method: str,
    *,
    phase_token: str,
    nuclear_token: str,
    cytoplasm_token: str = "",
) -> list[str]:
    """The segmentation slots *phase_token* illegally occupies: ``nuclear``/``cytoplasm``.

    The phase channel skips flat-field and background correction outright, so
    handing it to a fluorescence routine as a segmentation input means running
    that routine over an uncorrected brightfield frame. The run completes and
    writes a full-looking set of CSVs measured on the wrong image, which is why
    this is refused rather than warned about. The label-free routines are exempt:
    segmenting the phase image is what they are for.

    Shared by the Analyze form and ``process_microscopy``'s argument validation
    so the rule has one definition — the CLI and remote runs never reach the
    form's copy.
    """
    phase = str(phase_token or "").strip().lower()
    if not phase or segmentation_method in LABELFREE_METHODS:
        return []
    slots = {"nuclear": nuclear_token}
    if segmentation_method == "stardist_seeded_watershed_cell":
        slots["cytoplasm"] = cytoplasm_token
    return [slot for slot, tok in slots.items()
            if phase == str(tok or "").strip().lower()]


def channel_tokens_from_roles(role_pairs) -> ChannelTokens:
    """Aggregate an ordered ``[(name, role), …]`` list into a :class:`ChannelTokens`.

    Takes the first ``nuclear`` and first ``phase`` (extra picks fall to
    fluorescent); ``skip`` channels are dropped from every role.

    A phase channel **stays in** ``fluor_tokens``, unlike the nuclear one. Both
    consumers require that: the pipeline quantifies the phase channel like any
    other (it only skips its background correction), and Analyze carries the
    Phase designation as a checkbox *on* a fluorescent-channel row, so a
    ``phase_token`` naming a channel outside ``fluor_tokens`` has nowhere to be
    ticked and the designation is lost along with the channel.
    """
    pairs = list(role_pairs)
    nuclear = next((n for n, r in pairs if r == "nuclear"), "")
    phase = next((n for n, r in pairs if r == "phase"), "")
    fluor = [
        n for n, r in pairs
        if n != nuclear and r in ("fluor", "nuclear", "phase")
    ]
    return ChannelTokens(nuclear_token=nuclear, fluor_tokens=fluor, phase_token=phase)
