# One inspector — plan and tracker for the pop-up, menu and side-panel reorganisation

This is a tracker in the sense of CLAUDE.md §6.1, for one body of work: replacing
All-Well's many kinds of pop-up, drop-down panel, side dock and nested dialog with
**one inspector**, in one place, in both the phone layout and the desk layout. It
is written to be handed to another assistant session that has not seen the
conversation that produced it, so it states the decisions, the reasons for them,
the code each part touches, and how each part is proved.

**All of this work is one pull request**, the one this file arrived in: the
user asked for the menu and organisation changes as a single pull request, with
the design language as a second one (`DESIGN_LANGUAGE_TRACKER.md`, on its own
branch). So this tracker lives on that pull request's branch and is worked
there; the commit that closes the last row deletes it, before the pull request
merges, and the body says so. Counts are derived from the rows (§12 gives the
command); no total is typed anywhere in this file.

Read before starting: `CLAUDE.md` (all of it — §2 on test lanes, §3 on where a
module goes, §6 on PR cadence, §7.5–§7.8 on proving guards and tests, §7.10 on
phone surfaces that appear only after a press, §7.12 on menu arrows), and in
`_Docs/Markdowns/ARCHITECTURE.md` the *Phone layout* section (the Controls sheet,
the Wells sheet, the Properties sheet, *Windows of their own*), §5.4 (the left
sidebar), §5.8 (the Export Style sidebar) and the *Adding a Plotting leaf* table.
Open `_Docs/design/surfaces_mockup.html` in a browser: it is the clickable
mock-up of both layouts the user approved, and it is the reference for what each
row should look like when done. Line numbers below were read on the tree this
plan was written against; the tree moves, so re-find each by its symbol before
editing.

---

## 1. Why

The user's report, verbatim: *"The array of pop ups and side menus are
overwhelming, hard to navigated, illogically placed, and hard to find."*

A sweep of the non-test source found 28 popovers (20 built through
`review/core/ui_helpers.make_popover_button`, 8 hand-built on
`widgets/popover.Popover`), 46 `QMenu` constructions in 29 files, 23 dialogs and
separate windows, 172 `QMessageBox` call sites, and 11 surfaces that exist only
in phone layout. The Nanowells page alone has about 22 openable surfaces reached
from about 11 different places. §11 is that inventory, site by site.

Four patterns produce the complaint, and every row of this plan removes an
instance of one of them:

1. **The same job is a different kind of surface on each page.** A multi-select
   of timepoints, fields of view or gates is built eight separate ways, some of
   which apply as a box is ticked and some only when the menu closes. Live view
   options are a `Display ▾` popover on seven pages and a `Layers` menu with a
   submenu on Nanowells. Saving a figure lives in five places on one plot.
2. **Pop-ups open pop-ups.** The deepest chain is four levels: the
   `Fold change ▾` popover, its Control combo, the Per-sample control dialog that
   one combo entry opens, and the `Set all to ▾` menu inside that dialog. In
   phone layout every such chain starts inside the Controls sheet, so it is one
   level deeper again.
3. **Some things can only be found by accident.** The Image Table's per-cell
   override is right-click only; a Heat Map tick label is edited by double-click
   only; the copy and save buttons on a plot canvas appear on hover only; the
   Per-sample control dialog is an entry inside a combo inside a popover; and in
   phone layout Reset analysis, Reset view, Copy analysis, Storage, Reveal folder
   and the Presentation look cannot be reached at all.
4. **No kind of control has a fixed place.** Timepoints is in the band's left
   zone on three pages, inside a group on Nanowells, in the rail on Cell Browser
   and in a toolbar in the crops pop-out. Phone layout opens surfaces from all
   four screen edges. There are three conventions for the arrow that says "this
   opens a menu", and four spellings of the control that closes a panel.

## 2. Decisions already made with the user

These are settled. Do not reopen them; if implementation shows one cannot be met,
stop and report rather than substituting something else.

1. **Five kinds of surface, each with one job.** Nothing else is allowed to open.

   | Surface | Used for | Opens from |
   |---|---|---|
   | **Inspector** | Anything that changes what the page shows. Tabs **Data** (what is plotted), **Display** (how it is drawn), **Style** (the export look), and a contextual **Detail** tab while something is picked. A sub-option is a *page inside* the inspector with a back arrow. | Phone: the bottom bar's **Controls**. Desk: the band's fixed **Display** and **Style** buttons, or any band button that shows a value and ends in ›. |
   | **Wells** | Picking wells and saved selections. | Phone: the bottom bar's **Wells** sheet. Desk: the rail's plate, as today. |
   | **Menu** | Commands only: click and it closes. One level. No tick-lists, no sliders, no fields. | The button it belongs to. |
   | **Window** | Long or multi-step work away from the page (Manage Remote Servers, Storage, Test detection). Full screen in phone layout, as today. | A button on a page, or a menu command. Never from a pop-up. |
   | **Alert** | Confirming something destructive, or reporting an error. | The centre of the screen. |

2. **Depth is never more than one.** While an inspector page, a menu or a window
   is open, nothing it contains may open a second transient surface, except an
   alert or the operating system's file dialog. The one sanctioned exception is
   a window opened from a window, limited to two deep (Manage Remote Servers and
   the form it edits a host with).
3. **The same component at the desk and on a phone; only the host differs.**

   | | Phone layout | Desk layout |
   |---|---|---|
   | Inspector host | A bottom sheet upright (half height, or full height for an editor), a right-hand panel sideways, a docked column on an unfolded foldable — the three forms `PhoneShell._build_sheet` builds today | A right-hand column beside the page, which stays open while the user works |
   | Data | The first tab of the sheet: the page's `ControlBand`, laid out as a form (`ControlBand.setForm`, as today) | The band row above the plot, as today |
   | Display, Style | The second and third tabs | Inspector tabs, opened by two fixed buttons at the band's right end |
   | A sub-option (Fold change, Timepoints, Subset, …) | A page inside the sheet | A page inside the inspector; its band button reads its value, for example `Fold change: vs DMSO ›` |
   | Detail (a nanowell site, a scatter cell) | The inspector at half height over the plot | The Detail tab |
   | Share | A fourth, permanent button of the foot strip | A third fixed band button |
   | Dataset tools | `⋯` › *Dataset tools* | The dataset menu at the head of the rail |

4. **Fixed places.** The phone's foot strip has four permanent slots — page
   picker, **Wells**, **Controls**, **Share** — and a slot a page cannot use is
   disabled, never removed. The desk band ends in three fixed buttons —
   **Display**, **Style**, **Share** — on every page that has a band. In phone
   layout everything rises from the bottom edge; `☰` is the only side panel and
   it is navigation only.
5. **Every row shows its current value.** A row that opens a page reads, for
   example, `Timepoints  3 of 8  ›`, so the Data tab doubles as a summary of the
   page's state.
6. **One component per job**: one multi-select page (All / None, applies as a box
   is ticked, summarised as "3 of 8"), one range page (minimum, maximum, Auto),
   one colour page (swatches, then a custom colour on the same page).
7. **Nothing by gesture alone.** Right-click, double-click and hover stay at the
   desk as *shortcuts* to something that is also reachable from a visible
   control. In phone layout the visible control is the only route.
