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
| H1 | A screen fixture: the offscreen screen reports a desktop (1640×980), a phone upright (432×936) or a phone sideways (936×432), and the window is placed to match | `todo` | |
| H2 | Data-filled datasets for the full app: the mini dataset with its four wells selected and the pages that need setup configured (Heat Map, Statistics groups, Multivariate columns, Cell Browser generated); a nanowell dataset; an image dataset | `todo` | |
| H3 | A page tour that visits every page of every mode (Review pages and sub-pages, Datasets tools, Analyze views, and the phone's drawer, Controls sheet and Wells sheet) and reports each by name | `todo` | |
| H4 | Layout checks on a live window: text clipped by its label or button; scroll-area content clipped with no scroll bar; widgets past the window edge; matplotlib text outside its figure; matplotlib text overlapping other text; a scroll area squeezed to a sliver; a table that needs a sideways scroll; a page menu whose label is not the page on screen | `todo` | |
| H5 | A slow-lane test that renders every page at every screen on every dataset and asserts the checks are clean except for a roster of known cases, each with a reason; saves a PNG of each page when `ALLWELL_PAGE_SHOTS` names a directory | `todo` | |
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
| D6 | Nanowells → Trajectories, Position, Array: footer lines cut off at the right; Position's x-axis label cut off; Extremes' title cut off; Identity's message cut off at both edges (upright) | `todo` | |
| D7 | Nanowells → Distribution and Trajectories: the baseline legend overlaps the panel titles (upright) | `todo` | |
| D8 | Plotting → Heat Map: the column numbers "10 11 12" run together and the grid is a few cells at the top of a tall canvas (upright) | `todo` | |
| D9 | Spatial: the per-well "No cells…" messages run into each other (upright) | `todo` | |
| D10 | The page-menu button for "Validate & Repair" shows "Validate ̲Repair": the ampersand is read as a keyboard mnemonic | `todo` | |
| D11 | Analyze → Settings: only a sliver of the form is visible (about 170 px), because the status bar, run bar and header take the rest (sideways) | `todo` | |
| D12 | Analyze → Progress: the mini plate's column numbers and row letters collide ("123456789101112") (sideways) | `todo` | |
| D13 | The page menu showed "Playground" while Cell Browser was on screen in one sideways shot — seen once, not confirmed | `todo` | |
| D14 | Batch Export → Nanowells: content 45 px wider than its viewport with no scroll bar, at the desk (found by the calibration run) | `todo` | |
| D15 | Pages whose first sweep showed only an empty state for lack of setup, so their rendering with data is unchecked: Statistics, Multivariate, Spatial, Cell Browser | `todo` | |
