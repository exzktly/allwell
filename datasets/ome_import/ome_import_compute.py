"""Qt-free planning + validation for the OME-TIFF converter.

Reads the OME layout (via :mod:`pipeline.ome_explode`) and resolves the user's
well-grid, FOVs-per-well and frame-order into a :class:`ConversionPlan` — the
same split maths the explode uses, surfaced ahead of the run so the page can show
a live readout and block an inconsistent request before any file is written.

No Qt, no file writing — so it is unit-testable without an event loop, and the
page/worker layer stays a thin shell on top (CLAUDE.md §3/§5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.ome_explode import (
    FRAME_ORDERS,
    OmeLayout,
    read_ome_layout,
)
from util.plate_grid import normalize_well_order, serpentine_well_labels

_OME_SUFFIXES = (".ome.tif", ".ome.tiff")


class OmeImportError(Exception):
    """A user-facing reason the OME-TIFF can't be converted with these settings."""


@dataclass(frozen=True)
class ConversionParams:
    """The user's de-interleave settings from the converter page."""

    grid_rows: int
    grid_cols: int
    origin: str = "A01"
    direction: str = "row"
    fovs_per_well: int | None = None      # None = derive
    frame_order: str = "single"           # single-image files only
    experiment: str = ""                  # blank = derive from the filename
    workers: int = 0                      # 0 = auto (per-well thread pool)
    # An explicit acquisition ordering (click-defined on the plate mockup). When
    # non-empty it replaces the rectangular serpentine: wells are visited in this
    # order and the grid fields only seed it. May be non-rectangular.
    well_order: tuple[str, ...] = ()


@dataclass
class ConversionPlan:
    """The resolved shape of a conversion, for the readout and a sanity gate."""

    single_image: bool
    n_positions: int
    n_timepoints: int
    fovs_per_well: int
    well_labels: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)

    @property
    def n_wells(self) -> int:
        return len(self.well_labels)

    def summary(self) -> str:
        chans = ", ".join(self.channels) if self.channels else "?"
        return (
            f"{self.n_wells} well(s) × {self.fovs_per_well} FOV(s) × "
            f"{self.n_timepoints} timepoint(s) · channels: {chans}"
        )


@dataclass
class SelectionTarget:
    """How many wells the file's metadata expects you to place, for the readout.

    The OME metadata fixes a total count — stage *positions* (multi-image) or
    *frames* (single-image) — and ``positions = wells × FOVs`` (a single-image
    file with timepoints folds those in too, so the position count isn't knowable
    from metadata until the frame-order splits them out). This carries that count
    and, when FOVs-per-well is pinned, the well count it implies, so the page can
    tell the user up front how many wells to select rather than only erroring at
    the end.
    """

    positions: int | None    # total stage positions the file holds, if knowable
    noun: str                # "stage position" | "frame" — for the wording
    fovs_fixed: int | None   # user-pinned FOVs/well, else None (auto/derive)

    @property
    def expected_wells(self) -> int | None:
        """The well count the position budget implies at the pinned FOVs, if any."""
        if self.positions is None or not self.fovs_fixed:
            return None
        if self.positions % self.fovs_fixed:
            return None
        return self.positions // self.fovs_fixed

    def hint(self) -> str:
        """A one-line, forward-looking target for the readout (no ✓/⚠ — the
        plan's own validation line carries the pass/fail)."""
        if self.positions is None:
            return (
                "↳ Frames pack positions × timepoints — set FOVs per well so "
                "positions = wells × FOVs and timepoints = frames ÷ positions."
            )
        exp = self.expected_wells
        if exp is not None:
            return (
                f"↳ Target: {exp} well(s) at {self.fovs_fixed} FOV(s)/well "
                f"({self.positions} {self.noun}(s))."
            )
        return (
            f"↳ Target: place {self.positions} {self.noun}(s) — select wells so "
            f"wells × FOVs = {self.positions}."
        )


def selection_target(layout: OmeLayout, params: ConversionParams) -> SelectionTarget:
    """Read the well-placement target from *layout* under *params*' frame-order.

    Qt-free so the page's readout stays a thin renderer over it (and it is
    unit-testable). See :class:`SelectionTarget` for what "knowable" means.
    """
    if not layout.single_image:
        positions, noun = layout.n_positions, "stage position"
    elif params.frame_order == "single":
        positions, noun = layout.n_frames, "frame"
    else:
        positions, noun = None, "frame"
    return SelectionTarget(positions, noun, params.fovs_per_well)


# ── channel roles ───────────────────────────────────────────────────────────
# The converter writes the OME channel names as filename tokens but leaves their
# analysis *roles* (which is the nuclear/segmentation channel, which are the
# fluorescent readouts, which is a phase/brightfield frame) to Analyze. Assigning
# them here — seeded by a name heuristic, user-overridable — lets the page hand a
# ready channel mapping to Analyze on "Open in Analyze" instead of the user
# retyping every token.

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


