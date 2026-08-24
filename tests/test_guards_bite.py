"""Every AST guard is shown catching a violation, and clearing a clean file.

`scripts/prove_guard.sh` proves a guard the day it is written, by watching it
fail on a base branch that still violates the rule. That proof cannot live in
CI: once the fix merges, the rule *holds* on the base and the script correctly
reports "passed on the base" forever after. So the authoring-time proof expires,
and nothing afterwards ever demonstrates that these guards still bite.

This is the durable half. Each case hands a guard a synthetic file containing
the violation it exists to prevent and asserts it fires, then hands it a clean
one and asserts it does not. Both directions matter: a guard that fails on
everything would pass a test that only checks the first half, and a guard whose
scan silently walks past its input would pass one that only checks the second.

The violations are not invented. Every one is a shape that either shipped in
this repository or was demonstrated to slip past an earlier version of the
guard — the conditional `setFrameShape`, the aliased `QFrame`, the wrapped pixel
literal, the `[0-9]` spelling of `\\d`, the roster with a non-card string mixed
in. Reproducing them here is what stops a future simplification of a guard from
quietly reopening the hole.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def _run_scan(monkeypatch, module_name: str, test_name: str, files: list[Path]):
    """Point *module*'s scan at *files* and run one of its tests.

    Returns the `AssertionError` it raised, or `None` if it passed. The guards
    reach the tree through `tests._repo_scan.python_sources`, imported into
    their own namespace, so replacing that one attribute redirects the scan
    without touching the rule itself. `rel` goes with it: it renders a path
    relative to the repo root, and a temp file is not under it.
    """
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "python_sources", lambda **kw: list(files), raising=False)
    for attr in ("rel", "_rel"):
        if hasattr(module, attr):
            monkeypatch.setattr(module, attr, lambda p: str(p), raising=False)
    try:
        getattr(module, test_name)()
    except AssertionError as exc:
        return exc
    return None


def _run_rooted_scan(monkeypatch, module_name: str, test_name: str, root: Path,
                     extra: dict | None = None):
    """The same, for the two guards that walk from `REPO_ROOT` themselves."""
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "REPO_ROOT", root, raising=False)
    for name, value in (extra or {}).items():
        monkeypatch.setattr(module, name, value, raising=False)
    try:
        getattr(module, test_name)()
    except AssertionError as exc:
        return exc
    return None


# ── dividers ────────────────────────────────────────────────────────────────

DIVIDER_VIOLATIONS = {
    "plain": "def build(p):\n    f = QFrame(p)\n    f.setFrameShape(QFrame.HLine)\n",
    # Batch Export's split rule picks its shape at run time; the first version
    # of the scan only understood a plain attribute argument.
    "conditional": ("def build(p, stack):\n    f = QFrame(p)\n"
                    "    f.setFrameShape(QFrame.HLine if stack else QFrame.VLine)\n"),
    # `QFrame` is imported under an alias in home/groups/tab_view.py.
    "aliased": "def build(p):\n    f = _QFrame(p)\n    f.setFrameShape(_QFrame.HLine)\n",
    # The shape and the shadow OR'd into setFrameStyle is the same act.
    "frame_style": "def build(p):\n    f = QFrame(p)\n    f.setFrameStyle(QFrame.HLine | QFrame.Plain)\n",
}


@pytest.mark.parametrize("shape", sorted(DIVIDER_VIOLATIONS))
def test_divider_guard_catches_an_unnamed_frame(tmp_path, monkeypatch, shape):
    bad = _write(tmp_path, "bad_divider.py", DIVIDER_VIOLATIONS[shape])
    exc = _run_scan(monkeypatch, "tests.test_divider_naming",
                    "test_no_unnamed_divider_frames", [bad])
    assert exc is not None, f"the {shape} divider violation went uncaught"
    assert "bad_divider.py" in str(exc)


def test_divider_guard_clears_a_named_frame(tmp_path, monkeypatch):
    good = _write(tmp_path, "good_divider.py",
                  'def build(p):\n    f = QFrame(p)\n'
                  '    f.setObjectName("Separator")\n'
                  '    f.setFrameShape(QFrame.HLine)\n')
    assert _run_scan(monkeypatch, "tests.test_divider_naming",
                     "test_no_unnamed_divider_frames", [good]) is None


# ── colours and pixel sizes ─────────────────────────────────────────────────

def test_colour_guard_catches_an_inline_hex(tmp_path, monkeypatch):
    bad = _write(tmp_path, "bad_colour.py", 'ACCENT = "#FF6B35"\n')
    exc = _run_scan(monkeypatch, "tests.test_hardcoded_tokens",
                    "test_no_inline_hex_colours", [bad])
    assert exc is not None and "bad_colour.py" in str(exc)


def test_colour_guard_clears_a_token_read(tmp_path, monkeypatch):
    good = _write(tmp_path, "good_colour.py", "ACCENT = theme.Colors.accent\n")
    assert _run_scan(monkeypatch, "tests.test_hardcoded_tokens",
                     "test_no_inline_hex_colours", [good]) is None


EMBEDDED_HEX_VIOLATIONS = {
    # The two that shipped in analyze_remote_dialog.py: a hex inside a longer
    # f-string, which the whole-literal `^…$` match walked straight past.
    "fstring": ('def paint(lbl, alias):\n'
                '    lbl.setText(f"<span style=\'color: #4caf50;\'>ok {alias}</span>")\n'),
    "plain": 'STYLE = "QLabel { color: #e57373; }"\n',
    "qss_block": ('def qss():\n'
                  '    return """\n    QFrame { background: #1A2230; }\n    """\n'),
}


@pytest.mark.parametrize("shape", sorted(EMBEDDED_HEX_VIOLATIONS))
def test_colour_guard_catches_a_hex_embedded_in_a_longer_string(
        tmp_path, monkeypatch, shape):
    bad = _write(tmp_path, "bad_embedded.py", EMBEDDED_HEX_VIOLATIONS[shape])
    exc = _run_scan(monkeypatch, "tests.test_hardcoded_tokens",
                    "test_no_inline_hex_colours", [bad])
    assert exc is not None, f"the {shape} embedded hex went uncaught"
    assert "bad_embedded.py" in str(exc)


def test_a_file_level_colour_exemption_does_not_cover_an_embedded_hex(
        tmp_path, monkeypatch):
    """The two allowlists have different granularities, on purpose.

    ``COLOUR_ALLOWLIST`` is per *file* and was written for an icon painter,
    where every whole literal in the file is a colour by design.
    ``EMBEDDED_HEX_ALLOWLIST`` is per *site*. Letting the file-level exemption
    also cover the embedded scan is CLAUDE.md §7.5 #10 — allowlist the site, not
    the file — broken inside the file that documents the rule, and it made the
    one place a buried hex is hardest to see the one place the scan could not
    look.
    """
    import tests.test_hardcoded_tokens as guard

    bad = _write(tmp_path, "exempt_file.py",
                 'PALETTE = ["#FF6B35", "#1A2230"]\n'
                 'STYLE = "QLabel { color: #e57373; }"\n')
    monkeypatch.setattr(guard, "COLOUR_ALLOWLIST", {str(bad)})
    exc = _run_scan(monkeypatch, "tests.test_hardcoded_tokens",
                    "test_no_inline_hex_colours", [bad])
    assert exc is not None, (
        "a hex embedded in a string went uncaught because the file carried a "
        "whole-literal exemption")
    assert "#e57373" in str(exc)
    # ...and the exemption still does what it is for.
    assert "#FF6B35" not in str(exc) and "#1A2230" not in str(exc), (
        f"the file-level exemption stopped covering whole literals: {exc}")


def test_colour_guard_clears_a_hex_named_in_a_docstring(tmp_path, monkeypatch):
    """Prose that names a colour is documentation, not paint — this repository's
    own guard file names four in its comments. Flagging those would push the
    guard's own authors toward writing vaguer notes."""
    good = _write(tmp_path, "good_docstring.py",
                  '"""The [active] rule alone gives #98a2b3, which is wrong."""\n'
                  "COLOUR = theme.Colors.accent\n")
    assert _run_scan(monkeypatch, "tests.test_hardcoded_tokens",
                     "test_no_inline_hex_colours", [good]) is None


PIXEL_VIOLATIONS = {
    "bare": "def build(w):\n    w.setFixedWidth(137)\n",
    # The evasion an earlier version walked past ten times: the literal is a
    # floor inside a `max()`, which is still a size written at the call site.
    "wrapped": "def build(w, fm):\n    w.setMinimumWidth(max(137, fm.height()))\n",
    "arithmetic": "def build(w, n):\n    w.setFixedHeight(137 * n)\n",
}


@pytest.mark.parametrize("shape", sorted(PIXEL_VIOLATIONS))
def test_pixel_guard_catches_a_literal(tmp_path, monkeypatch, shape):
    bad = _write(tmp_path, "bad_pixels.py", PIXEL_VIOLATIONS[shape])
    exc = _run_scan(monkeypatch, "tests.test_hardcoded_tokens",
                    "test_no_inline_pixel_sizes", [bad])
    assert exc is not None, f"the {shape} pixel violation went uncaught"
    assert "bad_pixels.py" in str(exc)


def test_pixel_guard_clears_a_named_size(tmp_path, monkeypatch):
    good = _write(tmp_path, "good_pixels.py",
                  "_FIELD_W = 137\n\n\ndef build(w):\n    w.setFixedWidth(_FIELD_W)\n")
    assert _run_scan(monkeypatch, "tests.test_hardcoded_tokens",
                     "test_no_inline_pixel_sizes", [good]) is None


# ── well tokens ─────────────────────────────────────────────────────────────

WELL_VIOLATIONS = {
    "canonical_spelling": 'import re\n_RE = re.compile(r"^([A-Ha-h])(\\d{1,2})$")\n',
    # The spelling an earlier version did not know.
    "digit_class": 'import re\n_RE = re.compile(r"^([A-H])([0-9]{1,2})$")\n',
    "unanchored_fullmatch": 'import re\n\n\ndef ok(n):\n    return re.fullmatch(r"[A-H]\\d{1,2}", n)\n',
    # `re.match` anchors the start itself, so a tail-anchored pattern is a
    # whole-token judge here — not the sanctioned trailing extractor.
    "match_tail_anchored": 'import re\n\n\ndef ok(t):\n    return re.match(r"([A-Ha-h])(\\d{1,2})$", t)\n',
}


@pytest.mark.parametrize("shape", sorted(WELL_VIOLATIONS))
def test_well_token_guard_catches_a_local_parser(tmp_path, monkeypatch, shape):
    bad = _write(tmp_path, "bad_well.py", WELL_VIOLATIONS[shape])
    exc = _run_scan(monkeypatch, "tests.test_well_token_single_parser",
                    "test_no_module_keeps_its_own_whole_token_well_regex", [bad])
    assert exc is not None, f"the {shape} well parser went uncaught"
    assert "bad_well.py" in str(exc)


WELL_SANCTIONED = {
    # A trailing token on a longer stem, read with `re.search` — §15 names this
    # as a separate operation and it must stay allowed.
    "trailing_extractor": 'import re\n\n\ndef well(s):\n    return re.search(r"([A-Ha-h])(\\d{1,2})$", s)\n',
    "filename_shape": 'import re\n_RE = re.compile(r"^([A-Ha-h])(\\d{1,2})_out\\.zip$")\n',
    "log_scraper": 'import re\n_RE = re.compile(r"\\[remote\\] . ([A-Ha-h]\\d{1,2}): up to date")\n',
}


@pytest.mark.parametrize("shape", sorted(WELL_SANCTIONED))
def test_well_token_guard_clears_the_sanctioned_shapes(tmp_path, monkeypatch, shape):
    good = _write(tmp_path, "good_well.py", WELL_SANCTIONED[shape])
    assert _run_scan(monkeypatch, "tests.test_well_token_single_parser",
                     "test_no_module_keeps_its_own_whole_token_well_regex", [good]) is None


# ── the plot-card roster ────────────────────────────────────────────────────

def test_roster_guard_catches_a_restated_list(tmp_path, monkeypatch):
    bad = _write(tmp_path, "bad_roster.py",
                 'def f(win):\n    for attr in ("_line_card", "_bar_card", "_heatmap_card"):\n'
                 "        pass\n")
    exc = _run_scan(monkeypatch, "tests.test_plot_card_roster",
                    "test_the_shell_imports_the_roster_rather_than_restating_it", [bad])
    assert exc is not None and "bad_roster.py" in str(exc)


