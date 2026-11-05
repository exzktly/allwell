# Phone layout — migration plan and tracker

**This document is temporary.** It exists because this body of work spans
several pull requests and more than one session, and CLAUDE.md §6.1 requires
that a plan of this size live on disk rather than in conversation. It is
deleted by the pull request that closes its last row, and that pull request
says in its body that it is gone. Until then it is the plan *and* the record of
where the work stands.

It plans one change of direction for phone layout (ARCHITECTURE.md, *Phone
layout*): from a shell that finds widgets it does not own and rewrites them
after they are built, to widgets that are built knowing their phone form and
hand it to one registry. Nothing a person sees on the phone or at the desk is
meant to change. The work is structural, and every row is judged by whether the
phone layout is the same afterwards and cheaper to keep correct.

---

## This document is live, and it leads the work

1. **Read it before touching anything.** The first act of any session working
   on this is to open this file and find the first row that is not `done` or
   `declined` in the lowest-numbered phase that is not yet merged. That row is
   the work.
2. **The row and the code move in the same commit.** Never a batch at the end
   of a session, never "once CI is green".
3. **What a row learns goes into the row.** A measurement, a refuted
   assumption, an answer from the user, a site that turned out to be somewhere
   else — it is written where the next reader will look.
4. **A finding is a row.** Anything a reviewer, a test failure or the tree
   itself turns up becomes a numbered row before it is acted on, under the row
   that produced it and keeping its number (`P2.1a`).
5. **A row that turns out to be wrong is `declined` with the reason, never
   deleted.**
6. **Report progress by reading this file, not by remembering.**
7. **The design sections are rewritten, not annotated.** When a measurement
   changes the design, §2 is rewritten to say what is now true. As each phase
   merges, the part of §2 it implemented moves into ARCHITECTURE.md's *Phone
   layout* section, which describes the tree as it is.

## How to read the rows

Every row has an id, a place, a state, and — once it is `done` — its evidence.

- **`todo`** — not started.
- **`wip`** — started, not finished. Never left across a session boundary
  without a note in the evidence column saying where it stopped.
