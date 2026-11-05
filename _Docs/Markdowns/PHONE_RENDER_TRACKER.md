# Phone and desktop rendering — harness and defect tracker

**This document is temporary.** It exists because this body of work spans
several sessions and more than a handful of items, and CLAUDE.md §6.1 requires
that a plan of this size live on disk rather than in conversation. It is deleted
by the pull request that closes its last row, and that pull request says in its
body that it is gone.

It does two things. It makes the slow (full-app) lane render **every page, with
data, at the screen size a person actually has** — a desktop, a phone held
upright, a phone held sideways — and check each page for the defects a human
found by looking. And it fixes the defects that a first manual sweep found.

## Why

The slow lane's one phone file, `tests/test_phone_layout_app.py`, runs on
Qt's default offscreen screen, which is **800×800**: a square, not a phone and
not a desktop. The layout snapshot (`tests/test_phone_layout_snapshot.py`) did
the same, so its "upright" was not phone-shaped. The tests that do patch the
screen to a real phone shape (432×936 upright, 936×432 sideways) are fast-lane
tests of single dialogs and pages. Nothing rendered the whole app's pages at
432 px, so twelve defects that are visible at a glance — text cut off at the
screen edge, a page running off its right side, nanowell footers clipped —
passed every test. A manual sweep (screenshots of every page, both
orientations, with data) found them.

## How to read the rows

Every row has an id, a state, and — once `done` — its evidence.

- **`todo`** — not started.
- **`wip`** — started, not finished; never left across a session boundary
  without a note saying where it stopped.
