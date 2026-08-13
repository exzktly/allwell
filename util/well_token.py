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


# A `[A-Ha-h]` row + 1- or 2-digit column with column ∈ [1, 12].
_WELL_RE = re.compile(r"([A-Ha-h])(\d{1,2})")


def canonical_well_label(token: str) -> str | None:
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


def split_background_wells(value) -> list[str]:
    """Parse the comma-separated background-well value into a clean, de-duped list.

    Accepts a single well (``"H12"``), a comma-separated string (``"H12, G12"``),
    or an already-split list. Order-preserving; case-insensitive de-dupe.

    Semicolons separate too. The Review-image side had its own copy of this
    parser that accepted them where this one did not, and this one accepted a
    pre-split list where that one did not — two spellings of one question,
    disagreeing in both directions. A well name cannot contain either
    character, so taking both here is a superset of what each did and lets the
    copy go.
    """
    items = (value if isinstance(value, (list, tuple))
             else str(value or "").replace(";", ",").split(","))
    out: list[str] = []
    seen: set = set()
    for it in items:
        w = str(it or "").strip()
        if w and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out


def background_well_key(token) -> str:
    """Match key for a background well, tolerant of zero-padding (``A1`` ≡ ``A01``).

    Canonicalises a 96-well token to its padded form (``"A1"`` → ``"a01"``) so a user
    who types the unpadded well still matches the padded filenames the pipeline
    reads; a non-well identifier falls back to a plain lowercase match."""
    c = canonical_well_label(token)
    return c.lower() if c else str(token or "").strip().lower()


def safe_well(label: str) -> str:
    """Sanitise a well label for use as a filename component.

    Lives here rather than in ``pipeline.io`` so a caller that only needs to
    name a well's file — the remote client's staging planner, the per-well skip
    decisions — does not have to import a module that pulls numpy / tifffile /
    skimage at module scope. ``pipeline.io`` re-exports it as ``_safe_well``.
    """
    return label.replace("/", "_").replace("\\", "_")
