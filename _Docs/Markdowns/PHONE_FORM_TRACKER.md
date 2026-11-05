# Phone layout — migration plan and tracker

**This document is temporary.** It exists because this body of work spans
several pull requests and more than one session, and CLAUDE.md §6.1 requires
that a plan of this size live on disk rather than in conversation. It is
deleted by the pull request that closes its last row, and that pull request
says in its body that it is gone. Until then it is the plan, the instructions
for whoever implements it, *and* the record of where the work stands.

It plans one change of direction for phone layout (ARCHITECTURE.md, *Phone
layout*): from a shell that searches the whole application for widgets it does
not own and rewrites them after they are built, to widgets that **declare**
their phone form when they are constructed. Nothing a person sees on the phone
or at the desk is meant to change. Every row is judged by two things: the phone
layout is the same afterwards, and it is cheaper to keep correct.

---

## 0. Start here — the implementer's brief

You are picking this up without the conversation that produced it. Everything
you need is in this file, CLAUDE.md, and the code. Do not rely on anything else.

### 0.1 Read these first, in this order

1. **CLAUDE.md** in full, and especially: §2 (which test lane to run — running
   the wrong one hangs the container), §3 rule 4 (*Qt is a boundary*; `widgets/`
   has no app knowledge), §5, §6 (commit and PR cadence), §6.1 (how this
   tracker works), §7.5 (sweeps and the guards that hold them), §7.7 and §7.8
   (proving a test bites).
2. **ARCHITECTURE.md, *Phone layout*** — from the heading `### Phone layout`
   (around line 152) to the end of *Windows of their own* (around line 720).
   That is the behaviour this migration must preserve, described in detail.
3. **This file, §1 and §2**, then the first open row in §3.
4. **`ui/phone_shell.py`** lines 488–534 (`_KIND_BY_CLASS`, `_adapt_kind`,
   `_phone_relevant`), 762–800 (`_PhoneEventFilter`), 1002–1016
   (`_restyle_in_place`), 1211–1345 (`set_on`, `_apply_chrome`, `adapt` and
   the adapters) and 1381–1441 (`_sweep`, `_adapt_all`). Line numbers are as
   of this plan; search for the names if they have moved.

### 0.2 Picking the next piece of work

Find the first row in §3 whose state is not `done` or `declined`, in the
lowest-numbered phase. That row is the work. Do the rows of a phase in order
unless a row says otherwise. Never start a row whose phase depends on a phase
that has not merged into `main`.

### 0.3 The loop for every row

1. **Re-measure the row's premise.** Every count and site list in this file was
   measured when it was written; the tree has moved since. Run the row's
   *Measure* command (or an equivalent grep) and, if the answer differs, write
   the new answer into the row *before* changing code (CLAUDE.md §6.1, last
   paragraph).
