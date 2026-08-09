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

**A statement is not a prompt.** A figure written to disk carries no placeholder
with it, so the plot renderers write one line onto an otherwise-empty figure —
"No wells selected — nothing to plot." — and ARCHITECTURE's rule is that degraded
output must state its own shortfall *in the artifact*. That reads as a report, not
as an instruction, and the rule below is written to tell the two apart: it fires
on the imperative verbs only. An exemption mechanism exists for a case that
genuinely needs one, and a liveness test fails any entry that stops describing a
real violation.
"""
from __future__ import annotations

import ast
import re

from tests._repo_scan import python_sources, rel

#: Files allowed to paint a selection *prompt* onto axes, with the reason.
#: Empty, and that is the point: the export renderers write
#: ``EMPTY_SELECTION_EXPORT_NOTE`` — "No wells selected — nothing to plot." — which
#: is a statement about the artifact, not an instruction, so the rule permits it
#: outright. ``test_every_exemption_still_covers_a_real_violation`` is what proved
#: that: added as exemptions first, it reported all three as exempting nothing.
#: Kept as a mechanism for a case that genuinely needs it, with the liveness test
#: standing guard over it.
_EXPORT_RENDERER_EXEMPTIONS: dict[str, str] = {}

#: Words that make a message about the *plate selection* specifically, matched on
#: word boundaries: a bare substring test catches "as well as" and "dwell time",
#: which would push honest prose into worse wording to satisfy the guard.
_SELECTION_WORD_RE = re.compile(r"\bwells?\b", re.I)

#: The message must *ask for a selection* in the imperative, not merely mention
#: one. Matched on word boundaries against the bare verbs, so "selected" and
#: "selection" do not count: a renderer reporting what it did — "Showing 12 of 40
#: selected wells", "the summary produced no wells" — is stating a result, and
#: banning that wording would push honest reports into worse phrasing to satisfy
#: a substring. "Select wells on the left panel" still matches, which is the
#: whole family this guard exists to keep off the axes.
_ASK_RE = re.compile(r"\b(select|choose|pick)\b", re.I)

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
    if _ASK_RE.search(low) is None:
        return False
    return _SELECTION_WORD_RE.search(low) is not None


def _string_constants(tree: ast.AST) -> dict[str, str]:
    """Every ``NAME = "..."`` binding in the file, at any nesting.

    Distribution reached its message through a module constant and a helper;
    Spatial reaches its through ``st.last_error``. A guard that only read literal
    call arguments was blind to both, and a guard that only read *module-level*
    constants is still blind to the most natural way to write the banned thing —
    a local ``msg = "..."`` two lines above the draw call.
    """
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        name = None
        if isinstance(target, ast.Name):
            name = target.id
        elif isinstance(target, ast.Attribute):
            # ``st.last_error = "Select one or more wells…"`` — the shape that
            # survived the first sweep of this branch untouched.
            name = target.attr
        if name and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            out[name] = node.value.value
    return out


def _text_of(arg, consts: dict[str, str]) -> str | None:
    """Best-effort string behind a call argument."""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.Name):
        return consts.get(arg.id)
    if isinstance(arg, ast.Attribute):
        return consts.get(arg.attr)
    if isinstance(arg, ast.JoinedStr):
        # f-string: judge the literal parts, which is where the prose lives.
        return "".join(v.value for v in arg.values
                       if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return None


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
            text = _text_of(arg, consts)
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


def test_every_exemption_still_covers_a_real_violation() -> None:
    """A stale exemption is a hole, so each one must still name an offender.

    Checking only that the file exists is not a liveness test — it passes for
    ever after the violation it was written for is gone, quietly exempting
    everything else in that file. This asserts the exemption is still *doing*
    something.
    """
    from tests._repo_scan import REPO_ROOT

    dead = []
    for name in _EXPORT_RENDERER_EXEMPTIONS:
        path = REPO_ROOT / name
        if not path.is_file():
            dead.append(f"{name} (no such file)")
            continue
        if not _offenders_in(path, path.read_text(encoding="utf-8")):
            dead.append(f"{name} (no longer paints one)")
    assert not dead, (
        "exemptions that no longer describe a real violation — drop them, or the "
        f"whole file stays exempt for nothing: {dead}"
    )


def test_the_helper_names_the_guard_relies_on_still_exist() -> None:
    """``_DRAW_HELPERS`` is a hard-coded roster; a rename silently narrows it."""
    import subprocess
    from tests._repo_scan import REPO_ROOT

    missing = []
    for helper in _DRAW_HELPERS:
        hit = subprocess.run(
            ["grep", "-rqE", rf"def {helper}\b", str(REPO_ROOT / "well_viewer")],
            capture_output=True,
        )
        if hit.returncode != 0:
            missing.append(helper)
    assert not missing, (
        f"_DRAW_HELPERS names functions that no longer exist: {missing} — the guard "
        "is silently not looking at them any more"
    )
