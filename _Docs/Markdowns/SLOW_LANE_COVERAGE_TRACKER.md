# Slow-lane coverage tracker: the tooling added since the last slow-lane run

A remediation tracker in the sense of CLAUDE.md §6.1. It is deleted by the pull request that closes its last row.

## Why this exists

The slow lane (`.github/workflows/slow-lane.yml`, `scripts/test_slow.sh`) runs only when someone dispatches it. Its
last run was on 2026-10-16, on a feature branch, and failed; its last green run was 2026-09-12. Between 2026-10-16 and
2026-11-01 about 34,000 lines changed under `review/`, `analyze/` and `datasets/`, most of them on the Nanowells page.

Measured with coverage on 2026-11-01, the two full-app files that reach the Nanowells page (`test_nanowell_page_app.py`,
`test_nanowell_follows_group_edits.py`, six tests) build every one of its views but drive almost none of them:
`_identity_controller` 21 %, `_anchor_queue` 20 %, `_statistics_render` 15 %, `subset_view` 47 %, `controller` 45 %.

The page *itself* is well tested in the fast lane, through its real builder `build_nanowell_tab` inside a minimal
shell. So a full-app test here earns its cost only where the page meets something the minimal shell does not have or
stubs: the shell's mode band, the rail plate, the window's own shortcuts, other tabs reading the same data, the Home
editors, Saved Plots, persistence across a dataset switch. Each row names that seam. A row that would only repeat what a
fast-lane test already drives through the real builder is not a row.

The Analyze and Datasets panes are a different case. Their full-construction tests build the real `AnalyzeTab` /
`DatasetsTab` and run in the fast lane, because neither builds a `ReviewApp`; rows for those panes are opened only
where coverage under those tests shows a module the real tab never reaches.

## Rows

State is `open`, `done` (with the test that pins it, seen failing against a mutation) or `declined` (with the reason).

| Row | Seam | What the test drives | State | Test |
|---|---|---|---|---|
| N1 | Mode band | Each of the page's view tabs clicked in the real band switches the view and draws without a logged error; the strip shows only on the Nanowells page | open | |
| N2 | Rail plate, window shortcuts | The Anchor queue walked with the real rail plate (the fast test stubs it) and driven by its keys: → skips, ← goes back, a canvas click anchors, Esc stops and the rail's selection is restored | open | |
| N3 | Home's Cell Gating panel, other tabs | The band's Rim focus switch mirrors Cell Gating's nanowell focus switch, and the soft nanowell's cell leaves another tab's numbers | open | |
| N4 | Saved Plots | The Statistics view's choices (compare, test, control) and the Subset survive a Saved Plot round trip through the real store | open | |
| N5 | The app window | The crops pop-out opens owned by the app's window, draws the selected nanowell, and closes with the page | open | |

## Counts

Derived from the rows above, never typed: run
`grep -cE '^\| [A-Z][0-9]+ .*\| open \|' _Docs/Markdowns/SLOW_LANE_COVERAGE_TRACKER.md` (and `done`, `declined`).