8. **One convention each** for the menu arrow (§7.12 of CLAUDE.md already fixes
   this), for closing an inspector (a button reading **Close**, top right), for
   going back (‹, top left, hidden on a tab's first page) and for a collapsible
   section (`widgets.CollapsibleSection`).
9. **The destructive resets return to the phone**, under `⋯` › *Dataset tools*,
   each behind a confirming alert. They were hidden in phone layout as
   fat-finger targets (`allwell.py`, the `desk_only` list); the user approved
   the mock-up that restores them. State this in the pull request that does it
   (row S23) so the user can object before it merges.
10. **Wording.** The user-facing word for the panel is **Inspector** at the desk
    and **Controls** on the phone's button; the tabs are **Data**, **Display**,
    **Style**, **Detail**; the export button is **Share** on both. `Save ▾`,
    `Export ▾`, `Properties`, `Decorate ▾`, `LUT ▾`, `Colors ▾`, `Layers`,
    `View ▾` stop being button labels. *Decorate* becomes the row
    **Colour & split**; *LUT* becomes **Brightness**; *Colors* becomes
    **Colours**.

## 3. What the research says, and where each rule comes from

The rules in §2 are not invented. They are the platform guidance for exactly this
problem, and a reviewer may ask for the source.

| Rule | Source |
|---|---|
| No popovers on a phone-width screen; use a sheet. "Never show a cascade or hierarchy of popovers." | Apple Human Interface Guidelines, *Popovers* |
| One sheet at a time. "Do Not Stack Bottom Sheets." "Display only one sheet at a time from the main interface." | Nielsen Norman Group, *Bottom Sheets: Definition and UX Guidelines*; Apple HIG, *Sheets* |
| A non-modal sheet when the content behind it is what is being adjusted | Apple HIG, *Sheets* (the Notes text-format sheet); NN/g |
| Half and full resting heights with a grabber; always a visible Close; Back dismisses | Apple HIG, *Sheets*; NN/g |
| Sheets are for short interactions; long or multi-step work gets a full-screen view | NN/g; Apple HIG |
| Menus are for commands: short, most-used first, at most one level of submenu | Apple HIG, *Menus* |
| Group tools, keep the frequent ones visible, put the rare ones behind `⋯`, keep the picture in view | Adobe, the Lightroom phone redesign |
| Hidden navigation is used less; with four or fewer top-level destinations, show them | NN/g, *Hamburger Menus and Hidden Navigation* |
| On a desktop, repeated input whose result is watched belongs in a panel beside the content, not a modal sheet | Apple HIG, *Sheets* |
| Warnings are alerts, never popovers or sheets | Apple HIG, *Popovers* |

## 4. Design

### 4.1 The inspector widget (row S01)

A new Qt-only widget, `widgets/inspector.py`, class `Inspector`. It belongs in
`widgets/` because it knows nothing about All-Well (CLAUDE.md §3, rule 4): it
holds tabs, a stack of pages per tab, a header, and the lending of widgets.

- **Header.** Left: a back button `‹`, hidden while the current tab shows its
  first page. Centre: a one-line breadcrumb (small, muted) above the page title.
  Right: a button reading **Close**.
- **Tabs.** A `widgets.segmented_control` row under the header, shown only on a
  tab's first page. `setTabs([(key, title)])`, `setTabWidget(key, widget)`,
  `showTab(key)`, `setTabEnabled(key, bool)`. A tab with no widget is disabled,
  not hidden, so the inspector has the same shape on every page.
- **Pages.** `pushPage(key, title, widget, *, large=False)` lends `widget` to the
  inspector and shows it; `popPage()` gives it back; `popToRoot()`; `depth()`;
  `currentKey()`. `large=True` asks the host for its full height.
- **Lending.** A page's widget is *lent*, exactly as the Controls sheet lends a
  band today: its parent layout and index are read before it moves, and it goes
  back to that index when its page is popped, when the inspector closes, and
  when the inspector is destroyed. Copy the discipline of
  `PhoneShell._give_band_back` and `review/core/phone_panel_sheet._give_back`,
  including restoring width pins. Never wrap a `QLayoutItem` to do it
  (CLAUDE.md §7.10 #7): find a widget's place with `indexOf`.
- **Signals.** `closeRequested`, `pageChanged(str)`, `sizeWanted(str)` with
  `"half"` or `"full"`. The inspector never closes itself; its host does.
- **Phone form.** It declares itself to `widgets.form_registry` in its
  constructor so `PhoneShell` can find it without recognising a class name
  (`tests/test_phone_mode_stays_in_views.py` holds that at zero).

### 4.2 The hosts (rows S02, S03)

There is **one inspector per Review window**, not one per tab. That is what
makes "one surface, one place" true, and it matches the phone, which already has
one Controls sheet per window.

- **Desk host.** A right-hand column in Review's centre splitter, `win._h_pane`
  (`review/core/app_shell_view.py`, where the rail is pane 0 and
  `win._central_pane_stack` pane 1). `ui/phone_dock.DockedSheet` already
  inserts a column into this splitter and takes it out again on a foldable;
  generalise that class to take a side, move it to `widgets/` as a docked
  panel, and use it for both. The column's width is remembered per window. The
  inspector is registered in Review's widget registry under one key, the way
  `ui.phone_drawer.DRAWER_KEY` is, so features reach it through `ctx.widgets`
  and never through a new `app._*` attribute.
- **Phone host.** `PhoneShell._build_sheet` builds a scroll area and a holder
  and lends the band into it. The holder becomes the inspector; the band becomes
  the Data tab's widget. The three forms (bottom `Drawer`, right `Drawer`,
  `DockedSheet`) and the rule that the Wells sheet and the Controls sheet are
  never open together (`close_sheets`) stay. `sizeWanted("full")` raises the
  bottom drawer to its full height; today it stops at `SHEET_HEIGHT_SHARE`.
- **A service module**, `review/core/inspector_service.py`, is the only thing
  features call: `open(ctx, tab=None)`, `push(ctx, key, title, widget, *,
  tab, large=False, summary=None)`, `close(ctx)`, `show_detail(ctx, widget,
  title)`. It finds the window's inspector, picks the host by
  `core.phone_layout.is_on()`, and follows page changes the way
  `PhoneShell.refresh` does, so leaving a page gives that page's widgets back.
- **Split View.** With two panes showing, the inspector serves the pane that
  was last interacted with, and its header names that page. Find how
  `review/core/split_view.py` tracks the pane a control belongs to before
  writing this; if there is no such notion, add the smallest one that works and
  record it in row S02's evidence.

### 4.3 The popover seam (rows S05–S07)

`make_popover_button(text, parent)` returns `(button, content, content_layout)`
and has 19 call sites, which fill `content_layout` with live controls. Keep that
signature so no call site changes in the first step. Change what the button
*does*: it no longer owns a `QMenu` holding a `QWidgetAction`; a click calls
`inspector_service.push` with `content` as the page. Add three keyword
arguments: `tab` (`"data"` or `"display"`, default `"display"`), `root`
(`True` for the one panel per page that *is* the Display tab's first page —
`Display ▾`, `View ▾`), and `summary` (a callable returning the row's current
value).

What a call site becomes:

- `root=True` panels disappear from the band; their content is the Display tab.
- A `tab="data"` panel keeps a button in the desk band, which reads
  `<Label>: <summary> ›` and opens its page. In the phone's Data form it is a
  row, not a button in a grid.
- A `tab="display"` panel that is not the root becomes a row on the Display tab
  that opens its page.

§11.1 says which of the 20 panels is which.

Three things will break, and each is a finding, not a nuisance:

- **Code that refreshes a panel when its menu opens.**
  `review/features/barplot/tab_view.py` connects
  `_fc_btn.menu().aboutToShow` to a refresh, and there are 21 `aboutToShow`
  connections in the application. Each one on a converted panel must be re-wired
  to the inspector's `pageChanged`, or the page shows stale values.
- **Tests that reach a panel through `button.menu()`.** 51 lines under `tests/`
  call `.menu()`. A test that asserted a popover exists was asserting the thing
  this work removes (CLAUDE.md §6, *A failing test may be asserting the bug*):
  rewrite it around the report — the control is reachable in one step and
  changes what it should — and say so in the commit.
- **`widgets/control_band_form.BandForm`** sorts band actions into Display and
  Actions grids by the `bandRole` property the two factories set. Its Display
  grid goes away (those are rows and tabs now), and its Actions grid shrinks to
  nothing once Share and Style have fixed places (rows S06, S09).

### 4.4 Style, Share and Detail (rows S08–S10)

- **Style** is the Properties sidebar, `ExportStyleSidebar`
  (`review/features/export/properties_view.py`, built in
  `review/features/export/style.py`), lent to the inspector's Style tab instead
  of mounted in a plot's own right dock. Its sixteen `CollapsibleSection`s
  become sixteen rows that each open a page, so the tab's first page fits one
  screen. `make_properties_button`, `make_plot_with_right_dock`,
  `mount_widget_in_plot_dock` and `review/core/phone_panel_sheet.py` are
  retired by this row; `phone_panel_sheet.present` has no caller afterwards.
  The plot must restyle live while the tab is open, as it does today.
- **Share** is `build_plot_save_menu`'s menu, already shared by ten plot pages,
  with its button renamed and given a fixed place. It stays a menu: its entries
  are commands.
- **Detail** is whatever a page shows for one picked thing: the Scatter cell
  viewer (`review/features/scatter/cell_viewer_view.py`) and the Nanowells
  site-detail column (`review/features/nanowell/tab_view.py`). Picking another
  thing while it is open updates it in place.

### 4.5 One component per job (rows S11–S15)

- `widgets/multi_select.py`: a list of check rows under an All / None pair, one
  signal `changed(list)` emitted on every tick, and `summary()` returning
  `"3 of 8"`, `"All"` or `"None"`. It replaces eight builds (§11.3). Two of
  them (`home/cell_gating/fov_view.py`, `home/cell_gating/tab_view.py`) apply
  only when their menu closes, because the recompute behind them is expensive;
  decision 6 says "applies as a box is ticked", so those two keep their cost
  under control with a debounce on `changed` rather than by deferring to a
  close that no longer exists. Measure the recompute before choosing the delay
  and write the figure in the row's evidence.
- `widgets/range_page.py`: minimum, maximum, Auto, Reset. Replaces the two
  `LUT ▾` panels and the Nanowells *Display range* popover, whose button
  appears in four places today and becomes one row.
- `widgets/colour_page.py`: the existing `widgets/color_swatch_row.py` and the
  body of `widgets/color_picker_popover.py` on one page. Replaces three chains
  that are three pop-ups deep.
- The colour-map picker, `widgets/lut_selector.py`, opens a searchable
  `Popover`; its list becomes a page.

### 4.6 The shell (rows S23–S27)

- The header's right-hand cluster holds eleven icons (`allwell.py`, mounted
  around the `_build_header` code). Reset analysis, Reset view, Copy analysis,
  Reveal folder and Storage move into the dataset menu at the head of the rail
  (`review/core/app_shell_view.py`, the menu that lists Open… and the recent
  datasets) under a *Dataset tools* caption. The header keeps Split View,
  Refresh, the remote-node button, the phone-layout button and Help.
