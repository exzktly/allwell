"""Colours and pixel sizes are named, not written at the call site.

ARCHITECTURE §3 and §8 have said "never hardcode colours or pixel sizes" since
the theme was introduced, but §16 recorded the rule as review-only because the
tree carried too many inline literals for a guard to be clean. These two scans
are that guard, with the allowlists that made it possible.

Both are ratchets: an allowlist entry may be removed, never added without a
reason written beside it.

**Colours.** A hex literal in application code is a colour that cannot follow a
theme. The exceptions are palettes — a colormap's own stops, a rank-assigned
selection palette — where the literal *is* the data, and the `__main__` demo
blocks widgets carry for manual inspection.

**Pixels.** The scan covers the fixed/minimum/maximum size setters only, by
exact name: `setMinimum`/`setMaximum` are also the spelling of `QProgressBar`'s
value range, `QProgressDialog.setMinimumDuration` (milliseconds) and
`QPlainTextEdit.setMaximumBlockCount` (documents blocks), none of which are
pixel sizes. ``0`` and Qt's ``QWIDGETSIZE_MAX`` (16777215) are allowed as bare
literals: both mean "no constraint" rather than a measurement, and naming them
makes a call site harder to read, not easier.

`datasets/` is out of scope for now — its six tool pages are mid-consolidation
into `datasets/_common/`, and naming constants inside modules that are about to
move would land the same numbers twice.
"""

from __future__ import annotations

import ast
import re
from tests._repo_scan import python_sources, rel as _rel


_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

# ── colour allowlist ────────────────────────────────────────────────────────
# path -> why. A palette's stops are data; a `__main__` block is a developer
# harness that never ships in a window.
COLOUR_ALLOWLIST = {
    "widgets/lut_selector.py": "colormap stop triples — the literals are the colormap",
    "well_viewer/features/image_browser/image_table/builder_view.py":
        "cmap swatch colours, keyed by the matplotlib colormap they preview",
    "well_viewer/domain/selections_model.py":
        "the rank-assigned selection palette (theme.WELL_COLORS_TUPLE's fallback twin)",
    "widgets/color_picker_popover.py": "__main__ demo block",
    "widgets/color_swatch_row.py": "__main__ demo block",
    "widgets/gradient_strip.py": "__main__ demo block",
    "widgets/saved_selections_list.py":
        "widgets/ may not import well_viewer; the two fallbacks restate theme's",
    "ui/theme/styles.py": "the two-token legacy remnant ARCHITECTURE §8 documents",
    "allwell.py": "the app-icon painter's own art palette (see _ICON_* below it)",
}

# ── pixel allowlist ─────────────────────────────────────────────────────────
PIXEL_SETTERS = frozenset({
    "setFixedWidth", "setFixedHeight", "setFixedSize",
    "setMinimumWidth", "setMinimumHeight", "setMinimumSize",
    "setMaximumWidth", "setMaximumHeight", "setMaximumSize",
})
# 0 = "no constraint"; 16777215 = Qt's QWIDGETSIZE_MAX, the same statement.
FREE_VALUES = frozenset({0, 16777215})




# Pixel literals reachable only *inside* an expression — a floor or a multiplier
# beside a font-metric term, e.g. ``setMinimumWidth(max(80, fm.horizontalAdvance(…)))``.
# The floor is as much a design decision written at the call site as a bare
# number is, so the scan reports them; these predate this ratchet and sit in
# modules the naming sweep did not otherwise touch, so lifting them is separate
# work rather than a reason to weaken the scan. Shrink-only: name one, delete
# its line.
EXPRESSION_ALLOWLIST = {
    "analyze_tab.py:328",
    "well_viewer/core/plot_toggles.py:59",
    "widgets/color_picker_popover.py:237",
    "widgets/color_picker_popover.py:242",
    "widgets/empty_state.py:81",
    "widgets/empty_state.py:82",
    "widgets/lut_selector.py:121",
    "widgets/lut_selector.py:126",
    "widgets/lut_selector.py:167",
    "widgets/lut_selector.py:254",
    "widgets/lut_selector.py:255",
    "widgets/rail_nav.py:83",
    "widgets/stepper.py:197",
    "widgets/stepper.py:265",
    "widgets/styled_slider.py:41",
    "widgets/styled_slider.py:44",
}