def test_roster_guard_catches_a_list_with_a_stray_string(tmp_path, monkeypatch):
    """The shape an `all()` check short-circuited past."""
    bad = _write(tmp_path, "bad_roster_mixed.py",
                 'ITEMS = ("_line_card", "_bar_card", "something_else")\n')
    exc = _run_scan(monkeypatch, "tests.test_plot_card_roster",
                    "test_the_shell_imports_the_roster_rather_than_restating_it", [bad])
    assert exc is not None and "bad_roster_mixed.py" in str(exc)


def test_roster_guard_clears_an_unrelated_name_list(tmp_path, monkeypatch):
    """`btn_card` in an `__all__` is not a roster — it has no leading underscore."""
    good = _write(tmp_path, "good_roster.py", '__all__ = ["btn_card", "btn_primary"]\n')
    assert _run_scan(monkeypatch, "tests.test_plot_card_roster",
                     "test_the_shell_imports_the_roster_rather_than_restating_it",
                     [good]) is None


# ── the Qt-free layers ──────────────────────────────────────────────────────

QT_FREE_VIOLATIONS = {
    "direct_qt": "from PySide6.QtWidgets import QComboBox\n",
    # A widget import pulls Qt transitively — the hole the services contract
    # already plugs, and the reason `widgets` is forbidden alongside Qt itself.
    "via_widgets": "from widgets.stepper import Stepper\n",
    "function_local": "def build():\n    from PySide6.QtCore import Qt\n    return Qt\n",
}


@pytest.mark.parametrize("shape", sorted(QT_FREE_VIOLATIONS))
def test_qt_free_guard_catches_qt_in_domain(tmp_path, monkeypatch, shape):
    _write(tmp_path, "well_viewer/domain/bad_qt.py", QT_FREE_VIOLATIONS[shape])
    module = importlib.import_module("tests.test_qt_free_layers")
    exc = _run_rooted_scan(
        monkeypatch, "tests.test_qt_free_layers", "test_qt_free_layers_import_no_qt",
        tmp_path, {"QT_FREE_ROOTS": {"well_viewer/domain": module.QT_FREE_ROOTS["well_viewer/domain"]},
                   "ALLOWLIST": set()})
    assert exc is not None, f"the {shape} Qt import went uncaught"
    assert "bad_qt.py" in str(exc)


def test_qt_free_guard_clears_pure_logic(tmp_path, monkeypatch):
    _write(tmp_path, "well_viewer/domain/good.py", "import math\n\n\ndef f(x):\n    return math.sqrt(x)\n")
    module = importlib.import_module("tests.test_qt_free_layers")
    assert _run_rooted_scan(
        monkeypatch, "tests.test_qt_free_layers", "test_qt_free_layers_import_no_qt",
        tmp_path, {"QT_FREE_ROOTS": {"well_viewer/domain": module.QT_FREE_ROOTS["well_viewer/domain"]},
                   "ALLOWLIST": set()}) is None


# ── Datasets tool isolation ─────────────────────────────────────────────────

def test_tool_isolation_guard_catches_a_cross_tool_import(tmp_path, monkeypatch):
    _write(tmp_path, "datasets/subset/subset_compute.py",
           "from datasets.concatenate.concatenate_compute import known_channel_tokens\n")
    module = importlib.import_module("tests.test_datasets_tool_isolation")
    monkeypatch.setattr(module, "_tool_packages", lambda: ["subset", "concatenate"])
    exc = _run_rooted_scan(monkeypatch, "tests.test_datasets_tool_isolation",
                           "test_no_tool_imports_another_tool", tmp_path,
                           {"DATASETS": tmp_path / "datasets"})
    assert exc is not None and "subset_compute.py" in str(exc)


def test_tool_isolation_guard_clears_a_common_import(tmp_path, monkeypatch):
    _write(tmp_path, "datasets/subset/subset_compute.py",
           "from datasets._common.dataset_paths import known_channel_tokens\n")
    module = importlib.import_module("tests.test_datasets_tool_isolation")
    monkeypatch.setattr(module, "_tool_packages", lambda: ["subset", "concatenate"])
    assert _run_rooted_scan(monkeypatch, "tests.test_datasets_tool_isolation",
                            "test_no_tool_imports_another_tool", tmp_path,
                            {"DATASETS": tmp_path / "datasets"}) is None


# ── the Analyze layout readout ───────────────────────────────────────────────
# The shipped shape: the readout composed its own sentence from its own copy of
# the layout rules, and promised "using as input" over a run that then raised
# "Cannot determine input layout". The f-string variant is the one that survived
# the first pass of the fix — a label written from an interpolated literal is
# still a second opinion about the layout, ``✓`` prefix and all.

@pytest.mark.parametrize("shape", [
    'self._layout_lbl.setText("✓ Found in/ subfolder — using as input")',
    'self._layout_lbl.setText(f"✓ Will group {n} loose TIFs in in/")',
    'tab._layout_lbl.setText("No TIF files or in/ folder found.")',
])
def test_layout_readout_guard_catches_a_hand_written_verdict(
        tmp_path, monkeypatch, shape):
    _write(tmp_path, "analyze_tab.py",
           f"def _refresh_output(self):\n    {shape}\n")
    exc = _run_scan(monkeypatch, "tests.test_layout_readout_single_source",
                    "test_the_layout_readout_is_not_written_in_the_gui",
                    [tmp_path / "analyze_tab.py"])
    assert exc is not None, f"the guard walked past {shape!r}"
    assert "analyze_tab.py" in str(exc)


@pytest.mark.parametrize("shape", [
    "self._layout_lbl.setText(plan.message)",
    'self._layout_lbl.setText("")',
    "self._layout_lbl.setText(msg)",
])
def test_layout_readout_guard_clears_a_quoted_value(tmp_path, monkeypatch, shape):
    _write(tmp_path, "analyze_tab.py",
           f"def _refresh_output(self):\n    {shape}\n")
    assert _run_scan(monkeypatch, "tests.test_layout_readout_single_source",
                     "test_the_layout_readout_is_not_written_in_the_gui",
                     [tmp_path / "analyze_tab.py"]) is None


# ── ON-cutoff sweep ──────────────────────────────────────────────────────────
#
# Every plot path must reach its ON cutoff through `on_cutoff_for_column`, or a
# spot tally gets measured against a fluorescence threshold that sits above the
# whole range a tally can occupy — the plot then draws nothing at all.
#
# All four shapes below are real. The bare call is what most of the converted
# sites looked like; the aliased import is how both batch-export panels spell
# it; `app._get_thresh_frac_on` is the god-object shim in `runtime_app`. The
# fourth — binding the accessor to a local and calling it on the next line — is
# the one that matters most here: the first version of this guard matched
# `ast.Call` and reported a clean sweep straight past two sites written that
# way, which is also the shape a deliberate evasion would take.

@pytest.mark.parametrize("shape", [
    "    threshold = get_thresh_frac_on(ctx, ctx.state.channel.active_channel)",
    "    threshold = _get_thresh_frac_on(self._ctx, key)",
    "    threshold = app._get_thresh_frac_on(channel)",
    "    read = get_thresh_frac_on\n    threshold = read(ctx)",
    "    threshold = get_thresh_count_on(ctx, channel)",
])
def test_on_cutoff_guard_catches_a_raw_accessor(tmp_path, monkeypatch, shape):
    _write(tmp_path, "controller.py", f"def redraw_foo(ctx):\n{shape}\n")
    exc = _run_scan(monkeypatch, "tests.test_on_cutoff_sweep",
                    "test_no_path_picks_its_own_on_cutoff",
                    [tmp_path / "controller.py"])
    assert exc is not None, f"the guard walked past {shape!r}"
    assert "redraw_foo" in str(exc)


# The four shapes a reviewer used to walk straight past an earlier version of
# this guard. Each stayed green; each is now a case.

@pytest.mark.parametrize("shape", [
    "from x import get_thresh_frac_on as fetch_cutoff\nfetch_cutoff(ctx, ch)",
    '_raw = _lazy("well_viewer.core.channel_state_controller", "get_thresh_frac_on")',
    'threshold = getattr(csc, "get_thresh_frac_on")(ctx, ch)',
    'threshold = vars(csc)["get_thresh_count_on"](ctx, ch)',
])
def test_on_cutoff_guard_catches_an_indirect_reach(tmp_path, monkeypatch, shape):
    _write(tmp_path, "controller.py", f"{shape}\n")
    exc = _run_scan(monkeypatch, "tests.test_on_cutoff_sweep",
                    "test_no_path_picks_its_own_on_cutoff",
                    [tmp_path / "controller.py"])
    assert exc is not None, f"the guard walked past {shape!r}"


def test_on_cutoff_exemption_is_not_shared_by_bare_name(tmp_path, monkeypatch):
    """An exemption belongs to one closure, not to every closure of that name.

    ``refresh_thresh_frac_quick_edit._sync`` legitimately reads a raw accessor:
    it picks between the two by the same predicate the resolver uses. Keyed on
    the bare ``_sync``, that exemption covered any closure of that name anywhere
    in a 1,300-line module — including one added later by a plot path, which is
    precisely the reach the rule exists to catch.
    """
    src = ("def refresh_thresh_frac_quick_edit(ctx):\n"
           "    def _sync(attr):\n"
           "        return get_thresh_frac_on(ctx, attr)\n"
           "\n"
           "def redraw_something(ctx):\n"
           "    def _sync(attr):\n"
           "        return get_thresh_frac_on(ctx, attr)\n")
    path = _write(tmp_path, "channel_state_controller.py", src)
    module = importlib.import_module("tests.test_on_cutoff_sweep")
    monkeypatch.setattr(module, "python_sources", lambda **kw: [path])
    monkeypatch.setattr(
        module, "rel",
        lambda p: "well_viewer/core/channel_state_controller.py")
    try:
        module.test_no_path_picks_its_own_on_cutoff()
    except AssertionError as exc:
        message = str(exc)
    else:
        pytest.fail("the guard walked past a second _sync closure")
    assert "redraw_something._sync" in message
    assert "refresh_thresh_frac_quick_edit._sync" not in message


def test_on_cutoff_roster_rejects_a_name_that_does_not_exist(monkeypatch):
    """The roster's own liveness check fires on a symbol nothing defines."""
    module = importlib.import_module("tests.test_on_cutoff_sweep")
    monkeypatch.setattr(module, "GUARDED", ("get_thresh_frac_on", "no_such_accessor"))
    with pytest.raises(AssertionError, match="no_such_accessor"):
        module.test_guarded_names_are_real()


@pytest.mark.parametrize("shape", [
    "    threshold = on_cutoff_for_column(ctx, val_col)",
    "    threshold = on_cutoff_for_column(ctx, val_col, channel=ch_x)",
    "    threshold = _on_cutoff_for_column(self._ctx, col, channel=key)",
])
def test_on_cutoff_guard_clears_the_resolver(tmp_path, monkeypatch, shape):
    _write(tmp_path, "controller.py", f"def redraw_foo(ctx):\n{shape}\n")
    assert _run_scan(monkeypatch, "tests.test_on_cutoff_sweep",
                     "test_no_path_picks_its_own_on_cutoff",
                     [tmp_path / "controller.py"]) is None

# ── selection messages on axes ──────────────────────────────────────────────
# Every shape below either shipped in this repository or slipped past an earlier
# version of the guard. The direct ``ax.text`` call is what four of the five
# copies looked like; the message handed to ``draw_bar_empty_state`` is the one
# the first version could not see, and it was the worst instance — Bar Plots
# painted it onto three axes in a loop. The module-constant form is how
# Distribution and Line Graphs actually reached their message, so a guard reading
# only literal call arguments would have cleared both.

AXES_SELECTION_VIOLATIONS = {
    "direct": 'def draw(ax):\n    ax.text(0.5, 0.5, "No wells selected. Select wells to plot.")\n',
    "helper": ('MSG = "No wells or well groups selected.\\nSelect wells on the left panel."\n'
               "def draw(ctx, a, b):\n    draw_bar_empty_state(ctx, a, b, MSG)\n"),
    "constant": ('PROMPT = "Select one or more wells to summarise."\n'
                 "def draw(ax):\n    ax.text(0.5, 0.5, PROMPT)\n"),
    "suptitle": 'def draw(fig):\n    fig.suptitle("Choose wells on the plate to begin.")\n',
    "keyword": 'def draw(ax):\n    ax.text(0.5, 0.5, s="Pick a well to view its images.")\n',
}


