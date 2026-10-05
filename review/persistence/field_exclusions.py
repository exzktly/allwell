"""Fields excluded by hand — the ``field_exclusions`` section of ``persistence.json``.

Written as a list of ``{"well", "fov", "timepoint", "reason", "at"}`` records,
read into ``ctx.state.dataset.field_exclusions`` keyed by ``(well, fov,
timepoint)``. The layer itself, and why a well with untrusted FOV labels is
never filtered, is :mod:`review.data.field_exclusions`. Cleared by the
*Curation* reset scope, beside the crop and the per-cell overrides, because it
is the same kind of decision: the user removing data by hand.

An absent section is no exclusions. A record that cannot be read is dropped and
logged rather than guessed at: an exclusion is a decision to remove data, and a
half-read one could remove the wrong field.
"""

from __future__ import annotations

import logging

from review.persistence import _doc

_logger = logging.getLogger("review")

SECTION = "field_exclusions"


def to_records(exclusions: dict) -> list[dict]:
    """The on-disk list, sorted so an unchanged set writes identical bytes."""
    out = []
    for (well, fov, tp), meta in sorted(exclusions.items()):
        meta = meta if isinstance(meta, dict) else {}
        out.append({"well": well, "fov": fov, "timepoint": tp,
                    "reason": str(meta.get("reason") or ""),
                    "at": str(meta.get("at") or "")})
    return out


def from_records(raw) -> dict:
    """The live map from the on-disk list; unreadable records are dropped."""
    out: dict[tuple[str, str, str], dict] = {}
    if not isinstance(raw, list):
        if raw is not None:
            _logger.warning("field_exclusions: section is not a list; ignored")
        return out
    for rec in raw:
        if not isinstance(rec, dict) or not rec.get("well"):
            _logger.warning("field_exclusions: unreadable record %r dropped", rec)
            continue
        key = (str(rec.get("well") or ""), str(rec.get("fov") or ""),
               str(rec.get("timepoint") or ""))
        out[key] = {"reason": str(rec.get("reason") or ""),
                    "at": str(rec.get("at") or "")}
    return out


def save_to_data_dir(ctx) -> None:
    exclusions = getattr(ctx.state.dataset, "field_exclusions", None) or {}
    _doc.set_section(ctx, SECTION, to_records(exclusions) or None)


def load_from_data_dir(ctx) -> None:
    ctx.state.dataset.field_exclusions = from_records(_doc.get_section(ctx, SECTION))
