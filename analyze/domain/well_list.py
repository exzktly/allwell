"""The well lists the Analyze form takes as text: *Only these wells* and
*Force these wells*.

Qt-free so the parse is testable without a form, and one function so the two
fields cannot disagree about what a separator is.
"""

from __future__ import annotations

from util.well_token import canonical_well_label


def parse_well_list(text: str) -> list[str]:
    """``"A1, b02; C3"`` → ``["A1", "b02", "C3"]``.

    Commas, semicolons and whitespace all separate. Labels are returned as
    typed: the pipeline matches them canonically (``C8`` == ``C08``), and the
    form should not quietly rewrite what the user can see in the field.
    """
    return [w for w in str(text or "").replace(";", ",").replace(",", " ").split()
            if w.strip()]


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