@pytest.mark.parametrize("shape", sorted(AXES_SELECTION_VIOLATIONS))
def test_axes_selection_guard_catches_a_painted_prompt(tmp_path, monkeypatch, shape):
    bad = _write(tmp_path, "bad_renderer.py", AXES_SELECTION_VIOLATIONS[shape])
    exc = _run_scan(monkeypatch, "tests.test_no_axes_selection_message",
                    "test_no_interactive_renderer_paints_a_selection_message", [bad])
    assert exc is not None, f"the {shape} selection prompt went uncaught"
    assert "bad_renderer.py" in str(exc)


@pytest.mark.parametrize("shape", [
    # Not about the plate selection — these are the per-plot causes the
    # placeholder cannot know about, and they must stay on the axes.
    'def draw(ax):\n    ax.text(0.5, 0.5, "No timepoints loaded.")\n',
    'def draw(ax):\n    ax.text(0.5, 0.5, "Select a timepoint above")\n',
    'def draw(ax):\n    ax.text(0.5, 0.5, "No smFISH/mask pair found for this frame.")\n',
    # Reporting a computed result that happens to mention wells is not a prompt.
    'def draw(fig):\n    _draw_message(fig, "The summary produced no wells or timepoints.")\n',
    # A title naming the well being shown is not asking for one.
    'def draw(ax, well):\n    ax.set_title(f"{well} - t=0 h")\n',
])
def test_axes_selection_guard_clears_a_non_prompt(tmp_path, monkeypatch, shape):
    good = _write(tmp_path, "good_renderer.py", shape)
    assert _run_scan(monkeypatch, "tests.test_no_axes_selection_message",
                     "test_no_interactive_renderer_paints_a_selection_message",
                     [good]) is None


# ── help topics ─────────────────────────────────────────────────────────────
#
# The help-topic guard cannot be proven with `scripts/prove_guard.sh`: the maps
# it reads live in `help_topics.py`, a module the base branch does not have, so
# the run there dies on an import before measuring anything. This is the whole
# proof it gets, so both rules are exercised in both directions.

_FAKE_README = """# Manual

## Datasets mode — dataset-level tools

### Merge Plates

## Analyze mode — running the pipeline

### 1. Filename schema
"""

_FAKE_DATASETS_TAB = '''
_FEATURES = (
    _Feature(name="Merge Plates", icon="layout-grid", build=_b, kind="merge_plates"),
    _Feature(name="Brand New Tool", icon="boxes", build=_b, kind="brand_new"),
)
'''

_FAKE_ANALYZE_FORM = '''
class Form:
    def _build(self, parent):
        self._section(parent, "Filename Schema")
        self._section(parent, "Brand New Section", collapsible=True)
'''


def _help_topics_root(tmp_path: Path) -> Path:
    """A synthetic repo root holding the three files the guard reads."""
    _write(tmp_path, "_Docs/Markdowns/README_WellViewer.md", _FAKE_README)
    _write(tmp_path, "datasets_tab.py", _FAKE_DATASETS_TAB)
    _write(tmp_path, "analyze_tab_form.py", _FAKE_ANALYZE_FORM)
    return tmp_path


def test_help_topic_guard_catches_a_datasets_tool_with_no_topic(tmp_path, monkeypatch):
    """The shape that shipped: seven Datasets tools, zero topic keys."""
    root = _help_topics_root(tmp_path)
    exc = _run_rooted_scan(monkeypatch, "tests.test_help_topics",
                           "test_every_datasets_tool_has_a_topic", root)
    assert exc is not None, "an unmapped Datasets tool went uncaught"
    assert "Brand New Tool" in str(exc)


def test_help_topic_guard_catches_an_analyze_section_with_no_topic(tmp_path, monkeypatch):
    root = _help_topics_root(tmp_path)
    exc = _run_rooted_scan(monkeypatch, "tests.test_help_topics",
                           "test_every_analyze_section_has_a_topic", root)
    assert exc is not None, "an unmapped Analyze form section went uncaught"
    assert "Brand New Section" in str(exc)


def test_help_topic_guard_catches_a_topic_naming_no_heading(monkeypatch):
    """A value that matches no heading scrolls nowhere, silently.

    Run against the *real* manual, with one entry repointed. A synthetic README
    holding a handful of headings would fail this guard on every topic at once,
    with or without the repointing — the case would go green while proving
    nothing about the entry it claims to be testing.
    """
    import help_topics
    from tests import test_help_topics as guard

    guard.test_every_topic_names_a_real_heading()      # clean before
    monkeypatch.setitem(help_topics.DATASETS_PAGE_HELP, "Merge Plates",
                        "No Such Heading")
    with pytest.raises(AssertionError) as caught:
        guard.test_every_topic_names_a_real_heading()
    assert "No Such Heading" in str(caught.value)
    assert "Merge Plates" in str(caught.value), "the failure must name the key at fault"


def test_help_topic_guard_catches_a_duplicated_heading(tmp_path, monkeypatch):
    """Two identical headings share one anchor, so one topic aims at the other's
    page."""
    root = tmp_path
    _write(root, "_Docs/Markdowns/README_WellViewer.md",
           _FAKE_README + "\n### Merge Plates\n")
    exc = _run_rooted_scan(monkeypatch, "tests.test_help_topics",
                           "test_headings_are_unique", root)
    assert exc is not None and "Merge Plates" in str(exc)


def test_help_topic_guard_clears_the_real_tree(monkeypatch):
    """The other direction: against the repository itself every rule holds, so
    the guard is measuring rather than failing on everything."""
    import tests.test_help_topics as guard
    guard.test_every_datasets_tool_has_a_topic()
    guard.test_every_analyze_section_has_a_topic()
    guard.test_every_topic_names_a_real_heading()
    guard.test_headings_are_unique()
    guard.test_no_topic_keys_for_pages_that_no_longer_exist()


# ── the dataset-health roster ───────────────────────────────────────────────

HEALTH_ROSTER_VIOLATIONS = {
    # What actually shipped: datasets/validate/dashboard.py carried its own
    # copy of the four states before the predicate was extracted. Running the
    # guard against that file (restored from the base branch) named it at
    # line 17, which is the measurement this case makes durable.
    "tuple": '_WELL_STATES = ("complete", "partial", "missing", "no_output")\n',
    "list": 'STATES = ["complete", "partial", "missing", "no_output"]\n',
    "set": 'STATES = {"complete", "partial", "missing", "no_output"}\n',
    # Three of four. A roster that has already dropped a state is more
    # suspect than a complete one, not less — §7.5 records one that went
    # six-of-eight stale without anyone noticing.
    "stale": '_STATES = ("complete", "partial", "no_output")\n',
    # Mixed in with unrelated strings, the shape the plot-card roster guard
    # was defeated by on its first version.
    "diluted": 'ORDER = ("header", "complete", "partial", "missing", "footer")\n',
}


@pytest.mark.parametrize("shape", sorted(HEALTH_ROSTER_VIOLATIONS))
def test_health_roster_guard_catches_a_second_copy(tmp_path, monkeypatch, shape):
    bad = _write(tmp_path, "bad_health.py", HEALTH_ROSTER_VIOLATIONS[shape])
    exc = _run_scan(monkeypatch, "tests.test_no_second_completeness_predicate",
                    "test_no_module_restates_the_dataset_health_roster", [bad])
    assert exc is not None, f"the {shape} roster went uncaught"
    assert "bad_health.py" in str(exc)


HEALTH_ROSTER_CLEAN = {
    # The import the sweep put in dashboard.py's place.
    "import": "from pipeline.completeness import HEALTH_STATES as _WELL_STATES\n",
    # A legend pairs each state with its label. One state per inner tuple, so
    # this is presentation rather than a second roster, and it must stay legal
    # — dashboard.py builds two of these and a colour map besides.
    "legend": ('ROWS = (("complete", "Complete"), ("partial", "Partial"),\n'
               '        ("missing", "No measurements"), ("no_output", "No output"))\n'),
    "colour_map": ('COLORS = {"complete": theme.Colors.success,\n'
                   '          "partial": theme.Colors.warn,\n'
                   '          "missing": theme.Colors.danger,\n'
                   '          "no_output": theme.Colors.text_muted}\n'),
    # A single state named in passing.
    "one_state": 'if state == "no_output":\n    pass\n',
}


@pytest.mark.parametrize("shape", sorted(HEALTH_ROSTER_CLEAN))
def test_health_roster_guard_clears_presentation_and_imports(tmp_path, monkeypatch, shape):
    good = _write(tmp_path, "good_health.py", HEALTH_ROSTER_CLEAN[shape])
    assert _run_scan(monkeypatch, "tests.test_no_second_completeness_predicate",
                     "test_no_module_restates_the_dataset_health_roster",
                     [good]) is None, f"the {shape} shape was wrongly flagged"


# ── heat-map hover readout ──────────────────────────────────────────────────
#
# The heat-map guard walks its own directory rather than the repo tree, so it
# is redirected through `_HEATMAP_DIR` instead of `python_sources`. Its two AST
# checks read `tab_view.py` and `controller.py` by name from that directory, so
# every case below writes files under those names.

def _run_heatmap_scan(monkeypatch, tmp_path, test_name):
    return _run_rooted_scan(monkeypatch, "tests.test_heatmap_no_hover_readout",
                            test_name, tmp_path,
                            extra={"_HEATMAP_DIR": tmp_path})


def test_heatmap_guard_catches_a_reinstated_status_label(tmp_path, monkeypatch):
    _write(tmp_path, "tab_view.py",
           'def build(ctx, reg):\n'
           '    reg.register("_heatmap_status_lbl", QLabel(""))\n')
    exc = _run_heatmap_scan(monkeypatch, tmp_path,
                            "test_no_hover_status_widget_in_the_heatmap_tab")
    assert exc is not None and "tab_view.py" in str(exc)


def test_heatmap_guard_clears_a_band_without_a_status_label(tmp_path, monkeypatch):
    _write(tmp_path, "tab_view.py",
           'def build(ctx, reg):\n'
           '    reg.register("_heatmap_tp_cb", LabeledComboBox("Timepoint"))\n')
    assert _run_heatmap_scan(monkeypatch, tmp_path,
                             "test_no_hover_status_widget_in_the_heatmap_tab") is None


def test_heatmap_guard_catches_motion_dispatch_to_a_hover_handler(tmp_path, monkeypatch):
    _write(tmp_path, "tab_view.py",
           'def _on_canvas_motion(ctx, evt):\n'
           '    on_heatmap_label_drag_motion(ctx, evt)\n'
           '    on_heatmap_motion(ctx, evt)\n')
    exc = _run_heatmap_scan(monkeypatch, tmp_path,
                            "test_canvas_motion_only_drives_the_label_drag")
    assert exc is not None and "on_heatmap_motion" in str(exc)


def test_heatmap_guard_clears_motion_dispatch_to_the_drag_alone(tmp_path, monkeypatch):
    _write(tmp_path, "tab_view.py",
           'def _on_canvas_motion(ctx, evt):\n'
           '    on_heatmap_label_drag_motion(ctx, evt)\n')
    assert _run_heatmap_scan(monkeypatch, tmp_path,
                             "test_canvas_motion_only_drives_the_label_drag") is None


def test_heatmap_guard_catches_a_reinstated_hover_handler(tmp_path, monkeypatch):
    _write(tmp_path, "controller.py",
           'def on_heatmap_motion(ctx, event):\n    return None\n')
    exc = _run_heatmap_scan(monkeypatch, tmp_path,
                            "test_controller_exposes_no_hover_motion_handler")
    assert exc is not None and "on_heatmap_motion" in str(exc)


def test_heatmap_guard_clears_a_controller_with_click_handlers_only(tmp_path, monkeypatch):
    _write(tmp_path, "controller.py",
           'def on_heatmap_click(ctx, event):\n    return None\n')
    assert _run_heatmap_scan(monkeypatch, tmp_path,
                             "test_controller_exposes_no_hover_motion_handler") is None


# ── nested removable sub-packages ───────────────────────────────────────────
#
# The container guard walks the tree itself and reads `profiles/full.toml`, so
# a synthetic case has to supply both: a catalog naming a nested removable unit
# and a source tree that either violates it or does not.