- **`done`** — implemented **and** pinned by a test that was watched failing
  without the change (CLAUDE.md §7.7 #2). For a question row, `done` means
  answered, and the answer is written in the row.
- **`blocked`** — cannot proceed until a named row closes. The blocker is named.
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

The measurements below are the starting point the rows are written against.
Each is a hypothesis about the tree as it stood then (CLAUDE.md §6.1); a row
that depends on one re-measures it before acting.

**Three tiers exist already, unevenly.**

- **Rules and the flag** — `core/phone_layout.py` (197 lines): `is_on`,
  `set_on`, `is_switching`, `switching()`, listeners, the phone point, the type
  scale, the touch sizes, the plot DPI factor. Qt-free. This tier is right and
  the plan keeps it as it is.
- **The shell** — `ui/phone_shell.py` (2,280 lines), `ui/phone_window.py`
  (325), `ui/phone_drawer.py` (134), `ui/phone_fit.py` (427). It owns the
  window chrome (top bar, foot strip, drawer, Controls and Wells sheets, ⋯
  menu, window geometry, the switch banner, the restyle) **and** it adapts
  widgets it does not own. The second job is the one this plan moves.
- **Views that own their phone form** — `analyze/phone_view.py` (380),
  `review/features/home/_phone.py` (165), `review/features/home/sample_map/_phone.py`
  (281), `review/features/image_browser/image_table/_phone_fit.py` (150),
  `review/core/phone_panel_sheet.py` (172). These are the target shape: each
  listens to `phone_layout`, is applied by its owner, and is tested with it.

**What the shell adapts that it does not own.** `PhoneShell._sweep` runs every
`_SWEEP_MS` (300 ms) while phone layout is on, plus on window focus and page
change, walks `QApplication.allWidgets()` for widgets without the
`_awPhoneSeen` mark, installs `_PhoneEventFilter` on the relevant ones, and
adapts them by kind. The kinds are recognised by **class name or object name
strings**, not by anything the widget declares:

| Kind | Recognised by | Where the widget is built | What the shell does |
|---|---|---|---|
| controls | class name `ControlBand`, or property `phoneControls` + `setStacked` | `widgets/control_band.py` (9 constructions); `image_table/tab_view.py` | hides it in the page, moves it to the Controls sheet |
| toolbar | class name `MplToolbar` | 9 constructions | hides it |
| stepper | class name `Stepper` | `widgets/stepper.py` (10 constructions) | `setTouchMode(on)` |
| form_row | object name `AnalyzeFormRow` | `analyze/form_chrome.py:292` | turns the row top-to-bottom, saves and restores the label's width pins |
| canvas | duck type (`draw_idle` + `figure`) | 7 `FigureCanvas(...)` sites + `widgets/plot_card.py:257` | DPI, minimum height per axes row, swipe-to-scroll |
| window | `phone_window.kind` | any dialog or pop-out, including Qt's own | the phone form (scroll, foot buttons) or keep-on-screen |
| ⓘ hint | object name `InfoHint` | `review/features/batch_export/nanowell_panel.py:183` and others | a tap shows the tooltip text |
| scroll area | `QAbstractScrollArea` + `drag_scroll_eligible` | anywhere, including combo popups | drag-to-scroll on the viewport |

**What the shell is told by the window.** `PhoneHandles` (`ui/phone_shell.py:73`)
carries about thirty fields from `AllWellApp._build_phone_shell`
(`allwell.py:687`). Some are chrome the shell legitimately needs (header, band,
mode bar, navigator, pages). Others are lists of other panes' widgets the shell
acts on because the pane did not: `desk_only`, `phone_hidden_sections`,
`fit_roots`, `logs`, `dashboards`, `analyze_body`, `analyze_rule`.

**Where `phone_layout` is read outside `ui/` and `core/`.** 33 call sites of
`is_on` / `add_listener` / `is_switching`, by file: `home/sample_map/_phone.py`
8, `home/_phone.py` 6, `allwell.py` 5, `image_table/_phone_fit.py` 3,
`review/core/plot_style.py` 3, `review/core/ui_helpers.py` 2,
`review/core/phone_panel_sheet.py` 2, `image_table/controller.py` 1,
`review/core/split_view.py` 1, `review/core/app_shell_view.py` 1,
`analyze/phone_view.py` 1. No `renderer.py`, `collect.py`, `*_compute.py`,
`domain/`, `services/` or `pipeline/` module reads it; the two that mention
"phone" (`spatial/renderer.py:444`, `nanowell/qc_compute.py:346`) do so in a
comment. That separation is the part that is already right and must stay so.

**The costs the current shape carries**, each documented in ARCHITECTURE.md:
the sweep and its marks; per-widget event filters (an application-wide filter
crashed on Windows and is forbidden); the restyle bracket in
`PhoneShell._restyle_in_place`, which takes every adapted form off and puts it
back so the rescale pass only sees desk-shaped widgets (a stepper scaled while
in touch form came back to the desk 227 px wide instead of 96); about 6 s per
switch on a loaded app, nearly all of it `QApplication.setStyleSheet`.

**The test base.** Fifteen phone test files, about 300 tests
(`tests/test_phone_*.py`, `test_*phone*.py`). Several are UI-lane files that
CI does not run (`tests/_lanes.py::UI_LANE_FILES`), so the local fast lane is
their only gate.

---

## 2. The target design

### 2.1 The rule, in one sentence

**A widget's phone form is declared by the code that builds the widget, and
applied by one registry; the shell owns only the window's chrome.**

### 2.2 The four layers and what may live in each

| Layer | Holds | May read `phone_layout`? |
|---|---|---|
| Data and drawing — `domain/`, `services/`, `pipeline/`, `util/`, every `collect.py`, `renderer.py`, `*_compute.py` | numbers and figures; sizes arrive as arguments (DPI, height) | **never** |
| Tokens — `core/phone_layout.py`, `ui/theme.py` | the phone point, touch floors, type scale, the stylesheet | yes; this is where the rules are |
| Registry — `core/phone_form.py` (new) | which widgets have a phone form and how to apply it | yes |
| Views — `tab_view.py`, `*_view.py`, a sibling `_phone.py` | what each widget *becomes* on a phone | only through the registry, or in a `_phone.py` sibling |
| Chrome — `ui/phone_shell.py`, `ui/phone_drawer.py`, `ui/phone_window.py` | top bar, strip, drawer, sheets, ⋯ menu, window geometry, banner, restyle | yes |

A bare `if phone_layout.is_on():` in a controller or a shared helper is the
thing this table exists to stop: it is how a second mode spreads through code
that should not know there is one.

### 2.3 `core/phone_form.py` — the registry

`core/` is application infrastructure and may use Qt, and it imports no pane,
so every pane can register with it. Its surface, to be settled in P1.1:

- `register(widget, apply: Callable[[bool], None], *, scaled: bool = False)` —
  `apply(on)` gives the widget its phone form (`True`) or its desk form
  (`False`). The registry calls it once at registration with the current state,
  on every switch, and — when `scaled=False`, the default — with `False` before
  each restyle made inside phone layout and `True` after it, which is the
  bracket `PhoneShell._restyle_in_place` performs by hand today. It holds the
  widget weakly and drops it on `destroyed`, treating a `RuntimeError` from a
  deleted wrapper as a drop.
- `desk_only(widget)` — hidden in phone layout, shown again at the desk. Replaces
  the `PhoneHandles.desk_only` list.
- `canvas(canvas)` — the plot's phone form (DPI, minimum height per axes row,
  swipe-to-scroll), so the shell's canvas branch becomes a registration at the
  places plots are built.
