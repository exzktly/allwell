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
from tests._repo_scan import parse_source


_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
#: The same colour, anywhere inside a string rather than as the whole of one.
#: The first version of this scan matched only a whole literal, so a hex written
#: into a longer f-string — ``f"<span style='color: #4caf50;'>✓ …"``, the two
#: that shipped in ``analyze_remote_dialog.py`` — was invisible to it. The file
#: was not in the allowlist below, so those were an evasion rather than an
#: exemption: a colour that cannot follow a theme, with nothing saying so.
_EMBEDDED_HEX = re.compile(r"#[0-9a-fA-F]{6}\b")

#: Embedded hexes that are prose rather than paint: ``(path, colour)`` -> why.
#: Keyed on the colour rather than the line so an unrelated edit above it does
#: not silently retarget the entry, and narrow enough that a *second* hex in the
#: same file is still reported. ``test_the_embedded_hex_allowlist_is_live``
#: fails on an entry that has stopped describing a real site.
EMBEDDED_HEX_ALLOWLIST = {
    ("widgets/rail_nav.py", "#98a2b3"):
        "a measured before/after inside a QSS /* */ comment, not a painted value",
    ("widgets/rail_nav.py", "#e6e9ef"): "the same comment's other half",
}

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

# What this roster deliberately does NOT cover, so the omission is a recorded
# decision rather than something the next reader has to rediscover:
#
#   * ``setSpacing`` / ``setContentsMargins`` — 147 bare literals across the
#     app at the time of writing. Many are ``0`` ("no gap", not a design
#     value) and the rest are a sweep in their own right, not a ratchet that
#     can be closed alongside unrelated work. Adding the names here without
#     doing that sweep would only add an allowlist the size of the violation.
#   * ``font-size`` inside QSS strings — the guard reads the AST, so a size
#     written into a stylesheet f-string is invisible to it. ``selection_chip``
#     had one and it was found by reading, not by this.
#
# Both are real gaps. Widening the roster is the fix; doing it means converting
# the sites first, exactly as the pixel setters were converted.




# Pixel literals reachable only *inside* an expression — a floor or a multiplier
# beside a font-metric term, e.g. ``setMinimumWidth(max(80, fm.horizontalAdvance(…)))``.
# The floor is as much a design decision written at the call site as a bare
# number is, so the scan reports them; these predate this ratchet and sit in
# modules the naming sweep did not otherwise touch, so lifting them is separate
# work rather than a reason to weaken the scan. Shrink-only: name one, delete
# its line.
EXPRESSION_ALLOWLIST = {
    "widgets/color_picker_popover.py:237",
    "widgets/color_picker_popover.py:242",
    "widgets/empty_state.py:123",
    "widgets/empty_state.py:124",
    "widgets/lut_selector.py:121",
    "widgets/lut_selector.py:126",
    "widgets/lut_selector.py:167",
    "widgets/lut_selector.py:254",
    "widgets/lut_selector.py:255",
    "widgets/rail_nav.py:83",
    "widgets/stepper.py:229",
    "widgets/stepper.py:307",
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


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """The ``id()`` of every module/class/function docstring constant.

    A docstring naming a colour is documentation, not a painted value — this
    file's own prose names four — so the embedded scan skips them. A docstring
    is the only string in the tree that is definitionally not an argument to
    anything.
    """
    docs = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            docs.add(id(body[0].value))
    return docs


def _hex_offenders() -> list[str]:
    """Every inline colour, whole-literal or embedded, outside the allowlists.

    The two allowlists are consulted at different granularities on purpose.
    ``COLOUR_ALLOWLIST`` is per *file* and exempts only the whole-literal scan,
    because that is the shape it was written for — an icon painter's palette,
    where every literal in the file is a colour by design.
    ``EMBEDDED_HEX_ALLOWLIST`` is per *site*, keyed ``(file, colour)``.

    Letting the file-level exemption cover the embedded scan too was the
    original mistake here, and it is the one CLAUDE.md §7.5 #10 names — allowlist
    the site, not the file — broken inside the file that documents the rule. A
    hex buried in an f-string inside an allowlisted file is exactly what the
    embedded scan was added to find, and it was the one place it could not look.
    """
    offenders = []
    for path in python_sources():
        if path.name in _TOKEN_HOMES:
            continue
        rel = _rel(path)
        whole_literals_exempt = rel in COLOUR_ALLOWLIST
        tree = parse_source(path)
        docs = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if _HEX.match(node.value):
                if not whole_literals_exempt:
                    offenders.append(f"{rel}:{node.lineno} — {node.value}")
            elif id(node) not in docs:
                for found in _EMBEDDED_HEX.findall(node.value):
                    if (rel, found) in EMBEDDED_HEX_ALLOWLIST:
                        continue
                    offenders.append(f"{rel}:{node.lineno} — {found} (inside a string)")
    return offenders


def test_no_inline_hex_colours():
    offenders = _hex_offenders()
    assert not offenders, (
        "Inline hex colour(s). Add a token to `theme.Colors` (screen chrome and "
        "plot accents) or `theme.CPub` (the dark-on-light ink an exported figure "
        "draws with) and read it from there, so the colour can follow a theme:\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_the_embedded_hex_allowlist_is_live():
    """An entry that no longer names a real embedded hex is a ratchet that has
    stopped biting — the same failure the expression allowlist guards against
    below, and the reason the colour list is keyed on the colour rather than a
    line number."""
    live = set()
    for path in python_sources():
        if path.name in _TOKEN_HOMES:
            continue
        rel = _rel(path)
        tree = parse_source(path)
        docs = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docs and not _HEX.match(node.value)):
                for found in _EMBEDDED_HEX.findall(node.value):
                    live.add((rel, found))
    stale = sorted(set(EMBEDDED_HEX_ALLOWLIST) - live)
    assert not stale, (
        "These embedded-hex exemptions no longer name a real site — delete "
        "them:\n  " + "\n  ".join(f"{path} — {colour}" for path, colour in stale))


def test_no_inline_pixel_sizes():
    offenders = []
    for path in python_sources():
        if path.name in _TOKEN_HOMES:
            continue
        rel = _rel(path)
        tree = parse_source(path)
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
        tree = parse_source(path)
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