_NESTED_CATALOG = (
    'name = "full"\n\n'
    '[[feature]]\n'
    'name = "Ratios"\n'
    'parent = "Home"\n'
    'paths = ["well_viewer/features/home/ratios"]\n'
)


def _nested_tree(tmp_path: Path, container_body: str) -> Path:
    (tmp_path / "profiles").mkdir(parents=True, exist_ok=True)
    (tmp_path / "profiles" / "full.toml").write_text(_NESTED_CATALOG)
    pkg = tmp_path / "well_viewer" / "features" / "home"
    (pkg / "ratios").mkdir(parents=True, exist_ok=True)
    (pkg / "ratios" / "tab_view.py").write_text("PANEL = 1\n")
    (pkg / "controller.py").write_text(container_body)
    return tmp_path


def _run_nested_scan(monkeypatch, root: Path, test_name: str, exempt=frozenset()):
    """Point the container guard at *root* and run one of its tests.

    It derives its scan surface from `tests/_repo_scan.python_sources`, imported
    into its own namespace, so redirecting that plus `REPO_ROOT` / `WELL_VIEWER`
    aims the whole rule at the synthetic tree.
    """
    files = [q for q in sorted((root / "well_viewer").rglob("*.py"))]
    return _run_rooted_scan(
        monkeypatch, "tests.test_architecture_contracts", test_name, root,
        extra={"WELL_VIEWER": root / "well_viewer",
               "python_sources": lambda **kw: files,
               "_DISPATCH_EXEMPT": set(exempt)})


_GUARD = "test_a_container_feature_does_not_import_its_own_removable_sub_packages"

#: Every shape that reaches a nested removable sub-package from its container.
#: The first is the one the guard was written for; the rest are evasions a
#: reviewer demonstrated against the first version of it, and the function-local
#: one is the shape that actually shipped a defect on this branch — the guard
#: used to *clear* it.
NESTED_VIOLATIONS = {
    "module_scope": "from well_viewer.features.home.ratios.tab_view import PANEL\n",
    "function_local": ("def build(ctx, frame):\n"
                       "    from well_viewer.features.home.ratios.tab_view import PANEL\n"
                       "    return PANEL\n"),
    "plain_import": "import well_viewer.features.home.ratios.tab_view as rv\n",
    "relative": "from .ratios.tab_view import PANEL\n",
    "try_wrapped": ("try:\n"
                    "    from well_viewer.features.home.ratios.tab_view import PANEL\n"
                    "except ImportError:\n"
                    "    PANEL = None\n"),
    "aliased": "from well_viewer.features.home.ratios.tab_view import PANEL as _p\n",
}


@pytest.mark.parametrize("shape", sorted(NESTED_VIOLATIONS))
def test_nested_guard_catches_every_reach_into_a_sub_package(
        tmp_path, monkeypatch, shape):
    root = _nested_tree(tmp_path, NESTED_VIOLATIONS[shape])
    exc = _run_nested_scan(monkeypatch, root, _GUARD)
    assert exc is not None, f"the {shape} reach went uncaught"
    assert "controller.py" in str(exc) and "Ratios" in str(exc)


def test_nested_guard_names_a_function_local_reach_as_still_fatal(
        tmp_path, monkeypatch):
    """The message has to say why deferring does not help, or the next reader
    'fixes' the failure by indenting the import."""
    root = _nested_tree(tmp_path, NESTED_VIOLATIONS["function_local"])
    exc = _run_nested_scan(monkeypatch, root, _GUARD)
    assert "function-local" in str(exc)


def test_nested_guard_clears_a_module_inside_the_sub_package_itself(
        tmp_path, monkeypatch):
    """A sub-package importing its own siblings is not a removability problem —
    the whole directory goes at once."""
    root = _nested_tree(tmp_path, "STUB = 1\n")
    (root / "well_viewer" / "features" / "home" / "ratios" / "controller.py"
     ).write_text("from well_viewer.features.home.ratios.tab_view import PANEL\n")
    assert _run_nested_scan(monkeypatch, root, _GUARD) is None


def test_nested_guard_clears_a_listed_dispatch_site(tmp_path, monkeypatch):
    """The one sanctioned reach: a container selecting a builder by tab name,
    whose branch is unreachable once the tab is gone."""
    root = _nested_tree(tmp_path, NESTED_VIOLATIONS["function_local"])
    assert _run_nested_scan(
        monkeypatch, root, _GUARD,
        exempt={("well_viewer/features/home/controller.py",
                 "well_viewer.features.home.ratios")}) is None


def test_nested_exemption_liveness_catches_an_entry_that_no_longer_bites(
        tmp_path, monkeypatch):
    """An exemption naming an import that has since been repaired must be
    reported, or it outlives the violation it was written for."""
    root = _nested_tree(tmp_path, "STUB = 1\n")
    exc = _run_nested_scan(
        monkeypatch, root, "test_the_dispatch_exemption_still_describes_real_imports",
        exempt={("well_viewer/features/home/controller.py",
                 "well_viewer.features.home.ratios")})
    assert exc is not None and "no longer describe" in str(exc)


# ── Home sub-tab help topics ────────────────────────────────────────────────

_HOME_REGISTRY_SRC = (
    "FEATURES = (\n"
    '    FeatureDescriptor(name="Home", group="Review"),\n'
    '    FeatureDescriptor(name="Ratios", parent="Home", build=_build_ratios),\n'
    ")\n"
)


def _run_home_topic_scan(monkeypatch, tmp_path, topics: dict):
    import help_topics

    (tmp_path / "well_viewer" / "features").mkdir(parents=True, exist_ok=True)
    (tmp_path / "well_viewer" / "features" / "registry.py").write_text(
        _HOME_REGISTRY_SRC)
    monkeypatch.setattr(help_topics, "SECTION_BY_PAGE", topics)
    return _run_rooted_scan(monkeypatch, "tests.test_help_topics",
                            "test_every_home_subtab_has_a_topic", tmp_path)


def test_home_topic_guard_catches_a_subtab_with_no_topic(tmp_path, monkeypatch):
    exc = _run_home_topic_scan(monkeypatch, tmp_path, {"Home": "Home"})
    assert exc is not None and "Ratios" in str(exc)


def test_home_topic_guard_clears_a_subtab_that_has_one(tmp_path, monkeypatch):
    assert _run_home_topic_scan(
        monkeypatch, tmp_path, {"Home": "Home", "Ratios": "Ratios"}) is None


# ── timers inside a logging handler ─────────────────────────────────────────

# Both shipped shapes, plus the two spellings that would have slipped past a
# guard written to match only the first. `nested_in_a_function` is how the shell
# defines `_RingHandler` — inside the method that installs it, invisible to a
# scan that reads `tree.body`. `aliased` is that same site's `import QTimer as
# _QTimer`. `deferred_in_a_lambda` and `constructed` are the two other ways to
# bind a timer to the emitting thread.
LOG_TIMER_VIOLATIONS = {
    "module_level": (
        "class _GUILogHandler(logging.Handler):\n"
        "    def emit(self, record):\n"
        "        QTimer.singleShot(0, lambda: self._append(record))\n"),
    "nested_in_a_function": (
        "def install(app):\n"
        "    class _RingHandler(logging.Handler):\n"
        "        def emit(self, record):\n"
        "            QTimer.singleShot(0, lambda: app.append(record))\n"),
    "aliased": (
        "class _H(logging.Handler):\n"
        "    def emit(self, record):\n"
        "        _QTimer.singleShot(0, lambda: self._append(record))\n"),
    "deferred_in_a_lambda": (
        "class _H(Handler):\n"
        "    def emit(self, record):\n"
        "        run_later(lambda: QTimer.singleShot(0, self._append))\n"),
    "constructed": (
        "class _H(logging.Handler):\n"
        "    def emit(self, record):\n"
        "        t = QTimer()\n"
        "        t.timeout.connect(self._append)\n"),
    "start_timer": (
        "class _H(logging.Handler):\n"
        "    def emit(self, record):\n"
        "        self.startTimer(0)\n"),
}


@pytest.mark.parametrize("shape", sorted(LOG_TIMER_VIOLATIONS))
def test_log_handler_guard_catches_a_timer_in_emit(tmp_path, monkeypatch, shape):
    bad = _write(tmp_path, "bad_handler.py", LOG_TIMER_VIOLATIONS[shape])
    exc = _run_scan(monkeypatch, "tests.test_log_handler_marshalling",
                    "test_no_timer_inside_a_logging_handler_emit", [bad])
    assert exc is not None, f"the {shape} timer violation went uncaught"
    assert "bad_handler.py" in str(exc)


LOG_TIMER_CLEAN = {
    # The sanctioned route: a GuiLogRelay built on the GUI thread.
    "relay": (
        "class _H(logging.Handler):\n"
        "    def emit(self, record):\n"
        "        self._relay.post(self.format(record))\n"),
    # A timer outside emit is fine — that method runs where it was called from,
    # and the rule is about the thread emit() is entered on.
    "timer_outside_emit": (
        "class _H(logging.Handler):\n"
        "    def emit(self, record):\n"
        "        self._relay.post(record.msg)\n"
        "    def flush_later(self):\n"
        "        QTimer.singleShot(0, self.flush)\n"),
    # A plain class with an emit method is not a logging handler.
    "not_a_handler": (
        "class Sparkline:\n"
        "    def emit(self, value):\n"
        "        QTimer.singleShot(0, lambda: self._draw(value))\n"),
}


@pytest.mark.parametrize("shape", sorted(LOG_TIMER_CLEAN))
def test_log_handler_guard_clears_the_sanctioned_shapes(tmp_path, monkeypatch, shape):
    good = _write(tmp_path, "good_handler.py", LOG_TIMER_CLEAN[shape])
    assert _run_scan(monkeypatch, "tests.test_log_handler_marshalling",
                     "test_no_timer_inside_a_logging_handler_emit", [good]) is None


# ── the Events total stays out of drawing code ──────────────────────────────
#
# The violations are the real shapes. `compute_rep_n_above` — a near-synonym of
# `compute_rep_n_above_stats` differing only by an absent suffix — was read by
# the Line tab's numerator and by both fold-change denominators, so the two
# tabs drew different numbers for the same replicate set and a set divided by
# itself came out at 1/n. Its replacement is named for its consumer, and this
# is what keeps that meaning from eroding.

EVENTS_TOTAL_VIOLATIONS = {
    "from-import": (
        "from well_viewer.domain.stats_compute import compute_rep_n_above_total\n"
        "def draw(ctx, rset, t):\n"
        "    return compute_rep_n_above_total(ctx, rset, t)\n"
    ),
    # The attribute spelling, which a from-import guard alone would walk past.
    "attribute": (
        "from well_viewer.domain import stats_compute as sc\n"
        "def draw(ctx, rset, t):\n"
        "    return sc.compute_rep_n_above_total(ctx, rset, t)\n"
    ),
    # Aliased on import — the shape that hides the name from a text search.
    "aliased": (
        "from well_viewer.domain.stats_compute import (\n"
        "    compute_rep_n_above_total as _n,\n"
        ")\n"
        "def draw(ctx, rset, t):\n"
        "    return _n(ctx, rset, t)\n"
    ),
}

EVENTS_TOTAL_CLEAN = (
    "from well_viewer.domain.stats_compute import compute_rep_n_above_stats\n"
    "def draw(ctx, rset, t):\n"
    "    mean, spread = compute_rep_n_above_stats(ctx, rset, t, True)\n"
    "    return mean, spread\n"
)


@pytest.mark.parametrize("shape", sorted(EVENTS_TOTAL_VIOLATIONS))
def test_events_total_guard_catches(tmp_path, monkeypatch, shape):
    f = _write(tmp_path, "well_viewer/features/barplot/renderer.py",
               EVENTS_TOTAL_VIOLATIONS[shape])
    exc = _run_scan(monkeypatch, "tests.test_events_total_isolation",
                    "test_the_events_total_stays_out_of_drawing_code", [f])
    assert exc is not None, f"the {shape} spelling walked past the guard"


def test_events_total_guard_clears_the_plotted_statistic(tmp_path, monkeypatch):
    f = _write(tmp_path, "well_viewer/features/barplot/renderer.py",
               EVENTS_TOTAL_CLEAN)
    exc = _run_scan(monkeypatch, "tests.test_events_total_isolation",
                    "test_the_events_total_stays_out_of_drawing_code", [f])
    assert exc is None, f"the guard fired on the statistic every panel draws: {exc}"