- `controls(widget)` — a page's controls, which the Controls sheet takes. The
  sheet is chrome and stays in the shell; the registry is how the shell learns
  which blocks are controls without matching class names.
- The shell drives it: `PhoneShell.set_on` calls `phone_form.apply_all(on)`
  where it calls `_adapt_all` now, and the restyle calls the bracket.

**`widgets/` stays free of app knowledge.** A reusable widget exposes a neutral
setter that says what it does, not why — `Stepper.setTouchMode`,
`ControlBand.setStacked`, `PlateProgress.setStacked` already do. The view that
builds it registers it: `phone_form.register(stepper, stepper.setTouchMode)`.
Nothing in `widgets/` imports `core.phone_form`.

**Lazy pages register when they build.** A tab body built after the switch
registers its widgets as it creates them and receives the current state at
once, so the "one more sweep on the next turn of the loop for a page that
builds its body only once shown" disappears for owned widgets.

### 2.4 What the registry cannot reach, and stays a sweep

Some widgets are built by Qt or by third-party code and no All-Well code runs
when they are created: `QMessageBox` and the other standard dialogs, a combo
box's popup list, scroll areas inside them. For those the shell keeps a sweep,
narrowed to **top-level windows** (`QApplication.topLevelWidgets()`, driven by
`focusWindowChanged` and the existing timer) instead of every widget in the
process. Whether the timer can go entirely is question Q1, answered by
measurement, not assumed.

### 2.5 When a view gets a separate phone view

Most widgets need a different *size* or *arrangement*, which a registered
`apply(on)` covers. A sibling `phone_view.py` beside the desk view, sharing the
controller and the state, is warranted only when the phone's *interaction*
differs — a different navigation, a different flow, a different control set.
`analyze/phone_view.py` (the run dashboard) is the existing example. A new one
needs a written reason in its module docstring.

### 2.6 What does not change

- The phone point, touch floors, type scale and every user-visible size.
- The restyle mechanism and its cost (the stylesheet re-polish is ~6 s and is
  out of scope; P0.3 records it so a regression can be told apart from it).
- The rule that no event filter is installed on the application.
- The drawer, the sheets, the top bar and the ⋯ menu — chrome, and the shell's.
- README_WellViewer.md: nothing a person does changes. A row that finds itself
  changing behaviour stops and becomes a question row.

---

## 3. Rows

### Phase P0 — baseline and guards (one PR, before any code moves)