#: The modules that *define* the tokens, and so may write the literals. Same
#: shape as the well-token guard exempting ``util/well_token.py``.
_TOKEN_HOMES = {"theme.py"}


def _bare_ints(node: ast.AST) -> list[int]:
    """Integer literals reachable inside *node*, through arithmetic and through
    a ``min``/``max``/``int``/``round`` wrapper.

    ``setFixedWidth(max(2, ratio * 3))`` hardcodes a pixel size just as much as
    ``setFixedWidth(2)``: the floor is a design decision written at the call
    site. The first version of this scan required an ``ast.Constant`` at the
    top of the argument and so walked straight past ten live sites.
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, int) and not isinstance(node.value, bool):
            return [node.value]
        return []
    if isinstance(node, ast.BinOp):
        return _bare_ints(node.left) + _bare_ints(node.right)
    if isinstance(node, ast.UnaryOp):
        return _bare_ints(node.operand)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in ("min", "max", "int", "round")):
        return [v for a in node.args for v in _bare_ints(a)]
    return []


def test_no_inline_hex_colours():
    offenders = []
    for path in python_sources():
        if path.name in _TOKEN_HOMES:
            continue
        rel = _rel(path)
        if rel in COLOUR_ALLOWLIST:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and _HEX.match(node.value)):
                offenders.append(f"{rel}:{node.lineno} — {node.value}")
    assert not offenders, (
        "Inline hex colour(s). Add a token to `theme.Colors` (screen chrome and "
        "plot accents) or `theme.CPub` (the dark-on-light ink an exported figure "
        "draws with) and read it from there, so the colour can follow a theme:\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_no_inline_pixel_sizes():
    offenders = []
    for path in python_sources():
        if path.name in _TOKEN_HOMES:
            continue
        rel = _rel(path)
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (not isinstance(node, ast.Call)
                    or not isinstance(node.func, ast.Attribute)
                    or node.func.attr not in PIXEL_SETTERS):
                continue
            for arg in node.args:
                bare = _bare_ints(arg)
                site = f"{rel}:{node.lineno}"
                for value in bare:
                    if value not in FREE_VALUES and site not in EXPRESSION_ALLOWLIST:
                        offenders.append(f"{rel}:{node.lineno} — "
                                         f"{node.func.attr}(… {value} …)")
    assert not offenders, (
        "Pixel size(s) written at the call site. Give the number a name: a "
        "`theme.Metrics` token when the role is shared across panes, a "
        "module-level constant when it belongs to one module's controls "
        "(the precedent is `analyze_tab_chrome._ROW_LABEL_W`, "
        "`core/app_shell_view.RAIL_W`):\n  " + "\n  ".join(sorted(offenders))
    )


def test_the_expression_allowlist_is_live():
    """An allowlist entry that no longer names a violating site is a ratchet
    that has quietly stopped biting — the same failure this file's colour
    allowlist would have if a hex moved."""
    import ast as _ast

    live = set()
    for path in python_sources():
        rel_path = _rel(path)
        tree = _ast.parse(path.read_text(), filename=str(path))
        for node in _ast.walk(tree):
            if (isinstance(node, _ast.Call) and isinstance(node.func, _ast.Attribute)
                    and node.func.attr in PIXEL_SETTERS):
                for arg in node.args:
                    if any(v not in FREE_VALUES for v in _bare_ints(arg)):
                        live.add(f"{rel_path}:{node.lineno}")
    stale = sorted(EXPRESSION_ALLOWLIST - live)
    assert not stale, (
        "Stale EXPRESSION_ALLOWLIST entries — these sites no longer write a "
        "pixel literal, so drop them:\n  " + "\n  ".join(stale)
    )