# ── the fold-change request is read by name, never unpacked ─────────────────
#
# The shapes below are the ones that really shipped. Both were live defects on
# a branch whose CI was green, because both sit on paths only the slow lane
# reaches — a `TypeError` on every bar redraw, and a helper every caller of
# "is fold change on?" goes through.

FC_UNPACK_VIOLATIONS = {
    # `barplot/renderer._set_frac_ylim`, verbatim.
    "attribute-call": (
        "from well_viewer.domain import fold_change as _fc\n"
        "def f(ctx):\n"
        "    fc_on, _, fc_t0 = _fc.fold_change_state(ctx)\n"
        "    return fc_on or fc_t0\n"
    ),
    # `fold_change.is_active`, verbatim — the bare-name spelling, which a guard
    # matching only on the attribute form would walk past.
    "bare-call": (
        "def f(ctx):\n"
        "    vs_ctrl, _, vs_t0 = fold_change_state(ctx)\n"
        "    return vs_ctrl or vs_t0\n"
    ),
    # The coercion helper produces the same object from a caller's argument.
    "coerce": (
        "from well_viewer.domain import fold_change as _fc\n"
        "def f(fc_state):\n"
        "    on, label, t0 = _fc.coerce_fc_state(fc_state)\n"
        "    return on, label, t0\n"
    ),
    # A list target unpacks exactly the same way and reads as deliberate.
    "list-target": (
        "def f(ctx):\n"
        "    [on, label, t0] = fold_change_state(ctx)\n"
        "    return on\n"
    ),
}

FC_UNPACK_CLEAN = (
    "from well_viewer.domain import fold_change as _fc\n"
    "def f(ctx):\n"
    "    fc = _fc.fold_change_state(ctx)\n"
    "    if fc.per_member:\n"
    "        return fc.control_map_dict\n"
    "    return fc.active and fc.control\n"
)


@pytest.mark.parametrize("shape", sorted(FC_UNPACK_VIOLATIONS))
def test_fc_unpack_guard_catches(tmp_path, monkeypatch, shape):
    f = _write(tmp_path, "well_viewer/features/barplot/renderer.py",
               FC_UNPACK_VIOLATIONS[shape])
    exc = _run_scan(monkeypatch, "tests.test_fc_state_is_not_unpacked",
                    "test_the_fold_change_request_is_read_by_name", [f])
    assert exc is not None, f"the {shape} spelling walked past the guard"


def test_fc_unpack_guard_clears_reading_it_by_name(tmp_path, monkeypatch):
    f = _write(tmp_path, "well_viewer/features/barplot/renderer.py",
               FC_UNPACK_CLEAN)
    exc = _run_scan(monkeypatch, "tests.test_fc_state_is_not_unpacked",
                    "test_the_fold_change_request_is_read_by_name", [f])
    assert exc is None, f"the guard fired on the correct spelling: {exc}"


FC_UNPACK_METHOD_VIOLATION = (
    "class Panel:\n"
    "    def run(self):\n"
    "        fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0 = self._fc_state()\n"
    "        return fc_vs_ctrl\n"
)


def test_fc_unpack_guard_sees_the_panels_own_accessor(tmp_path, monkeypatch):
    """The spelling both Batch Export panels carried before the typed object.

    A guard that only knew the two module-level producers would have been
    blind to exactly the shape it was written for.
    """
    f = _write(tmp_path, "well_viewer/features/batch_export/bar_panel.py",
               FC_UNPACK_METHOD_VIOLATION)
    exc = _run_scan(monkeypatch, "tests.test_fc_state_is_not_unpacked",
                    "test_the_fold_change_request_is_read_by_name", [f])
    assert exc is not None, "the method-call spelling walked past the guard"


# ── no function reads a name nothing binds ──────────────────────────────────
#
# The shapes below are the two that really shipped, plus the closure and
# comprehension forms a hand-rolled AST walk gets wrong — those must stay
# *clear*, or the guard drowns in false positives and gets deleted.

UNBOUND_VIOLATIONS = {
    # `barplot/renderer._set_frac_ylim`, after the tuple unpack was replaced.
    "renamed-binding": (
        "def f(ctx):\n"
        "    fc = get_state(ctx)\n"
        "    if fc_on or fc_t0:\n"
        "        return\n"
    ),
    # `base_panel._render_group_figure`: a method, so a scan that only walks
    # module-level functions never reaches it.
    "inside-a-method": (
        "class Panel:\n"
        "    def render(self, fc_state):\n"
        "        fc = coerce(fc_state)\n"
        "        return fc_vs_ctrl and fc.active\n"
    ),
}

UNBOUND_CLEAN = {
    # A closure reading its enclosing function's locals.
    "closure": (
        "def outer(ctx, threshold):\n"
        "    def inner(key):\n"
        "        return ctx.get(key), threshold\n"
        "    return inner\n"
    ),
    # A comprehension reading the enclosing scope.
    "comprehension": (
        "def f(items, gate):\n"
        "    return [i for i in items if i > gate]\n"
    ),
    # `except … as` and `global`.
    "except-and-global": (
        "COUNT = 0\n"
        "def f():\n"
        "    global COUNT\n"
        "    try:\n"
        "        COUNT += 1\n"
        "    except ValueError as exc:\n"
        "        return str(exc)\n"
    ),
    # A method reading its own module's globals and its own class.
    "module-globals": (
        "import math\n"
        "LIMIT = 3\n"
        "class C:\n"
        "    def f(self):\n"
        "        return math.sqrt(LIMIT) + self.x\n"
    ),
}


@pytest.mark.parametrize("shape", sorted(UNBOUND_VIOLATIONS))
def test_unbound_name_guard_catches(shape):
    from tests.test_no_unbound_names import offenders_in
    found = offenders_in(UNBOUND_VIOLATIONS[shape], "probe.py")
    assert found, f"the {shape} spelling walked past the guard"


@pytest.mark.parametrize("shape", sorted(UNBOUND_CLEAN))
def test_unbound_name_guard_clears(shape):
    """A false positive here gets the guard deleted, so these matter as much
    as the violations do."""
    from tests.test_no_unbound_names import offenders_in
    found = offenders_in(UNBOUND_CLEAN[shape], "probe.py")
    assert not found, f"the guard fired on a legitimate {shape}: {found}"


# ── the context-menu seam ────────────────────────────────────────────────────
#
# Both spellings matter. The direct `menu.exec(...)` is the hang the guard
# exists to prevent; the by-name import is subtler — it leaves the seam in
# place and still unpatchable, because the function is bound into the importing
# module's globals before the fixture ever runs.

MENU_VIOLATIONS = {
    "direct-exec": (
        "def open_it(menu, btn):\n"
        "    menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))\n"
    ),
    "exec-underscore": (
        "def open_it(menu, pos):\n"
        "    menu.exec_(pos)\n"
    ),
    "attribute-menu": (
        "class W:\n"
        "    def open_it(self, pos):\n"
        "        self._menu.exec(pos)\n"
    ),
    "imported-by-name": (
        "from widgets.menu_exec import exec_menu\n"
        "def open_it(menu, pos):\n"
        "    return exec_menu(menu, pos)\n"
    ),
}

MENU_CLEAN = {
    "through-the-seam": (
        "from widgets import menu_exec\n"
        "def open_it(menu, btn):\n"
        "    return menu_exec.exec_menu(\n"
        "        menu, btn.mapToGlobal(btn.rect().bottomLeft()))\n"
    ),
    # A dialog's exec is the *other* guard's business, and is patchable.
    "a-dialog-not-a-menu": (
        "def ask(dlg):\n"
        "    return dlg.exec()\n"
    ),
}


@pytest.mark.parametrize("shape", sorted(MENU_VIOLATIONS))
def test_menu_seam_guard_catches(tmp_path, monkeypatch, shape):
    path = _write(tmp_path, f"probe_{shape}.py", MENU_VIOLATIONS[shape])
    exc = _run_scan(monkeypatch, "tests.test_menu_exec_seam",
                    "test_no_module_opens_a_context_menu_directly", [path])
    assert exc is not None, f"the {shape} spelling walked past the guard"


@pytest.mark.parametrize("shape", sorted(MENU_CLEAN))
def test_menu_seam_guard_clears(tmp_path, monkeypatch, shape):
    path = _write(tmp_path, f"probe_{shape}.py", MENU_CLEAN[shape])
    exc = _run_scan(monkeypatch, "tests.test_menu_exec_seam",
                    "test_no_module_opens_a_context_menu_directly", [path])
    assert exc is None, f"the guard fired on a legitimate {shape}: {exc}"


# ── swallow / swallowed ──────────────────────────────────────────────────────
#
# `with swallowed():` raises AttributeError: __enter__ *before* its body, so
# the guarded work never runs at all. It shipped in
# `fold_change_controls_view._open_editor`, where it meant the per-sample
# control editor could not be opened by the one control that opens it.

SAFE_VIOLATIONS = {
    # The shape that shipped.
    "with-swallowed": (
        "from well_viewer.core.safe import swallowed\n"
        "def f(ctx):\n"
        "    with swallowed():\n"
        "        risky(ctx)\n"
    ),
    # Same thing reached through the module, which a name-only check misses.
    "with-swallowed-qualified": (
        "from well_viewer.core import safe\n"
        "def f(ctx):\n"
        "    with safe.swallowed('note'):\n"
        "        risky(ctx)\n"
    ),
    # The mirror image: the context manager called and dropped.
    "swallow-as-statement": (
        "from well_viewer.core.safe import swallow\n"
        "def f(ctx):\n"
        "    swallow('note')\n"
        "    risky(ctx)\n"
    ),
    # A suppression logged where nothing was suppressed.
    "swallowed-outside-except": (
        "from well_viewer.core.safe import swallowed\n"
        "def f(gate):\n"
        "    if gate is None:\n"
        "        swallowed('no gate')\n"
    ),
}

SAFE_CLEAN = {
    "with-swallow": (
        "from well_viewer.core.safe import swallow\n"
        "def f(ctx):\n"
        "    with swallow('note'):\n"
        "        risky(ctx)\n"
    ),
    # The overwhelmingly common correct use, and the one a naive rule would
    # flag: `swallowed()` as a bare statement, inside a handler.
    "swallowed-in-except": (
        "from well_viewer.core.safe import swallowed\n"
        "def f(ctx):\n"
        "    try:\n"
        "        risky(ctx)\n"
        "    except Exception:\n"
        "        swallowed('note')\n"
    ),
    # Nested a level down inside the handler — still covered.
    "swallowed-nested-in-except": (
        "from well_viewer.core.safe import swallowed\n"
        "def f(items):\n"
        "    try:\n"
        "        risky(items)\n"
        "    except Exception:\n"
        "        for _ in items:\n"
        "            swallowed('note')\n"
    ),
    # The pipeline-side twin, used correctly.
    "aw_safe-context-manager": (
        "from util.aw_safe import swallow\n"
        "def f(p):\n"
        "    with swallow('io'):\n"
        "        p.unlink()\n"
    ),
}


@pytest.mark.parametrize("shape", sorted(SAFE_VIOLATIONS))
def test_safe_helper_guard_catches(shape):
    from tests.test_safe_helpers_used_right import offenders_in
    found = offenders_in(SAFE_VIOLATIONS[shape], "probe.py")
    assert found, f"the {shape} spelling walked past the guard"


@pytest.mark.parametrize("shape", sorted(SAFE_CLEAN))
def test_safe_helper_guard_clears(shape):
    """A false positive gets this guard deleted, so the clean cases matter as
    much as the violations — `swallowed()` in an except block is the norm."""
    from tests.test_safe_helpers_used_right import offenders_in
    found = offenders_in(SAFE_CLEAN[shape], "probe.py")
    assert not found, f"the guard fired on a legitimate {shape}: {found}"


# ── a disabled feature's state slice ─────────────────────────────────────────
#
# `AppState.__getattr__` raises for a removable feature's slice when that
# feature is not in FEATURES. The bar renderer reached `ctx.state.stats` behind
# a `getattr` default, which cannot help — the default covers a missing
# attribute on an object that already exists, and reaching the object is what
# raises. A profile build without Statistics hit it on every bar redraw.