2. **Write the test first and watch it fail** for the reason the row names —
   read the failure message, not only the red (§7.4 #4). A test that passes
   before the change is not yet a test of the change.
3. **Make the change.**
4. **Run the row's *Mutation* check**: undo the essential line of the change in
   a scratch edit, run the new test, and see it fail with an
   `AssertionError` that names the problem. Restore the line. For a guard over
   a rule that already holds on `main`, add the violating case to
   `tests/test_guards_bite.py` instead (§7.5 #1; `scripts/prove_guard.sh`
   cannot prove a guard over code that is new on the branch).
5. **Run the snapshot check** once P0.1 exists (§0.5). An unexplained
   difference means the row is not done.
6. **Run the checks in §0.5.**
7. **Update the row** — state, and the evidence column: the test name, what
   the mutation was and the failure it produced, the snapshot result. Same
   commit as the code.
8. **Commit** with a message in normal prose, ending with the attribution lines
   the session gives you. Push to the session branch (free while no PR is open).

A row left `wip` at the end of a session carries a note in its evidence column
saying exactly where it stopped.

### 0.4 Pull requests

- **One pull request per phase**, based on `main`, opened only once every row
  of that phase is `done` or `declined` and the checks in §0.5 pass. Opening
  the PR is what starts CI and what costs money; do not open it early (CLAUDE.md
  §2, §6).
- **Do not stack phases.** Start phase N+1 from `main` after phase N has
  merged. Stacked branches here have merged into intermediate branches instead
  of `main` before (CLAUDE.md §6).
- The PR body states which rows it closes, what was measured, and which tests
  were run against which commit. Do not touch `version.py`. Label nothing
  `minor`: this changes no behaviour.
- Fetch `main` and read `git log origin/main -- ui/phone_shell.py ui/phone_window.py widgets/`
  when you start a phase **and** before you push: other sessions edit these
  files.

### 0.5 The checks, every row

```bash
ruff check .
scripts/test.sh tests/test_optional_returns_declared.py tests/test_no_unbound_names.py \
  tests/test_no_silent_except.py tests/test_safe_helpers_used_right.py \
  tests/test_architecture_contracts.py tests/test_layering.py tests/test_guardrails.py \
  tests/test_complexity_ratchet.py tests/test_monolith_budgets.py \
  tests/test_stale_app_attrs.py tests/test_doc_version_sync.py tests/test_ctx_app_burndown.py
scripts/test.sh tests/test_phone_layout.py tests/test_phone_fit.py tests/test_phone_windows.py \
  tests/test_phone_panel_sheet.py tests/test_phone_form_swipe.py tests/test_phone_plot_swipe.py \
  tests/test_phone_titles_fit.py tests/test_phone_remote_windows.py \
  tests/test_phone_nanowell_popout.py tests/test_phone_datasets_shell.py \
  tests/test_home_phone_layout.py tests/test_sample_map_phone_layout.py \
  tests/test_analyze_phone_view.py tests/test_analyze_phone_progress_settles.py \
  tests/test_phone_form_registry.py          # once P1.1 has created it
# Full-app phone files: ONE per invocation, with -m "":
scripts/test.sh tests/test_phone_layout_app.py -m ""
scripts/test.sh tests/test_phone_layout_snapshot.py -m ""   # once P0.1 has created it
```

Before each phase's PR, also run the whole fast lane including the UI tests —
`scripts/test.sh` with no arguments. Most phone tests are in
`tests/_lanes.py::UI_LANE_FILES`, which **CI never runs**, so this local run is
their only gate.

Never run a bare `pytest`, never `python -m pytest <files>`, never a glob over
`tests/test_*phone*.py` (it pulls in full-app files and outlives its timeout).

### 0.6 Stop and ask the human — do not decide these alone

- The snapshot (P0.1) differs and the difference is not obviously the
  harness's own noise.
- Anything a person would see changes: a size, a control's place, what is
  hidden, how a tap behaves. This migration changes no behaviour; a row that
  needs to is a question row first.
- A ratchet or budget would have to go *up* (`test_monolith_budgets`,
  `test_complexity_ratchet`, or the ratchets this plan adds).
- A design point in §2 turns out not to fit the code. Write what you found into
  the row, set it `blocked`, and ask; do not improvise a different design.
- A question row (Q1, Q2) — its answer comes from measurement, but what to do
  with the answer is the human's call if it changes the plan.

### 0.7 Never

- An event filter installed on the `QApplication` (it crashed on Windows;
  ARCHITECTURE *Phone layout*, step 4).
- `processEvents` or a bare `sendPostedEvents()` inside the restyle (step 3).
- An import of `review`, `analyze` or `allwell` from `widgets/`
  (`test_architecture_contracts.py::test_widgets_do_not_import_the_app`).
- A silent `except`; deleted-wrapper `RuntimeError`s go through
  `core.safe.swallowed()` inside the `except` block, as the shell does now.
- Deleting a row. A row that turns out wrong is `declined` with the reason.

---

## How to read the rows

Every row has an id, a state, and — once it is `done` — its evidence. Each
phase has a status table (the source of truth for state) followed by one spec
per row: what to do, what *done* means, and the mutation that must make the new
test fail.

- **`todo`** — not started.
- **`wip`** — started, not finished. Never left across a session boundary
  without a note in the evidence column saying where it stopped.
- **`done`** — implemented **and** pinned by a test that was watched failing
  without the change (CLAUDE.md §7.7 #2). For a question row, `done` means
  answered, and the answer is written in the row.
- **`blocked`** — cannot proceed until a named row closes or the human answers.
  The blocker is named.
- **`declined`** — examined and deliberately not done, with the reason written
  in the row and at the site.

**Counts are derived from the rows, never typed.** The states reach `grep`
through a variable, so the command cannot match itself, and the legend above is
prose rather than a table, so it is not counted either:

```bash
F=_Docs/Markdowns/PHONE_FORM_TRACKER.md
for s in todo wip done blocked declined; do
  printf '%-9s %s\n' "$s" "$(grep -c "| \`$s\` |" "$F")"
done
```

---

## 1. Where the tree stands (measured when this plan was written)

Each figure here is a hypothesis about the tree as it stood then. A row that
depends on one re-measures it first (§0.3 step 1).

### 1.1 Three tiers exist already, unevenly

- **Rules and the flag** — `core/phone_layout.py` (197 lines): `is_on`,
  `set_on`, `is_switching`, `switching()`, `add_listener` / `remove_listener`,
  the phone point, the type scale, the touch sizes, the plot DPI factor.
  Qt-free. This tier is right; the plan does not change it.
- **The shell** — `ui/phone_shell.py` (2,280 lines), `ui/phone_window.py`
  (325), `ui/phone_drawer.py` (134), `ui/phone_fit.py` (427). It owns the
  window chrome (top bar, foot strip, drawer, Controls and Wells sheets, ⋯
  menu, window geometry, the switch banner, the restyle) **and** it finds and
  adapts widgets it does not own. The second job is what this plan moves.
- **Views that own their phone form** — `analyze/phone_view.py` (380),
  `review/features/home/_phone.py` (165), `review/features/home/sample_map/_phone.py`
  (281), `review/features/image_browser/image_table/_phone_fit.py` (150),
  `review/core/phone_panel_sheet.py` (172). These are already the target
  shape: each listens to `phone_layout`, is applied by its owner, and is tested
  with it. This plan does not touch them except where a row says so.

### 1.2 How the shell finds what it adapts today

`PhoneShell._sweep` runs every `_SWEEP_MS` (300 ms) while phone layout is on,
plus on window focus and page change. It walks `QApplication.allWidgets()` for
widgets without the `_awPhoneSeen` property, installs `_PhoneEventFilter` on
the relevant ones (marked `_awPhoneWatched`), and adapts each by kind — at once
if it is visible, otherwise on its next `Show`. On entering and leaving phone
layout, and around every restyle made inside it, `_adapt_all` walks
`allWidgets()` again and calls `adapt(w, on)` on each.

The kinds are recognised by **class-name and object-name strings**, never by
anything the widget declares:

| Kind | Recognised by | Constructed at (first-party, excluding tests) | What the shell does |
|---|---|---|---|
| controls | class name `ControlBand`; or property `phoneControls` + a `setStacked` method | `ControlBand(` — 8 sites: `widgets/page_body.py:60`, `review/core/centre_view.py:211`, `review/core/ui_helpers.py:649`, `cell_browser/tab_view.py:63`, `spatial/tab_view.py:177`, `multivariate/tab_view.py:100`, `nanowell/tab_view.py:308`, `preview/panel_view.py:93`. `phoneControls`: `image_table/tab_view.py:113` | hides it in the page; the Controls sheet shows it |
| toolbar | class name `MplToolbar` | `MplToolbar(` — 6 sites: `widgets/plot_card.py:263`, `cell_gating/tab_view.py:480`, `cell_gating/opt_curve_view.py:127`, `smfish/tab_view.py:277`, `playground/sweep_curve_view.py:67`, `review/core/focus_curve_view.py:57` (plus a demo at `widgets/mpl_toolbar.py:381`) | hides it |
| stepper | class name `Stepper` | `Stepper(` — 6 sites: `image_table/tab_view.py:198`, `:212`, `:480`, `image_table/builder_view.py:880`, `playground/tab_view.py:496`, `preview/panel_view.py:341` (plus a demo at `widgets/stepper.py:510–512`) | `setTouchMode(on)` |
| form_row | object name `AnalyzeFormRow` | `analyze/form_chrome.py:292` | turns the row top-to-bottom; saves and restores the label's width pins (`_awLabelPin`) |
| canvas | duck type: `draw_idle` on the type and a `figure` attribute | `widgets/plot_card.py:257` (`PlotCard`, 15 constructions), and 6 direct `FigureCanvas(` sites: `cell_gating/tab_view.py:468`, `cell_gating/opt_curve_view.py:122`, `smfish/tab_view.py:268`, `smfish/tab_view.py:542` (called as `ctx.runtime.smfish.FigureCanvas(...)` — an attribute call), `playground/sweep_curve_view.py:64`, `review/core/focus_curve_view.py:54` | DPI (`scale_canvas`), minimum height per row of axes (`size_canvas`), swipe-to-scroll (`_swipe_plot_area`) |
| hint | object name `InfoHint` on a `QLabel` with a tooltip | `widgets/info_icon.py:26` (the one factory), and set directly at `batch_export/nanowell_panel.py:183`, `nanowell/subset_view.py:258`, `:356` | a tap shows the tooltip |
| window | `phone_window.kind(w)` | any dialog or pop-out, including Qt's own (`QMessageBox`, `QFileDialog`, …) | the phone form, or keep-on-screen |
| scroll area | `QAbstractScrollArea` and `drag_scroll_eligible` | anywhere, including a combo box's popup list | drag-to-scroll on its viewport |

The plan's earlier draft counted 10 `Stepper`, 9 `ControlBand` and 9
`MplToolbar` sites; those grep counts included each class's own definition and
demo code. The table above is the corrected list.

**The important fact for the design:** for four of the eight kinds the class
that is recognised — `Stepper`, `ControlBand`, `MplToolbar`, `PlotCard`, plus
the `info_icon` factory — lives in `widgets/`. So the cheapest complete
declaration is *in the constructor*, once, rather than at each call site. That
is why the registry in §2 sits in `widgets/`.

### 1.3 What the window hands the shell

`PhoneHandles` (`ui/phone_shell.py:73`) carries about thirty fields from
`AllWellApp._build_phone_shell` (`allwell.py:687`). Some are chrome the shell
legitimately needs (header, band, mode bar, navigator, pages, the mode, the
⋯ menu's callbacks). Others are lists of other panes' widgets the shell acts on
because the pane did not: `desk_only`, `phone_hidden_sections`, `fit_roots`,
`logs`, `dashboards`, `analyze_body`, `analyze_rule`.

### 1.4 Where `phone_layout` is read outside `ui/` and `core/`

33 calls of `is_on` / `add_listener` / `is_switching`, by file:
`home/sample_map/_phone.py` 8, `home/_phone.py` 6, `allwell.py` 5
(lines 844, 860, 1537, 2193, 2255), `image_table/_phone_fit.py` 3,
`review/core/plot_style.py` 3 (1769, 1786, 1851), `review/core/ui_helpers.py` 2
(339, 365), `review/core/phone_panel_sheet.py` 2, `image_table/controller.py` 1
(174), `review/core/split_view.py` 1 (471), `review/core/app_shell_view.py` 1
(233), `analyze/phone_view.py` 1.

Measure:
```bash
grep -rnE "phone_layout\.(is_on|add_listener|is_switching)" --include=*.py . \
  | grep -vE "^./(tests|ui|core)/" | awk -F: '{print $1}' | sort | uniq -c | sort -rn
```

No `renderer.py`, `collect.py`, `*_compute.py`, or module under `domain/`,
`services/`, `pipeline/` or `util/` reads it; the two that mention "phone"
(`spatial/renderer.py:444`, `nanowell/qc_compute.py:346`) do so in a comment.
That separation is the part that is already right and must stay so.

### 1.5 Costs, and the test base

Documented in ARCHITECTURE *Phone layout*: the sweep and its marks (about 6 ms
per tick at 4,300 widgets reading the marks, 25 ms forced); per-widget event
filters; the restyle bracket in `PhoneShell._restyle_in_place`; about 6 s per
switch on a loaded app, nearly all of it `QApplication.setStyleSheet`. 

**Measured on this tree (P0.3).** Offscreen, the mini dataset loaded and pages
visited in Plotting, Statistics and Image Browser; medians of 15 runs (7 for
the forced sweep and `_adapt_all`), by `time.perf_counter` around the call, in
a scratch script that built `AllWellApp(None)` and called `shell.set_on(True)`:

| What | Measured |
|---|---|
| Widgets in the process, at the desk after load | 4,562 |
| Widgets in the process, in phone layout | 5,105 |
| `PhoneShell.set_on(True)` | 11.0 s |
| `_sweep(force=False)`, once every 300 ms | 18.4 ms |
| `_sweep(force=True)` | 109.4 ms |
| `_adapt_all(True)` | 80.2 ms |
| Widgets carrying `_awPhoneWatched`, so with an event filter on them | 120 |

The unforced sweep costs about three times what ARCHITECTURE.md says (6 ms), so
roughly 6 % of the main thread's time is spent re-finding widgets while phone
layout is on. The switch time is dominated by the stylesheet re-polish, which
this plan does not touch.

Fifteen phone test files, about 300 tests. `tests/test_phone_layout_app.py` is
the full-app one (`pytestmark = pytest.mark.slow`, a module-scoped `win`
fixture that builds `AllWellApp(None)`, and `mini_dataset` from
`tests/conftest.py` for a loaded Review). The offscreen screen is 800 px on its
short side, so the phone point there is `800 / 390`.

---

## 2. The target design

### 2.1 The rule, in one sentence

**A widget declares its phone form where it is constructed; the shell is told
the moment it exists and applies the form; the shell otherwise owns only the
window's chrome.**

### 2.2 The layers, and which may read the mode

| Layer | Holds | May read `phone_layout`? |
|---|---|---|
| Data and drawing — `domain/`, `services/`, `pipeline/`, `util/`, every `collect.py`, `renderer.py`, `*_compute.py` | numbers and figures; sizes arrive as arguments (DPI, height) | **never** |
| Tokens — `core/phone_layout.py`, `ui/theme.py` | the phone point, touch floors, type scale, the stylesheet | yes |
| Declarations — `widgets/form_registry.py` (new) | which widgets have a phone form, of which kind | no — it knows *kinds*, not phones |
| Views — `tab_view.py`, `*_view.py`, a sibling `_phone.py` / `phone_view.py` | what each widget becomes; custom forms | only in a `_phone.py` / `phone_view.py` sibling, or allowlisted with a reason |
| Chrome — `ui/phone_shell.py`, `ui/phone_drawer.py`, `ui/phone_window.py`, `ui/phone_fit.py` | top bar, strip, drawer, sheets, ⋯ menu, window geometry, banner, restyle, the per-kind adapters | yes |

A bare `if phone_layout.is_on():` in a controller or shared helper is what this
table exists to stop: it is how a second mode spreads through code that should
not know there is one.

### 2.3 `widgets/form_registry.py` — declarations by kind

It lives in `widgets/` because the classes that most need to declare
themselves are there (§1.2), and `widgets/` may not import the app. It knows
nothing about phones: it records *that a widget has an alternate form of a
given kind*, and tells whoever is listening. Proposed surface (settle the exact
names in P1.1, and write them here when settled):

- `declare(widget, kind: str)` — called from a constructor (`Stepper.__init__`,
  `ControlBand.__init__`, `MplToolbar.__init__`, `PlotCard` for its canvas,
  `info_icon`) or by the view that builds a widget (`form_chrome` for a form
  row). It stores the kind on the widget (a Qt property, so it survives Python
  wrapper churn) and notifies every listener with `(widget, kind)`.
- `register(widget, apply: Callable[[bool], None])` — a view's own custom form,
  for widgets that are not one of the shared kinds. Kind `"custom"`; the
  adapter calls `apply(on)`.
- `add_listener(fn)` / `remove_listener(fn)` — the shell listens.
- `declared(kind=None)` — the live declared widgets, for `_adapt_all` and the
  restyle bracket. Held weakly; a widget leaves on its `destroyed` signal, and a
  deleted wrapper's `RuntimeError` is treated as having left.
- Kind names are plain words with no phone in them: `"controls"`, `"toolbar"`,
  `"stepper"`, `"form_row"`, `"canvas"`, `"hint"`, `"custom"`.

**What the shell does with a declaration.** The listener runs inside the
widget's constructor, before the widget is finished, so it must **not** adapt
there. While phone layout is on it installs `_PhoneEventFilter` on the widget
(exactly as the sweep does today) and the adaptation happens on the widget's
first `Show` — the same moment it happens now for a widget built after the
switch. `PhoneShell._ADAPTERS` stays the one map from kind to adapter;
`_adapt_kind` reads the declared kind instead of class and object names.

**What stays exactly as it is:** every adapter's body, the restyle and its
bracket order, the drawer, the sheets, the ⋯ menu, `phone_window`'s fitting,
`phone_fit`. This is a change in *how widgets are found*, not in what is done
to them.

### 2.4 What declarations cannot reach, and stays a sweep

Widgets built by Qt or third-party code run no All-Well constructor: Qt's
standard dialogs, a combo box's popup list, scroll areas inside them. For those
the shell keeps a sweep, narrowed in P5 to **top-level windows** and their
scroll areas (`QApplication.topLevelWidgets()`, driven by `focusWindowChanged`
and the existing timer) instead of every widget in the process. Whether the
timer can go entirely is Q1, answered by measurement.

### 2.5 When a view gets a separate phone view

A widget that needs a different *size* or *arrangement* declares a kind or
registers an `apply(on)`. A sibling `phone_view.py`, sharing the controller and
state with the desk view, is warranted only when the phone's *interaction*
differs — navigation, flow, the control set. `analyze/phone_view.py` (the run
dashboard) is the existing example. A new one states its reason in its module
docstring.

### 2.6 What does not change

- Every user-visible size, place and behaviour. README_WellViewer.md is not
  touched.
- The restyle mechanism and its ~6 s cost — out of scope; P0.3 records it so a
  regression can be told apart from it.
- The rule that no event filter is installed on the application.
- The five views that already own their phone form (§1.1).

---

## 3. Rows

### Phase P0 — baseline and guards (one PR, before anything moves)

| Id | Item | State | Evidence |
|---|---|---|---|
| P0.1 | Layout snapshot harness and committed baseline | `done` | `tests/test_phone_layout_snapshot.py` + `tests/fixtures/phone_snapshot_baseline.json` (53 scenarios, 2,996 widgets, 779 KB, ~42 s): upright every Review page, Datasets tool and Analyze view; sideways Analyze and each section's first page; large text size on two pages. Identical on 5 consecutive runs. Mutations watched failing: `_adapt_stepper` made a no-op -> `Stepper ... touch_mode: True -> False` on Image Browser/Playground; `_adapt_toolbar` made a no-op -> `MplToolbar` appeared on Plotting/Bar Plots and Home/Cell Gating. Two sources of nondeterminism found and settled in `collect()`'s `take()`: Analyze's 'run problems' line (written by a timer, `_live_run_check`) and the shell's 300 ms sweep not yet having hidden a late-built control band; both are now run synchronously before each snapshot. Skips with the reason on another Qt version, font or screen |
| P0.2 | Guard: data and drawing layers never read the mode | `done` | `test_data_and_drawing_layers_never_read_the_mode`; rule already held on main, so proven by 7 bite cases in `test_guards_bite.py` |
| P0.3 | Record the costs on this tree | `done` | measured below in §1.5 |
| P0.4 | Ratchet: reads of the mode outside the sanctioned layers | `done` | `test_every_read_of_the_mode_outside_the_sanctioned_layers_is_rostered`; scratch `is_on()` in `stats/controller.py` failed it naming the line; 13 reads rostered (scan finds 16: `image_table/_phone_fit.py` owns its phone form and is sanctioned by path) |
| P0.5 | Ratchet: widgets the shell recognises by name | `done` | `test_the_shell_recognises_no_more_widgets_by_name_than_the_budget`, budget 10 (3 class keys, 1 object-name key, `InfoHint` x2, `draw_idle` x4); scratch extra key failed it |

**P0.1 — Layout snapshot harness.**
*Do:* write `tests/phone_snapshot.py`, a helper that walks a window's visible
widgets and returns, per widget: class name, object name, the declared kind
(once P1 exists; `None` before), `geometry()` relative to the window,
`isVisibleTo(window)`, font point size, `minimumHeight()`, and where they apply
`isTouchMode()`, a box layout's `direction()`, a canvas's figure DPI. Sort the
output so it is deterministic. Write `tests/test_phone_layout_snapshot.py`
(`pytestmark = pytest.mark.slow`; reuse the `win` fixture pattern from
`test_phone_layout_app.py` and `mini_dataset`): load the mini dataset, enter
phone layout through `win._phone.desk_button.click()`, visit every Review page
through the page menu, every Datasets tool and both Analyze views, and record
a snapshot of each, upright; then repeat sideways if the existing tests have a
way to turn the screen (search `test_phone_layout_app.py` for `landscape`). Keep
it to **one** app construction. Commit the baseline as
`tests/fixtures/phone_snapshot_baseline.json`, and make the test compare
against it, printing the first differing widgets.
*Done when:* the test passes on an unchanged tree twice in a row (determinism),
and fails after a deliberate one-line change.
*Mutation:* in `PhoneShell._adapt_stepper`, return early; the test must fail
naming a `Stepper` whose touch mode changed. Also check the harness itself: the
baseline must be non-empty and hold more than one page (CLAUDE.md §7.3 #1).
*Note:* if any field is nondeterministic across runs (timers, fonts), drop that
field and say so in this row rather than loosening the comparison.

**P0.2 — Guard: data and drawing never read the mode.**
*Do:* `tests/test_phone_mode_stays_in_views.py` with a test that no
`renderer.py`, `collect.py`, `*_compute.py`, or module under `domain/`,
`services/`, `pipeline/`, `util/` imports `core.phone_layout`,
`widgets.form_registry`, or `ui.phone_*`, at any nesting. Read files through
`tests/_repo_scan.parse_source` and take roots from `tests/_repo_scan.py`
(CLAUDE.md §2 rule 2, §7.5 #3).
*Done when:* it passes on the tree (the rule already holds) and has a case in
`tests/test_guards_bite.py` — a synthetic `renderer.py` importing
`core.phone_layout` inside a function — that it fires on.
*Mutation:* the guards-bite case is the mutation.

**P0.3 — Record the costs.**
*Do:* on the `win` fixture with the mini dataset loaded, measure: the switch
into phone layout (wall time of `set_on(True)`), one `_sweep(force=False)` and
one `_sweep(force=True)`, the number of widgets carrying `_awPhoneWatched`, and
`len(QApplication.allWidgets())`. A throwaway script in the session's scratch
directory is enough; do not commit it. Write the numbers and the method into
§1.5.
*Done when:* §1.5 holds the numbers with the method beside each. No test.

**P0.4 — Ratchet: reads of the mode outside the sanctioned layers.**
*Do:* in the same test file, count calls of `phone_layout.is_on`,
`add_listener` and `is_switching` outside `ui/`, `core/`, files named
`*_phone.py` or `*phone_view.py`, and `review/core/phone_panel_sheet.py`. Hold
them against an allowlist keyed by **file and function name** (not by file —
§7.5 #10), one written reason per entry. Today that is 13 rostered calls (the scan finds 16 with the Image Table's own `_phone_fit.py`, sanctioned by path): `allwell.py`
5, `review/core/plot_style.py` 3, `review/core/ui_helpers.py` 2,
`image_table/controller.py` 1, `review/core/split_view.py` 1,
`review/core/app_shell_view.py` 1 — re-count before writing the roster. Add a
liveness check: an allowlist entry that no longer matches a call fails the test,
so the roster can only shrink.
*Done when:* passes on the tree; a guards-bite case with a new `is_on()` call
in a synthetic `controller.py` fires it; deleting one roster entry fires it.

**P0.5 — Ratchet: widgets the shell recognises by name.**
*Do:* in the same file, read `ui/phone_shell.py`'s `_KIND_BY_CLASS` and
`_KIND_BY_OBJECT_NAME` dicts and the string and duck-type checks in
`_phone_relevant` and `_PhoneEventFilter.eventFilter`, through the AST, and
assert their total is at most a budget constant — measured as 10: 3 class keys + 1 object-name key + `"InfoHint"` compared in 2 places + the `draw_idle` duck type in 4.
Each later row lowers the budget in its own commit. P4.4 takes it to zero.
*Done when:* passes on the tree; adding a key to `_KIND_BY_CLASS` in a scratch
edit fails it.

### Phase P1 — the declarations registry (one PR; nothing migrates yet)

| Id | Item | State | Evidence |
|---|---|---|---|
| P1.1 | `widgets/form_registry.py` and its unit tests | `done` | `widgets/form_registry.py`, `tests/test_phone_form_registry.py` (11 tests). Liveness is `shiboken6.isValid` in `declared()`, not a `destroyed` connection (a wrapper outlives its C++ widget). Mutation: dropping the validity check failed `test_a_deleted_widget_leaves_declared_even_while_its_wrapper_lives` only after the test was strengthened — the first version asserted `declared('stepper')`, which drops a dead widget by itself because its kind reads as None, and survived the mutation; it now also asserts the unfiltered `declared()`. Listeners are not replayed (tested) |
| P1.2 | The shell listens to declarations and reads declared kinds first | `done` | `PhoneShell._listen_for_declarations` / `_on_declared`; `_adapt_kind` reads a declared kind first; new `"custom"` kind with `_adapt_custom`. Tests in `test_phone_form_registry.py` (a widget declared after the switch is adapted on Show with the sweep timer stopped; undeclared one left alone; none watched at the desk; a gone shell leaves no listener). Mutation: dropping the filter install failed the first. `test_phone_layout.py::test_every_kind_phone_layout_adapts_is_watched_and_dispatched` needed a `custom` widget in its list because the roster grew. Snapshot unchanged. NOT EXPLAINED: the first run of `test_phone_layout_app.py` after this change reported `2 failed, 10 passed` (12 items; the file collects 11) and its output was not kept; two later runs, with nothing changed in between but the roster test above, passed 11 of 11. Watch for it |
| P1.3 | Guard: `form_registry` stays free of phones and of the app | `done` | `test_the_form_registry_knows_kinds_and_not_phones`; 7 bite cases in `test_guards_bite.py` (core / ui / function-local review import, a phone identifier, string and attribute) and one clean file. The registry docstring was reworded so the word does not appear anywhere in the file |
| P1.4 | ARCHITECTURE.md: describe the registry and the layer table | `done` | ARCHITECTURE.md *Phone layout*: new paragraphs *Which layers know about phone layout* and *Widgets declare their phone form*, and a `form_registry` row in the `widgets/` table |

**P1.1 — The registry.**
*Do:* implement §2.3 in `widgets/form_registry.py`, Qt only, importing nothing
from the app (not even `core/`). Write `tests/test_phone_form_registry.py` with
real widgets: a declaration stores the kind on the widget and notifies a
listener once; `declared(kind)` lists live widgets of that kind; a widget
deleted with `deleteLater()` and an event-loop turn is gone from `declared()`;
`register(w, apply)` records kind `"custom"` and keeps `apply` callable; a
listener added after a declaration is not called retroactively (or is — decide,
and test what you decide). Name the wrong implementation each test rules out
before keeping it (§7.8).
*Done when:* those tests pass and the module is under the budgets.
*Mutation:* remove the `destroyed` connection; the deletion test must fail.

**P1.2 — The shell listens.**
*Do:* `PhoneShell` subscribes to `form_registry` at construction. On a
declaration while phone layout is on, it installs `_PhoneEventFilter` and marks
`_awPhoneWatched` (never adapting inside the constructor — §2.3). `_adapt_kind`
checks the declared kind **first**, then falls back to the existing class- and
object-name rules, so nothing changes until P2 declares anything. `_adapt_all`
is unchanged in this row.
*Done when:* a test builds a bare `QWidget`, declares it `"stepper"`-like with a
fake `setTouchMode`, after phone layout is on and before it is shown, shows
it, and asserts the fake was called with `True`. P0.1's snapshot is unchanged.
*Mutation:* drop the filter installation in the listener; the test must fail.

**P1.3 — Guard.**
*Do:* extend `tests/test_phone_mode_stays_in_views.py`: `widgets/form_registry.py`
imports nothing from `core`, `ui`, `review`, `analyze`, `datasets` or
`allwell`, and contains no identifier or string with `phone` in it.
*Done when:* a guards-bite case (a registry importing `core.phone_layout`)
fires it.

**P1.4 — ARCHITECTURE.md.**
*Do:* in *Phone layout → Where it lives*, describe the registry, its place in
`widgets/` and why, and the layer table of §2.2 — as what is true now, no
"new" or "previously" (CLAUDE.md §6). Add `widgets/form_registry.py` wherever
ARCHITECTURE lists the modules of `widgets/`.
*Done when:* `tests/test_doc_version_sync.py` passes and the section reads
correctly against the code.

### Phase P2 — the shared kinds declare themselves (one PR, a commit per row)

Each row: the constructor declares its kind; the class-name or object-name rule
for that kind is deleted from `_KIND_BY_CLASS` / `_KIND_BY_OBJECT_NAME`;
P0.5's budget drops by one in the same commit; P0.1's snapshot is unchanged.
Each row adds one test that builds a real page holding the widget **after**
phone layout is on (the lazy-page case the sweep exists for) and asserts the
phone form arrived — through the page's real builder, not by constructing the
widget alone (§7.8 rule 3).

| Id | Item | State | Evidence |
|---|---|---|---|
| P2.1 | `Stepper` declares `"stepper"` | `done` | `Stepper.__init__` declares `"stepper"`; `"Stepper"` left `_KIND_BY_CLASS`; P0.5 budget 10 -> 9. `test_a_stepper_built_after_the_switch_takes_its_touch_form_without_a_sweep` (real `Stepper`, sweep timer stopped). Mutation: deleting the `declare` line failed it (`None == 'stepper'`) and failed the snapshot (`Stepper#Stepper touch_mode: True -> False`, width 447 -> 125 on Image Browser/Playground). The snapshot no longer records the declared kind (it is how the shell finds a widget, not what is seen); schema 2, baseline rewritten without that field — nothing else in it changed |
| P2.2 | `MplToolbar` declares `"toolbar"` | `done` | `MplToolbar.__init__` declares `"toolbar"`; `"MplToolbar"` left `_KIND_BY_CLASS`; budget 9 -> 8. `test_a_plot_toolbar_built_after_the_switch_is_hidden_without_a_sweep` (real toolbar on a real canvas, sweep stopped). Mutation: deleting the `declare` failed it (`None == 'toolbar'`) and the snapshot (`MplToolbar ... appeared` on Plotting/Bar Plots at large text and Home/Cell Gating). `test_phone_layout.py::test_a_matplotlib_toolbar_shown_in_phone_layout_is_hidden` built a stub class named `MplToolbar` so the by-name rule would match it — it asserted the mechanism being removed, so it now uses the real class (same intent: a toolbar is hidden on a phone) |
| P2.3 | Analyze form rows declare `"form_row"` | `todo` | |
| P2.4 | `ControlBand` and the Image Table's controls declare `"controls"` | `todo` | |

**P2.1 — Stepper.** *Do:* `widgets/stepper.py`, end of `Stepper.__init__`:
`form_registry.declare(self, "stepper")`. Remove `"Stepper"` from
`_KIND_BY_CLASS`. *Test:* build the Playground tab (it holds a stepper,
`playground/tab_view.py:496`) after entering phone layout; assert the stepper
is in touch mode after it is shown. *Mutation:* delete the `declare` line; the
test must fail with the stepper's touch mode `False`.

**P2.2 — Plot toolbar.** *Do:* `widgets/mpl_toolbar.py`, `MplToolbar.__init__`:
declare `"toolbar"`. Remove `"MplToolbar"` from `_KIND_BY_CLASS`. *Test:* a page
with a `PlotCard` (its toolbar is built at `widgets/plot_card.py:263`) built
after the switch; its toolbar is hidden once shown. *Mutation:* delete the
`declare`; the toolbar must be visible and the test fail.

**P2.3 — Analyze form row.** *Do:* `analyze/form_chrome.py:292`, where the row
gets its object name, also declare `"form_row"`. Keep the object name (the
stylesheet or `form_chrome.py:330` may use it — check). Remove `AnalyzeFormRow`
from `_KIND_BY_OBJECT_NAME`. *Test:* the Analyze form, built after the switch;
a row's layout direction is top-to-bottom, and the label's width pin returns at
the desk. *Mutation:* delete the `declare`; direction stays left-to-right.

**P2.4 — Controls.** *Do:* `widgets/control_band.py`, `ControlBand.__init__`:
declare `"controls"`. `image_table/tab_view.py:113`: replace
`setProperty("phoneControls", True)` with `declare(self, "controls")`. Remove
`"ControlBand"` from `_KIND_BY_CLASS` and the `phoneControls` property check
from `_is_phone_controls`. Leave `_band_managed`'s parent walk and the Controls
sheet's logic as they are unless a test shows they need the registry. *Test:*
open a plot page with a band after the switch; the band is hidden in the page
and present in the Controls sheet (see the existing
`test_the_controls_sheet_reads_as_one_column_of_labelled_rows` in
`test_phone_layout_app.py` for how to open it). *Mutation:* delete the
`ControlBand` `declare`; the band stays visible in the page.

### Phase P3 — plots and hints (one PR)

| Id | Item | State | Evidence |
|---|---|---|---|
| P3.1 | One way to build a canvas, which declares `"canvas"` | `todo` | |
| P3.2 | The canvas duck type leaves the shell | `todo` | |
| P3.3 | ⓘ hints declare `"hint"` | `todo` | |

**P3.1 — Canvases.** *Do:* add `widgets/canvas.py::make_canvas(figure, parent=None)`
that constructs `FigureCanvasQTAgg` and declares `"canvas"`. `PlotCard` uses it
at `widgets/plot_card.py:257`. Convert the six direct sites listed in §1.2,
including the attribute-call site at `smfish/tab_view.py:542`, where
`ctx.runtime.smfish.FigureCanvas` is a stored class — find where that attribute
is assigned and decide whether the call or the stored class changes. Add a
guard to `tests/test_phone_mode_stays_in_views.py`: no call to a name or
attribute ending in `FigureCanvas`, `FigureCanvasQTAgg` or `FigureCanvasQT`
outside `widgets/canvas.py` — and add guards-bite cases for the aliased import
(`as FigureCanvas`) and the attribute call, because those are the two spellings
in the tree (§7.5 #5). *Done when:* the guard's count before converting equals
seven (the six direct sites plus `PlotCard`'s); if it reports fewer, the guard
is blind, not the sweep over-eager (§7.5 #1). *Mutation:* revert one site to a
bare `FigureCanvas(`; the guard fails naming it.

**P3.2 — Canvases leave the duck type.** *Do:* `_adapt_kind` gains
`"canvas"`, dispatching to what `_sweep` and `_PhoneEventFilter` do for a
canvas now (`scale_canvas` then `size_canvas`); remove the
`draw_idle`/`figure` checks from `_phone_relevant`, `_sweep` and
`_PhoneEventFilter.eventFilter`. `_size_canvases` and `scale_canvases` iterate
`form_registry.declared("canvas")` instead of `_canvases()`, if that is
equivalent — check what `_canvases()` returns first. Lower P0.5's budget.
*Test:* a plot page built after the switch has the phone DPI and the minimum
height per axes row. *Mutation:* remove `"canvas"` from `_ADAPTERS`; the DPI is
the desk's and the test fails.

**P3.3 — Hints.** *Do:* `widgets/info_icon.py:26` declares `"hint"`. For the
three direct `setObjectName("InfoHint")` sites (§1.2), either build through
`info_icon` or declare at the site; note that `nanowell/subset_view.py:356`
switches the object name between `"Warn"` and `"InfoHint"` at run time, so read
what it is for before choosing. The tap behaviour moves from
`_PhoneEventFilter.eventFilter` into a `"hint"` handling that the filter
dispatches by declared kind. Remove the `"InfoHint"` checks from the shell;
lower P0.5's budget. *Test:* an `info_icon` built after the switch shows its
tooltip on a tap (a synthetic `MouseButtonRelease`; see how
`tests/test_phone_layout.py` tests the existing behaviour — search for
`InfoHint`). *Mutation:* delete the `declare`; no tooltip.

### Phase P4 — what the window hands the shell (one PR)

| Id | Item | State | Evidence |
|---|---|---|---|
| P4.1 | `desk_only` widgets declare themselves | `todo` | |
| P4.2 | Phone-hidden Review sections are declared in the feature registry | `todo` | |
| P4.3 | Decide each remaining pane-owned handle | `todo` | |
| P4.4 | P0.5's budget is zero | `todo` | |

**P4.1 — `desk_only`.** *Do:* add a kind `"desk_only"` (adapter: hide on the
phone, show again at the desk, through `_hide` / `_hid` as today). Each widget
`_build_phone_shell` puts in `PhoneHandles.desk_only` declares it where it is
built; remove the field from `PhoneHandles`. Read `_apply_chrome` first: it
hides these *before* the restyle and restores them on the way out, and the
declaration route must keep that order. *Test:* the existing round-trip test in
`test_phone_layout_app.py` asserts which buttons are hidden — confirm it still
does, and that it fails if one declaration is removed. *Mutation:* remove one
widget's declaration; that widget is visible on the phone.

**P4.2 — Phone-hidden sections.** *Do:* Review CSV and Batch Export are hidden
from the rail on a phone via `PhoneHandles.phone_hidden_sections`. Move that to
a field each feature declares in `review/features/registry.py` (read how the
registry declares a feature first, and follow its pattern). Add a test that
**every** registered section states its phone visibility explicitly, so a new
section that omits it fails rather than defaulting (CLAUDE.md §3, *A new tab is
not one edit*). *Mutation:* flip Batch Export's flag; the existing test of the
hidden sections fails.

**P4.3 — The remaining handles.** For each of `fit_roots`, `logs`,
`dashboards`, `analyze_body`, `analyze_rule`, decide: chrome the shell
legitimately owns (stays a handle), or a pane's widget with a phone form of its
own (declares a kind or registers an `apply`). Write the decision and its reason
for each handle into this row's evidence column, then implement the ones that
move, one commit each. `dashboards` (`PlateProgress.setStacked`) is the likeliest
to move; `fit_roots` (the Datasets pane's scroll areas, fitted by `_FitOnShow`)
the likeliest to stay. If a decision is not clear, ask (§0.6).

**P4.4 — Zero.** P0.5's budget constant is 0, `_KIND_BY_CLASS` and
`_KIND_BY_OBJECT_NAME` are gone, and the shell recognises widgets only by
declared kind and by `phone_window.kind` (windows). *Done when:* the ratchet
test asserts 0.

### Phase P5 — narrow the sweep (one PR, after Q1)

| Id | Item | State | Evidence |
|---|---|---|---|
| P5.1 | The sweep looks only at top-level windows and their scroll areas | `blocked` | blocked on Q1 |
| P5.2 | Drag-to-scroll applied per window and per declared page | `blocked` | blocked on Q1 |

**P5.1** — `_sweep` iterates `QApplication.topLevelWidgets()` and, for each
window `phone_window.kind` accepts, that window's scroll areas — not
`allWidgets()`. `_adapt_all` iterates `form_registry.declared()`. Re-measure
P0.3's numbers and write both into §1.5. *Test:* a `QMessageBox` and a dialog
with a combo box opened after the switch still get their phone forms (the
existing `test_phone_windows.py` likely covers this — confirm it fails when the
window branch is removed).

**P5.2** — drag-to-scroll is applied when a window gets its phone form and when
a declared page is shown, rather than to every scroll area the sweep meets. A
combo box's popup list is the case to test.

### Phase P6 — the remaining inline reads (one PR)

| Id | Item | State | Evidence |
|---|---|---|---|
| P6.1 | `allwell.py`'s five reads | `todo` | |
| P6.2 | `review/core/plot_style.py`'s three reads | `todo` | |
| P6.3 | `split_view.py`, `app_shell_view.py`, `image_table/controller.py` | `todo` | |
| P6.4 | P0.4's allowlist at its final size | `todo` | |

For each read: either it moves into the shell (chrome), a `_phone.py` sibling,
or a declared `apply(on)`, or it stays on P0.4's allowlist with its reason
written at the site and in the roster. The window is entitled to know its own
layout, so several of `allwell.py`'s five (the close confirmation at 2193, the
startup preference) are likely to stay. Each row removes the entries it moves
from P0.4's roster in the same commit; the liveness check fails otherwise. No
behaviour changes: the P0.1 snapshot and the existing phone tests are the
evidence.

### Phase P7 — close (the last PR)

| Id | Item | State | Evidence |
|---|---|---|---|
| P7.1 | Reconcile every row and every claim in §1 against the tree | `todo` | |
| P7.2 | ARCHITECTURE.md *Phone layout* rewritten to the new design | `todo` | |
| P7.3 | CLAUDE.md §3: where a phone form goes | `todo` | |
| P7.4 | Delete this file | `todo` | |

**P7.1** — re-read every original row against the code, not against this file
(CLAUDE.md §6.1 #6). Re-run P0.1 and P0.3; put the final numbers in the PR body.

**P7.2** — rewrite *Phone layout* to describe declarations, the narrowed sweep
and the remaining chrome as what is true. Run `scripts/rename_sweep_check.sh`
for each helper that moved, and grep the section for "sweep", "class name",
"object name" and "every widget" for sentences the migration made false
(§7.5 #6).

**P7.3** — one line under CLAUDE.md §3's *Where new code goes*: a widget with a
phone form declares it with `widgets.form_registry` where it is constructed;
a separate phone view needs a written reason.

**P7.4** — delete this file; say so in the PR body.

### Questions

| Id | Question | State | Answer |
|---|---|---|---|
| Q1 | Once P4 has merged, what does the 300 ms sweep still find that no declaration covers? | `todo` | |
| Q2 | Does `_PhoneEventFilter` remain necessary once every kind is declared? | `todo` | |

**Q1** — instrument `_sweep` (a scratch edit, not committed) to log the class
and object name of every widget it adapts or watches that carries no declared
kind. Drive the loaded `win` fixture through every page, every Datasets tool,
both Analyze views, and the dialogs the tests already open. If the list holds
only top-level windows and their contents, P5 goes ahead as written; if it
holds widgets All-Well constructs, each becomes a new P2/P3 row first. Write
the list here.

**Q2** — a declared widget still needs its `Show` to be adapted (§2.3), so the
filter probably stays on declared widgets and windows. Decide after P3 from what
is left in `_PhoneEventFilter.eventFilter`, and write the answer here.

---

## 4. Risks every row must watch

- **A widget the sweep reached and no declaration does.** It is the one failure
  this migration can introduce, and it is silent: the widget keeps its desk
  form on the phone and nothing raises. P0.1's snapshot is the defence; a row
  that leaves the snapshot changed is not `done`.
- **Declaring inside a constructor.** The listener runs before the widget is
  finished, and before a subclass's `__init__` has run. Adapt on `Show`, never
  in the listener.
- **Lazily built pages.** Each P2/P3 test builds its page *after* entering
  phone layout, because that is the case the sweep handled and a declaration
  must now handle.
- **Deleted wrappers.** Any call on a widget Shiboken has released raises
  `RuntimeError`; treat it as the widget having left, through
  `core.safe.swallowed()` inside the `except` block.
- **The restyle bracket.** Widgets with a phone form give it up before every
  restyle and take it back after (`_restyle_in_place`, `set_on`). When
  `_adapt_all` changes what it iterates, the bracket must still reach every
  declared widget — P0.1 run with a text-size change (⋯ → S / M / L) is the
  check.
- **UI-lane tests are not run by CI.** Run `scripts/test.sh` locally before
  every phase's PR (§0.5).
