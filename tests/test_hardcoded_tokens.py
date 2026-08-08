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
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCAN_ROOTS = ("well_viewer", "widgets", "ui")
SCAN_FILES = ("allwell.py", "analyze_tab.py", "analyze_tab_form.py",
              "analyze_tab_chrome.py", "analyze_tab_fields.py",
              "analyze_tab_pinfo.py", "analyze_tab_seg_params.py",
              "analyze_input.py", "analyze_progress.py", "datasets_tab.py",
              "analyze_remote_dialog.py", "analyze_job_edit.py")

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


def _py_files():
    for root in SCAN_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path
    for name in SCAN_FILES:
        path = REPO_ROOT / name
        if path.exists():
            yield path


def test_no_inline_hex_colours():
    offenders = []
    for path in _py_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
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
    for path in _py_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (not isinstance(node, ast.Call)
                    or not isinstance(node.func, ast.Attribute)
                    or node.func.attr not in PIXEL_SETTERS):
                continue
            for arg in node.args:
                if (isinstance(arg, ast.Constant) and isinstance(arg.value, int)
                        and arg.value not in FREE_VALUES):
                    offenders.append(f"{rel}:{node.lineno} — "
                                     f"{node.func.attr}({arg.value})")
    assert not offenders, (
        "Pixel size(s) written at the call site. Give the number a name: a "
        "`theme.Metrics` token when the role is shared across panes, a "
        "module-level constant when it belongs to one module's controls "
        "(the precedent is `analyze_tab_chrome._ROW_LABEL_W`, "
        "`core/app_shell_view.RAIL_W`):\n  " + "\n  ".join(sorted(offenders))
    )