SLICE_VIOLATIONS = {
    "plain-attribute": (
        "def draw(ctx):\n"
        "    return ctx.state.stats.pairwise\n"
    ),
    "behind-a-getattr-default": (
        "def draw(ctx):\n"
        "    return list(getattr(ctx.state.stats, 'pairwise', []) or [])\n"
    ),
    "assignment-into-the-slice": (
        "def restore(ctx, groups):\n"
        "    ctx.state.stats.groups = groups\n"
    ),
    "another-removable-feature": (
        "def draw(ctx):\n"
        "    return ctx.state.multivariate.loadings\n"
    ),
}

SLICE_CLEAN = {
    "through-feature-slice": (
        "def draw(ctx):\n"
        "    st = ctx.state.feature_slice('stats')\n"
        "    if st is None:\n"
        "        return []\n"
        "    return list(getattr(st, 'pairwise', []) or [])\n"
    ),
    # Kept-forever slices cannot be absent, so they are not the subject.
    "a-slice-that-is-never-removed": (
        "def draw(ctx):\n"
        "    return ctx.state.bar.tp_selections\n"
    ),
    # A same-named attribute on something that is not the state bundle.
    "not-the-state-bundle": (
        "def draw(run):\n"
        "    return run.results.stats.mean\n"
    ),
}


@pytest.mark.parametrize("shape", sorted(SLICE_VIOLATIONS))
def test_feature_slice_guard_catches(tmp_path, monkeypatch, shape):
    path = _write(tmp_path, f"probe_{shape}.py", SLICE_VIOLATIONS[shape])
    exc = _run_scan(monkeypatch, "tests.test_disabled_feature_slices",
                    "test_no_code_outside_a_feature_reaches_its_slice_by_attribute",
                    [path])
    assert exc is not None, f"the {shape} spelling walked past the guard"


@pytest.mark.parametrize("shape", sorted(SLICE_CLEAN))
def test_feature_slice_guard_clears(tmp_path, monkeypatch, shape):
    """The kept-forever case matters most: flagging `state.bar` would push
    every caller through a `None` check for something that cannot be None."""
    path = _write(tmp_path, f"probe_{shape}.py", SLICE_CLEAN[shape])
    exc = _run_scan(monkeypatch, "tests.test_disabled_feature_slices",
                    "test_no_code_outside_a_feature_reaches_its_slice_by_attribute",
                    [path])
    assert exc is None, f"the guard fired on a legitimate {shape}: {exc}"


# ── deleting a directory the archive only partly covers ──────────────────────
#
# The shape that shipped is the indirect one: the two worst sites never named
# ``atomic_zip`` in the function that did the deleting, they zipped through a
# one-line helper in the same module. A guard that only looked for a literal
# ``atomic_zip`` beside the ``rmtree`` found two of the four sites and read as
# a completed sweep.

UNARCHIVED_DELETE_VIOLATIONS = {
    # Zips an extension-filtered subset, then removes the whole folder.
    "direct": (
        "import shutil\n"
        "from util.zip_write import atomic_zip\n"
        "def pack(folder, target):\n"
        "    tifs = [p for p in folder.iterdir() if p.suffix == '.tif']\n"
        "    with atomic_zip(target) as zf:\n"
        "        for t in tifs:\n"
        "            zf.write(t, arcname=t.name)\n"
        "    shutil.rmtree(folder, ignore_errors=True)\n"
    ),
    # The archive write is one call away, in the same module.
    "through-a-local-helper": (
        "import shutil\n"
        "from util.zip_write import atomic_zip\n"
        "def _zip_it(folder, target):\n"
        "    with atomic_zip(target) as zf:\n"
        "        for t in folder.glob('*.tif'):\n"
        "            zf.write(t, arcname=t.name)\n"
        "def stage(folder, target):\n"
        "    _zip_it(folder, target)\n"
        "    shutil.rmtree(folder)\n"
    ),
    # Two hops, still the same module.
    "through-two-local-helpers": (
        "from util.zip_write import atomic_zip\n"
        "from pipeline.io import remove_directory\n"
        "def _write(target, files):\n"
        "    with atomic_zip(target) as zf:\n"
        "        for f in files:\n"
        "            zf.write(f, arcname=f.name)\n"
        "def _pack(folder, target):\n"
        "    _write(target, list(folder.glob('*.tif')))\n"
        "def consume(folder, target):\n"
        "    _pack(folder, target)\n"
        "    remove_directory(folder)\n"
    ),
}

UNARCHIVED_DELETE_CLEAN = {
    # The fixed shape: delete what was archived, keep the rest.
    "routes-through-the-helper": (
        "from util.zip_write import atomic_zip, replace_folder_with_archive\n"
        "def pack(folder, target):\n"
        "    tifs = [p for p in folder.iterdir() if p.suffix == '.tif']\n"
        "    with atomic_zip(target) as zf:\n"
        "        for t in tifs:\n"
        "            zf.write(t, arcname=t.name)\n"
        "    replace_folder_with_archive(folder, tifs)\n"
    ),
    # Writes an archive and removes nothing.
    "no-removal": (
        "from util.zip_write import atomic_zip\n"
        "def pack(folder, target):\n"
        "    with atomic_zip(target) as zf:\n"
        "        for t in folder.glob('*.tif'):\n"
        "            zf.write(t, arcname=t.name)\n"
    ),
    # Removes a directory and writes no archive — most of the tree.
    "no-archive": (
        "import shutil\n"
        "def clear(dest):\n"
        "    shutil.rmtree(dest, ignore_errors=True)\n"
    ),
    # The propagation is module-local on purpose: a same-named helper in some
    # other module must not make this function an archive writer.
    "same-name-helper-lives-elsewhere": (
        "import shutil\n"
        "from somewhere.else_ import _zip_it\n"
        "def stage(folder, target):\n"
        "    _zip_it(folder, target)\n"
        "    shutil.rmtree(folder)\n"
    ),
}


@pytest.mark.parametrize("shape", sorted(UNARCHIVED_DELETE_VIOLATIONS))
def test_unarchived_delete_guard_catches(shape):
    from tests.test_no_unarchived_delete import offenders_in
    found = offenders_in(UNARCHIVED_DELETE_VIOLATIONS[shape], "probe.py")
    assert found, f"the {shape} spelling walked past the guard"


@pytest.mark.parametrize("shape", sorted(UNARCHIVED_DELETE_CLEAN))
def test_unarchived_delete_guard_clears(shape):
    from tests.test_no_unarchived_delete import offenders_in
    found = offenders_in(UNARCHIVED_DELETE_CLEAN[shape], "probe.py")
    assert not found, f"the guard fired on a legitimate {shape}: {found}"


# ── figures registered without a stable preference key ───────────────────────
#
# The guard reads `_FIG_PREF_KEY_MAP` from the real module, so what these
# exercise is the half that can drift silently: which call sites the scan
# recognises as registering a figure, and which lookalikes it must not.

FIGURE_REGISTRATION_SEEN = {
    "plain-register": (
        "def build(ctx, frame):\n"
        "    reg = ctx.widgets\n"
        "    reg.register('_sp_fig', card.figure)\n"
    ),
    "register-through-the-context": (
        "def build(ctx, frame):\n"
        "    ctx.widgets.register('_mv_fig', card.figure)\n"
    ),
    "register-used-as-an-expression": (
        "def build(ctx, frame):\n"
        "    fig = reg.register('_stats_fig', _StatsFigure(figsize=(5, 2.8)))\n"
        "    return fig\n"
    ),
}

FIGURE_REGISTRATION_UNSEEN = {
    # `_is_line_fig` is a predicate on the export sidebar, not a figure, and it
    # appears as a bare literal elsewhere in the tree. Matching every string
    # that ends in `_fig` would drag it in and demand a key for it.
    "a-bare-literal-that-is-not-a-registration": (
        "def f(app, fig):\n"
        "    if getattr(app, '_line_fig', None) is fig:\n"
        "        return '_is_line_fig'\n"
    ),
    "registering-something-that-is-not-a-figure": (
        "def build(ctx, frame):\n"
        "    ctx.widgets.register('_sp_canvas', card.canvas)\n"
    ),
}


@pytest.mark.parametrize("shape", sorted(FIGURE_REGISTRATION_SEEN))
def test_figure_pref_key_guard_sees(shape):
    from tests.test_figure_pref_keys import _registered_figure_names
    found = _registered_figure_names(FIGURE_REGISTRATION_SEEN[shape])
    assert found, f"the {shape} spelling registered a figure the guard cannot see"


@pytest.mark.parametrize("shape", sorted(FIGURE_REGISTRATION_UNSEEN))
def test_figure_pref_key_guard_ignores(shape):
    from tests.test_figure_pref_keys import _registered_figure_names
    found = _registered_figure_names(FIGURE_REGISTRATION_UNSEEN[shape])
    assert not found, f"the guard read a {shape} as a figure registration: {found}"


# ── a function that can return None but does not say so ──────────────────────

OPTIONAL_RETURN_VIOLATIONS = {
    "bare-return": (
        "def f(xs):\n"
        "    if not xs:\n"
        "        return\n"
        "    return xs[0]\n"
    ),
    "explicit-none": (
        "def f(xs):\n"
        "    if not xs:\n"
        "        return None\n"
        "    return xs[0]\n"
    ),
    "none-after-the-value": (
        "def f(xs):\n"
        "    for x in xs:\n"
        "        if x:\n"
        "            return x\n"
        "    return None\n"
    ),
    "inside-a-method": (
        "class C:\n"
        "    def f(self, xs):\n"
        "        if not xs:\n"
        "            return None\n"
        "        return xs[0]\n"
    ),
}

OPTIONAL_RETURN_CLEAN = {
    "declared": (
        "def f(xs) -> int | None:\n"
        "    if not xs:\n"
        "        return None\n"
        "    return xs[0]\n"
    ),
    "declared-with-Optional": (
        "from typing import Optional\n"
        "def f(xs) -> Optional[int]:\n"
        "    if not xs:\n"
        "        return\n"
        "    return xs[0]\n"
    ),
    "never-returns-none": (
        "def f(xs):\n"
        "    if not xs:\n"
        "        return 0\n"
        "    return xs[0]\n"
    ),
    "only-returns-none": (
        "def f(xs):\n"
        "    if not xs:\n"
        "        return\n"
        "    return\n"
    ),
    # The None belongs to a nested function, not to this one. Counting it would
    # flag every factory whose inner callable can return None.
    "the-none-is-a-nested-functions": (
        "def outer(xs):\n"
        "    def inner():\n"
        "        return None\n"
        "    return inner\n"
    ),
    # Falling off the end is the same defect and is deliberately out of scope:
    # separating a real fall-off from an unreachable one needs flow analysis,
    # and a guard with false positives gets deleted.
    "implicit-fall-off-is-not-claimed": (
        "def f(xs):\n"
        "    for x in xs:\n"
        "        if x:\n"
        "            return x\n"
    ),
}


@pytest.mark.parametrize("shape", sorted(OPTIONAL_RETURN_VIOLATIONS))
def test_optional_return_guard_catches(shape):
    from tests.test_optional_returns_declared import offenders_in
    found = offenders_in(OPTIONAL_RETURN_VIOLATIONS[shape], "probe.py")
    assert found, f"the {shape} spelling walked past the guard"


@pytest.mark.parametrize("shape", sorted(OPTIONAL_RETURN_CLEAN))
def test_optional_return_guard_clears(shape):
    from tests.test_optional_returns_declared import offenders_in
    found = offenders_in(OPTIONAL_RETURN_CLEAN[shape], "probe.py")
    assert not found, f"the guard fired on a legitimate {shape}: {found}"


# ── text columns of a loaded plate ──────────────────────────────────────────

TEXT_COLUMN_VIOLATIONS = {
    # The shape that shipped: five call sites normalised the FOV column this
    # way, and every one raised on the first plate whose fields are not
    # numbered from one.
    "direct-fov-fill": 'def f(df):\n    return df["fov"].fillna("1")\n',
    # The `.loc[mask, "timepoint"]` spelling, whose slice is a tuple — the
    # barplot renderer's fallback read the timepoint column that way.
    "loc-tuple-slice": 'def f(df, m):\n    return df.loc[m, "timepoint"].fillna("")\n',
    # A category-preserving call in between changes nothing: `copy()` returns a
    # categorical, so the fill still validates against the category list.
    "through-a-copy": 'def f(df):\n    return df["fov"].copy().fillna("1")\n',
}

