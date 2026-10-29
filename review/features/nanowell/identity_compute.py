"""What the identity check (``pipeline/nanowell_identity.py``) says about the wells a figure draws, as data.

Review joins a well's timepoints by nanowell address; the identity check registers each timepoint's montage onto a
reference timepoint's on the central square and confirms every address lands on itself. A figure over wells whose
timepoints were not all confirmed says so in its footer (:func:`identity_note`), because an unverified join looks
exactly like a verified one. A ``contradicted`` timepoint is already kept out of every join
(``series_compute._addressed_grid``); the note names it so its absence is explained.

Qt-free.
"""
from __future__ import annotations

from pipeline.nanowell_identity import CONTRADICTED, GOOD, INCONCLUSIVE, NOT_CHECKED

#: Wells named in a footer line at most; the rest are counted.
MAX_NAMED = 6


def _named(items: list[str]) -> str:
    shown = ", ".join(items[:MAX_NAMED])
    return shown + (f" and {len(items) - MAX_NAMED} more" if len(items) > MAX_NAMED else "")


def identity_counts(data, wells) -> dict[str, dict[str, list[str]]]:
    """``verdict -> {well -> [timepoints]}`` over *wells*, in the data's timepoint order."""
    out: dict[str, dict[str, list[str]]] = {}
    identity = getattr(data, "identity", None) or {}
    for well in sorted(set(wells)):
        for tp, (verdict, _why) in (identity.get(well) or {}).items():
            out.setdefault(verdict, {}).setdefault(well, []).append(tp)
    return out


def identity_note(data, wells) -> str:
    """The footer sentence(s) for *wells*: which timepoints the identity check contradicted (left out), and which
    wells it could not confirm (inconclusive or not checked); ``""`` when every timepoint is confirmed or is the
    reference."""
    counts = identity_counts(data, wells)
    parts = []
    bad = counts.get(CONTRADICTED) or {}
    if bad:
        parts.append("Nanowell identity contradicted at " + _named([f"{w} {'/'.join(t)}" for w, t in bad.items()])
                     + ": those timepoints' addresses name other nanowells, so their rows are left out of every "
                       "comparison over time (see Identity).")
    unsure = sorted(set(counts.get(INCONCLUSIVE) or {}) | set(counts.get(NOT_CHECKED) or {}))
    if unsure:
        unchecked = set(counts.get(NOT_CHECKED) or {})
        label = ("not checked" if unchecked >= set(unsure) else "not confirmed")
        parts.append(f"Nanowell identity {label} for {_named(unsure)}: that each address names the same nanowell "
                     "at every timepoint is unverified there (see Identity).")
    return " ".join(parts)


def all_good(data, wells) -> bool:
    """Whether every timepoint of *wells* is confirmed or the reference."""
    counts = identity_counts(data, wells)
    return all(verdict in GOOD for verdict in counts)
