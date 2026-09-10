"""Single source of truth for the 96-well token canonicaliser.

Three call sites previously kept their own copy of the same regex and
normalisation logic:

* `WellPlateZipper._extract_well_from_filename`
* `process_microscopy._canonical_well_label`
* `services/input_resolution_service._is_well_named` /
  `analyze.tab._WELL_NAME_RE`

They agreed by coincidence; this module is the canonical home so a
future change can't drift the parsers apart again. It depends only on
the stdlib, so the pipeline (`process_microscopy.py`) can import it
without violating the "no well_viewer/widgets imports" contract from
ARCHITECTURE §6.2.
"""

from __future__ import annotations

import re


# A `[A-Ha-h]` row + 1- or 2-digit column with column ∈ [1, 12].
#
# The digits are spelled `[0-9]` rather than `\d` on purpose. `\d` is Unicode by
# default, so it matches every decimal digit in Unicode — ARABIC-INDIC ZERO,
# FULLWIDTH ONE and some six hundred others — and `int()` then converts them
# happily. `"A١"`, `"A１"` and `"A1"` are three distinct filename tokens naming
# three distinct files, and all three canonicalised to `"A01"`, silently merging
# them onto one well. A well label written by an instrument is ASCII; anything
# else is a token this parser is not entitled to read.
_WELL_RE = re.compile(r"([A-Ha-h])([0-9]{1,2})")


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

    **A rectangle is a well list.** ``H01:H12`` is the whole of row H, ``A01:D12``
    the top half, and either can sit among ordinary wells (``H01:H12, A01``).
    The corners expand to canonical labels in reading order, by
    :func:`_expand_well_range` — the same reading the pairing map's right-hand
    side has always used, so ``:`` means one thing wherever a set of wells is
    written. Expanded here, in the one function every reader of the field goes
    through, so a range means the same thing to the model build, the remote
    staging planner, the batched hold-out, both incremental-scope readers, the
    pairing editor's list, the Review preview and the run receipt. Nothing that
    parsed before parses differently: a token containing ``:`` was not a well,
    so a run naming one refused to start.
    """
    items = (value if isinstance(value, (list, tuple))
             else str(value or "").replace(";", ",").split(","))
    out: list[str] = []
    seen: set = set()
    for it in items:
        text = str(it or "").strip()
        if not text:
            continue
        # An unreadable range falls through as the token it is, so the caller
        # refuses it by name — the same way it refuses any other well it cannot
        # find. Swallowing it here would drop a well the user believes is a
        # blank, which is the one outcome this field must not produce.
        for w in _expand_well_range(text) or [text]:
            if w.lower() not in seen:
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


#: Separates one blank's entry from the next in a written map. A newline works
#: too, so a map can be pasted in as lines.
_MAP_ENTRY_SEP = ";"
#: Separates the blank from the wells it corrects.
_MAP_ASSIGN = "="
#: Separates the two corners of a rectangular block, ``A01:D12``.
_MAP_RANGE = ":"


def _expand_well_range(text: str) -> list[str] | None:
    """The wells of a rectangular block written ``A01:D12``, or ``None``.

    Rows A–D by columns 1–12, in reading order. Rectangular because a plate's
    blocks are rectangular: a treatment occupies a set of rows, a set of
    columns, or a quadrant, and letting the two corners describe anything else
    would mean guessing which of several readings the user meant.

    Either corner may be given first — ``D12:A01`` is the same block — because
    a user dragging a selection has no reason to start at the top left.
    Derives the span from the canonical labels rather than importing the plate's
    geometry: ``util.plate_grid`` imports *this* module, and
    ``canonical_well_label`` has already established that the rows are A–H and
    the columns 1–12.
    """
    if _MAP_RANGE not in text:
        return None
    first, _, second = text.partition(_MAP_RANGE)
    lo, hi = canonical_well_label(first), canonical_well_label(second)
    if lo is None or hi is None:
        return None
    rows = sorted((lo[0], hi[0]))
    cols = sorted((int(lo[1:]), int(hi[1:])))
    return [f"{chr(r)}{c:02d}"
            for r in range(ord(rows[0]), ord(rows[1]) + 1)
            for c in range(cols[0], cols[1] + 1)]


def _covered_wells(text: str, *, blank: str, blanks=()) -> list[str]:
    """The canonical wells one map entry's right-hand side names.

    Accepts a comma-separated list of wells, rectangular blocks, or a mix.
    Raises ``ValueError`` naming the offending text rather than dropping it: a
    token this cannot read is a well the user believes is corrected and which
    would otherwise be silently left out of the map — and an uncovered well is
    the thing the map exists to make impossible.

    A **block** drops any well in *blanks*, because a block is a shorthand for a
    region and a plate's blanks usually sit inside the region they serve: the
    natural way to pair the bottom half of a plate is ``G12=E01:H12``, whose
    rectangle contains both G12 and H12. Nothing is ambiguous there — a blank is
    the reference for its block and cannot also be a sample of it — and it is
    what the editor's own splits write, so the written grammar can say what the
    plate dialog says. A well named **individually** is not dropped: that is a
    statement about one well, and a blank among them is a typo worth refusing.
    """
    out: list[str] = []
    seen: set[str] = set()
    held = set(blanks)
    for piece in text.split(","):
        item = piece.strip()
        if not item:
            continue
        wells = _expand_well_range(item)
        if wells is None:
            one = canonical_well_label(item)
            if one is None:
                raise ValueError(
                    f"{blank!r} is mapped to {item!r}, which is not a well or a "
                    f"block of wells. Write a well (A01), a block (A01:D12), or "
                    f"a comma-separated list of either.")
            wells = [one]
        else:
            wells = [w for w in wells if w not in held]
        for well in wells:
            if well not in seen:
                seen.add(well)
                out.append(well)
    if not out:
        raise ValueError(
            f"{blank!r} is mapped to nothing. Give it the wells it corrects, or "
            f"remove it — a blank that corrects no well is a blank the run has "
            f"no use for.")
    return out


def parse_background_well_map(value) -> dict[str, list[str]]:
    """Parse a blank-well → treatment-wells map, or return ``{}`` for none.

    Written ``H12=A01:D12; G12=E01:H12`` — one entry per blank, entries
    separated by ``;`` or a newline, the wells a blank corrects given as a
    comma-separated list of wells and rectangular blocks. Well tokens are
    canonicalised, so ``h12`` and ``H12`` and ``H1 2``'s padded and unpadded
    spellings all land on the same key. An already-parsed mapping passes through
    canonicalised, which is what lets a saved run's map round-trip.

    Order is preserved, because it is the order the user wrote and the order the
    editor will show back.

    Four things are refused rather than absorbed, each naming what it choked on:
    an entry with no ``=``; a right-hand side this cannot read; an empty group;
    and **a well two blanks both claim**, which is the one a silent rule would
    resolve by picking one — and picking one means half a plate corrected by a
    reference the user did not choose, which is the defect the map exists to
    prevent. A blank named *individually* among the wells it corrects is refused
    for the same reason: it is almost always a typo, and reading it either way
    changes the numbers.

    A blank inside a **block** is the one case that is resolved rather than
    refused, and it is resolved because there is nothing to choose between: the
    block is a shorthand for a region, the blanks of the run are held out of it,
    and a blank is in any case corrected by the model built from itself. See
    :func:`_covered_wells`.
    """
    if not value:
        return {}
    if isinstance(value, dict):
        items = [(str(k), ",".join(str(w) for w in v)) for k, v in value.items()]
    else:
        entries = str(value).replace("\n", _MAP_ENTRY_SEP).split(_MAP_ENTRY_SEP)
        items = []
        for entry in entries:
            text = entry.strip()
            if not text:
                continue
            if _MAP_ASSIGN not in text:
                raise ValueError(
                    f"{text!r} is not a blank-well assignment. Write "
                    f"'<blank>=<wells>', for example 'H12=A01:D12'.")
            left, _, right = text.partition(_MAP_ASSIGN)
            items.append((left, right))

    # Every blank is canonicalised before any right-hand side is expanded,
    # because a block drops the blanks it contains and one entry's block can
    # contain a later entry's blank.
    ordered: list[tuple[str, str]] = []
    for raw_blank, raw_wells in items:
        blank = canonical_well_label(raw_blank)
        if blank is None:
            raise ValueError(
                f"{raw_blank.strip()!r} is not a well, so it cannot be a blank "
                f"in the background-well map.")
        if blank in {b for b, _ in ordered}:
            raise ValueError(
                f"{blank} appears twice in the background-well map. Give each "
                f"blank one entry listing every well it corrects.")
        ordered.append((blank, raw_wells))
    blanks = {b for b, _ in ordered}

    out: dict[str, list[str]] = {}
    claimed: dict[str, str] = {}
    for blank, raw_wells in ordered:
        wells = _covered_wells(raw_wells, blank=blank, blanks=blanks)
        for well in wells:
            if well in claimed:
                raise ValueError(
                    f"{well} is mapped to two blanks, {claimed[well]} and "
                    f"{blank}. A well is corrected by one blank; the run cannot "
                    f"choose between them.")
            claimed[well] = blank
        out[blank] = wells

    overlap = sorted(set(out) & set(claimed))
    if overlap:
        raise ValueError(
            f"{', '.join(overlap)} is named as a blank and also as a well some "
            f"blank corrects. A blank is the reference, not a sample; check the "
            f"map for a mistyped well.")
    return out


def format_background_well_map(mapping: dict[str, list[str]]) -> str:
    """Render a parsed map back to the written form.

    The explicit well list, never a range: what a machine writes has to mean
    exactly one thing when it is read back, and a rectangle is a claim about the
    plate's shape that stops being true when the map is edited. Ranges are for
    the human typing a command line, which :func:`parse_background_well_map`
    accepts; this is what the form and the sidecar store.
    """
    return "; ".join(
        f"{blank}{_MAP_ASSIGN}{','.join(wells)}"
        for blank, wells in mapping.items() if wells)


def blank_wells_for_run(background_well="", background_well_map="") -> list[str]:
    """Every well this run treats as a blank, however it was named.

    The union of ``--background_well`` and the keys of ``--background_well_map``,
    canonical and order-preserving. One function because "which wells are
    blanks?" is asked in five places that must agree — the model build, the
    remote staging planner (a blank is uploaded whole and never incrementally
    diffed), the batched hold-out that keeps the blank out of the slicing, and
    the two incremental-scope readers — and they have already drifted once:
    ``remote_runner`` grew a local whitespace-splitting regex that read
    ``"H12 G12"`` as two wells where :func:`split_background_wells` reads it as
    one, and every batch of that run died on the guard. Two more copies of that
    regex are still in ``services.input_resolution_service``; this is what
    replaces them.

    A superset is the safe direction for every caller: staging a well whole that
    turns out not to be a blank costs an upload, while missing one starves the
    whole plate of its correction.
    """
    out: list[str] = []
    seen: set[str] = set()
    for token in split_background_wells(background_well):
        key = background_well_key(token)
        if key not in seen:
            seen.add(key)
            out.append(canonical_well_label(token) or str(token).strip())
    for blank in parse_background_well_map(background_well_map):
        key = background_well_key(blank)
        if key not in seen:
            seen.add(key)
            out.append(blank)
    return out


def safe_well(label: str) -> str:
    """Sanitise a well label for use as a filename component.

    Lives here rather than in ``pipeline.io`` so a caller that only needs to
    name a well's file — the remote client's staging planner, the per-well skip
    decisions — does not have to import a module that pulls numpy / tifffile /
    skimage at module scope. ``pipeline.io`` re-exports it as ``_safe_well``.
    """
    return label.replace("/", "_").replace("\\", "_")
