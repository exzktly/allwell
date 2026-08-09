"""No interactive renderer may paint a "pick some wells" message onto its axes.

That message belongs to the tab's shared ``EmptyState`` placeholder, which
renders where the plot would be, once, in the same words and the same position on
every page. Painting it on a matplotlib canvas instead is what produced the state
this guard exists to prevent: four copies of one sentence across three modules and
a fourth unused in ``runtime_app``, at three font sizes, in two colours, drawn
once per stacked panel so it stuttered down Bar Plots and Line Graphs — and on
Heat Map not drawn at all, which left a grid of blank cells under a colourbar
spanning a range nothing had measured.

The rule is narrow on purpose. It bans *selection* messages, not empty-state
messages: "no timepoints", "no channel data", "no feature columns", "no
smFISH/mask pair" all stay on the axes, because those are per-plot causes the
placeholder cannot know about.

**Export renderers are exempt, deliberately.** A figure written to disk carries no
placeholder with it, and ARCHITECTURE's rule is that degraded output must state
its own shortfall *in the artifact*. Banning the message there would forbid the
one place it is required. The exemption is per-file and asserted live below, so it
cannot quietly grow to cover an interactive renderer.
"""
from __future__ import annotations

import ast

from tests._repo_scan import python_sources, rel

#: Files allowed to paint a selection message onto axes, with the reason.
#: Keep this per-site and short; ``test_the_exemptions_still_describe_real_files``
#: fails when an entry stops naming a real file.
_EXPORT_RENDERER_EXEMPTIONS = {
    "well_viewer/features/saved_plots/_report.py":
        "writes a PDF report; a placeholder cannot travel with the file",
}

#: Words that make a message about the *plate selection* specifically.
_SELECTION_WORDS = ("well", "wells")

#: The message must *ask for a selection*, not merely mention its absence.
#: "no wells" on its own is not enough: a renderer reporting what it computed
#: ("the summary produced no wells or timepoints") is stating a result, and
#: banning that wording would push honest reports into worse phrasing to satisfy
#: a substring. "select" also matches "selection", which is the shape the old
#: messages used ("… in the current selection").
_ASK_WORDS = ("select", "choose", "pick")

#: Calls that put text on a matplotlib canvas.
_TEXT_CALLS = {"text", "suptitle", "set_title"}

#: Helpers that take a message and draw it onto axes themselves. Without these
#: the guard reads only direct ``ax.text`` calls and is blind to the shape Bar
#: Plots actually used — the message handed to ``draw_bar_empty_state``, which
#: painted it onto three axes in a loop. A rule that cannot see the worst
#: instance of what it forbids is not a rule.
_DRAW_HELPERS = {
    "draw_bar_empty_state", "_draw_message", "_msg_ax", "_empty_msg",
    "_smfish_paint_empty",
}


def _is_selection_message(value: str) -> bool:
    low = value.lower()
    if not any(w in low for w in _ASK_WORDS):
        return False
    return any(f"{w}" in low for w in _SELECTION_WORDS)


def _string_constants(tree: ast.AST) -> dict[str, str]:
    """Module-level ``NAME = "..."`` constants, so an indirected message is seen.

    Distribution reaches its message through a module constant and a helper
    rather than passing a literal at the call site; a guard that only looked at
    literal arguments would have been blind to exactly the shape that existed.
    """
    out: dict[str, str] = {}
    for node in getattr(tree, "body", []):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            out[target.id] = node.value.value
    return out


def _offenders_in(path, source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - a syntax error is another test's job
        return []
    consts = _string_constants(tree)
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute):
            drawing = fn.attr in _TEXT_CALLS or fn.attr in _DRAW_HELPERS
        elif isinstance(fn, ast.Name):
            drawing = fn.id in _DRAW_HELPERS
        else:
            drawing = False
        if not drawing:
            continue
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            text = None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                text = arg.value
            elif isinstance(arg, ast.Name):
                text = consts.get(arg.id)
            if text and _is_selection_message(text):
                hits.append(f"{rel(path)}:{node.lineno} -> {text!r}")
    return hits


def test_no_interactive_renderer_paints_a_selection_message() -> None:
    offenders: list[str] = []
    for path in python_sources():
        name = rel(path)
        if name in _EXPORT_RENDERER_EXEMPTIONS:
            continue
        offenders.extend(_offenders_in(path, path.read_text(encoding="utf-8")))
    assert not offenders, (
        "A 'select some wells' message is painted onto matplotlib axes. That "
        "message is the tab's shared EmptyState placeholder now — pass a reason "
        "to wrap_with_empty_state instead, so every page says it once, in the "
        "same words, in the same place:\n" + "\n".join(offenders)
    )


def test_the_exemptions_still_describe_real_files() -> None:
    """An allowlist entry that no longer names a real file is a stale claim."""
    from tests._repo_scan import REPO_ROOT
    missing = [name for name in _EXPORT_RENDERER_EXEMPTIONS
               if not (REPO_ROOT / name).is_file()]
    assert not missing, (
        f"exemptions name files that no longer exist: {missing} — drop the entries"
    )
