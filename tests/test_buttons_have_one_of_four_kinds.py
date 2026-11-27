"""Every button is given one of the four kinds (``ui/buttons.py``): primary, default, quiet or destructive.

A button's kind is its ``variant`` property, and the stylesheet styles exactly four. A fifth value is a look
nobody chose for it — before the four existed the app set eleven, five of which no rule read, so those buttons
fell through to the bare default without anyone deciding they should. The scan reads every place a variant is
set: ``setProperty("variant", …)``, ``widgets._support.set_variant``, ``ui.buttons.set_kind`` and the
``ui_helpers._btn`` funnel, and every string a conditional or a table could hand them.

The buttons that carry an on/off state (``toggle``, ``toggle_active``, ``toggle_muted``, ``toggle_accent``,
``sem``, ``sem_warn``) are on :data:`PENDING`, per site, because the design language makes each of them a
switch, a checkbox or a segmented control rather than a fifth kind of button — that conversion is
``DESIGN_LANGUAGE_TRACKER.md`` row D13, and the roster only shrinks.
"""

from __future__ import annotations

import ast
from collections import Counter

from tests._design_rosters import BUTTON_CONSTRUCTOR
from tests._design_scan import call_name, count_sites, over_roster, stale_roster
from tests._repo_scan import parse_source, python_sources, rel

KINDS = {"primary", "default", "quiet", "destructive"}

_STATE = "carries an on/off state: a switch, checkbox or segmented control under D13, not a kind of button"

#: ``(file, value)`` → how many sites there set that value, and why it is not one of the four yet.
PENDING: dict[tuple[str, str], tuple[int, str]] = {
    ("review/core/ui_helpers.py", "sem"): (1, _STATE),
    ("review/core/ui_helpers.py", "sem_warn"): (1, _STATE),
    ("review/core/ui_helpers.py", "toggle"): (1, _STATE),
    ("review/data/crop_tool.py", "toggle"): (2, _STATE),
    ("review/features/barplot/_drag_reorder.py", "toggle_accent"): (1, _STATE),
    ("review/features/barplot/_drag_reorder.py", "toggle_muted"): (1, _STATE),
    ("review/features/barplot/tab_view.py", "toggle"): (3, _STATE),
    ("review/features/barplot/tab_view.py", "toggle_muted"): (1, _STATE),
    ("review/features/batch_export/base_panel.py", "sem"): (1, _STATE),
    ("review/features/batch_export/base_panel.py", "sem_warn"): (1, _STATE),
    ("review/features/batch_export/base_panel.py", "toggle"): (1, _STATE),
    ("review/features/heatmap/layout_view.py", "toggle"): (1, _STATE),
    ("review/features/nanowell/anchor_queue_view.py", "toggle"): (1, _STATE),
    ("review/features/nanowell/tab_view.py", "toggle"): (3, _STATE),
    ("review/features/preview/panel_view.py", "toggle"): (5, _STATE),
    ("review/features/scatter/cells_tab_view.py", "toggle"): (2, _STATE),
    ("widgets/selection_chip.py", "accent"): (1, "SelectionChip's own variant parameter: a chip, not a button. "
                                                  "It is the rail's count, which only reports: row D21 makes it plain text"),
}

_SETTERS = {"set_variant", "set_kind", "_set_variant"}


def _strings(node: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _variant_values(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        f = call.func
        name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
        if (name == "setProperty" and len(call.args) >= 2 and isinstance(call.args[0], ast.Constant)
                and call.args[0].value == "variant"):
            found += _strings(call.args[1])
        elif name in _SETTERS and len(call.args) >= 2:
            found += _strings(call.args[1])
        elif name == "_btn" and len(call.args) >= 4:
            found += _strings(call.args[3])
    # A table of (key, text, variant, …) rows feeding a loop, as Job Queue's recipe actions are built: a
    # variant handed through a name is still set, and a scan that read only call arguments missed it.
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Tuple):
            names = [t.id for t in node.target.elts if isinstance(t, ast.Name)]
            if "variant" in names and isinstance(node.iter, (ast.Tuple, ast.List)):
                at = names.index("variant")
                for row in node.iter.elts:
                    if isinstance(row, ast.Tuple) and len(row.elts) > at:
                        found += _strings(row.elts[at])
    # A helper's default kind: ``def _button(text, …, *, variant="secondary")`` sets every button the helper
    # builds without saying so at any call — the Nanowells QC helper did, and a call-site scan never saw it.
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = fn.args
            positional = args.posonlyargs + args.args
            pairs = list(zip(positional[len(positional) - len(args.defaults):], args.defaults))
            pairs += [(a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None]
            found += [v for a, d in pairs if a.arg == "variant" for v in _strings(d)]
    return found


def _sites() -> Counter:
    seen: Counter = Counter()
    for path in python_sources():
        for value in _variant_values(parse_source(path)):
            if value not in KINDS:
                seen[(rel(path), value)] += 1
    return seen


def test_every_button_is_given_one_of_the_four_kinds():
    seen = _sites()
    unexpected = sorted(f"{path}: variant {value!r} ({n} site{'s' * (n > 1)})" for (path, value), n in seen.items()
                        if (path, value) not in PENDING or n > PENDING[(path, value)][0])
    assert not unexpected, (
        "a button given a variant that is not one of the four kinds — build it with ui.buttons "
        "(primary / default / quiet / destructive):\n  " + "\n  ".join(unexpected))


def test_the_pending_roster_only_describes_real_sites():
    seen = _sites()
    stale = sorted(f"{path} {value!r}: roster {n}, found {seen.get((path, value), 0)}"
                   for (path, value), (n, _why) in PENDING.items() if seen.get((path, value), 0) < n)
    assert not stale, "these PENDING entries are larger than what is left — lower or delete them:\n  " + \
        "\n  ".join(stale)


# ── buttons built past ui.buttons ────────────────────────────────────────────

#: The panes and the app-level UI, where a button is a command a person presses and so one of the four kinds.
#: ``widgets/`` is left out: a reusable widget builds its own parts and knows nothing of the kinds.
_PANES = ("analyze/", "datasets/", "review/", "ui/", "allwell.py")


def _built_by_hand(_path: str, tree: ast.Module):
    yield from (c for c in ast.walk(tree)
                if isinstance(c, ast.Call) and call_name(c) in ("QPushButton", "QToolButton"))


def _in_a_pane(r: str) -> bool:
    return (r.startswith(_PANES) or any(f"/{p}" in r for p in _PANES)) and not r.endswith("ui/buttons.py")


def _hand_built():
    return count_sites(_built_by_hand, python_sources(), rel, skip=lambda r: not _in_a_pane(r))


def test_a_pane_builds_its_buttons_through_ui_buttons():
    bad = over_roster(_hand_built(), BUTTON_CONSTRUCTOR)
    assert not bad, ("a QPushButton or QToolButton built by hand in a pane — build it with ui.buttons:\n  "
                     + "\n  ".join(bad))


def test_the_hand_built_roster_only_describes_sites_that_remain():
    stale = stale_roster(_hand_built(), BUTTON_CONSTRUCTOR)
    assert not stale, "lower these roster entries:\n  " + "\n  ".join(stale)