| Id | Item | Place | State | Evidence |
|---|---|---|---|---|
| P0.1 | **Layout snapshot harness.** For each Review page, each Datasets tool and Analyze, in phone layout upright and sideways at a 390-point phone, dump every visible widget's class, object name, geometry, visibility, font size, minimum height and stepper/stack state to JSON. Commit the baseline. Every later phase diffs against it and must be identical or explain each difference in its row. The harness checks its own output is non-empty and that it exited 0 before printing a verdict (CLAUDE.md §7.3 #1). Full-app: run a file at a time, never in the fast lane | `tests/phone_snapshot.py` (helper), `tests/test_phone_layout_snapshot.py` (slow) | `todo` | |
| P0.2 | **Guard: data and drawing never read the mode.** No `renderer.py`, `collect.py`, `*_compute.py`, or module under `domain/`, `services/`, `pipeline/`, `util/` imports `core.phone_layout` or `core.phone_form`. Passes on the base, so it is a rule that already holds; add a `tests/test_guards_bite.py` case so it is proven to fire | `tests/test_phone_mode_stays_in_views.py` | `todo` | |
| P0.3 | **Record the costs.** Switch time on the loaded-app fixture, sweep time per tick, number of watched widgets, number of event filters installed. Written into §1 with the method beside each number | §1 of this file | `todo` | |
| P0.4 | **Ratchet: reads of the mode outside the sanctioned layers.** Count `phone_layout.is_on` / `add_listener` / `is_switching` calls outside `ui/`, `core/phone_*`, `*_phone.py`, `*phone_view.py`, `phone_panel_sheet.py`. Start at today's count — 13 when this plan was written: `allwell.py` 5, `plot_style.py` 3, `ui_helpers.py` 2, `image_table/controller.py` 1, `split_view.py` 1, `app_shell_view.py` 1; re-count before writing the number, allowlisted by site with a reason each, and only moving down (CLAUDE.md §4, §7.5 #10) | `tests/test_phone_mode_stays_in_views.py` | `todo` | |
| P0.5 | **Ratchet: class-name and object-name recognition in the shell.** Count the entries in `_KIND_BY_CLASS`, `_KIND_BY_OBJECT_NAME` and the `"InfoHint"` / duck-typed canvas checks in `_phone_relevant`. Each phase below removes entries; the ratchet goes to zero at the end of P4 | `tests/test_phone_mode_stays_in_views.py` | `todo` | |

### Phase P1 — the registry (one PR; nothing migrates yet)

| Id | Item | Place | State | Evidence |
|---|---|---|---|---|
| P1.1 | **`core/phone_form.py`**: `register`, `desk_only`, `canvas`, `controls`, `apply_all`, the restyle bracket, weak references dropped on `destroyed`. Unit tests with real widgets: a registered widget receives the current state at once, on every switch, `False`/`True` around a restyle, and nothing after deletion. Name the wrong implementation for each test before keeping it (§7.8) — e.g. a registry that forgets the restyle bracket must fail the bracket test | `core/phone_form.py`, `tests/test_phone_form.py` | `todo` | |
| P1.2 | **The shell drives the registry** beside its existing paths: `set_on` calls `apply_all`, `_restyle_in_place` calls the bracket. With nothing registered yet, P0.1's snapshot is unchanged | `ui/phone_shell.py` | `todo` | |
| P1.3 | **Guard: `widgets/` does not import `core.phone_form`.** Reusable widgets expose neutral setters; registration is the view's job. Extend `test_architecture_contracts.py` rather than adding a file if the widgets rule already lives there | `tests/test_architecture_contracts.py` | `todo` | |
| P1.4 | ARCHITECTURE.md *Phone layout*: rewrite *Where it lives* to describe the registry and the layer table of §2.2 | `_Docs/Markdowns/ARCHITECTURE.md` | `todo` | |

### Phase P2 — the four adapted kinds (one PR, a commit per row)

