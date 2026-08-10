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