TEXT_COLUMN_CLEAN = {
    # `astype` has already widened the column away from the categorical, so
    # there is no category list left to validate against.
    "after-astype": 'def f(df):\n    return df["fov"].astype(str).fillna("")\n',
    # A numeric column is never stored as categories.
    "numeric-column": 'def f(df):\n    return df["QC_Pass"].fillna(1)\n',
    # The helper is the sanctioned route.
    "through-the-helper": "def f(df):\n    return fov_text(df)\n",
}


@pytest.mark.parametrize("shape", sorted(TEXT_COLUMN_VIOLATIONS))
def test_text_column_guard_catches(shape):
    from tests.test_text_column_access import offenders_in
    found = offenders_in(TEXT_COLUMN_VIOLATIONS[shape], "probe.py")
    assert found, f"the {shape} spelling walked past the guard"


@pytest.mark.parametrize("shape", sorted(TEXT_COLUMN_CLEAN))
def test_text_column_guard_clears(shape):
    from tests.test_text_column_access import offenders_in
    found = offenders_in(TEXT_COLUMN_CLEAN[shape], "probe.py")
    assert not found, f"the guard fired on a legitimate {shape}: {found}"


# ── one definition of a run's remote directory ──────────────────────────────

_RUN_DIR_GUARD = (
    "tests.test_one_definition_of_the_run_dir",
    "test_only_the_funnel_spells_the_run_directory",
)

#: The guard refuses to run without its anchor — a scan surface with no funnel
#: in it means it has nothing to compare against, and it says so rather than
#: passing. Each synthetic source therefore carries the definition, which is
#: also what the real repo looks like.
_FUNNEL_STUB = (
    'def remote_run_dir(host, run_id):\n'
    '    return f"{host.remote_staging_dir}/runs/{run_id}"\n\n\n'
)

RUN_DIR_VIOLATIONS = {
    # The plain tenth site, in whatever module an extraction put it.
    "f-string": 'def d(host, run_id):\n'
                '    return f"{host.remote_staging_dir}/runs/{run_id}/out"\n',
    # A different spelling of the run id — three were in use, and a fourth
    # would have been just as invisible to a guard keyed on the interpolation.
    "attribute-spelling": 'def d(host, run):\n'
                          '    return f"{host.remote_staging_dir}/runs/{run.run_id}"\n',
    # Implicit concatenation, which is one JoinedStr with the static parts split.
    "split-literal": 'def d(host, run_id):\n'
                     '    return (f"{host.remote_staging_dir}/ru"\n'
                     '            f"ns/{run_id}/out")\n',
    # The four spellings that are not f-strings at all. Reading only
    # `ast.JoinedStr` made each of these a way around the rule, and
    # `PurePosixPath` is already imported in the module the guard holds.
    "percent-format": 'def d(staging, run_id):\n'
                      '    return "%s/runs/%s" % (staging, run_id)\n',
    "str-format": 'def d(staging, run_id):\n'
                  '    return "{}/runs/{}".format(staging, run_id)\n',
    "concatenation": 'def d(staging, run_id):\n'
                     '    return staging + "/runs/" + run_id\n',
    "posixpath-join": 'import posixpath\n'
                      'def d(staging, run_id):\n'
                      '    return posixpath.join(staging, "runs", run_id)\n',
    "purepath-div": 'from pathlib import PurePosixPath\n'
                    'def d(staging, run_id):\n'
                    '    return str(PurePosixPath(staging) / "runs" / run_id)\n',
}

RUN_DIR_CLEAN = {
    # The dataset address, which is a different thing with its own definition.
    "dataset-dir": 'def d(host, key):\n'
                   '    return f"{host.remote_staging_dir}/datasets/{key}"\n',
    # A path that merely mentions runs in prose.
    "prose": 'MSG = "look under runs/ on the host"\n',
    # A bare "runs" outside a path-building position is a word. The rule is
    # keyed on where the segment sits, not on the word itself, or every
    # sentence in the codebase that uses it would be a violation.
    "runs-as-a-word": 'HEADING = "runs"\nLABEL = ("runs", "wells")\n',
    # A join that is not building a run directory.
    "unrelated-join": 'def f(parts):\n    return "/".join(parts)\n',
    # The dataset address again, in the slash-free spelling.
    "dataset-join": 'import posixpath\n'
                    'def d(staging, key):\n'
                    '    return posixpath.join(staging, "datasets", key)\n',
}


@pytest.mark.parametrize("shape", sorted(RUN_DIR_VIOLATIONS))
def test_run_dir_guard_catches(tmp_path, monkeypatch, shape):
    bad = _write(tmp_path, f"bad_rundir_{shape}.py",
                 _FUNNEL_STUB + RUN_DIR_VIOLATIONS[shape])
    exc = _run_scan(monkeypatch, *_RUN_DIR_GUARD, [bad])
    assert exc is not None and bad.name in str(exc), (
        f"the {shape} spelling walked past the guard")


@pytest.mark.parametrize("shape", sorted(RUN_DIR_CLEAN))
def test_run_dir_guard_clears(tmp_path, monkeypatch, shape):
    good = _write(tmp_path, f"good_rundir_{shape}.py",
                  _FUNNEL_STUB + RUN_DIR_CLEAN[shape])
    assert _run_scan(monkeypatch, *_RUN_DIR_GUARD, [good]) is None, (
        f"the guard fired on a legitimate {shape}")

def test_run_dir_guard_refuses_without_its_anchor(tmp_path, monkeypatch):
    """Every case above prepends the funnel, so none of them tests the anchor.

    The guard fails closed: a scan surface with no ``remote_run_dir`` in it
    means the funnel was renamed or moved out from under the guard, so it has
    nothing to compare offenders against and cannot know whether it found
    every spelling or simply read the wrong tree. Reporting *no violations*
    from that position is the fail-open shape this whole file exists to
    prevent.

    The surface here is deliberately **clean**. Feeding it a violation makes
    the guard fire for the ordinary reason, and the test then passes whether
    the anchor exists or not — which is how the first draft of this test
    measured nothing.
    """
    orphan = _write(tmp_path, "orphan_rundir.py", RUN_DIR_CLEAN["dataset-dir"])
    exc = _run_scan(monkeypatch, *_RUN_DIR_GUARD, [orphan])
    assert exc is not None, (
        "the guard passed on a scan surface with no funnel in it at all — it "
        "failed open, which is the one outcome it must not have")
    assert "remote_run_dir" in str(exc), (
        "the guard fired, but not about its missing anchor. Its message must "
        f"name the funnel it could not find; it said: {exc}")



# ── the run journal's well-state vocabulary ─────────────────────────────────

_JOURNAL_GUARD = (
    "tests.test_no_second_journal_vocabulary",
    "test_no_module_restates_the_journal_well_states",
)

JOURNAL_ROSTER_VIOLATIONS = {
    # The plain restatement — what a second reader would write for itself.
    "tuple": 'STATES = ("uploaded", "analysed", "fetched", "skipped")\n',
    # A list, and one state short of the real roster. A copy that has already
    # fallen behind is the case the three-name threshold exists to catch: it is
    # more suspect than a complete one, not less.
    "stale-list": 'for s in ["fetched", "skipped", "failed"]:\n    pass\n',
    # A set comprehension's source, which is still a literal collection.
    "set": 'TERMINAL = {"fetched", "failed", "not-fetched"}\n',
    # The shape that got past the first version of this guard. A dict of words
    # is presentation — until the module holding it never consults the
    # canonical roster, at which point it *is* the module's only list of the
    # states, and `_reconcile_fetch` proved what that costs by iterating one to
    # build a tally that then silently dropped every state it did not spell.
    # The evasion the first exemption allowed: the roster is spelled out, and
    # the only mention of WELL_STATES is prose. A whole-file substring test
    # read that as "consults the canonical roster" and waved the module
    # through — including `remote_runner.py`, where the defect happened.
    "roster-named-only-in-a-comment": (
        '# Ordered to match WELL_STATES.\n'
        'PHRASES = {"fetched": "fetched", "skipped": "skipped",\n'
        '           "failed": "failed", "not-fetched": "not fetched"}\n'
    ),
    "unanchored-label-dict": (
        'PHRASES = {"fetched": "fetched", "skipped": "skipped",\n'
        '           "failed": "failed", "not-fetched": "not fetched"}\n'
        'parts = [PHRASES[k] for k in PHRASES]\n'
    ),
}

JOURNAL_ROSTER_CLEAN = {
    # One state in a comparison is ordinary code, not a roster.
    "single-state": 'def f(rec):\n    return rec.state == "fetched"\n',
    # Two states grouped for one count — below the threshold, and the shape
    # `_well_tally` and the resume banner both legitimately use.
    "pair": 'DONE = ("analysed", "fetched")\n',
    # Labels: each state sits alone inside its own pair, so pairing a state
    # with the words shown for it is presentation, not a second definition.
    "label-pairs": (
        'ROWS = (("fetched", "Fetched"), ("skipped", "Skipped"),\n'
        '        ("failed", "Failed"), ("not-fetched", "Never fetched"))\n'
    ),
    # The same dict, in a module that does reach for the canonical roster. The
    # words are still presentation, and the order and completeness now come
    # from the one place that can supply them — which is the whole distinction.
    "anchored-label-dict": (
        'from services.run_journal import WELL_STATES\n'
        'PHRASES = {"fetched": "fetched", "skipped": "skipped",\n'
        '           "failed": "failed", "not-fetched": "not fetched"}\n'
        'parts = [PHRASES.get(k, k) for k in WELL_STATES]\n'
    ),
}


@pytest.mark.parametrize("shape", sorted(JOURNAL_ROSTER_VIOLATIONS))
def test_journal_vocabulary_guard_catches(tmp_path, monkeypatch, shape):
    bad = _write(tmp_path, f"bad_journal_{shape}.py",
                 JOURNAL_ROSTER_VIOLATIONS[shape])
    exc = _run_scan(monkeypatch, *_JOURNAL_GUARD, [bad])
    assert exc is not None and bad.name in str(exc), (
        f"the {shape} spelling walked past the guard")


@pytest.mark.parametrize("shape", sorted(JOURNAL_ROSTER_CLEAN))
def test_journal_vocabulary_guard_clears(tmp_path, monkeypatch, shape):
    good = _write(tmp_path, f"good_journal_{shape}.py",
                  JOURNAL_ROSTER_CLEAN[shape])
    assert _run_scan(monkeypatch, *_JOURNAL_GUARD, [good]) is None, (
        f"the guard fired on a legitimate {shape}")


# ── names that appear only in an annotation ─────────────────────────────────
#
# `from __future__ import annotations` is on every module here, so an
# annotation is stored as a string and never compiled as a load. The compiler
# never sees the name, which means `symtable` above cannot either, and ruff
# cannot because F821 is in this repo's ignore list. Extracting a module
# dropped a `Callable, Iterable` import down to `Callable` that way: the
# annotation still read `Iterable[Path | str]`, nothing raised at import or at
# call, and the whole fast lane stayed green.

ANNOTATION_VIOLATIONS = {
    # The shape that shipped: the annotation survives the import that fed it.
    "dropped-import": (
        "from collections.abc import Callable\n"
        "def f(also: Iterable[str] = ()) -> None: ...\n"
    ),
    # A return annotation, which is a separate node from the arguments.
    "return": "def f() -> Mapping[str, int]: ...\n",
    # A module-level annotated assignment.
    "annassign": "rows: Sequence[int] = []\n",
    # Nested inside a subscript, where a name is easiest to overlook.
    "nested": "def f(x: dict[str, Nowhere]) -> None: ...\n",
    # Keyword-only and *args/**kwargs carry annotations of their own.
    "kwonly": "def f(*rest: Nowhere, flag: bool = False) -> None: ...\n",
}