- **`done`** — implemented **and** pinned by a check or test that was watched
  failing without the change (CLAUDE.md §7.7 #2). For a defect row that means the
  harness check that found it fails on the unfixed code and passes on the fixed.
- **`blocked`** — cannot proceed until a named row closes or the human answers.
- **`declined`** — examined and deliberately not done, reason written in the row.

**Counts are derived from the rows, never typed:**

```bash
F=_Docs/Markdowns/PHONE_RENDER_TRACKER.md
for s in todo wip done blocked declined; do
  printf '%-9s %s\n' "$s" "$(grep -c "| \`$s\` |" "$F")"; done
```

---

## 1. The harness

| Id | Item | State | Evidence |
|---|---|---|---|
| H1 | A screen fixture: the offscreen screen reports a desktop (1640×980), a phone upright (432×936) or a phone sideways (936×432), and the window is placed to match | `wip` | `tests/_screens.py` written (`FakeScreen`, `place`, `pump`); not yet proven on a run |
| H2 | Data-filled datasets for the full app: the mini dataset with its four wells selected and the pages that need setup configured (Heat Map, Statistics groups, Multivariate columns, Cell Browser generated); a nanowell dataset; an image dataset | `wip` | `tests/_page_datasets.py`: the first nine-case run left pages on an empty state — mini: Nanowells/*, smFISH, Image Table, Cell Browser, Saved Plots; nanowell: Heat Map, Statistics, smFISH, Image Table, Cell Browser, Saved Plots; images: Heat Map, Statistics, Nanowells/*, smFISH, Cell Browser, Saved Plots. Extended: Heat Map, Statistics and Multivariate set up for every dataset (wells split into two groups); the mini dataset now carries its smFISH column; Saved Plots gets one saved figure; Cell Browser is filled through `apply_filters(live_sync=True)` (the Generate button read an empty timepoint list). STOPPED HERE: the extended set-up is being run on images/phone-upright, mini/desk, nanowell/desk; EMPTY roster not yet written |
| H3 | A page tour that visits every page of every mode (Review pages and sub-pages, Datasets tools, Analyze views, and the phone's drawer, Controls sheet and Wells sheet) and reports each by name | `wip` | `tests/_page_tour.py` written; first run of one case (mini, phone upright) in progress |
| H4 | Layout checks on a live window: text clipped by its label or button; scroll-area content clipped with no scroll bar; widgets past the window edge; matplotlib text outside its figure; matplotlib text overlapping other text; a scroll area squeezed to a sliver; a table that needs a sideways scroll; a page menu whose label is not the page on screen | `done` | `tests/_layout_checks.py`: text, scroll, window, figure, overlap, ticks, sliver, table and menu checks. Calibrated on a data-filled desktop (clean except real defects: the Cell Gating notice, Spatial's label, Array's labels, Playground, Batch Export). Each check fired on the defect it was written for in the nine-case run: `text` on QC Triage's buttons, `scroll` on Cell Gating and Playground, `window` on Playground, `figure` on Scatter, Heat Map and the Nanowells footers, `overlap` on the baseline legend, `ticks` on Heat Map (`101112`), `sliver` on Analyze Settings sideways, `menu` on the stale label and on 'Validate & Repair' |
| H5 | A slow-lane test that renders every page at every screen on every dataset and asserts the checks are clean except for a roster of known cases, each with a reason; saves a PNG of each page when `ALLWELL_PAGE_SHOTS` names a directory | `wip` | `tests/test_every_page_renders.py` renders each (dataset, screen) case in a process of its own (`tests/_page_render_case.py`, three at a time): the first version ran all nine in one process, which slowed from seconds per page to minutes per page once the apps piled up (the failure CLAUDE.md §2 warns of) and hit its 50-minute limit after six cases. One case alone (mini, phone upright) ran in 95 s and flagged the Cell Gating, QC Triage, Pipeline Info, Scatter and Playground defects. STOPPED HERE: the nine-case run with the per-process runner is in progress; KNOWN and EMPTY rosters still empty |
| H6 | `test_phone_layout_app.py` and the layout snapshot run on the real phone screen (the snapshot baseline is regenerated at phone resolution, upright and sideways) | `todo` | |
| H7 | Documentation: ARCHITECTURE.md (test lanes), CLAUDE.md §2 (how to run the page-render lane) | `todo` | |

## 2. The defects a manual sweep found

Each is checked by H4/H5 where it can be, or by a targeted test where it cannot
(painted widgets, a label's content). Upright means 432×936, sideways 936×432.

| Id | Defect | State | Evidence |
|---|---|---|---|
| D1 | Home → Cell Gating: help text cut off at the right edge, mid-sentence (upright) | `todo` | |
| D2 | Image Browser → Playground: the preview takes a narrow left column and the controls run off the right edge (upright); also a scroll area clipped with no scroll bar at the desk | `todo` | |
| D3 | Home → QC Triage: the row of action buttons is truncated ("Segm", "ccep") (upright) | `todo` | |
| D4 | Home → Pipeline Info: the Value column is scrolled out of sight (upright) | `todo` | |
| D5 | Plotting → Scatter Plot: the x-axis label is cut off at the right (upright) | `todo` | |
| D6 | Nanowells → Trajectories, Position, Array: footer lines cut off at the right; Position's x-axis label cut off; Extremes' title cut off; Identity's message cut off at both edges (upright) | `wip` | footer/label/title cases fixed in `renderer.py` by figure width in points; `tests/test_nanowell_page_on_a_phone.py` pins footer wrapping by width whatever the mode; harness: all D6 `figure` findings gone on nanowell and mini. Identity/QC message cut-off did not reproduce in any run (declined as not reproduced). |
| D7 | Nanowells → Distribution and Trajectories: the baseline legend overlaps the panel titles (upright) | `wip` | occupancy key sized in points above the titles; harness: Distribution/Trajectories legend overlaps gone sideways and upright; final run (`after3`) shows no overlap findings. |
| D8 | Plotting → Heat Map: the column numbers "10 11 12" run together and the grid is a few cells at the top of a tall canvas (upright) | `todo` | |
| D9 | Spatial: the per-well "No cells…" messages run into each other (upright) | `todo` | |
| D10 | The page-menu button for "Validate & Repair" shows "Validate ̲Repair": the ampersand is read as a keyboard mnemonic | `done` | `tests/test_phone_datasets_shell.py` (fails when `_menu_text` returns the name unchanged); harness: finding gone on mini, phone upright and sideways. |
| D11 | Analyze → Settings: only a sliver of the form is visible (about 170 px), because the status bar, run bar and header take the rest (sideways) | `done` | `tests/test_analyze_phone_view.py` (fails on old code: 'sideways, the run strip still takes the page's height'); harness: `sliver FooterScrollArea` gone. Cost: Settings sideways hides the Idle status and overall bar. |
| D12 | Analyze → Progress: the mini plate's column numbers and row letters collide ("123456789101112") (sideways) | `done` | `tests/test_a_plate_keeps_its_size.py` (measures labels at the painter; fails on old code: "column '10' is 12.4px wide in a 10.8px column"); not harness-detectable, judged from a screenshot. Edits shared `widgets/well_plate_selector.py`. |
| D13 | The page menu showed "Playground" while Cell Browser was on screen in one sideways shot — seen once, not confirmed | `done` | CONFIRMED by the harness's `menu` check and FIXED: the page button said 'Overview' while Cell Gating (and every later page of a section) was on screen, because only a section change told the shell. `allwell.py` now tells `PhoneShell.page_changed` from the hook that already watches every page-tab bar for the history (`_on_page_tab_changed`). Before: 24 `menu` findings per phone case; after: none in all six phone cases of the nine-case run |
| D14 | Batch Export → Nanowells: content 45 px wider than its viewport with no scroll bar, at the desk (found by the calibration run) | `todo` | |
| D15 | Pages whose first sweep showed only an empty state for lack of setup, so their rendering with data is unchecked: Statistics, Multivariate, Spatial, Cell Browser | `todo` | |
| D16 | Plotting → Heat Map: the colorbar's label is cut off at the right edge (found by the harness's `figure` check: the label spans 465..601 px of a 406 px figure) (upright) | `todo` | |
| D17 | Spatial: the x-axis label falls below the bottom of its figure and overlaps the panel titles, at the desk and upright (found by the harness) | `todo` | |
| D18 | Plotting → Bar Plots: the "n/a" marks overlap the "Fraction of Cells Above Threshold" title, at the desk (found by the harness) | `todo` | |
| D19 | Home → Cell Gating: the wrapped "analysed before border…" notice needs 32 px and has 21, so its second line is cut off, at the desk (found by the harness) | `todo` | |
| D20 | Multivariate: neighbouring tick labels overlap (upright) (found by the harness) | `todo` | |
| D21 | Nanowells, with a nanowell dataset: Array's per-nanowell labels overlap each other (desk, upright, sideways); Array's colorbar label and Statistics' y label sit on the figure's edge; Extremes' crop captions overlap; Distribution's tick labels run into the baseline legend (upright); Position's footer is cut off sideways too (found by the harness) | `wip` | Array labels skip any that would overlap (footer counts them), colourbar room and Statistics margin from points; tests in `tests/test_nanowell_page.py`; harness: gone at all three screens. Remaining `ticks` on Distribution/Trajectories reproduces on `edb51e2` and is not part of D21. |
| D22 | Plotting → Distribution: the x-axis label overlaps the footnote beneath it (upright) (found by the harness) | `todo` | |
| D23 | Image Browser → Playground at the desk: content up to 650 px wide in a 349 px viewport with no scroll bar (found by the harness; D2's desk half) | `todo` | |

| D24 | Nanowells → Distribution and Trajectories: neighbouring tick labels overlap on every screen (reproduces on `edb51e2`; found by the harness) | `todo` | |
