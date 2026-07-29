"""Qt-free planning + validation for the OME-TIFF converter.

Reads the OME layout (via :mod:`pipeline.ome_explode`) and resolves the user's
well-grid, FOVs-per-well and frame-order into a :class:`ConversionPlan` — the
same split maths the explode uses, surfaced ahead of the run so the page can show
a live readout and block an inconsistent request before any file is written.

No Qt, no file writing — so it is unit-testable without an event loop, and the
page/worker layer stays a thin shell on top (CLAUDE.md §3/§5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pipeline.ome_explode import (
    FRAME_ORDERS,
    OmeLayout,
    read_ome_layout,
)
from util.plate_grid import serpentine_well_labels

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
    if rows <= 0 or cols <= 0:
        raise OmeImportError(
            "Enter the imaged well-grid dimensions (rows × columns) as positive "
            "whole numbers."
        )
    n_wells = rows * cols
    try:
        well_labels = serpentine_well_labels(
            rows, cols, origin=params.origin or "A01", direction=params.direction,
        )
    except ValueError as exc:
        raise OmeImportError(str(exc)) from exc

    fovs = params.fovs_per_well
    if layout.single_image:
        n_positions, n_timepoints, fovs = _split_single_image(
            layout.n_frames, n_wells, fovs, params.frame_order, rows, cols,
        )
    else:
        n_positions, n_timepoints, fovs = _split_multi_image(
            layout.n_positions, n_wells, fovs, layout.size_t, rows, cols,
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
    total: int, n_wells: int, fovs: int | None, size_t: int, rows: int, cols: int,
) -> tuple[int, int, int]:
    if fovs is None:
        if total % n_wells != 0:
            raise OmeImportError(
                f"{total} stage position(s) don't divide evenly into {rows}×{cols} "
                f"= {n_wells} wells. Set FOVs per well, or fix the grid."
            )
        fovs = total // n_wells
    elif n_wells * fovs != total:
        raise OmeImportError(
            f"{rows}×{cols} wells × {fovs} FOV(s) = {n_wells * fovs}, but the file "
            f"has {total} stage position(s)."
        )
    return total, max(size_t, 1), fovs


def _split_single_image(
    n_frames: int, n_wells: int, fovs: int | None, order: str, rows: int, cols: int,
) -> tuple[int, int, int]:
    if order not in FRAME_ORDERS:
        raise OmeImportError(f"Unknown frame order {order!r}.")
    if order == "single":
        if fovs is None:
            if n_frames % n_wells != 0:
                raise OmeImportError(
                    f"{n_frames} frames don't divide evenly into {rows}×{cols} = "
                    f"{n_wells} wells. Set FOVs per well, or fix the grid."
                )
            fovs = n_frames // n_wells
        if n_wells * fovs != n_frames:
            raise OmeImportError(
                f"{rows}×{cols} wells × {fovs} FOV(s) = {n_wells * fovs}, but the "
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
            f"{n_frames} frames don't split into {rows}×{cols} × {fovs} FOV(s) = "
            f"{n_positions} positions × a whole number of timepoints."
        )
    return n_positions, n_timepoints, fovs