ANNOTATION_CLEAN = {
    # The same annotation, with the import it needs.
    "imported": (
        "from collections.abc import Callable, Iterable\n"
        "def f(also: Iterable[str] = ()) -> None: ...\n"
    ),
    # A TYPE_CHECKING import is the idiomatic way to annotate without paying
    # the runtime cost, and it binds the name as far as this guard is
    # concerned — flagging it would make the guard unusable.
    "type-checking": (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from pathlib import Path\n"
        "def f(p: Path) -> None: ...\n"
    ),
    # A string forward reference resolves against a namespace this file cannot
    # see, so it is not this guard's to judge.
    "forward-ref": 'def f(x: "SomethingLater") -> None: ...\n',
    # Builtins and PEP 604 unions need no import at all.
    "builtin-union": "def f(x: str | None) -> list[int]: ...\n",
    # A class defined in the same module is bound by its own definition.
    "own-class": (
        "class Row: ...\n"
        "def f(r: Row) -> Row: return r\n"
    ),
    # An attribute path only loads its root, which is imported.
    "attribute": (
        "import collections.abc as abc\n"
        "def f(x: abc.Iterable) -> None: ...\n"
    ),
}


@pytest.mark.parametrize("shape", sorted(ANNOTATION_VIOLATIONS))
def test_annotation_name_guard_catches(shape):
    from tests.test_no_unbound_names import annotation_offenders_in
    found = annotation_offenders_in(ANNOTATION_VIOLATIONS[shape], "probe.py")
    assert found, f"the {shape} spelling walked past the guard"


@pytest.mark.parametrize("shape", sorted(ANNOTATION_CLEAN))
def test_annotation_name_guard_clears(shape):
    """The first cut of this guard allowlisted every name in `dir(typing)`,
    which swallows `Iterable`, `Callable`, `Mapping` and `Sequence` — exactly
    the names a dropped import loses. It passed on the real defect it was
    written for. A clean case that is too generous is how that happens."""
    from tests.test_no_unbound_names import annotation_offenders_in
    found = annotation_offenders_in(ANNOTATION_CLEAN[shape], "probe.py")
    assert not found, f"the guard fired on a legitimate {shape}: {found}"
# ── replacing a live JSON file by truncating it ─────────────────────────────

_ATOMIC_JSON_GUARD = (
    "tests.test_dataset_tool_integrity",
    "test_no_live_json_file_is_replaced_by_truncating_it",
)

#: Every spelling that truncates a destination holding JSON. The first is the
#: shape that shipped; the rest were each measured walking past an earlier
#: version of the guard, which matched only a literal `json.dumps` argument to
#: `write_text`.
ATOMIC_JSON_VIOLATIONS = {
    "direct": (
        'import json\n'
        'def save(p, x):\n'
        '    p.write_text(json.dumps(x, indent=2))\n'
    ),
    # The payload bound to a name first — one refactor away from the above.
    "two-step": (
        'import json\n'
        'def save(p, x):\n'
        '    payload = json.dumps(x, indent=2)\n'
        '    p.write_text(payload)\n'
    ),
    # `from json import dumps`, so the call has no `json.` prefix to match on.
    "bare-dumps": (
        'from json import dumps\n'
        'def save(p, x):\n'
        '    p.write_text(dumps(x))\n'
    ),
    # The payload is the *receiver* of `.encode()`, not an argument to it, so a
    # guard that only walks a call's arguments never reaches the `dumps`.
    "write-bytes": (
        'import json\n'
        'def save(p, x):\n'
        '    p.write_bytes(json.dumps(x).encode("utf-8"))\n'
    ),
    # The streaming form, straight onto the destination.
    "open-and-dump": (
        'import json\n'
        'def save(p, x):\n'
        '    with open(p, "w", encoding="utf-8") as fh:\n'
        '        json.dump(x, fh, indent=2)\n'
    ),
}

#: Shapes the guard must NOT fire on. Every one of these is real code in this
#: repository, and an earlier draft reported all of them as defects — a guard
#: whose failures are mostly false is a guard that gets deleted.
ATOMIC_JSON_CLEAN = {
    # The helper itself.
    "the-helper": (
        'from util.json_write import write_json_atomic\n'
        'def save(p, x):\n'
        '    write_json_atomic(p, x)\n'
    ),
    # A hand-rolled stage-and-rename: doing by hand what the helper does.
    "tmp-then-replace": (
        'import json, os\n'
        'def save(p, x):\n'
        '    tmp = p.with_suffix(".tmp")\n'
        '    tmp.write_text(json.dumps(x, indent=2))\n'
        '    os.replace(tmp, p)\n'
    ),
    # mkstemp + fdopen: the handle is a staging file, so streaming into it is
    # the rule rather than a breach of it.
    "mkstemp-and-dump": (
        'import json, os, tempfile\n'
        'def save(p, x):\n'
        '    fd, tmp = tempfile.mkstemp(dir=str(p.parent))\n'
        '    with os.fdopen(fd, "w") as fh:\n'
        '        json.dump(x, fh, indent=2)\n'
        '    os.replace(tmp, p)\n'
    ),
    # The staging write in a nested helper, the rename in its parent — the
    # shape `well_viewer/persistence/_io.atomic_write_json` uses, because its
    # rename is wrapped in a retry. Judging each nested `def` separately splits
    # the pair in half and reports the repo's most careful writer as a defect.
    "nested-write-parent-rename": (
        'import json, os\n'
        'def save(p, x):\n'
        '    tmp = p.with_suffix(".tmp")\n'
        '    def _write():\n'
        '        with open(tmp, "w") as fh:\n'
        '            json.dump(x, fh)\n'
        '    _write()\n'
        '    os.replace(tmp, p)\n'
    ),
    # Not JSON at all.
    "plain-text": (
        'def save(p, text):\n'
        '    p.write_text(text)\n'
    ),
}


@pytest.mark.parametrize("shape", sorted(ATOMIC_JSON_VIOLATIONS))
def test_atomic_json_guard_catches(tmp_path, monkeypatch, shape):
    bad = _write(tmp_path, f"bad_json_{shape}.py", ATOMIC_JSON_VIOLATIONS[shape])
    exc = _run_scan(monkeypatch, *_ATOMIC_JSON_GUARD, [bad])
    assert exc is not None and bad.name in str(exc), (
        f"the {shape} spelling walked past the guard")


@pytest.mark.parametrize("shape", sorted(ATOMIC_JSON_CLEAN))
def test_atomic_json_guard_clears(tmp_path, monkeypatch, shape):
    good = _write(tmp_path, f"good_json_{shape}.py", ATOMIC_JSON_CLEAN[shape])
    assert _run_scan(monkeypatch, *_ATOMIC_JSON_GUARD, [good]) is None, (
        f"the guard fired on a legitimate {shape}")


# ── the Qt-free layers, reached one hop away ────────────────────────────────

#: Every way a "Qt-free" module has reached Qt without importing it itself.
#: These are shapes that were *measured* walking past an earlier draft of
#: ``test_a_qt_free_layer_stays_importable_without_qt``, not invented ones.
TRANSITIVE_QT_VIOLATIONS = {
    # The shape that shipped: a pure module importing a core module that
    # imports PySide6.
    "plain-hop": (
        "well_viewer/core/_bite_hop.py",
        "from PySide6.QtCore import QTimer\n",
        "from well_viewer.core._bite_hop import QTimer\n",
    ),
    # An import inside a module-level ``if`` still runs at import time. A draft
    # that walked only ``tree.body`` never saw it.
    "guarded-by-if": (
        "well_viewer/core/_bite_hop.py",
        "from PySide6.QtCore import QTimer\n",
        'import os\nif os.environ.get("NEVER") != "1":\n'
        "    from well_viewer.core._bite_hop import QTimer\n",
    ),
    # ``try/except ImportError`` around it, likewise.
    "guarded-by-try": (
        "well_viewer/core/_bite_hop.py",
        "from PySide6.QtCore import QTimer\n",
        "try:\n    from well_viewer.core._bite_hop import QTimer\n"
        "except ImportError:\n    QTimer = None\n",
    ),
    # The endpoint is ``widgets``, not Qt itself — importing a widget pulls Qt
    # transitively, which is why the direct check forbids it too. A draft that
    # terminated only on QT_ROOTS walked straight through this.
    "endpoint-is-widgets": (
        "well_viewer/core/_bite_hop.py",
        "from widgets.rail_nav import RailNav\n",
        "from well_viewer.core._bite_hop import RailNav\n",
    ),
}

#: And the shapes it must NOT fire on — a function-local import of a
#: Qt-touching module is the *fix* for this class of defect.
TRANSITIVE_QT_CLEAN = {
    "function-local-hop": (
        "well_viewer/core/_bite_hop.py",
        "from PySide6.QtCore import QTimer\n",
        "def f():\n    from well_viewer.core._bite_hop import QTimer\n"
        "    return QTimer\n",
    ),
    "hop-to-a-pure-module": (
        "well_viewer/core/_bite_hop.py",
        "VALUE = 1\n",
        "from well_viewer.core._bite_hop import VALUE\n",
    ),
}


def _qt_chain(tmp_path, monkeypatch, hop_rel, hop_src, pure_src):
    """Point the transitive guard's module resolver at a temp tree."""
    import tests.test_qt_free_layers as guard

    (tmp_path / "well_viewer" / "core").mkdir(parents=True)
    (tmp_path / "well_viewer" / "domain").mkdir(parents=True)
    (tmp_path / hop_rel).write_text(hop_src)
    (tmp_path / "well_viewer" / "domain" / "_bite_pure.py").write_text(pure_src)
    monkeypatch.setattr(guard, "REPO_ROOT", tmp_path)
    return guard._qt_path_from("well_viewer.domain._bite_pure")


@pytest.mark.parametrize("shape", sorted(TRANSITIVE_QT_VIOLATIONS))
def test_transitive_qt_guard_catches(tmp_path, monkeypatch, shape):
    chain = _qt_chain(tmp_path, monkeypatch, *TRANSITIVE_QT_VIOLATIONS[shape])
    assert chain is not None and len(chain) > 1, (
        f"the {shape} spelling reaches Qt but the guard found no chain")


@pytest.mark.parametrize("shape", sorted(TRANSITIVE_QT_CLEAN))
def test_transitive_qt_guard_clears(tmp_path, monkeypatch, shape):
    chain = _qt_chain(tmp_path, monkeypatch, *TRANSITIVE_QT_CLEAN[shape])
    assert chain is None, (
        f"the guard fired on a legitimate {shape}: {chain}")


# ── feature_slice names ─────────────────────────────────────────────────────

FEATURE_SLICE_VIOLATIONS = {
    "literal": 'def f(ctx):\n    return ctx.state.feature_slice("barplot")\n',
    # A module constant, one assignment away from the literal form.
    "module-constant": (
        '_NAME = "barplot"\n'
        "def f(ctx):\n    return ctx.state.feature_slice(_NAME)\n"
    ),
    # The bound method — the call has no ``feature_slice`` attribute on it.
    "bound-method": (
        "def f(ctx):\n    fn = ctx.state.feature_slice\n"
        '    return fn("barplot")\n'
    ),
}

FEATURE_SLICE_CLEAN = {
    "registered-name": 'def f(ctx):\n    return ctx.state.feature_slice("bar")\n',
    "registered-via-constant": (
        '_NAME = "heatmap"\n'
        "def f(ctx):\n    return ctx.state.feature_slice(_NAME)\n"
    ),
    # A genuinely dynamic name cannot be judged, and guessing would produce
    # false failures on the one call site that legitimately varies.
    "dynamic-name": (
        "def f(ctx, name):\n    return ctx.state.feature_slice(name)\n"
    ),
}


def _slice_offenders(tmp_path, monkeypatch, body):
    import tests.test_feature_slice_names as guard

    path = tmp_path / "probe.py"
    path.write_text(body)
    monkeypatch.setattr(guard, "python_sources", lambda **kw: [path])
    monkeypatch.setattr(guard, "rel", lambda p: p.name)
    try:
        guard.test_every_feature_slice_lookup_names_a_registered_slice()
    except AssertionError as exc:
        return str(exc)
    return None


@pytest.mark.parametrize("shape", sorted(FEATURE_SLICE_VIOLATIONS))
def test_feature_slice_guard_catches(tmp_path, monkeypatch, shape):
    got = _slice_offenders(tmp_path, monkeypatch, FEATURE_SLICE_VIOLATIONS[shape])
    assert got is not None and "barplot" in got, (
        f"the {shape} spelling walked past the guard")


@pytest.mark.parametrize("shape", sorted(FEATURE_SLICE_CLEAN))
def test_feature_slice_guard_clears(tmp_path, monkeypatch, shape):
    got = _slice_offenders(tmp_path, monkeypatch, FEATURE_SLICE_CLEAN[shape])
    assert got is None, f"the guard fired on a legitimate {shape}: {got}"
