"""The well lists the Analyze form takes as text: *Only these wells*,
*Skip these wells* and *Force these wells*.

Qt-free so the parse is testable without a form, and one function so the two
fields cannot disagree about what a separator is.
"""

from __future__ import annotations

import re

from util.well_token import canonical_well_label, expand_well_range

#: A colon and any spaces around it, so ``A1 : C3`` is one range rather than three tokens.
_RANGE_COLON = re.compile(r"\s*:\s*")


def parse_well_list(text: str) -> list[str]:
    """``"A1, b02; C3"`` → ``["A1", "b02", "C3"]``; ``"A1:B2"`` → ``["A01", "A02", "B01", "B02"]``.

    Commas, semicolons and whitespace all separate. A single well is returned
    as typed: the pipeline matches labels canonically (``C8`` == ``C08``), and
    the form should not quietly rewrite what the user can see in the field.

    **Two corners joined by a colon are a rectangle of wells**, the same
    reading the background-well field gives it (``util.well_token.expand_well_range``):
    ``A01:C06`` is rows A–C by columns 1–6, in reading order, and either corner
    may come first. Ranges and single wells mix freely, and a well named twice
    — by two overlapping rectangles, or by a rectangle and on its own — is
    listed once. A range that does not read as two wells (``A1:Z9``) stays as
    the token it is and reaches the pipeline like any other well the plate
    does not hold — *Only these wells* refuses the run naming it, *Skip* and
    *Force* report it in the log (``pipeline.well_skip``) — rather than being
    dropped while the user believes it is listed.
    """
    flat = _RANGE_COLON.sub(":", str(text or "")).replace(";", ",").replace(",", " ")
    out: list[str] = []
    seen: set[str] = set()
    for token in flat.split():
        for well in expand_well_range(token) or [token]:
            key = canonical_well_label(well) or well.lower()
            if key not in seen:
                seen.add(key)
                out.append(well)
    return out


def unreadable_wells(text: str) -> list[str]:
    """The entries of a typed well list that are neither a well nor a rectangle of wells, as typed.

    ``pipeline.well_skip`` canonicalises these lists and drops what does not
    read as a well, so an entry it cannot read is not refused there but simply
    vanishes — and an *Only these wells* field whose every entry vanished sends
    no ``--only_wells`` at all, which runs the whole plate. The form refuses
    them by name before the run instead.
    """
    return [w for w in parse_well_list(text) if canonical_well_label(w) is None]


def well_scope_error(fields: dict[str, str]) -> str | None:
    """The run-blocking message for unreadable entries in the scope fields, or ``None``.

    *fields* maps each field's label to its text.
    """
    lines = []
    for label, text in fields.items():
        bad = unreadable_wells(text)
        if bad:
            lines.append(f"{label}: " + ", ".join(f"'{w}'" for w in bad))
    if not lines:
        return None
    return ("Not a well or a range of wells:\n" + "\n".join(lines) + "\n\n"
            "Write a well as a row A–H and a column 1–12 (B02), and a rectangle "
            "of wells as two corners joined by a colon (A01:C06).")


def only_wells_note(opts) -> str:
    """The sentence a subset run announces itself with, or ``""``.

    A run over part of a plate looks exactly like a whole-plate run on every
    other surface — the same dashboard, the same log shape, a plate map with
    most wells simply not moving — so it says so where the user is looking
    when it starts.
    """
    wells = [str(w) for w in (opts or {}).get("only_wells") or ()]
    if not wells:
        return ""
    shown = ", ".join(wells[:8]) + ("…" if len(wells) > 8 else "")
    return (f"Only {len(wells)} well(s) will run ({shown}); every other "
            "well's output is left as it is.")


def skip_wells_note(opts) -> str:
    """The sentence a run with *Skip these wells* announces itself with, or ``""``."""
    wells = [str(w) for w in (opts or {}).get("skip_wells") or ()]
    if not wells:
        return ""
    shown = ", ".join(wells[:8]) + ("…" if len(wells) > 8 else "")
    return f"{len(wells)} well(s) will be skipped ({shown}); their output is left as it is."


def incremental_is_moot(opts) -> bool:
    """Whether *Incremental* can change nothing about this run, because every well it runs is forced.

    Incremental keeps a well's finished fields and processes only the missing ones; a forced well is rebuilt
    from scratch whatever the setting says (``pipeline.well_skip.incremental_for_well``). So it is moot under
    *Force reprocess all wells*, and under a *Force these wells* list that covers every well in *Only these
    wells* — the shape QC triage's *Re-run wells* hands over. A setting that changes nothing must not be the
    reason a run is refused: a remote nanowell run refuses Incremental, and refused exactly this re-run of
    the wells a QC pass found missing fields in, with every one of them forced.
    """
    opts = opts or {}
    if opts.get("force"):
        return True

    def canon(wells) -> set[str]:
        return {canonical_well_label(str(w).strip()) or str(w).strip()
                for w in wells or () if str(w).strip()}

    only = canon(opts.get("only_wells"))
    return bool(only) and only <= canon(opts.get("force_wells"))
