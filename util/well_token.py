"""Single source of truth for the 96-well token canonicaliser.

Three call sites previously kept their own copy of the same regex and
normalisation logic:

* `WellPlateZipper._extract_well_from_filename`
* `process_microscopy._canonical_well_label`
* `services/input_resolution_service._is_well_named` /
  `analyze_tab._WELL_NAME_RE`

They agreed by coincidence; this module is the canonical home so a
future change can't drift the parsers apart again. It depends only on
the stdlib, so the pipeline (`process_microscopy.py`) can import it
without violating the "no well_viewer/widgets imports" contract from
ARCHITECTURE §6.2.
"""

from __future__ import annotations

import re
from typing import Optional


# A `[A-Ha-h]` row + 1- or 2-digit column with column ∈ [1, 12].
_WELL_RE = re.compile(r"([A-Ha-h])(\d{1,2})")


def canonical_well_label(token: str) -> Optional[str]:
    """Return the canonical zero-padded uppercase form (e.g. ``"B03"``)
    for *token*, or ``None`` when *token* isn't a valid 96-well position.

    Accepts both unpadded (``a1``) and padded (``A01``) forms. The
    column must lie in the inclusive range [1, 12]; anything outside
    is rejected.
    """
    m = _WELL_RE.fullmatch((token or "").strip())
    if not m:
        return None
    try:
        col = int(m.group(2))
    except ValueError:
        return None
    if not (1 <= col <= 12):
        return None
    return f"{m.group(1).upper()}{col:02d}"


def is_valid_well_name(token: str) -> bool:
    """True iff *token* canonicalises to a valid 96-well position."""
    return canonical_well_label(token) is not None
