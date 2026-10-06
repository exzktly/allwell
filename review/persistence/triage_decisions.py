"""What the user decided about each QC triage finding — ``triage_decisions`` in ``persistence.json``.

A map from a finding's stable key (``features/home/triage/collect.TriageRow.key``)
to ``{"state", "note", "at"}``. Only *accepted* is stored: an excluded field's
state is read from the exclusion itself (:mod:`review.persistence.field_exclusions`),
so the two cannot disagree, and an open finding is the absence of a decision.

A decision whose finding is no longer reported is kept rather than pruned. A
finding disappears when its cause does — a rerun filled the missing field — and
also when the evidence for it is temporarily unavailable, such as a sidecar
written without its field-quality block. Pruning on the second would forget a
decision the user made, so a stale entry costs a few bytes and nothing else:
the table shows decisions only beside findings that exist.
"""

from __future__ import annotations

import logging

from review.persistence import _doc

_logger = logging.getLogger("review")

SECTION = "triage_decisions"

#: The decision states this file may hold.
STATES = ("accepted",)


def from_section(raw) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not isinstance(raw, dict):
        if raw is not None:
            _logger.warning("triage_decisions: section is not an object; ignored")
        return out
    for key, rec in raw.items():
        if not isinstance(rec, dict) or rec.get("state") not in STATES:
            _logger.warning("triage_decisions: unreadable decision %r dropped", key)
            continue
        out[str(key)] = {"state": rec["state"], "note": str(rec.get("note") or ""),
                         "at": str(rec.get("at") or "")}
    return out


def save_to_data_dir(ctx) -> None:
    decisions = getattr(ctx.state.dataset, "triage_decisions", None) or {}
    _doc.set_section(ctx, SECTION, dict(sorted(decisions.items())) or None)


def load_from_data_dir(ctx) -> None:
    ctx.state.dataset.triage_decisions = from_section(_doc.get_section(ctx, SECTION))
