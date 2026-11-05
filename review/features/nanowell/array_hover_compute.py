"""What the Array view says about the nanowell under the pointer.

Hovering a nanowell on the Array view shows one card of everything its row of the nanowell table holds at the
timepoint drawn: its address and ring, its state and why, the value the figure is coloured by (or why it has none),
its counts and confluence, each channel's fluorescence, the fold changes from its baseline and its growth rate, its
rim focus against the well's sharp rims, the identity check, and where it sits. The card is the controller's
tooltip (``controller.on_motion``); the one-line summary leads the canvas's own readout chip
(``axes._aw_hover_label``, set by ``renderer.render_array``).

Every number is read from the row (``series_compute.site_metrics``); nothing is recomputed here, so the card cannot
disagree with what the figures draw. A value the row does not define is written as having none, never as zero
(CLAUDE.md §7).

Qt-free.
"""

from __future__ import annotations

import math

import numpy as np

from review.domain.nanowell_rim_focus import MEASURABLE, OUT_OF_FOCUS, REFERENCE_PERCENTILE, MIN_SITES
from review.features.nanowell._array_key import state_word

#: The fold-change and rate columns the card writes, as (column, words); a channel's are added per channel.
_DERIVED = (("count_fc", "count fold change"), ("log2_count_fc", "log₂ count fold change"),
            ("confluence_fc", "confluence fold change"), ("growth_rate", "growth rate (per hour, from ln n)"))


def site_at(sites, x: float, y: float) -> int | None:
    """The index of the nanowell of *sites* (``array_compute.ArraySites``) whose disc holds the point (*x*, *y*), in
    the scene's frame; the nearest centre when discs overlap; ``None`` off every nanowell."""
    if sites is None or not len(sites) or x is None or y is None:
        return None
    dx = np.asarray(sites.x, dtype=float) - float(x)
    dy = np.asarray(sites.y, dtype=float) - float(y)
    d2 = dx * dx + dy * dy
    inside = d2 <= np.asarray(sites.radius, dtype=float) ** 2
    if not inside.any():
        return None
    return int(np.flatnonzero(inside)[np.argmin(d2[inside])])


