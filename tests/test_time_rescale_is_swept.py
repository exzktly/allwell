"""The two Time settings are read together, or the guard fails.

``PlotState`` carries the time-axis correction as two numbers — ``time_scale``
and ``time_offset`` — and every figure that prints a time label needs both. A
renderer that picked up only the multiplier would draw an axis that is wrong by
a constant *and* carry the marker saying it had been corrected, which is the one
outcome ``domain/time_rescale.py`` exists to prevent: a degraded label that is
indistinguishable from a good one.

Nothing in Python stops that. The offset arrived after the multiplier, into
twenty-odd call sites that already read ``ctx.state.plot.time_scale`` directly,
and every one of them would have kept compiling and kept drawing. So the rule is
that the two fields have exactly four readers — the dataclass that declares
them, the sidebar that writes them, the persistence block that saves them, and
``time_rescale.rescale_for``, which is the single door everything else goes
through — and this is what holds it.

It is a source scan rather than a runtime check because the failure is silent at
runtime by construction: the wrong axis draws perfectly.
"""

from __future__ import annotations

import ast

from tests._repo_scan import python_sources, rel
from tests._repo_scan import parse_source

#: The two fields. Named as strings because that is how the scan sees them.
FIELDS = ("time_scale", "time_offset")

#: The four modules entitled to read the fields apart, and why.
#:
#: Anything else reaches them through ``time_rescale.rescale_for``, which cannot
#: return half a correction.
ALLOWED = {
    # Declares them, and resets them together.
    "well_viewer/core/plot_state.py",
    # The single door: turns the two fields into one ``TimeRescale``.
    "well_viewer/domain/time_rescale.py",
    # Writes them, one control each, and reads them back for the preview.
    "well_viewer/features/export/sidebar_view.py",
    # Saves and restores them, under one key each.
    "well_viewer/persistence/view_state.py",
}


def _offenders() -> list[str]:
    out: list[str] = []
    for path in python_sources():
        name = rel(path)
        if name in ALLOWED:
            continue
        try:
            tree = parse_source(path)
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - not ours
            continue
        for node in ast.walk(tree):
            # ``ctx.state.plot.time_scale`` — the direct read the sweep removed.
            if isinstance(node, ast.Attribute) and node.attr in FIELDS:
                out.append(f"{name}:{node.lineno} .{node.attr}")
            # ``getattr(plot, "time_scale", 1.0)`` — the same read, spelled so an
            # attribute scan alone would not see it.
            elif isinstance(node, ast.Constant) and node.value in FIELDS:
                out.append(f"{name}:{node.lineno} {node.value!r}")
    return sorted(set(out))


def test_only_the_four_owners_read_the_time_fields_apart():
    offenders = _offenders()
    assert not offenders, (
        "These modules read PlotState.time_scale / .time_offset directly:\n  "
        + "\n  ".join(offenders)
        + "\n\nRead the correction through "
          "``well_viewer.domain.time_rescale.rescale_for(ctx.state.plot)``, "
          "which returns both halves as one TimeRescale. A site that reads one "
          "field draws an axis corrected by half the user's setting and marked "
          "as though it had been corrected in full."
    )


def test_the_allowlist_still_describes_real_readers():
    """An allowlist entry that stops being a reader is an exemption nobody is
    watching. §7.5 #10 — pair every allowlist with a liveness check."""
    from pathlib import Path

    from tests._repo_scan import REPO_ROOT

    stale = [
        name for name in sorted(ALLOWED)
        if not any(f in Path(REPO_ROOT / name).read_text(encoding="utf-8")
                   for f in FIELDS)
    ]
    assert not stale, (
        f"Allowlisted but no longer reads the fields: {stale}. Drop the entry."
    )