def is_ome_tiff(path: Path | str) -> bool:
    """True iff *path* names an ``.ome.tif`` / ``.ome.tiff`` file."""
    return str(path).lower().endswith(_OME_SUFFIXES)


def strip_ome_suffix(name: str) -> str:
    """Return *name* without a trailing ``.ome.tif`` / ``.ome.tiff`` extension."""
    low = name.lower()
    for suf in _OME_SUFFIXES:
        if low.endswith(suf):
            return name[: -len(suf)]
    return Path(name).stem


def default_dest(src: Path | str) -> Path:
    """The default destination dataset folder for *src*: ``<parent>/<stem>_dataset``."""
    src = Path(src)
    return src.parent / f"{strip_ome_suffix(src.name)}_dataset"


def read_layout(src: Path | str) -> OmeLayout:
    """Read the OME layout, converting any failure into an :class:`OmeImportError`."""
    try:
        return read_ome_layout(src)
    except OmeImportError:
        raise
    except Exception as exc:
        raise OmeImportError(
            f"Could not read {Path(src).name} as an OME-TIFF: {exc}"
        ) from exc


def plan_conversion(layout: OmeLayout, params: ConversionParams) -> ConversionPlan:
    """Resolve *params* against *layout* into a :class:`ConversionPlan`.

    Raises :class:`OmeImportError` when the grid, FOVs-per-well or frame-order
    can't produce a whole split, or the grid overflows the 96-well plate.
    """
    rows, cols = params.grid_rows, params.grid_cols
    if params.well_order:
        try:
            well_labels = normalize_well_order(params.well_order)
        except ValueError as exc:
            raise OmeImportError(str(exc)) from exc
    else:
        if rows <= 0 or cols <= 0:
            raise OmeImportError(
                "Enter the imaged well-grid dimensions (rows × columns) as positive "
                "whole numbers."
            )
        try:
            well_labels = serpentine_well_labels(
                rows, cols, origin=params.origin or "A01", direction=params.direction,
            )
        except ValueError as exc:
            raise OmeImportError(str(exc)) from exc
    n_wells = len(well_labels)

    # How to name the well count in errors: the grid shape when it's an actual
    # rectangle, else the plain count (a click-defined order needn't be one).
    if params.well_order or rows * cols != n_wells:
        wells_desc = f"{n_wells} well(s)"
    else:
        wells_desc = f"{rows}×{cols} = {n_wells} well(s)"

    fovs = params.fovs_per_well
    if layout.single_image:
        n_positions, n_timepoints, fovs = _split_single_image(
            layout.n_frames, n_wells, fovs, params.frame_order, wells_desc,
        )
    else:
        n_positions, n_timepoints, fovs = _split_multi_image(
            layout.n_positions, n_wells, fovs, layout.size_t, wells_desc,
        )
    return ConversionPlan(
        single_image=layout.single_image,
        n_positions=n_positions,
        n_timepoints=n_timepoints,
        fovs_per_well=fovs,
        well_labels=well_labels,
        channels=list(layout.channel_names),
    )


def _split_multi_image(
    total: int, n_wells: int, fovs: int | None, size_t: int, wells_desc: str,
) -> tuple[int, int, int]:
    if fovs is None:
        if total % n_wells != 0:
            raise OmeImportError(
                f"{total} stage position(s) don't divide evenly into {wells_desc}. "
                f"Set FOVs per well, or fix the grid."
            )
        fovs = total // n_wells
    elif n_wells * fovs != total:
        raise OmeImportError(
            f"{wells_desc} × {fovs} FOV(s) = {n_wells * fovs}, but the file "
            f"has {total} stage position(s)."
        )
    return total, max(size_t, 1), fovs


def _split_single_image(
    n_frames: int, n_wells: int, fovs: int | None, order: str, wells_desc: str,
) -> tuple[int, int, int]:
    if order not in FRAME_ORDERS:
        raise OmeImportError(f"Unknown frame order {order!r}.")
    if order == "single":
        if fovs is None:
            if n_frames % n_wells != 0:
                raise OmeImportError(
                    f"{n_frames} frames don't divide evenly into {wells_desc}. "
                    f"Set FOVs per well, or fix the grid."
                )
            fovs = n_frames // n_wells
        if n_wells * fovs != n_frames:
            raise OmeImportError(
                f"{wells_desc} × {fovs} FOV(s) = {n_wells * fovs}, but the "
                f"file has {n_frames} frame(s)."
            )
        return n_frames, 1, fovs
    if fovs is None:
        raise OmeImportError(
            "This file packs timepoints into the frame axis, so FOVs per well is "
            "needed to separate positions from timepoints (positions = wells × "
            "FOVs; timepoints = frames ÷ positions)."
        )
    n_positions = n_wells * fovs
    n_timepoints, rem = divmod(n_frames, n_positions)
    if rem or n_timepoints < 1:
        raise OmeImportError(
            f"{n_frames} frames don't split into {wells_desc} × {fovs} FOV(s) = "
            f"{n_positions} positions × a whole number of timepoints."
        )
    return n_positions, n_timepoints, fovs