- In phone layout the same five commands are a full-screen list reached from
  `⋯` › *Dataset tools*; the two resets ask first.
- Gesture-only actions get a visible equivalent (§11.6).

## 5. Tests for the new organisation

These are the tests that hold the new style in place. They are rows of the plan
(S04 creates the scaffolding; each later row tightens it), and each is proved
the way CLAUDE.md §7.5 and §7.7 require: a guard with
`scripts/prove_guard.sh` where the code it reads exists on `origin/main`, and by
mutating HEAD where the code is new. Every guard also gets a case in
`tests/test_guards_bite.py`.

### 5.1 The depth rule, measured on real widgets

`tests/_surface_checks.py` (a helper, like `tests/_layout_checks.py`):

- `open_transients(app)` returns every visible top-level that is a transient
  surface: a `QMenu`, a widget with the `Qt.Popup` flag (which covers
  `widgets.popover.Popover` and a combo box's list), a visible `QDialog` or
  `Qt.Window` other than the main window, an open `Drawer`, and the inspector's
  host when it is an overlay.
- `depth(app)` is how many of those are open at once, not counting a
  `QMessageBox` or a file dialog.

`tests/test_surface_depth.py` drives it. For every Review page's **real
builder** (`build_<feature>_tab`, through `tests/_minimal_shell.py` — not a
`SimpleNamespace` stand-in, CLAUDE.md §7.8), at the desk and in phone layout:
press every enabled button and tool button once, with the blocking calls stubbed
as CLAUDE.md §7.10 #6 describes, and after each press assert `depth(app) <= 1`.
While a row's conversion is pending, the pages it has not reached are on a
roster with the row's ID as the written reason; the roster only shrinks, and row
S28 requires it empty.

This file asserts what a press *opens*, which is behaviour: it must **not**
join `tests/_lanes.py::UI_LANE_FILES`, or CI stops running it.

The crawl over the fully built application belongs to the slow lane: add a stop
to `tests/_page_tour.py` for each inspector tab and each page inside it
(`"<page> ▸ Controls ▸ Display ▸ Fold change"`), so
`tests/test_every_page_renders.py` measures every inspector page on every screen
it already covers, and extend it with the same depth assertion.

### 5.2 The rules that are decidable from the syntax tree

One file per rule, each reading the source through `tests/_repo_scan`
(never its own `ast.parse`), each with a per-site roster that names the row
that will remove the site. A roster entry that no longer matches a real site
fails the test (CLAUDE.md §7.5 #10).

| Test file | Rule | Ends at |
|---|---|---|
| `tests/test_no_widget_lives_in_a_menu.py` | No `QWidgetAction` outside a roster. A menu that hosts widgets is a popover by another name. | The phone menu's caption labels (`PhoneMenuCaption`) only, with the reason written at the site |
| `tests/test_popovers_are_gone.py` | No construction of `widgets.popover.Popover` outside a roster | Zero |
| `tests/test_a_menu_holds_commands_only.py` | No checkable `QAction` added to a `QMenu`, and no `addMenu` on a menu that is itself a submenu | Zero checkable actions; submenus one level deep, each on a roster |
| `tests/test_one_way_to_pick_several.py` | No module outside `widgets/multi_select.py` builds a check-list picker; `widgets/checkable_dropdown.py` has no importer | The one module |
| `tests/test_nothing_is_reachable_by_gesture_alone.py` | Every `customContextMenuRequested`, `mouseDoubleClickEvent` and hover-revealed control is on a roster naming the visible control that does the same thing | Every entry names its equivalent |
| `tests/test_a_page_has_the_three_fixed_actions.py` | For every leaf the feature registry lists (`features.sub_leaves("Plotting")`, and the registry's other pages that own a band), the page offers Display, Style and Share through the one factory. Stated against the registry, so a new tab is covered by being declared (CLAUDE.md §3, *A new tab is not one edit*). | Every registered page, or a row with a written reason |

### 5.3 The component tests

- `tests/test_inspector.py`: a pushed widget is inside the inspector and
  visible; popping gives it back to its parent **at its index** with its width
  limits; Close gives back every page of every tab; Back on a first page is
  hidden; a disabled tab cannot be shown; `large=True` emits
  `sizeWanted("full")`; destroying the inspector with pages pushed gives them
  back. Build the fixture so right and wrong differ (CLAUDE.md §7.8 #2): put
  the lent widget *between* two siblings, so "appended at the end" and
  "returned to its index" are different outcomes.
- `tests/test_inspector_hosts.py`: at the desk the inspector is a pane of
  `_h_pane` and opening it never changes the rail's index; in phone layout the
  same calls open the bottom sheet; switching layout while a page is pushed
  gives the page back first.
- `tests/test_multi_select.py`, `tests/test_range_page.py`,
  `tests/test_colour_page.py`: each asserts the value that reaches the caller,
  not that a widget exists.

### 5.4 One test per converted control goes through the real control

For every page a row converts, a test builds the page through its real builder,
**clicks the real band button**, and asserts three things at the boundary
(CLAUDE.md §7.4 #1, §7.6 #1): the inspector is on that page; a control inside
it, when changed, changes the state or the spec the plot is drawn from; and
`depth(app) == 1`. Asserting only that the button is offered is the mistake
§7.6 documents. Name these `tests/test_<page>_controls_open_in_the_inspector.py`.

### 5.5 Phone fit and appearance

- Every inspector page, through its page's real builder, at phone size, is no
  wider than the sheet and never scrolls sideways (CLAUDE.md §7.10 #4). These
  are layout tests and join `UI_LANE_FILES` with a reason.
- `tests/test_a_menu_button_draws_one_arrow.py` gains a case for the band's
  value buttons (they end in `›` and have no menu, so Qt must draw no indicator)
  and for the Share button.
- `tests/test_phone_layout_snapshot.py` compares against
  `tests/fixtures/phone_snapshot_baseline.json`. Every row that moves a phone
  widget changes that baseline; regenerate it in the same commit and say in the
  commit message which widgets moved and why.

## 6. How to work this plan

- **One pull request for the whole plan**, already open: commit on its branch
  and stack every phase there. Because the pull request is open, every push
  starts a billed CI run (CLAUDE.md §2), so push by round: finish a phase,
  verify it locally, push it once. Keep the pull request's body describing the
  branch as it stands, and label it `minor`, since it changes what users see.
  Merge `origin/main` into the branch at the start of each phase and before
  each push; never rebase or force-push it.
- **Update the row in the same commit as the fix**, and move it to `done` only
  with the evidence written in its Evidence cell: the test that pins it and
  what mutation made that test fail.
- **Reproduce each inventory entry before converting it.** §11 was read on one
  tree. A site may have moved, been converted by an earlier row, or been
  described wrongly. When a row is only part right, write what was measured.
- **New code goes in new modules.** `ui/phone_shell.py` is over 2,600 lines,
  `review/core/ui_helpers.py` about 1,600 and
  `review/features/export/properties_view.py` about 2,800. Read
  `tests/test_monolith_budgets.py`, `tests/test_complexity_ratchet.py` and
  `tests/test_guardrails.py` before adding to any of them; this work should
  leave all three smaller.
- **Which lanes a change must pass locally:** the guard family and
  `ruff check .` always; the full fast lane (`scripts/test.sh`, which includes
  the UI tests CI never runs) for every phase, because each touches shared
  chrome; `scripts/test.sh --nanowell-features` for phase 5; and
  `scripts/test.sh tests/test_every_page_renders.py -m ""` (about nineteen
  minutes) before each phase is pushed. Run
  `scripts/test.sh tests/test_app_smoke.py -m ""` too:
  `test_no_tab_builder_raised_while_draining` is what notices a tab whose
  builder now raises.
- **Docs in the same commit series** (CLAUDE.md §6): ARCHITECTURE.md's *Phone
  layout* section describes the Controls sheet, the Properties sheet and
  `BandForm` in detail, and §5.4 and §5.8 describe the desk panels. Rewrite the
  prose to say what is true now; do not append notes. The manual,
  `README_WellViewer.md`, names `Save ▾`, `Properties`, `Decorate ▾` and the
  others throughout.

## 7. Things that are not in this plan

- The `QMessageBox` and file-dialog call sites. Alerts are one of the five
  surfaces and stay as they are.
- The Analyze form's own collapsible sections and the Datasets tool pages,
  except where a row names them. Analyze is a form, not a page with a plot, and
  has no inspector.
- Manage Remote Servers and the dialogs under it, beyond row S27's check that
  the chain is no more than two windows deep.
- Any change to what a control computes. A row that finds a control computing
  the wrong thing reports it and leaves it.

## 8. Risks and unknowns

- **A lent widget and a restyle.** Phone layout's rescale pass walks every
  widget and must only ever see desk-shaped ones (ARCHITECTURE.md, *Switching*).
  Pages pushed into the inspector must be given back before the pass, as the
  band is today (`_restyle_in_place`).
- **A widget lent while its owner rebuilds.** Several pages rebuild their band
  contents on a data change. A page whose content is deleted while lent must
  not leave the inspector holding a dead wrapper: the inspector pops a page
  whose widget is destroyed.
- **Split View** (§4.2) is the least understood part of the desk host.
- **The crops pop-out** (`review/features/nanowell/tab_view.py`,
  `_PopoutWindow`) is a second window with its own toolbar of menus. It gets
  the shared components (row S21) but not an inspector of its own unless the
  toolbar cannot be made to fit otherwise; decide that when the row is reached.
- **Screen width at the desk.** With the rail, the page and the inspector side
  by side, a narrow laptop leaves the plot little room. Measure the plot's
  width with the inspector open on a 1,280-pixel-wide screen; if it falls under
  what `review/core/margin_fit.py` needs, the inspector overlays the page's
  right edge below that width instead of taking a column.
- **Muscle memory.** Every band changes. The manual and the in-app help
  (`ui/help_topics.py`) are the mitigation, and the reason the pull request
  is labelled `minor`.

## 9. Glossary of what replaces what

| Today | After |
|---|---|
| `Display ▾`, `View ▾` (popover) | The Display tab's first page |
| `Fold change ▾`, `Decorate ▾`, `Fit ▾`, `Features ▾` (popover) | A Data row / band value button, opening a page |
| `LUT ▾`, `Colors ▾`, Nanowells `Layers`, *Display range* | Rows on the Display tab: Brightness, Colours, Layers |
| `Delete scope ▾`, `Crop scope ▾` | Data rows opening a page of three plain choices |
| Properties (sliders icon), the plot's right dock | The Style tab |
| `Save ▾`, `Export ▾`, canvas hover buttons | Share (hover buttons remain at the desk as shortcuts) |
| Scatter cell viewer, Nanowells site detail | The Detail tab |
| Timepoints / FOVs / gates check-menus, `CheckableDropdown` | The multi-select page |
| Per-sample control dialog, Subset editor panel, Image Table export settings dialog | Full-height inspector pages |
| Header icons: Reset analysis, Reset view, Copy analysis, Reveal folder, Storage | Dataset tools |
| `Excluded (n)`, the window strip, other controls that exist only in some state | A permanent row, plus a status chip under the plot when it applies |

## 10. Order of work

Each phase is one round of commits, pushed together, and leaves the application
fully working, so the pull request could be merged after any of them if the
user chose to stop there.

This plan does not wait for the design language. The inspector is built with
the application's existing buttons, labels and spacing; the design-language
pull request restyles it along with everything else.

- **Phase 0 — foundations (S01–S04).** The inspector, its two hosts and the
  test scaffolding. Nothing a user sees changes; the inspector exists and no
  page uses it.
- **Phase 1 — the band's pop-ups (S05–S07).** The seam converts all twenty
  `make_popover_button` panels at once, on both layouts.
- **Phase 2 — Style, Share, Detail (S08–S10).**
- **Phase 3 — one component per job (S11–S15).**
- **Phase 4 — dialogs opened from pop-ups (S16–S18).**
- **Phase 5 — Nanowells (S19–S22).**
- **Phase 6 — the shell and the conventions (S23–S27).**
- **Phase 7 — close (S28–S30).**

Phases 3 to 6 do not depend on each other and may be reordered or run by
different sessions at once, provided each fetches `main` and reads
`git log origin/main` for the files it is about to touch before starting and
before pushing (CLAUDE.md §6, *Another session may be in your files*).

## 11. Inventory — every site this plan converts

### 11.1 The twenty `make_popover_button` panels

| # | Label today | Site | Page | Becomes |
|---|---|---|---|---|
| 1 | Decorate | `review/core/centre_view.py` ~401 | every Plotting leaf | Data row *Colour & split*; always listed, and with no variables defined it says so and points at the Sample Map instead of hiding (today it is hidden, ~420) |
| 2 | Decorate | `review/features/nanowell/variables_view.py` ~61 | Nanowells | the same row, built by the same code as #1 |
| 3–5 | Fold change | `barplot/tab_view.py` ~104, `lineplot/tab_view.py` ~87, `response/tab_view.py` ~130 | Bar Plots, Line Graphs, Response Curves | Data row *Fold change* |
| 6 | Display | `barplot/tab_view.py` ~142 | Bar Plots | Display tab root |
| 7, 8 | Display | `lineplot/tab_view.py` ~94, `response/tab_view.py` ~119 | Line Graphs, Response Curves | Display tab root |
| 9 | Fit | `response/tab_view.py` ~200 | Response Curves | Data row *Fit* |
| 10 | Display | `heatmap/tab_view.py` ~116 | Heat Map | Display tab root; its colour-map picker is row S14 |
| 11, 12 | Display | `scatter/cells_tab_view.py` ~111, `scatter/agg_tab_view.py` ~108 | Scatter | Display tab root |
| 13 | Features | `multivariate/tab_view.py` ~111 | Multivariate | Data row *Features* (a multi-select, row S12) |
| 14 | LUT | `image_browser/cell_browser/tab_view.py` ~76 | Cell Browser | Display row *Brightness* (row S13) |
| 15 | Display | `image_browser/cell_browser/tab_view.py` ~89 | Cell Browser | Display tab root |
| 16 | View | `preview/panel_view.py` ~214 | Segmentation | Display tab root |
| 17 | LUT | `preview/panel_view.py` ~299 | Segmentation | Display row *Brightness* (row S13) |
| 18 | Colors | `preview/panel_view.py` ~359 | Segmentation | Display row *Colours* (row S14) |
| 19, 20 | Delete scope, Crop scope | `preview/frame_scope_view.py` ~52 (instances in `delete_scope_view.py` ~79, `crop_scope_view.py` ~34) | Segmentation | Data rows; their three `CheckableDropdown`s become three plain choices on the page (row S12) |

All paths under `review/features/` unless written in full.

### 11.2 The hand-built `Popover` sites

| Site | What | Row |
|---|---|---|
| `image_browser/cell_browser/tab_view.py` ~308 | channel colour swatch, inside the LUT panel | S14 |
| `review_image/controller.py` ~584 | Segmentation colour swatch, inside the Colors panel | S14 |
| `widgets/color_swatch_row.py` ~207 → `widgets/color_picker_popover.py` ~214 | custom colour, under each swatch popover | S14 |
| `nanowell/display_range_view.py` ~66 (buttons at `nanowell/tab_view.py` ~477, ~1011, ~1121, ~1393) | Nanowells display range | S13 |
| `widgets/lut_selector.py` ~242 (used at `heatmap/tab_view.py` ~124, `playground/tab_view.py` ~581) | colour-map picker | S15 |
| `widgets/saved_selections_list.py` ~515, ~926 | saved-selection compose, and Recolour… from its `⋯` menu | S14, S25 |
| `widgets/plot_card.py` ~841 | the "Mean · SEM" chip popover — unreachable today, because every `PlotCard` is built with `header=False` | S26 |

### 11.3 The eight multi-select builds (row S12)

| Site | Trigger wording today | Applies |
|---|---|---|
| `barplot/controller.py` ~424 | `Timepoints: N selected` | on tick |
| `scatter/tp_selector_view.py` ~41 | Timepoints | on tick |
| `response/controller.py` ~161, ~511 | `Series: None`; Timepoints | on tick |
| `image_browser/cell_browser/criteria_view.py` ~329, ~391 | Gate; Timepoints; FOVs (in the rail) | on tick |
| `home/cell_gating/fov_view.py` ~139 | `All FOVs` | when the menu closes |
| `home/cell_gating/tab_view.py` ~579 | `All gates`, one per gate-filter row | when the menu closes |
| `nanowell/tab_view.py` ~911; `nanowell/popout_view.py` ~42 | Extremes Timepoints; the pop-out's Timepoints / Channels / Grid | on tick |
| `widgets/checkable_dropdown.py` ~150 | used by the Segmentation scope panels and `analyze/qc_section.py` ~323 | on tick |

### 11.4 Menus that hold something other than commands, or nest

| Site | What | Row |
|---|---|---|
| `nanowell/tab_view.py` ~1203, ~1227 | Layers: eight checkable entries, a fill/outline group, a *Show as* submenu | S19 |
| `nanowell/qc_view.py` ~86 | Problem filter | S19 |
| `nanowell/exclude_view.py` ~51 | `Excluded (n)`, which exists only while a well is excluded | S19 |
| `nanowell/subset_view.py` ~284 | the subset condition's tiles menu | S17 |
| `image_browser/image_table/tab_view.py` ~429, ~450, ~531–571 | Table, Crop view, and Save with a quality submenu and *Export settings…* | S18 |
| `image_browser/image_table/builder_view.py` ~1275 | the channel gear: Render, Colour cells, Boundary, Log, Colour / LUT | S18 |
| `datasets/job_queue/row.py` ~373 (submenus ~474, ~494, ~542, ~559, ~569) | the job row's `⋯`, with a submenu two levels deep | S26 |
| `review/features/batch_export/base_panel.py` ~597 (submenu in `variable_select_panel.py` ~358) | Quick Setup, which shows no arrow at all | S26 |
| `analyze/presets.py` ~40, ~73 | Presets, a `QPushButton` with Qt's native indicator and a Delete submenu | S26 |
| `review/core/fold_change_pairing_view.py` ~291, ~306 | *Set all to* and *Auto-pair*, inside the Per-sample control dialog | S16 |

### 11.5 Dialogs and panels opened from a pop-up, or hidden until something else is pressed

| Site | What | Row |
|---|---|---|
| `review/core/fold_change_pairing_view.py` ~187, opened from `review/core/fold_change_controls_view.py` ~654 | Per-sample control, reached only by a combo entry | S16 |
| `nanowell/subset_view.py` ~878–950, ~485–540 | the Subset combo, *Edit…*, and the in-page editor it reveals; in phone layout *Edit…* closes the sheet it was tapped from (~896) | S17 |
| `image_browser/image_table/export.py` ~148 | Export settings, a modal dialog at the foot of the Save menu | S18 |
| `nanowell/window_view.py` ~69–75, `nanowell/resolve_view.py`, `nanowell/details_view.py` ~59, `nanowell/anchor_queue_view.py` ~72 | the window strip, the Resolve guide, the Details footer, the Queue group — each shown only in some state | S20 |
| `nanowell/tab_view.py` ~343, ~556–580 | the site-detail column, which takes the whole page in phone layout | S10 |
| `heatmap/layout_view.py` ~378, ~446 | the Heat Map Layout panel, sharing the right dock | S08 |
| `image_browser/image_table/tab_view.py` ~139–175, ~604 | the Image Table's own Properties dock, behind the same icon as the plot pages' different panel | S08 |

### 11.6 Reachable by gesture alone (row S25)

| Site | Gesture | Visible equivalent to add |
|---|---|---|
| `image_browser/image_table/grid_ops.py` ~46, wired at `image_table/controller.py` ~987 | right-click a cell (the application's only context menu) | select a cell, then a *Cell* row on the Data tab |
| `heatmap/tab_view.py` ~483, ~533 | double-click a tick label to edit it | an *Edit labels* row on the Display tab |
| `image_browser/image_table/builder_view.py` ~1246, ~1871 | press a plain label to open the root filter | a button |
| `widgets/plot_card.py` ~315–372 | hover the canvas for copy and save | Share |

### 11.7 The phone's desk-only list (row S23)

`allwell.py` ~762–772 hides, in phone layout: the Presentation look, Reset
analysis, Copy analysis, Reveal folder, Reset view, Storage and the status bar.
Manage Remote Servers is reachable in phone layout only through Analyze's
*Manage…* (`analyze/run_footer.py` ~159), because the header's remote-node
button is hidden with the header.

## 12. Rows

States: `todo`, `doing`, `done`, `declined` (with the reason in the row and at
the site). To count them, never type a total:

```bash
awk -F'|' '/^\| S[0-9]+ /{gsub(/ /,"",$4); n[$4]++} END{for(s in n) print s, n[s]}' \
    _Docs/Markdowns/SURFACES_TRACKER.md
```

| ID | Item | State | Where | Evidence |
|---|---|---|---|---|
| S01 | `Inspector` widget: header (‹, breadcrumb, title, Close), tabs, per-tab page stack, lending with give-back at index, `sizeWanted`, form-registry declaration (§4.1) | done | `widgets/inspector.py` (new) | `tests/test_inspector.py` (14 tests). Eight mutations of `widgets/inspector.py` each made a named test fail: give-back appended instead of at the index; the index not corrected for a sibling still lent; the lend not counting an earlier loan from the same layout; width limits not restored; the give-back on `DeferredDelete` removed; the hidden flag not restored; `large` ignored; Back shown on a first page. |
| S02 | Desk host: a right-hand column of `_h_pane`, width remembered, one inspector per window registered in the widget registry, follows page changes, Split View rule (§4.2); `review/core/inspector_service.py` | done | `review/core/inspector_service.py` (new), `review/core/app_shell_view.py`, `ui/phone_dock.py` generalised into `widgets/` | `tests/test_inspector_hosts.py` (12 tests) on the minimal shell with a splitter built as `app_shell_view` builds `_h_pane`. Mutations that failed a named test: the column docked at the left; Split View's focus not moved to the pressed control's pane; the width not saved; the column not closed on a page with nothing to show; the fixed button not closing its own tab; the give-back on a layout switch removed; the phone host not asked. **Split View:** `ctx.runtime.split.focused` already names the pane the rail follows, and `tab_controller.current_centre_tab` returns its leaf, so the inspector serves that pane; the one thing missing was that a button press does not move the focus (`_SplitFocusWatcher` ignores buttons on purpose), so `inspector_service.serve_widget` focuses the pane a pressed control sits in. `ui/phone_dock.DockedSheet` became `widgets/docked_panel.DockedPanel` (a side, and an optional user-resizable width). **Not yet measured:** the plot's width with the column open on a 1,280 px screen (§8) — done with the first page that uses the column, row S07. |
| S03 | Phone host: the Controls sheet's content is the inspector; the band is its Data tab; `sizeWanted("full")` raises the sheet; all three sheet forms kept | done | `ui/phone_shell.py` (`_build_sheet`, `_ensure_sheet`, `_give_band_back`, `_sync_dock`), extracted into a new `ui/` module rather than grown in place | Five tests at the end of `tests/test_phone_layout.py` (the band is the inspector's Data tab and goes back between its siblings; a pushed page is a page of the sheet and goes home when it closes; a `large` page raises the bottom sheet to the room above the strip; the window's inspector outlives a rebuilt sheet; `unmount`). Mutations that failed a named test: Close not wired to the sheet; `large` ignored by `ui/phone_inspector.sheet_height`. Two mutations survived and are recorded rather than hidden: removing `unmount` from `_ensure_sheet` (the new sheet's `mount` reparents the inspector before the old sheet's deferred delete runs, so `unmount` there is a second line of defence, covered directly by its own test), and removing the `sizeWanted` connection (`contentResized` re-fits the sheet as well). The existing 171 phone-layout tests pass unchanged; `tests/_page_tour.py` stops and the snapshot baseline move with rows S05–S06, when a page first puts something in the other tabs. |
| S04 | Test scaffolding: `tests/_surface_checks.py`, `tests/test_surface_depth.py` with its pending-page roster, and the six syntax-tree guards of §5.2 with rosters holding today's sites | done | `tests/` | `tests/_surface_checks.py` and `tests/test_surface_depth.py`: the measure is tested on real widgets (a dialog, a menu opened from it, an alert, a popover, a sheet, the docked column), and the crawl is shown reporting a menu opened from a dialog and a submenu. Nine registry pages are built through `desc.build(ctx, frame)` and crawled at the desk; the Plotting leaves, Image Browser and Batch Export cannot be crawled in the minimal shell (reasons measured, in `CRAWLED_ON_THE_BUILT_APP`) and go to the slow-lane crawl of row S28, as does phone layout. Five syntax-tree guards with rosters of today's sites, scans in `tests/_surface_guards.py`: `test_no_widget_lives_in_a_menu` (13 sites), `test_popovers_are_gone` (7), `test_a_menu_holds_commands_only` (3 checkable, 11 submenu functions), `test_one_way_to_pick_several` (4 importers, 15 builds), `test_nothing_is_reachable_by_gesture_alone` (13 — nine more than §11.6 lists: the Nanowells double clicks, three `itemDoubleClicked` lists, `StagePathView`, `_RemovableRow`'s hover, a second Heat Map handler). `scripts/prove_guard.sh` cannot prove these against `origin/main`: each roster holds exactly today's sites, so each passes on the base by construction. The proof that they bite is `tests/test_guards_bite.py` — 19 violation shapes (aliased imports, the class reached through its module, the chained and keyword forms of a checkable action, matplotlib's double click read with `getattr`) and 7 clean files. The sixth guard, `test_a_page_has_the_three_fixed_actions`, is created with the factory it checks, in row S07. |
| S05 | The popover seam: `make_popover_button` pushes an inspector page instead of owning a menu; `tab`, `root`, `summary` arguments; all twenty panels classified per §11.1; every `aboutToShow` refresh on a converted panel re-wired to `pageChanged` | done | `review/core/ui_helpers.py` (the factory moves to a new `review/core/` module), the 19 call sites in §11.1 | `review/core/inspector_panels.make_popover_button` (the factory left `ui_helpers`): a click shows the panel in the inspector; `tab`, `root`, `summary`, `large`; `on_open` / `on_close` replace `aboutToShow` / `aboutToHide`. All 19 call sites converted and classified per §11.1 — 9 Display roots (`View ▾` among them), Fold change ×3, Fit, Features, Colour & split ×2 and the two scope panels as Data rows, Brightness ×2 and Colours as rows of the Display tab. **Measured against §11.1:** there are 19 call sites, not 20 (`frame_scope_view` is one site building two panels); §4.3's "21 aboutToShow connections" has 2 on a converted panel (the Bar tab's relative-to row, and the scope panels, which also used `aboutToHide`) — both re-wired; the other 19 are on real menus. *Colour & split* is listed on every tab that decorates whether or not a variable is defined, and its page then says so and names Home › Sample Map. `tests/test_band_panels_open_in_the_inspector.py` builds five pages through the registry's builder and clicks the real buttons (one parametrised file rather than one file per page); four mutations failed it: the root finder returning nothing, the second press not leaving the page, the click not connected, and a hidden leaf's button still counted as current. Two full-app tests in `tests/test_phone_layout_app.py` hold the same in real phone layout: Data is the band, Display is the tab beside it, both given back. 23 tests that reached a panel through `button.menu()` were restated (the scope panels through `tests/_panels.py`; the Bar relative-to fit test against a real inspector page). `test_no_widget_lives_in_a_menu` lost its `make_popover_button` entry. |
| S06 | `BandForm`: a `tab="data"` panel is a row with its value; the Display grid is removed; the form is the Data tab | done | `widgets/control_band_form.py`, `widgets/control_band.py` | `widgets/control_band_form.py`: a panel row (`bandPanel="row"`) is a Data row with its title in the label column and its value and chevron in the control; a tab's root (`bandPanel="root"`) is parked, in no row and no grid; the Display grid is gone and the Data heading with it. `tests/test_control_band_form.py` rewritten around rows (13 tests); four mutations failed a named test (a root left in the form, a panel row left in Actions, the value-less row painting its own title, the Data heading left on). One survived: dropping the `setHidden` after parking — `setParent` into a hidden holder does not set the hidden flag on this Qt, so the line is the module's idiom rather than a behaviour, and is left. **Not done in this row:** per-page phone-fit tests of §5.5 — the every-page tour's `▸ Controls` stop measures the Data tab on all six screens; the other tabs' pages join the tour in row S22. |
| S07 | Desk band: `tab="data"` panels are value buttons ending in ›; one factory adds the fixed Display · Style · Share buttons | done | new `review/core/` module beside the factory; every page that owns a band | `review/core/band_fixed_actions.finish_band`, called by the ten plot pages, Cell Browser and Segmentation; `ControlBand.order_actions` keeps the three last however late an action is added. `tests/test_a_page_has_the_three_fixed_actions.py`: every function that builds a band or claims a Plotting slot calls the factory (AST, with a two-entry roster of the functions that hand out bands), every registry Plotting leaf's package finishes a slot, and eight pages built through their builders end their rows in Display · Style · Share with a disabled button where the page has none. Four mutations failed it (no ordering, an enabled placeholder, no placeholder, a page not calling the factory). `tests/test_a_menu_button_draws_one_arrow.py` has the value-button case: no menu, no `▾`, no indicator. The Properties icon is a button reading **Style** and `Save ▾` reads **Share ▾**; what they open is rows S08 and S09. **Width at the desk (§8):** rail 329 + inspector 360 leaves the page 591 px of a 1,280 px window; `margin_fit` states no minimum width, the column is user-resizable down to 280 px (page 671 px), and no overlay mode was added — reported rather than decided. |
| S08 | Style tab: `ExportStyleSidebar` lent to the inspector, its sections as rows opening pages; Heat Map Layout and the Image Table's own Properties become pages; `make_properties_button`, the per-plot right dock and `phone_panel_sheet` retired | doing | `review/features/export/properties_view.py`, `review/features/export/style.py`, `review/core/ui_helpers.py`, `review/core/phone_panel_sheet.py` (deleted), `heatmap/layout_view.py`, `image_table/tab_view.py` | **Done:** the figure's style panel (`ExportStyleSidebar`) is the inspector's Style tab at the desk and in the Controls sheet (`review/core/style_tab.py`; the panel is asked of the feature registry, `export_style.sidebar`); its sections are rows — folded headers that open their rows as a page (`CollapsibleSection.setOpener`) — so the tab's first page is a list; the panel has no title, close or width of its own; `style._resolve_export_dock` and its second roster are gone. Evidence: `tests/test_band_panels_open_in_the_inspector.py` presses the real Style button and the real Grid row on Bar Plots and asserts the **figure's grid lines** change while the page is open; `tests/test_phone_layout_app.py::test_style_is_the_tab_beside_data_in_the_controls_sheet` holds the same in real phone layout at depth one; `tests/test_properties_panel_frame.py` holds the rows and the absent title and close. **Not done:** Heat Map Layout and the Image Table's own Properties are still panels in the plot's right dock; `make_plot_with_right_dock`, `mount_widget_in_plot_dock` and `review/core/phone_panel_sheet.py` still exist, because the Scatter cell viewer (row S10) and those two panels still use the dock; `tests/test_phone_panel_sheet.py` is unchanged for the same reason. |
| S09 | Share: `build_plot_save_menu`'s button renamed and placed by the S07 factory; the phone foot strip's fourth slot; Image Table, Saved Plots and the crops pop-out use the same label and entry order; the Properties footer's Export… removed | doing | `review/core/ui_helpers.py`, `ui/phone_shell.py` (`PhoneStrip`), `image_table/tab_view.py`, `saved_plots/tab_view.py`, `nanowell/popout_view.py` | **Done:** `build_plot_save_menu`'s button reads **Share** and carries the `share` role, so `finish_band` places it last on every plot page (row S07); the Image Table's and Saved Plots' buttons read Share and carry the role; the crops pop-out's reads Share. The phone foot strip has four permanent slots — page, Wells, Controls, Share (`PhoneShell.strip_buttons`) — each shown on every Review page and **disabled, not removed,** where the page cannot use it (a section with no pages keeps its page slot, naming the section); Share pops the page's own menu from the strip after closing the sheets, and the page's Share button is not shown a second time inside the Controls sheet. Evidence, in `tests/test_phone_layout.py`: the four slots in order with Share disabled on a page with nothing to share; Share pressed for real pops the page's menu at depth one and a triggered entry reaches its callback; the parked button still counts as the page's. Four mutations failed a named test (the sheets not closed first, a slot left enabled, a slot hidden instead of disabled, the click not connected). **Not done:** the Properties footer's *Export…* is still there — it saves in five formats with the export profile's format as the default, and whether Share › *Save figure…* does the same was not established, so it was left rather than removed; the entry *order* of the Image Table's, Saved Plots' and the pop-out's menus was not aligned with the plot pages' (their entries differ). |
| S10 | Detail tab: the Scatter cell viewer and the Nanowells site detail; picking another updates it in place; phone shows it at half height | todo | `scatter/cell_viewer_view.py`, `nanowell/tab_view.py`, `nanowell/_phone.py` | Click a real point / nanowell, assert the Detail tab shows *that* one (fixture with two, so "the first" and "the picked" differ) |
| S11 | `widgets/multi_select.py` | done | `widgets/multi_select.py` (new) | `widgets/multi_select.py` (`MultiSelect`: check rows under All / None, `changed(list)` on every tick and once for All and None, `summary()`); `tests/test_multi_select.py` asserts what reaches the caller — the keys, in list order, on the tick — with keys that are not their labels and not sorted, so the labels and a sorted list are both wrong answers. Three mutations failed a named test (All not emitting, All emitting once per row, All emitting when nothing changed). |
| S12 | Convert the eight multi-select builds of §11.3 and the Multivariate Features panel; delete `widgets/checkable_dropdown.py`; Cell Gating's two apply through a measured debounce | doing | the sites in §11.3 | **Done (5 of the builds in §11.3):** Bar Plots Timepoints, Scatter Timepoints (cells and aggregate, one implementation), Response Curves Series and Timepoints, through `review/core/inspector_pages.open_multi_select`: a page of the inspector that applies as a box is ticked. `tests/test_band_panels_open_in_the_inspector.py::test_bar_timepoints_is_a_multi_select_page_and_a_tick_reaches_the_bar_state` presses the real button and ticks real boxes; tightening its last assertion found a live defect (a second press built a second picker and stacked a second page, because the first was the inspector's child, not the button's, while shown) — fixed, and the mutation that removes the toggle now fails. `tests/test_response_tab.py`'s 96 tests drive the real Series page. The `test_no_widget_lives_in_a_menu` and `test_one_way_to_pick_several` rosters each lost these four functions. **Not done:** Cell Browser's Gate / Timepoints / FOVs (`criteria_view`), Cell Gating's FOV and gate-filter menus (which need the measured debounce), Nanowells Extremes Timepoints and the crops pop-out (rows S19, S21), the Multivariate Features panel, and the four importers of `widgets/checkable_dropdown.py` (Segmentation's scope panels, `analyze/qc_section.py`, `analyze/section_summaries.py`, `datasets/core/timepoint_picker.py`) — so that module is not deleted. |
| S13 | `widgets/range_page.py`; convert both `LUT` panels and the Nanowells display range (four buttons become one row) | todo | `widgets/range_page.py` (new), `cell_browser/tab_view.py`, `preview/panel_view.py`, `nanowell/display_range_view.py` | `tests/test_range_page.py`; per-page test that the range reaches the image's display window |
| S14 | `widgets/colour_page.py`; convert the three swatch → picker chains and saved-selection Recolour | todo | `widgets/colour_page.py` (new), `widgets/color_swatch_row.py`, `widgets/color_picker_popover.py`, `review_image/controller.py`, `cell_browser/tab_view.py`, `widgets/saved_selections_list.py` | `tests/test_colour_page.py`; `test_popovers_are_gone` roster shrinks |
| S15 | The colour-map picker's searchable list as a page | todo | `widgets/lut_selector.py`, `heatmap/tab_view.py`, `playground/tab_view.py` | Picking a map through the page changes the figure's colour map |
| S16 | Per-sample control as a full-height page reached by a visible row; *Set all to* and *Auto-pair* as buttons with one-level menus | todo | `review/core/fold_change_pairing_view.py`, `review/core/fold_change_controls_view.py` | The existing per-sample tests re-entered through the real row (CLAUDE.md §7.6); depth is one throughout |
| S17 | Subset as a Data row: presets, saved subsets and *Custom subset* on one list; the editor a full-height page | todo | `nanowell/subset_view.py`, `nanowell/_saved_subsets.py` | `tests/test_nanowell_subset_fits_a_phone.py` re-stated; applying a custom subset changes the plotted set |
| S18 | Image Table: Table and Crop view menus reduced to commands; channel gear options as a page; Export settings and the quality submenu as Style pages | todo | `image_browser/image_table/tab_view.py`, `builder_view.py`, `export.py` | `test_a_menu_holds_commands_only` roster shrinks; export settings reach the exporter |
| S19 | Nanowells band: Layers, Problem, Excluded as rows (Excluded permanent, reading 0); the three-row band becomes the Data tab / one desk row | todo | `nanowell/tab_view.py`, `qc_view.py`, `exclude_view.py` | Per-control tests through the real builder; `scripts/test.sh --nanowell-features` |
| S20 | A status line under the plot: one chip per condition that applies (subset, excluded, window, gates), each opening the page that owns it; the window strip, Resolve guide and Queue group reached from it | todo | `widgets/` chip row (reuse `widgets/chip_group.py` if it fits), `nanowell/window_view.py`, `resolve_view.py`, `anchor_queue_view.py`, the plot pages | A chip appears when its condition holds and opens its page; none when nothing applies |
| S21 | The crops pop-out: its Timepoints / Channels / Grid menus use the multi-select component, its range the range page, its Save the Share order | todo | `nanowell/popout_view.py`, `nanowell/tab_view.py` (`_PopoutWindow`) | `tests/test_phone_nanowell_popout.py` re-stated; depth is one inside the pop-out |
| S22 | Nanowells in phone layout end to end: the every-page tour with every inspector page on all six screens | todo | `tests/_page_tour.py`, `nanowell/_phone.py` | `scripts/test.sh tests/test_every_page_renders.py -m ""` green; shots reviewed with `ALLWELL_PAGE_SHOTS` |
| S23 | Dataset tools: five header commands move to the dataset menu (desk) and to `⋯` › *Dataset tools* (phone, resets behind an alert); the header keeps five icons; Manage Remote Servers reachable from `⋯` in phone layout | todo | `allwell.py`, `review/core/app_shell_view.py`, `ui/phone_shell.py`, `review/core/reset_scopes_view.py`, `review/core/recipe_view.py` | Each command triggered from both places reaches its handler; a reset in phone layout does nothing until confirmed |
| S24 | One Close and one Back: every panel that survives uses the inspector's header; the four spellings in §1 are gone | todo | sites found by grep for the four spellings | An AST or text guard over the surviving spellings |
| S25 | Gesture-only actions get the visible equivalents of §11.6 | todo | the sites in §11.6 | `tests/test_nothing_is_reachable_by_gesture_alone.py` roster complete |
| S26 | Menu conventions: Quick Setup shows an arrow; Presets uses the shared factory; the job row's two-level submenu flattened; the unreachable stats-chip popover deleted | todo | `batch_export/base_panel.py`, `analyze/presets.py`, `datasets/job_queue/row.py`, `widgets/plot_card.py` | `tests/test_a_menu_button_draws_one_arrow.py` cases; submenu roster at one level |
| S27 | Window chains are at most two deep: audit Manage Remote Servers (the SSH key wizard and the scheduler test open from the host form, three deep) and every other dialog opened from a dialog; convert or decline each with a reason | todo | `analyze/remote_dialog.py`, `analyze/scheduler_test_dialog.py`, `analyze/granted_node_panel.py`, `home/cell_gating/optimizer_view.py` | The depth test extended to windows, with a roster of the sanctioned two-deep pairs |
| S28 | Every roster from S04 is empty or holds only entries with a written reason; the slow-lane crawl runs the depth rule on the built application in both layouts | todo | `tests/` | The rosters; the crawl's run |
| S29 | Reconcile: re-read §11 and the original sweep's categories against the tree (not against this table), and list anything not converted and not declined | todo | this file | The reconciliation, written into the pull request body |
| S30 | Docs and help: ARCHITECTURE.md (*Phone layout*, §5.4, §5.8, *Adding a Plotting leaf*), the manual, `ui/help_topics.py`; delete this tracker | todo | `_Docs/Markdowns/`, `ui/help_topics.py` | `tests/test_doc_version_sync.py` and the docs lane green; the pull request body says the tracker is gone |
