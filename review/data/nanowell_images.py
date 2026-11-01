"""A nanowell run's stitched montage, read as a frame any image viewer can show.

A nanowell run's ``<well>_out.zip`` holds no masks, top-hat images or overlays — only one downsampled 8-bit phase
montage per timepoint, ``<well>_<timepoint>_montage.png``: the stitched picture the run actually analysed. The
Image Table offers it as the virtual channel ``MONTAGE`` (``frame_load``); a montage has no field of view, so the
cell's FOV is ignored. Reading it needs no nanowell semantics, which is why it lives here, below every feature, and
works in a build without the Nanowells feature (the montage is an image; what is *in* it is that feature's).

Qt-free.
"""

from __future__ import annotations

import io
import math
import zipfile
from pathlib import Path

import numpy as np

MONTAGE_SUFFIX = "_montage.png"


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
    return a == a and b == b and math.isclose(a, b, abs_tol=1e-6)


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