def _number(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _num(value, unit: str = "") -> str:
    v = _number(value)
    return "" if v is None else f"{v:.3g}{unit}"


def _address(q, r) -> str:
    from util.nanowell_address import coordinate_text

    return coordinate_text(q, r) or "no address"


def rows_by_address(metrics, well: str, timepoint: str) -> dict:
    """``(q, r) -> row`` (a dict) of *well*'s nanowells at *timepoint*: built once per drawn figure, so a hover
    reads a dict rather than filtering the whole table on every pointer move."""
    if metrics is None or not len(metrics):
        return {}
    rows = metrics[(metrics["well"] == well) & (metrics["timepoint"] == timepoint)]
    out = {}
    for row in rows.to_dict("records"):
        q, r = _number(row.get("nw_q")), _number(row.get("nw_r"))
        if q is not None and r is not None:
            out[(int(q), int(r))] = row
    return out


def short_text(site, value, state: str, why: str = "") -> str:
    """One line for the readout chip: ``nanowell 3,-2 · measured · 4.2`` (or why it has no value)."""
    _w, q, r = site
    said = value_or_why(value, state, why)
    return " · ".join(filter(None, [f"nanowell {_address(q, r)}", state_word(state), said]))


def value_or_why(value, state: str, why: str = "") -> str:
    """The shown metric's value, or why the nanowell has none: its state when that is the reason, else *why*."""
    text = _num(value)
    if text:
        return text
    if state not in MEASURABLE:
        return "no value"
    return f"no value ({why})" if why else "no value"


def focus_line(row: dict, rule=None) -> str:
    """The nanowell's rim focus in words: its sharpness, against its well's sharp rims at this timepoint, and the
    verdict of the page's rule (``review.domain.nanowell_rim_focus``) — or why no verdict was reached."""
    focus = _number(row.get("focus"))
    if focus is None or focus <= 0:
        return "Rim focus: no rim fitted, so not judged"
    ref = _number(row.get("rim_focus_ref"))
    head = f"Rim focus: {focus:.3g}"
    if ref is None or ref <= 0:
        return f"{head} — not judged (fewer than {MIN_SITES} rims measured in this well at this timepoint)"
    share = focus / ref
    head += f" = {share:.0%} of the well's sharp rims ({ref:.3g}, the {REFERENCE_PERCENTILE:.0f}th percentile)"
    active = rule is not None and rule.active()
    if str(row.get("state")) == OUT_OF_FOCUS or row.get("rim_focus_ok") is False:
        cut = f"under {rule.fraction:.0%}" if active else "under the rule's cut"
        return f"{head} — OUT OF FOCUS ({cut}), so it has no value here"
    if not active:
        return f"{head} — rule off, not judged"
    if str(row.get("state")) not in MEASURABLE:
        return f"{head} — not judged ({state_word(str(row.get('state')))})"
    return f"{head} — in focus (needs {rule.fraction:.0%})"


def hover_text(row: dict | None, site, *, timepoint: str, value=None, label: str = "", why: str = "",
               channels=(), rule=None, name: str = "", state: str = "") -> str:
    """The card for the nanowell *site* ``(well, q, r)`` at *timepoint*, from its table *row* (``None`` when the
    table has no row for it, which the card then says). *value* and *label* are the metric the figure is coloured by,
    *why* why the nanowell has no value of it; *rule* is the page's rim focus rule; *name* what the page calls the
    well."""
    well, q, r = site
    lines = []
    head = f"{name or well} · nanowell {_address(q, r)}"
    if row is not None and _number(row.get("nw_ring")) is not None:
        head += f" (ring {int(_number(row['nw_ring']))})"
    hours = _number(row.get("time_h")) if row is not None else None
    lines.append(f"{head} · {timepoint}" + (f" ({hours:.3g} h)" if hours is not None else ""))
    if row is None:
        lines.append(f"State: {state_word(state)}" if state else "State: unknown")
        lines.append("The nanowell table has no row for it at this timepoint.")
        return "\n".join(lines)
    st = str(row.get("state") or state)
    said = f"State: {state_word(st)}"
    recorded = str(row.get("run_state") or "")
    if recorded and recorded != st:
        said += f" (the run recorded it {state_word(recorded)})"
    flags = [w for col, w in (("fused", "fused: on a tile seam, measured from a blended crop"),
                              ("partially_visible", "partially visible"),
                              ("count_disagreement", "count disagrees with the area estimate"))
             if row.get(col) is True]
    lines.append(said + (f" · {'; '.join(flags)}" if flags else ""))
    reason = str(row.get("reason") or "").strip()
    if reason:
        lines.append(f"Why: {reason}")
    if label:
        lines.append(f"Shown — {label}: {value_or_why(value, st, why)}")
    lines.append(_counts_line(row))
    lines += _channel_lines(row, channels)
    derived = _derived_line(row, channels)
    if derived:
        lines.append(derived)
    lines.append(focus_line(row, rule))
    identity = str(row.get("identity") or "")
    if identity:
        why_id = str(row.get("identity_reason") or "").strip()
        lines.append(f"Identity: {identity.replace('_', ' ')}" + (f" — {why_id}" if why_id else ""))
    lines.append(_where_line(row))
    return "\n".join(lines)


def _counts_line(row: dict) -> str:
    parts = []
    n, c = _num(row.get("n")), _num(row.get("c"))
    parts.append(f"Cells: {n}" if n else "Cells: no value")
    raw = _num(row.get("n_cells"))
    if not n and raw:
        parts[-1] += f" (the run counted {raw})"
    parts.append(f"confluence {c}" if c else "confluence: no value")
    area = _num(row.get("area_est_cells"))
    if area:
        parts.append(f"area estimate {area} cells")
    occupancy = str(row.get("occupancy_class") or "")
    if occupancy and occupancy.lower() != "nan":
        base = str(row.get("baseline_timepoint") or "")
        base = base if base and base != "nan" else ""
        n0 = _num(row.get("n0"))
        at = ", ".join(filter(None, [f"{n0} cells" if n0 else "", base]))
        parts.append(f"occupancy at baseline {occupancy}" + (f" ({at})" if at else ""))
    return " · ".join(parts)


def _channel_lines(row: dict, channels) -> list[str]:
    out = []
    for ch in channels or ():
        mean, total = _num(row.get(f"brightness_{ch}")), _num(row.get(f"signal_{ch}"))
        out.append(f"{ch}: cell fluorescence {mean or 'no value'} · nanowell fluorescence {total or 'no value'}")
    return out


def _derived_line(row: dict, channels) -> str:
    cols = list(_DERIVED) + [(f"{kind}_fc_{ch}", f"{ch} {words} fold change") for ch in channels or ()
                             for kind, words in (("brightness", "cell fluorescence"),
                                                 ("signal", "nanowell fluorescence"))]
    parts = [f"{words} {text}" for col, words in cols if (text := _num(row.get(col)))]
    if not parts:
        return ""
    base = str(row.get("baseline_timepoint") or "")
    return (f"From baseline {base}: " if base and base != "nan" else "From baseline: ") + " · ".join(parts)


def _where_line(row: dict) -> str:
    parts = []
    x, y = _num(row.get("well_x_um")), _num(row.get("well_y_um"))
    parts.append(f"at {x}, {y} µm" if x and y else "no measured position")
    radius = _num(row.get("radius_um"))
    if radius:
        parts.append(f"radius {radius} µm")
    visible = _number(row.get("visible_fraction"))
    if visible is not None:
        parts.append(f"{visible:.0%} visible")
    owner = str(row.get("owner") or "")
    if owner and owner != "nan":
        parts.append(f"tile {owner}")
    return "Where: " + " · ".join(parts)