Each row removes one kind from `_KIND_BY_CLASS` / `_KIND_BY_OBJECT_NAME` and
`PhoneShell._ADAPTERS`, registers every construction site instead, and adds one
test that goes through the real builder of a page holding the widget (§7.8 #3),
not through the registry alone. The P0.1 snapshot must be unchanged. Count the
construction sites by searching for the concept — what the widget does — not
only the class name (§7.5 #5).

| Id | Item | Place | State | Evidence |
|---|---|---|---|---|
| P2.1 | **Stepper** — every construction registers `setTouchMode`. Enumerate the ~10 `Stepper(` sites; a site the sweep reached and the registry does not is a stepper left desk-sized on the phone, so the test asserts a stepper on a lazily built page is in touch form | `widgets/stepper.py` callers | `todo` | |
| P2.2 | **Plot toolbar** — every `MplToolbar(` site (~9) registers `desk_only` | toolbar callers | `todo` | |
| P2.3 | **Analyze form row** — `analyze/form_chrome.py` registers the stacking, and the label-width pin save/restore moves out of `PhoneShell._stack_form_row` into the form, which owns those pins | `analyze/form_chrome.py`, `ui/phone_shell.py` | `todo` | |
| P2.4 | **Control bands** — the ~9 `ControlBand(` sites and the Image Table's `phoneControls` block register with `phone_form.controls`; the Controls sheet asks the registry for the current page's controls instead of matching `ControlBand` by name. `_band_managed`'s parent walk stays if the sheet still needs it — measure first | `widgets/control_band.py` callers, `image_table/tab_view.py:113`, `ui/phone_shell.py` | `todo` | |

### Phase P3 — plots and hints (one PR)

| Id | Item | Place | State | Evidence |
|---|---|---|---|---|
| P3.1 | **One way to build a canvas.** A `review/core` helper that builds a `FigureCanvas` and registers it with `phone_form.canvas`; the seven direct `FigureCanvas(` sites and `widgets/plot_card.py:257` go through it (PlotCard is in `widgets/`, so its *caller* registers it). AST guard: no `FigureCanvas(` call outside the helper | `review/core/`, the seven sites listed in §1, `tests/test_phone_mode_stays_in_views.py` | `todo` | |
| P3.2 | **Remove the canvas branch from `_sweep` and `_phone_relevant`.** Test: a plot on a page built after the switch gets its phone DPI and height — the case the sweep exists for | `ui/phone_shell.py` | `todo` | |
| P3.3 | **ⓘ hints** — the code that builds an `InfoHint` registers its tap behaviour (`review/core/ui_helpers.py` already listens to the mode, so it is the natural owner); the `"InfoHint"` object-name check leaves the shell | `review/core/ui_helpers.py`, `ui/phone_shell.py` | `todo` | |

### Phase P4 — what the window hands the shell (one PR)

| Id | Item | Place | State | Evidence |
|---|---|---|---|---|
| P4.1 | **`desk_only` becomes declarations.** Each widget in `PhoneHandles.desk_only` is registered with `phone_form.desk_only` where it is built; the handle is removed | `allwell.py:_build_phone_shell`, owners | `todo` | |
| P4.2 | **Phone-hidden sections are declared in the feature registry.** Review CSV and Batch Export carry a flag in `features/registry.py` instead of `PhoneHandles.phone_hidden_sections`; a new section states its phone visibility by being declared. Guard that every section declares it, so an omission is a failure, not a default | `review/features/registry.py`, `allwell.py`, `ui/phone_shell.py` | `todo` | |
| P4.3 | **`fit_roots`, `logs`, `dashboards`, `analyze_body`, `analyze_rule`**: for each, decide whether it is chrome (stays a handle) or a pane's own widget (registers itself). Write the decision per handle in this row | `ui/phone_shell.py:73`, `allwell.py` | `todo` | |
| P4.4 | P0.5's ratchet is at zero: the shell recognises no widget by class or object name | `tests/test_phone_mode_stays_in_views.py` | `todo` | |

### Phase P5 — narrow the sweep (one PR, after Q1)

| Id | Item | Place | State | Evidence |
|---|---|---|---|---|
| P5.1 | **The sweep looks at top-level windows only** — dialogs and pop-outs (`phone_window.kind`) and the scroll areas inside them. Owned widgets are all registered by now, so `allWidgets()` is no longer walked on each tick. Re-measure P0.3's sweep cost | `ui/phone_shell.py:_sweep` | `blocked` | blocked on Q1 |
| P5.2 | **Drag-to-scroll** is applied when a window gets its phone form and when a registered page builds, rather than to every scroll area the sweep meets. A combo popup's list is the case to test | `ui/phone_shell.py`, `ui/phone_window.py` | `blocked` | blocked on Q1 |

### Phase P6 — the remaining inline reads (one PR)

| Id | Item | Place | State | Evidence |
|---|---|---|---|---|
| P6.1 | `allwell.py` (5 reads): close confirmation, startup preference, analyze form handling, sidebar state. Each either moves to the shell (chrome) or stays allowlisted with a reason — the window is entitled to know its own layout | `allwell.py:844`, `:860`, `:1537`, `:2193`, `:2255` | `todo` | |
| P6.2 | `review/core/plot_style.py` (3 reads): the plot export style reads the mode. Plot sizing belongs to the canvas registration (P3.1); move what can move, allowlist the rest with reasons | `review/core/plot_style.py:1769`, `:1786`, `:1851` | `todo` | |
| P6.3 | `split_view.py:471`, `app_shell_view.py:233`, `image_table/controller.py:174`: each moves into a `_phone.py` sibling or a registration, or stays allowlisted with its reason | those sites | `todo` | |
| P6.4 | P0.4's ratchet is lowered to the final allowlist, every entry with a reason at the site and in the roster | `tests/test_phone_mode_stays_in_views.py` | `todo` | |

### Phase P7 — close (the last PR)

| Id | Item | Place | State | Evidence |
|---|---|---|---|---|
| P7.1 | **Reconcile.** Re-read every row and every claim in §1 against the tree, not against this file (CLAUDE.md §6.1 #6). Re-run P0.1's snapshot and P0.3's costs and write the final numbers into the PR body | — | `todo` | |
| P7.2 | **ARCHITECTURE.md *Phone layout* rewritten** to describe the registry, the layer table, the narrowed sweep and the remaining chrome — as what is true, not as a history. `scripts/rename_sweep_check.sh` for every helper that moved; grep the section for "sweep", "class name" and "every widget" for sentences the change made false (§7.5 #6) | `_Docs/Markdowns/ARCHITECTURE.md` | `todo` | |
| P7.3 | **CLAUDE.md §3** gains one line under *Where new code goes*: a widget's phone form is registered where it is built (`core/phone_form`), and a separate phone view needs a written reason | `CLAUDE.md` | `todo` | |
| P7.4 | **Delete this file**, and say so in the PR body | this file | `todo` | |

### Questions

| Id | Question | State | Answer |
|---|---|---|---|
| Q1 | Once every owned widget is registered (end of P4), what does the 300 ms sweep still find that nothing registered? Instrument it for a session of real use on the loaded-app fixture — every page, every dialog, every combo — and list the classes. If the list is only top-level windows and their contents, P5 goes ahead; if it holds owned widgets, each becomes a P2/P3 row first | `todo` | |
| Q2 | Does the per-widget `_PhoneEventFilter` survive the migration at all? A registered widget can be applied directly; the filter remains only for windows' Show events and ⓘ taps. Decide after P3 from what is left in `_PhoneEventFilter.eventFilter` | `todo` | |

---

## 4. Risks the rows must watch

- **A widget the sweep reached and the registry does not.** It is the one
  failure this migration can introduce, and it is silent: the widget keeps its
  desk form on the phone and nothing raises. P0.1's snapshot is the defence; a
  row that leaves the snapshot changed is not `done`.
- **Lazily built pages.** Each P2/P3 test builds the page *after* switching to
  phone layout, because that is the order the sweep handled and the
  registration must now handle.
- **Deleted wrappers.** A registry holding a widget Shiboken has released raises
  `RuntimeError` on any call; the registry treats it as a drop, never with a
  silent `except` (`core.safe.swallowed`, CLAUDE.md §5).
- **Full-app tests.** P0.1's harness and the real-builder tests build apps.
  They run a file at a time with `scripts/test.sh <file> -m ""`, never in a
  sweep across the phone files (CLAUDE.md §2).
- **UI-lane files are not run by CI.** Most phone tests are in
  `UI_LANE_FILES`. Run `scripts/test.sh --ui` locally before each phase's push;
  it is the only gate those tests have.
