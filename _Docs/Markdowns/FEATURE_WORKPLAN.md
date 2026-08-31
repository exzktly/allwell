# Feature workplan

Eight workstreams, planned together because they share sequencing and because a
body of work this size does not survive in conversation. This file is the state
of that work; it is not a design essay and not a changelog.

## This file is live

**This is the state of the work, not a description of it.** It is read at the
start of a session and written during one — never reconstructed from memory at
the end. The rules are `CLAUDE.md` §6.1, restated here so a session that opens
this file alone still follows them, plus what that section costs when it is
skipped: a 177-finding review was reported complete with 42 items still owed,
because the state lived in conversation while confidence in it survived every
compaction.

1. **A row moves in the same commit as the change it describes.** Not at the end
   of a session, not in a batch, not once CI is green. A commit that changes
   behaviour and moves no row is a commit that has desynchronised this file.
2. **A row moves before the work is described as done in any reply.** If the row
   still says `☐`, the answer to "is it done" is no — say that instead.
3. **Open the file before working and reconcile against the tree, not against
   this file.** A row is evidence of what someone believed, not of what the tree
   holds. Line numbers move; a row that names a site is checked against the site.
4. **The counts are derived, never typed.** `tests/test_workplan_is_honest.py`
   parses the rows; no total is written in prose anywhere in this file.
5. **`☑` requires evidence** — the test that pins the row, watched failing
   without the fix. "Implemented" is not `☑`. "Tests pass" is not `☑` either
   unless one of them would have failed before.
6. **An item that turns out not to be worth doing is `⊘`**, with the reason in
   its own note. Never deleted: a dropped row and a forgotten row look identical
   afterwards, which is the confusion this file exists to prevent.
7. **A workstream is closed only through its review gate** (below), and only
   after re-reading every one of its rows against the tree.
8. **This file is deleted by the PR that closes its last row**, and that PR's
   body says it is gone. A tracker left behind at zero outstanding is a stale
   document claiming work is owed.

Status glyphs: `☐` not started · `◐` in progress · `☑` done with evidence ·
`⊘` declined, reason in the note.

## Closing a workstream — the review gate

Every workstream ends with one, and it is a tracked row rather than a habit, so
skipping it is visible. It runs **before** that workstream's pull request opens,
over the whole of its diff:

```bash
git diff origin/main...HEAD -- <the workstream's paths>
```

An expert Python reviewer reads that diff against three questions, in this
order:

**1. Does it hold the contracts?** `_Docs/Markdowns/ARCHITECTURE.md` is the map,
and §16 lists what is enforced mechanically. The guards catch their own cases;
the reviewer is for what they cannot see — a feature reached from `core/`, state
put on a loose attribute instead of a typed slice, a renderer that does not end
in `apply_export_style_to_current`, a verdict recomputed in a second place, a
degraded result that looks like a good one. §7's rule is the one to read the
diff against hardest: **undefined must never be written as zero, and a shortfall
must reach the artifact rather than only the log.**

**2. Does it behave as claimed?** Not "do the tests pass" — the reviewer names
the single edit that would break the feature, and checks a test would catch it
(§7.7). A branch that cannot be reached, a control that is offered and does
nothing, a fix whose test never saw the bug: those are the failures this repo has
actually shipped, and none of them fails a suite.

**3. Is it over-architected?** This is the question most likely to go unasked, so
it is asked explicitly. The repo's own risk profile is not under-engineering —
it is a registry for two things, a base class with one caller, a seam introduced
for a second implementation that never arrives, a dataclass wrapping one value, a
protocol for a duck that quacks in one place. `DatasetToolController` is the
counter-example worth holding in mind: it was extracted only once six controllers
existed and it deliberately left `_on_queue_requested`, `_on_finished` and
`_on_succeeded` behind, because those measured six, five and two distinct shapes.
The reviewer names anything that would read as simpler written out longhand, and
anything whose abstraction has exactly one user.

The reviewer reports findings; it does not apply them. Each finding becomes a
row here — fixed, or `⊘` with the reason written at the site as well as in the
row. The gate's own row goes `☑` when its findings are all closed, which is what
makes "the review ran" and "the review was acted on" the same fact.

## Sequencing

```
A scale bar fix ──────────────┐
B coverage measurement ───────┼── independent, ship in any order
C performance pinning ────────┤
D Parquet storage ────────────┘   (C first, so D's win is measured not asserted)
E recent datasets ────────────    independent

F sample variables ───────────┬── F is the foundation
                              ├──► G response curves
                              └──► H statistical tests (the two-factor half)
```

B is deliberately early: it measures everything that follows it. C before D for
the same reason — a performance change with no baseline is a claim.

Each letter ends at its review gate, which is that workstream's last row and the
condition for opening its pull request. A workstream is not "done pending
review"; it is open until the gate closes.

## The tracker

| # | Workstream | Item | Status | Note |
|---|---|---|---|---|
| 1 | A | `draw_cell_chrome` refuses an oversized scale bar instead of clamping | ☑ | `bar_fits` decides; `draw_cell_chrome` refuses. Measured on `origin/main`: a 50 µm bar drew 37.7 µm in a 60 px cell. |
| 2 | A | The refusal is reported per pass, not silently | ☑ | Per-pass `state.image_table.scale_bar_refusals`; one `scale_bar_refusal_clause` said by the Generate line and all four export surfaces. First ticked on evidence that did not cover the report — the gate caught it. |
| 3 | A | Guard: a bar wider than its cell is never drawn short | ☑ | `tests/test_scale_bar_is_never_drawn_short.py`, 21 pure-lane cases covering the draw, the record AND the sentence. Three mutations watched failing, each on its own check. |
| 4 | A | Every artifact surface reports a truncated inventory, not just the bar | ☑ | `image_compose.partial_scan_clause`, the sibling of `scale_bar_refusal_clause`, now said by all five surfaces — the Generate line, the PNG/PDF/SVG export, both clipboard copies and the gallery save. It replaces **two** hand-written sentences that had already drifted in the worst direction: the Generate line counted the wells and named the scans' reasons, and the saved-PNG line did neither, so the artifact that outlives the session carried *less* than the transient line. The clause is neutral about grid-versus-file, because that framing is the caller's and the fact is the same in both. The invariant worth guarding is not that the sentence exists but that it travels with its sibling: a saved file naming a missing scale bar and silent about missing frames is worse than one naming neither, because the reader now believes the line is telling them what is wrong. `tests/test_both_shortfalls_travel_together.py` asserts the two clauses reach exactly the same **functions** — per function, not per file, because the scale bar's own first guard checked that the module mentioned the clause somewhere and would have passed with three of four surfaces silent. That guard deliberately imports nothing from the application: the first version lived beside the clause's own tests, and `scripts/prove_guard.sh` reported `NOT PROVEN (ImportError)` because the base branch has no such symbol — an assertion that measures nothing. Split out, it proves against `HEAD` naming all four surfaces that were not reporting. Seven mutations watched failing, plus four bite cases in `test_guards_bite.py` covering both directions of the pairing and a roster entry that has stopped reporting. |
| 5 | A | Review gate: the refusal path, and no second wording for it | ☑ | Ran on `c2a1c78`. Seven findings: a broken slow-lane test, an untested report, two wordings, a degenerate-shape hole, a clear-site asymmetry, an extraction leftover, a nit. All fixed except the inherited one, which is row 4. Verdict on over-architecture: not over-architected. |
| 6 | B | Fast lane emits coverage; slow lane emits coverage | ☑ | `ALLWELL_COVERAGE=1` in front of either lane's script. Parallel mode in `[tool.coverage.run]`; both scripts read the one variable. |
| 7 | B | `coverage combine` across both lanes | ☑ | `scripts/coverage.sh` — combine, `--zero`, `--diff`, `--html`. Proven end to end on the real fast lane: 7,262 tests, 73,839 statements, 63.9%. |
| 8 | B | Differential coverage on the PR diff, advisory at first | ☑ | `scripts/coverage.sh --diff`, local and advisory. Not a PR gate: the slow lane does not run on PRs, so genuinely-covered code would read uncovered, and a per-PR run costs 3x the lane. `coverage.yml` states what turning it on would cost. |
| 9 | B | One-off zero-coverage inventory, triaged into rows here | ☑ | Both lanes in one measurement — Slow lane #36 at `463722f`, six shards green, 54 batch data files, combined with the fast lane at the same head: **76,964 statements, 79.8%, five modules with no executed lines**. Triage table in §B3. One is genuinely unreachable and now says so in its own docstring (`all_well_launcher.py`, the PyInstaller entry point, guarded at source level instead); the other four now have tests — `widgets/color_picker_popover`, `widgets/color_swatch_row`, `widgets/chip_group` and `well_viewer/persistence/bar_groups`, 104 tests taking those 644 statements from **0% to 96.0%**, with 49 mutations watched failing and one real defect found on the way (the picker could not commit a recent). **None was dead code.** The value of doing this properly is in what the partial answer got wrong: the fast-lane-only list named eleven modules, and six of them are reached — five by a single slow-lane file. Acting on it would have deleted live code five times out of eleven. |
| 10 | B | `ci.yml`'s header states the lane it actually gates | ☑ | It read "~1,770 tests, ~60s". Measured on this container: **7,681 collected, ~5 min 50 s** — the lane grew more than fourfold underneath a comment nobody re-read. That comment is what a reader consults to decide whether the 15-minute job cap still has headroom, so a stale number there is the most load-bearing kind. Corrected, with the two-second command to re-measure written beside it. |
| 11 | B | Scoped mutation testing over `domain/` and `pipeline/` | ⊘ | **Declined on measurement, and the plan's own premise is what the measurement refutes.** §B says mutants over these two trees "run in the fast lane's budget" because both are Qt-free and pure. Counted: `well_viewer/domain/` is 4,042 statements across 36 files and `pipeline/` 2,937 across 21 — **6,979** statements, so roughly 7,000–14,000 mutants at mutmut's usual one-to-three per mutable statement. Each needs a test run: at an optimistic 5 s that is 10–19 hours, at a realistic 20 s it is 39–78. Not a budget overrun — two orders of magnitude out. Per-module is barely better where it would matter most: `domain/fold_change.py` alone is 829 statements, about 9 hours, and it is the module §7.5 #8 names as the one worth mutating. `mutmut` 3.7.0 does install cleanly against the pins (10 transitive deps, no conflict), so this is a cost decision and not a feasibility one. It is also C row 12's argument a second time: a lane nobody runs is this repository's largest existing gap rather than a fix for one. What already serves the purpose is the ritual itself — §7.7 #2's "name the edit that would break it, make it, watch a test fail" — run at the point of change, which is where the mutant is chosen by judgement rather than by enumeration; this session alone proved roughly sixty that way, and three of them found defects a full lane and a review pass had both missed. `scripts/prove_guard.sh` mechanises the half of it that has a base branch to compare against. If the by-hand half is worth scripting too, that is its own row and a small one; it is not this. |
| 12 | B | Review gate: the lanes combine, and no threshold masquerades as a gate | ☐ | |
| 13 | C | Complexity assertions for the four fixed hot paths | ☑ | `tests/test_hot_paths_stay_linear.py`. Counts the operation each fix removed, at two input sizes — deterministic and machine-independent. Four mutations watched failing, each on its own check. Hardened by the gate: the first-visits check asserts every distinct cell came back (it passed against a `_first_visits` returning nothing), and the DBSCAN check counts `set()` **calls** — one per `_dbscan` call against 200 and 400 for the rebuild — rather than element volume, which separated the two by only 1.33x above the threshold. |
| 14 | C | `pytest-benchmark` lane with in-repo baselines | ⊘ | Declined: it would be a second lane nobody runs, which is this repo's largest existing gap rather than a fix for one. Reason in full in §C. |
| 15 | C | Peak-memory ceiling on the CSV load path | ☑ | `tests/test_the_load_path_stays_small.py`. `tracemalloc` separates the typed read from the `dtype=str` one by 1.8x, so the ratio ceiling is meaningful. Both mutations watched failing. Hardened by the gate: the fixture's `filename` column now varies (384 distinct in 15,000 rows) so it can see `_CATEGORY_MAX_UNIQUE_SHARE` at all, and a third test asserts the other side of that constant — an all-distinct column stays object. Both mutations watched failing; lowering the constant to 0.001 left the file green before. |
| 16 | C | Review gate: the assertions pin behaviour, not the machine | ☑ | Ran on `d3c9da3`. Six findings, all closed: a vacuous first-visits check (HIGH), a memory fixture blind to the threshold it guards, `_CountingSet` as over-construction, a blanket `monkeypatch.undo()`, a fourth conflicting copy of the load measurement, and ARCHITECTURE not naming the new guards. The measurement conflict resolved to 1729/867 — `git log c7c8675` records 1749/1073 as the discarded in-process reading, since `ru_maxrss` never falls. §C2's "gap this leaves" paragraph amended on the reviewer's two points: a complexity regression hidden inside one library call, and the self-normalising ratio timing the plan never mentioned. Verdict on over-architecture: not, once `_CountingSet` went. |
| 17 | D | Parquet cache writer + fingerprint invalidation | ☑ | `well_viewer/data/parquet_cache.py`. `.aw_parquet/<well>.parquet` beside each CSV, keyed on `"<size>:<mtime_ns>:v<PARSER_VERSION>"`; staging file + `os.replace`; `prune` reclaims what nothing else can. Every failure mode is a miss that costs speed and nothing else. **This row's earlier note was wrong and the gate caught it**: it claimed "a write that stores none" had been watched failing. It had not — that run's red test was an unrelated one that has since been deleted, and I read "1 failed" as proof without reading the failure, which is §7.5 #1 exactly. Re-run after the fix, five mutations now fail on their own check: a read that ignores the fingerprint, a write that stores none, `PARSER_VERSION` dropped from the key, the fingerprint taken after the parse, the staging sweep removed. |
| 18 | D | The one door onto a well's rows reads Parquet | ☑ | `data_loading.read_well_frame`. Both `load_well_csv` callers converted (`load_and_project`, `smfish.tab_view._smfish_refresh_app_cache`); an AST guard fails on any third, in either spelling. Measured through the real path on a 120,000-row well: 3,068 ms → 51 ms (60.6x), and 7 ms for six columns (415.8x). Named for what it is rather than for `WellRepository`, which is not where the seam landed. |
| 19 | D | Column projection plumbed from the aggregation call sites | ⊘ | Declined: there is no call site. The row cache holds one frame per well and 56 `get_rows` sites read arbitrary columns from it, so a projected frame there is a silently-empty plot — §7's shortfall that looks like a result — and no read path in the app bypasses that cache. The parameter was written, tested and then removed rather than left present and unused, which is the seam-for-an-absent-implementation the gate's third question names. Reason at the site (`read_cached`'s docstring) as well as here. The 7 ms against 51 ms measurement stands and is what makes this worth revisiting when a consumer exists that does not populate the row cache — a DuckDB query layer being the obvious one. |
| 20 | D | Guard: Parquet is never written from a projected frame | ☑ | `test_the_cache_is_written_before_any_verdict_is_projected`, driven through `load_and_project` with a projector registered — not through `read_well_frame`, which has no projector loop and so could not fail. Moving the write below the loop was watched failing; the earlier version of this test stayed green under exactly that mutation. |
| 21 | D | Equality harness: CSV path vs Parquet path | ☑ | `assert_frame_equal` cell for cell and dtype for dtype, over a fixture carrying the two cases `load_well_csv`'s `na_values` handling exists for — a blank cell and a cell holding the literal text `NA`. Not on real plates: none is in the repo, so the fixture is synthetic and says so. §7.5 #8 applies — this is my own differential on my own fixtures, and the gate is where it gets a second reading. |
| 22 | D | Review gate: one storage seam, not a second data layer | ☑ | Ran on `45d045b`. Fourteen findings, four HIGH, all closed. **The cache could be deleted with one line and the full fast lane stayed green at 7,278 passed** — every test asserted a property of a *miss*, so "never cache anything" satisfied all of them (§7.7 #2, in its purest form). Now counted: two reads of one well must parse once. The reuse key omitted the parser, so an upgraded `load_well_csv` would have served the old frame from an untouched CSV — `PARSER_VERSION`, pinned against a hash of the parser's source, and the cache registered in `test_reuse_is_keyed_completely.py`. The fingerprint was stamped after a three-second parse, so a concurrent rewrite poisoned the cache permanently — taken before, re-checked at the write. And both guards skipped in CI, pyarrow being absent from `tests/requirements-dev.txt`. Mediums: the one-door guard let an aliased import through (fixed, with a `test_guards_bite` case for all three spellings), `pyarrow` unpinned in a pinned file (now `==22.0.0`, verified installed against numpy 1.24.4 / pandas 2.2.3), the bundle cost restated from a measurement (155 MB unpacked, not "roughly 100 MB"), a docstring naming a test file that does not exist, a docstring naming `Included` as a projected column when it is part of the parse, a WARNING per well per load, and no reclamation of orphaned cache or staging files. Verdict on over-architecture: not, and the `columns` removal was right. |
| 23 | E | `RecentDatasets` store on `QSettings` | ☑ | `well_viewer/core/recent_datasets.py`. Qt-free — settings injected, so the whole module runs against a dict stub and no test writes to the machine. Cap from the front, dedupe by `Path.resolve()`, every malformed settings value an empty list. Written by `load_controller._remember_if_it_loaded` on wells found, not on the folder opening. Five mutations watched failing: cap from the back, dedupe on the raw string, record regardless of wells, never record, store `data_dir` instead of the folder pointed at. |
| 24 | E | Recents on the no-dataset placeholder (`EmptyState`) | ☑ | `EmptyState.setEntries` / `clearEntries`, option (1) as planned — Home keeps the shared placeholder. An unreachable folder is drawn disabled with the reason, never dropped. Four mutations watched failing, including the two that made the first version of the disabled-row test vacuous (it clicked a row that was not there). |
| 25 | E | Recents card on Home Overview | ☑ | `_build_recents` / `_paint_recents`, same card shape as the saved-plots strip. Deviates from the plan in one place: not through `collect.py`, because the recents come from user settings rather than from the dataset's own record, which is what that module reads. The open dataset is filtered out by structure (`_is_the_open_dataset`), not by string equality — the list stores the folder pointed at and `data_dir` is the `out` inside it. |
| 26 | E | Review gate: the store is a list, not a service | ☑ | Ran on `455922f`. Twelve findings, two HIGH, all closed. **The feature was dead in the shipped app**: both call sites read `ctx.window._qsettings`, which belongs to the main window and not to the review widget `ctx.window` is — nothing written, nothing read, every sentence in the commit, ARCHITECTURE and the README false. It passed because the one full-app test used `monkeypatch.setattr(loaded_app, "_qsettings", …, raising=False)`, which *creates* the attribute; the fast-lane tests stubbed a `SimpleNamespace` window, so the real object was never consulted (§7.6 #2 exactly). Fixed by `core/app_settings.app_settings()`, a module function with no object to have the wrong one of, which also collapses five construction sites. `tests/test_no_invented_app_attributes.py` is the mechanical guard for the root cause and was watched catching both original sites. Second HIGH: `paint_recents_placeholder` had **zero** coverage — deleting it left 76 tests green — now tested at the painter and at `refresh_home`'s call. Mediums: a relative `--data_dir` was stored raw so a row could open a different plate; the stat-per-entry rationale was contradicted by the code in the same commit; a store failure logged only at DEBUG, indistinguishable from an empty history. Lows: stale rows in the hidden card, `deleteLater` leaking 60 widgets over 20 refreshes without an event loop, D's `prune` smuggled inside the recents recorder (now its own function), a docstring naming a symbol that does not exist, `opened_at` dead (now the row tooltip), `forget` with no caller (removed), and two formatters for one row (now `entry_row_text`). Six mutations watched failing. Verdict on over-architecture: right size, minus the four things above. |
| 27 | F | `variables` block: schema, accessors, migration | ☑ | `domain/sample_variables.py` (Qt-free model, block round trip) + `persistence/sample_variables.py` (registered domain, loaded by `load_controller`) + `SelectionState.variables`. Nine mutations watched failing, each on its own check: the roll-up skipping an unassigned well, `MIXED` collapsing to `None`, `assign` storing an undefined id, `prune_values` keeping danglers, a bool read as a number, the loader early-returning on a missing block, the write replacing the file, the load call removed, the domain unregistered. One real bug caught by a test rather than shipped: `domain.sample_definitions.save_to_pipeline_info` reads general and is not — it replaces the `sample_definitions` key, so the first version of the writer nested the variables inside it and deleted the user's selections. |
| 28 | F | Variables sub-tab: define name / type / unit / values | ☑ | `features/home/variables/` (tab_view + controller), registered `removable=True`, with its own **Sample variables** reset scope — the partition guard caught the missing one, and a user redoing groups must not lose a filled dose grid. Editing semantics went into `domain/sample_variables` (`new_variable`, `rename_variable`, `set_kind`, `set_unit`, `add_value`, `rename_value`, `remove_value`, `move_value`, `parse_number`, `unique_name`), so eleven rules are driven by fast-lane tests rather than by clicking. Eleven mutations watched failing: five on the domain rules (an unreadable label read as 0.0, a duplicate label accepted, a rename minting a new id, a duplicate variable name, a wrapping move) and six on the wiring (Add unconnected, Enter unconnected, an in-place edit read back by text, the value list never repainted, no edit reaching the autosave, wiring not idempotent). |
| 29 | F | Fill row — along rows or columns, repeat each N | ☑ | `Fill along [Rows/Columns] · repeat each [N] · Apply fill`, over the wells the dataset holds. Ordering is `plate_layout.wells_in_fill_order` and cycling is `sample_variables.fill` — one module knows the geometry, the other the values, and neither holds a second definition of the other's job. **A gap is closed, not counted**: a plate whose column 2 was never imaged is a plate of three wells, because leaving a slot for the absent one puts every later value on the wrong well with nothing on screen to notice by. Four mutations watched failing. |
| 30 | F | Select-then-chip assignment on the tab's own plate | ☑ | The tab's **own** plate (`role="editor"`), never the rail's — Groups guards its commit on the current sub-tab for exactly this reason, and two plates meaning different things is a recorded hazard. Chips carry their value id; a chip pressed with nothing selected **says so** rather than appearing inert. Not the `"paint"` model: paint latches a polarity on press, so dragging back over an assigned well clears it, and a chip would still be needed to say which value. Three mutations watched failing. |
| 31 | F | Plate readout: colour + glyph + unassigned ring + tooltip | ☑ | Three layers, none of them colour alone: a fill colour per value, a `setWellMarker` glyph for a second variable, and a ring on an unassigned well — plus a line naming the short wells. All asserted **at the plate**, because the first readout test only checked the sentence said "ringed" and passed against a controller that had stopped ringing anything (§7.4 #1). The tooltip extends the shared `well_plate_tooltip_provider`, so every plate in the app gains the variable row and a variable the well has no value for is **named** (`Dose: —`) rather than omitted. **Deviates from the plan**: an `Also show` combo instead of a chip pair, because with three or more variables a chip would have to mean "colour" or "glyph" depending on which the rail had selected — two meanings on one control. Nine mutations watched failing across rows 27–29. |
| 32 | F | Group rows roll up; `Mixed` is shown and never inherited | ☑ | A **By group** list under the plate, hidden when there are none — needing no groups at all is most of this tab's point. A group whose wells agree shows the value; one whose wells disagree reads `Mixed (n values across m wells)`; one holding a single unassigned well reads `no value` and **names** which wells are short. Nothing is resolved to the group's first well or its commonest value. Three mutations watched failing, the cardinal one being a group silently taking its neighbours' dose — which is the defect this whole feature is shaped around. |
| 33 | F | `member_variable_values` + collectors, in one commit | ☑ | `domain/plot_members`: `member_variable_values`, `split_members_by_variable`, `variable_replicate_sets`, `wells_without_variable`, `plot_replicate_sets`. **The plan's warning did not fire, and the reason matters**: grouping by a variable needs no third member-key shape, because pooling the wells that share a dose is structurally what a replicate set already is — so every collector's existing grouped branch is reused and `test_plot_members`' equality guard keeps holding untouched. A member takes a value only when every well in it agrees; the rest come back separately so a caller says how many rather than dropping them. |
| 34 | F | Band controls: Group by / Colour by / Split by | ☑ | **Group by** is scope — it decides what a member *is*, through `plot_replicate_sets`, and sits in the scope zone beside Channel and Property. Sixteen ctx-fed call sites were swept onto that seam with a guard that fails on any module reaching past it; the full lane caught two real defects in that sweep, a missing `ctx.state.plot` slice on the headless export path and a test patching a name the renderer no longer imports. **Colour by** and **Split by** are decoration, in a `Decorate ▾` popover rather than the scope zone, and the property pinned first is that neither moves a number: the same bars, the same means, different ink and a different band order. Applied **once over the finished `BarItem` list** rather than in each of the collector's four construction branches — a sweep that edited the branches is a sweep with a branch left out, and a figure where some bars honour the control and some do not is worse than one where none does. The split re-bands and **stably** re-sorts so each band is contiguous while the drag order survives inside it, and it says on the figure when it has replaced the timepoint or well bands the tab already draws. `DECORATION_CONTROLS` is one roster read by the popover, the deleted-variable clear and the reset scope, because the hand-written pair had already drifted. **Named `Split by`, not `Split into panels` as this plan asked.** "Panel" is taken on the tab it lands on — the Bar tab's panels are Mean, Fraction and N — and the plan's own naming section rejects `Condition` for exactly that reason. What it does is what was specified: one labelled band per value of a second variable. **A defect the mutations found in my own reasoning.** The palette picks were spread across all 96 plate colours to avoid "adjacent hues", with a comment explaining why that was better. Measured, it is worse: three values came out 30 apart in RGB (`#E56AFB` beside `#DC81EA`, one colour to any reader) against 199 for the palette's first three, which is already ordered for separation. The mutation swapping the two strategies left the file green, because the test asserted the colours *differed* — true of both. It now measures the separation. §7.3 #4, and the reason the comment is wrong is that I reasoned instead of measuring. Sixteen mutations watched failing. Also fixed here, in code this row owns: the Group by shortfall was pushed to the **persistent** status bar, where it outlived its condition — G's gate found the same defect in the Response tab, and ARCHITECTURE already recorded it for the bar significance note. It reaches the figure title, which a redraw clears. |
| 35 | F | Batch Export bar figures honour Colour by / Split by | ☑ | The batch key space turned out to be **already solved** next door: `collect_bar_items_for_group` builds a `members` list of `(key, display, wells, rset_name, colour)` and its fold-change resolver already passes `scope_wells={key: wells …}` with the comment "the member keys here are the group's own, not the tab's". The decoration reuses that same map. So the rule is shared and only the key-space *provider* differs: `plot_members.variable_values_for(vset, variable_id, wells_by_key)` is the pure rule, `member_variable_values` is it over `member_wells(ctx, scope)`, and the batch path is it over the group's own members — one definition of what a member's value is, two answers to which members are drawn. `variable_series.inks_for` / `bands_for` take resolved values for the same reason, so a batch figure and the tab it mirrors cannot disagree about what colour `HeLa` is. **Four mutations, and the first round of tests caught none of them.** The batch collector inks its members from the *same* `WELL_COLORS`, in member order — so with the values assigned in member order the decorated and undecorated inks are byte-identical, and deleting the decoration call outright left all five new tests green. Asserting "the colours differ" was the error: the batch palette already differs per member. The tests now assert the **exact** expected ink from `value_colors`, over a fixture whose values are assigned *against* the member order so the two are distinguishable at all. §7.4 #1 — assert the outbound value, not a property many values satisfy — and the third time this session that a distinctness check stood in for a real one. |
| 36 | F | Review gate: one annotation model, no premature generality | ☑ | Ran on `163177f`; all findings closed. **Three HIGH from one cause: the sweep was half done.** There are *two* raw pooling functions and only `active_replicate_sets` was converted, so four `replicate_sets_loaded` sites kept pooling by the user's groups — Batch Export wrote the identical whole-plate figure into every group's file, and the Heat Map averaged the whole plate into each dose cell while labelling them all with the first group's name. §7.5 #5 exactly. Fixed with `plot_replicate_sets_loaded`; the guard now covers both names and exempts **functions** rather than files, which is what hid `member_replicate_sets`. Third HIGH: a dataset swap replaced the state and left the tab painted — the `ratios.py` bug's screen half — now repainted through a `variables.refresh` service the reset scope shares. Also closed: an order-dependent `roll_up` printing two sentences for one plate (one with a wrong count); the reset scope leaving `group_by_variable` set; the last variable's chips surviving live-but-inert; a false ARCHITECTURE sentence about `ControlRef`s; `replicate_display_label` turning a dose of `10` into `A01,A03` (`name_is_verbatim` — §7.1, a predicate outside its domain); the shortfall reaching only a transient status line (now the figure title, which survives Save); duplicate variable ids and NaN accepted by `from_block`; no `create_missing` on the write, so an imported plate's whole grid went unwritten; and two seams row 31 added for row 32, removed per D's precedent. **One defect the gate did not find and a test of mine hid**: `draw_response` called `apply_ax_style(ax)` against a three-argument function, so every real call raised `TypeError` — invisible because the tab tests stubbed that helper. §7.3 #1. The renderer is now exercised against the real helper. |
| 37 | G | `features/response/` package + registry entry | ☑ | `collect.py` / `response_compute.py` / `renderer.py` / `tab_view.py` / `controller.py`, registered as a Plotting leaf with its own `_response_fig` pref scope so a log axis set here cannot leak onto Distribution. Deliberately **absent from the `plotting_only` profile**: its x is a Number variable declared on Home → Variables, a pane that profile does not ship, so the tab could only ever show an empty state pointing at a page that is not there. Thirteen mutations watched failing across the two test files. One real defect found by a failing test of my own: exporting with no X chosen wrote an empty CSV, which is indistinguishable from a plate that had nothing to say — it is refused with the fix named. |
| 38 | G | X combo (scope zone) listing Number variables | ☑ | `_response_x_cb`, added through `band.add_scope`, so `ControlBand._restack_trailing` keeps it ahead of the shared Threshold without this builder having to be called in any particular order — the placement rule is `tests/test_control_band_threshold_placement.py`'s, not a second copy here. Only `NUMBER` variables are listed: a Text variable groups perfectly well but has nowhere to sit on a numeric x, and offering it would order the axis by the order someone typed the values in. A plate with no Number variable gets a disabled combo carrying the sentence that names where to make one, rather than an empty dropdown that reads as a broken tab. Four mutations watched failing. |
| 39 | G | Timepoint selection, defaulting to `focus_timepoint` | ☑ | The Bar tab's checkbox menu over this tab's own `ResponseState.tp_selections`. Seeded from the shared `focus_timepoint` and **never writing it** — writing would drag Heat Map and Distribution to whatever this tab happened to open on. Seeded with one timepoint rather than all of them: twelve overlapping curves per condition is not a plot anyone can read. A selection the user has already emptied is left empty; timepoints the newly opened dataset lacks are dropped and the rest kept. Four mutations watched failing, after the first pair of tests were found vacuous: both asserted `focus_timepoint` still read 24.0 on a fixture whose seed *was* 24.0, so a tab that wrote the field wrote the same number back and nothing noticed. The replacements put the shared focus somewhere the seed cannot reach. |
| 40 | G | Log-x default from the data; vehicle control off-scale | ☑ | `resolved_log_x` asks the user first and the data second, and the two are told apart by `plot_style.figure_pref_is_set` — added for this, because `figure_prefs` returns an unset key as its neutral default, which makes "the user turned log off" indistinguishable from "the user has never touched it" and only the second is the tab's to decide. Unset, the axis goes log once the positive doses span two decades, the point at which a linear axis puts three quarters of the points in its leftmost tenth. A zero dose is drawn one decade below the lowest real dose behind a dotted axis break, with its tick reading `0`: dropping it is matplotlib's default and a silent substitution, and squeezing it onto the axis at some small positive number is a lie about the concentration. `_aw_force_xlog` pins the scale against `apply_export_style_prefs`, which re-reads the shared `x_log` on every restyle. Four mutations watched failing. |
| 41 | G | Omitted groups named in the figure and the CSV | ☑ | **Two places, not the three this row originally asked for** — the gate took the status line off it and the row is corrected rather than left reading true. `notify.set_status` writes the *persistent* bar and a successful redraw has no reason to write anything, so the sentence outlived its condition: fill in the missing dose, the figure's caption clears, and the bar goes on reporting it, contradicting the plot above with no redraw able to correct it. ARCHITECTURE already recorded exactly that for the bar tab's significance note, and this plan asked for the status line anyway. The two that remain each survive something the other does not: the caption travels with a saved PNG, and the CSV row survives being opened in a spreadsheet where someone counts rows. The three omission reasons stay distinct — not filled, wells disagree, value is not a number — because each has a different fix. A figure with **nothing** on it draws `collect.empty_reason` in place of the axes, with the ticks and frame removed so it reads as a message rather than as a draw that failed; six causes, six sentences, each naming the pane the fix is made on, and `test_every_cause_reads_differently` is what stops them collapsing into one generic line that would satisfy every other test in the group. `ResponseData.placed` exists so that "no group has a dose" and "every group has a dose and none was imaged then" stop arriving as the same zero. Eight mutations watched failing; the eighth found a real gap — deleting the line that fills `placed` left the whole lane green, because every test of that field built the dataclass by hand instead of driving the collector. |
| 42 | G | Fit popover: 4PL / Hill, fitted in log space | ☑ | `response_compute.fit_response` — Qt-free, ctx-free, driven by two lists. Fitted in log10(x) for the reason `fit_space` gives: decade-spaced doses fitted in linear x weight the top dose almost exclusively, so the fitter ignores the half of the curve that decides the EC50. Recovering an EC50 of 0.03 from a 0.01–30 series to a tenth of a percent is the test that only passes in log space. `Hill` is the same curve with its bottom pinned at zero — pinned, so it costs a degree of freedom rather than consuming one, and a caller that passes a bottom is overridden rather than obeyed, because a Hill fit with a bottom of 40 is a 4PL wearing the wrong name. The model and the two optional plateaus are one set for the whole figure: two timepoints fitted with independently floating tops give two EC50s that cannot be compared, and comparing them is the only reason to overlay them. The interval is symmetric about `log_ec50` and therefore asymmetric in the units the user reads, which is the truth about a dose estimated on a log axis; a single ± would claim a lower bound the fit does not support. The EC50 rides in its curve's own legend entry so two overlaid timepoints cannot have their numbers swapped by a reader, and again in the plot-data CSV. Eleven mutations watched failing, including both halves of the popover's own wiring. |
| 43 | G | Non-convergence states itself and draws no curve | ☑ | Five refusals, each with its own sentence, because "dose more", "dose wider" and "these doses cannot separate the parameters" have different fixes: too few distinct doses (naming how many the model needs), a flat response, a parameter that ran to the edge of its range, an EC50 outside the doses tested, and an optimiser that did not converge. `test_every_refusal_reads_differently` is what stops them collapsing into one line. A refused fit draws **no** line — `curve_y` returns `None` rather than an array, so a caller cannot accidentally draw a flat line across the floor — and its legend entry reads `EC50 — (no fit)` with the reason in the caption, which travels with a saved figure. Running the fitter rather than reading it found the defect this row is really about: a flat eight-point series fits a 4PL *perfectly* with top equal to bottom, at which point `log_ec50` is unconstrained and `curve_fit` hands back the initial guess — it printed `EC50 0.548 µM` from eight measurements that showed no response at all. A second, quieter one came with it: the "no CI" clause said "too few doses" for a singular covariance on eight points, naming a cause it had never checked, so the reason is now carried from where it is decided. |
| 44 | G | Fold change with its own scope; `vs t0` only above one timepoint | ☑ | The shared popover, over the shared state, through the **same call the Line tab makes** — `fold_change_pairing.normalize_member_pts`, handed AggPoint-shaped tuples. Nine zeros a point buys immunity to §7.5 #8's failure: the last time a caller reimplemented "the same" division with one extra `isinstance` check it turned a `numpy.float32` spread into no error bar at all, in the most numerically sensitive module in the app. The collector's loop is inverted for it — a member at a time rather than a timepoint at a time — because `vs t0` divides a member's whole series by its own baseline and a series has to exist before it can be normalised. `vs t0` is declined and **said** below one-plus timepoints rather than applied: with a single timepoint every point is its own baseline and the figure would be a flat line at 1.0. The suffix reaches the y label, because the state is shared and this axis can become normalised without anything on this page being touched; points the control could not be resolved for are named per (member, timepoint), and a point whose spread came back undefined is counted rather than drawn as a zero-length bar reading as perfect agreement. The scope is the feature descriptor's own `fold_change_scope_factory`, so removing the feature drops it. Nine mutations watched failing. |
| 45 | G | No un-pooled mode — said in the tab, not failed quietly | ☑ | A dose is a property of a condition, so one point per well at the same x would be a scatter of replicates rather than a curve. With `bar_pool_replicates` off the tab pools anyway and puts that on the figure, because the Bar tab a click away *is* drawing one bar per well and the two would otherwise disagree with nothing on either to explain it. The half that was nearly missed is not the collector but `plot_members._keys_are_replicate_sets`, which decides what a *key* resolves to when the per-sample control editor asks: it tested `scope == "line"` and so answered *wells* for every scope it had not heard of — and a set named `CTRL` is not a well, so the key resolved to nothing at all and the whole fold-change axis went quiet on a well-formed plate. Caught by mutation, not by review: reverting that one line left the whole response suite green, because nothing exercised the response scope's key resolution. Named set now, with five tests in `test_plot_members.py` and two more mutations watched failing. |
| 46 | G | The refusals name the user's own variable, never "dose" | ☑ | Found by the §6.1 #6 reconciliation, reading G's own naming rule back against the tree rather than against the tracker: *no control says "dose", "concentration" or "EC50" unless the user typed it — except the fit's own readout.* Every one of the fitter's five refusals said "dose", so a user who varied seeding density, MOI or temperature was told "no dose changed the measurement" — a caption describing a plot they did not make. `fit_response` takes an `x_name` and the collector passes the variable's own; the default is `x`, not `dose`. The scale notice and the Fit tooltip went the same way. Three mutations watched failing — and the last of them found a test of mine that had been red since the rewording, because I re-ran the two files I had edited and not the third that asserted the old string. |
| 47 | G | The tab builds in the real app, and a builder that raises is caught | ☑ | **The Response tab had never built.** `build_plot_save_menu`'s `cells_cb` is keyword-only and required — deliberately, so "a tab cannot acquire this menu without offering the cells the plot was drawn from" — and it was never passed, so `build_response_tab` raised `TypeError` on every real app construction from the moment the package existed. `centre_view` catches a failing builder, logs it and returns, so the tab rendered as an empty page: indistinguishable from a tab with nothing to show, and *removed from the pending map either way*, which is why `test_drain_builds_all_centre_tabs` passed throughout. About 130 tests of this tab's own were green the whole time, because every one drove its parts through a stub ctx and **not one called the builder** — §7.6's lesson, and the third time this session the convenient seam sat underneath the thing that is actually wired up. The fast lane cannot see it at all: it never builds an app. Fixed with `features/response/cell_scope_compute.py`, which is Line's scope except that it carries the **ticked** timepoints — Response draws only those, and an export writing twelve beside a figure showing one is the mismatch a cell-scope exists to prevent. `test_app_smoke.py::test_no_tab_builder_raised_while_draining` is the guard, and it is cheap — 2 seconds — because it reads the log the swallow already writes rather than building anything extra. Watched failing against the real defect, reporting the `TypeError` by name. Two stale assertions in `test_home_page.py` went with it: F row 26 added the **Variables** sub-tab and both of that file's Home rosters still listed six; `runtime.home.built` confirms Variables is eagerly built, so the contract held and only the test was behind. |
| 48 | G | Review gate: a leaf tab, not a plotting framework | ☑ | Ran on `ea2a381`. **Verdict on over-architecture: not over-architected** — five modules, one figure, and every rule that could live in a signal handler lives in `collect` or `response_compute` instead, where a list-driven test reaches it. Nothing here is a framework: there is no plugin seam, no second model of a member, no generality beyond the two fit models the plan named. Five findings, all closed. **Three HIGH, and all three were the same shape — a leaf that had not joined the app.** The tab carried no `on_activate` and no `on_selection_changed`, and the generic `redraw_app` fan-out reaches only Distribution and Heat Map: a body built while the plate was empty stayed empty, and selecting different wells left the curves drawn from the previous selection — a plot of the wrong data that looks exactly like a plot of the right one. The third: no `view_state_io` block, so a saved response plot restored with no curve, no EC50 and whatever timepoint the picker seeded, looking in every respect like a complete restore. That is §7's cardinal sin reached by omission, and it was invisible because *nothing was broken* — every part worked, and none of them had been connected. **One MEDIUM, and it was a rule already written down.** The shortfall was pushed to `notify.set_status`, which writes the *persistent* bar; a successful redraw has no reason to write anything, so filling in the missing dose cleared the figure's caption while the bar went on reporting it, contradicting the plot above with no redraw able to correct it. ARCHITECTURE records exactly this for the bar tab's significance note, and I wrote the row-38 plan asking for the status line anyway. It is off it now; the caption and the empty state cannot go stale, because the redraw that resolves the condition is the one that clears them. **One LOW, explained rather than removed**: an `x_log` derived from the doses leaves the Properties checkbox reading *off* beside a logarithmic axis. Seeding the preference to make them agree would spend the distinction `figure_pref_is_set` exists for — once set, the axis can never re-derive for a plate with different doses — so the caption says which it is and where to override it. **A test of mine was measuring nothing, again.** The fixture's X combo had no signal connected, so every test that changed X tested `refresh_x_combo` alone and the restore's `blockSignals` was unfalsifiable: the mutation that removed it left the whole file green. §7.6 #2 exactly — the convenient seam sat underneath the thing that is actually wired up. The fixture connects it now, and the mutation bites. Twelve mutations watched failing across the gate's fixes. |
| 49 | H | Effect size + CI on every pairwise comparison | ☐ |  |
| 50 | H | Omnibus gate (one-way ANOVA / Kruskal-Wallis) | ☐ |  |
| 51 | H | Dunnett vs a named control | ☐ |  |
| 52 | H | Paired / repeated-measures tests (blocked on F) | ☐ |  |
| 53 | H | Two-way ANOVA over two variables (blocked on F) | ☐ |  |
| 54 | H | Review gate: tests added, not a statistics layer | ☐ | |
---

## A — Scale bar: refuse rather than clamp

**The defect.** `image_compose.draw_cell_chrome` (`well_viewer/features/image_browser/image_table/image_compose.py:889`) clamps the bar to the image:

```python
blen = int(min(bar_px, W - 2))
```

Measured, with `pixel_size_um=0.65` and `scale_bar_um=50` (so `bar_px=77`):

```
crop W=2048  asked 77px (50.0um)  drawn 77px = 50.1um
crop W=  60  asked 77px (50.0um)  drawn 58px = 37.7um   <-- silently wrong
crop W=  40  asked 77px (50.0um)  drawn 38px = 24.7um   <-- silently wrong
```

A wrong number burned into a figure, with nothing on screen or in the log saying
so. This is §7's cardinal case and the same shape as the PowerPoint clip-cap bug:
the exporter producing a value the data never had.

Everything else about the bar was checked and is correct — `bar_px` is computed
once per Generate in source pixels, burned in before any thumbnail or export
downscale, nothing resamples in between, and the live and export paths share the
helper. Only the clamp is wrong.

**The fix.** Draw nothing when the bar cannot be drawn at its true length:

- `draw_cell_chrome` returns the bar's outcome rather than swallowing it. It is
  already a pure array function, so the cleanest shape is for it to *not* draw
  and for the caller to learn why — return the RGB plus a reason, or take an
  out-parameter set as `frame_load` does with `scan_misses` / `raw_fallbacks`.
  Prefer the latter: that pattern is established in this package and the callers
  already thread such sets.
- The threshold is `bar_px > W - 2 * margin`, not `W - 2`, so a bar is refused
  before it touches the edges rather than after.
- **Report it where the pass reports its other shortfalls.** `image_compose`
  already carries `partial_scan_report` and `reset_scan_misses` per Generate, and
  `state.image_table.partial_scans` holds one. Add the same shape for
  `scale_bar_refused`, and have the Image Table's status line name the count and
  the cells. The export path (`export.py:463`) reports through its own return.
- The user-facing sentence must say what to do: the bar is longer than the cell,
  so either shorten *Bar length µm* or widen the crop.

**Tests.** `tests/test_image_table_scalebar_render.py` gets the geometry cases as
pure-lane tests — they need no app, only the array function:

- a bar that fits is drawn at exactly `round(um / px)` pixels, at three cell sizes;
- a bar that does not fit is **not drawn at all**, and the refusal is recorded;
- the refusal reaches the status line (the existing app-level test can assert this).

Prove the guard the way §7.3 asks: run the new geometry test against a worktree of
`origin/main` with `scripts/prove_guard.sh` and require an `AssertionError` naming
the drawn length, not an `ImportError`.

**Note for the reader.** That file's one real render test is `slow`-marked, so it
did not run in the fast lane while the bug was live. The new cases are pure on
purpose.

**Scope note.** `pixel_size_um` is one global value per grid, so a table mixing
magnifications gets one wrong bar for the other magnification. Out of scope here;
recorded as a known limit. The proper fix is reading `PhysicalSizeX` out of the
OME metadata the converter already parses, which belongs with a pipeline change.

---

## B — Coverage measurement

**The problem.** `pytest-cov>=4.0` is in `tests/requirements-dev.txt:31` and
`--cov` appears in neither `scripts/test.sh` nor either workflow. With ~495
source files and ~719 test files, nothing knows which application modules no test
reaches.

**Not a gate on a number.** A percentage threshold over a repo this size is a
number to game, and this codebase's real risk is not thin coverage but *absent*
coverage in specific places. Three moves, in order:

**B1 — Measure both lanes and combine them.** `ALLWELL_COVERAGE=1` in front of
either lane's script; `scripts/coverage.sh` combines and reports. Parallel mode
is on in `[tool.coverage.run]`, which is what lets the slow lane's many short
processes each write their own data file instead of overwriting one another.

**Measured, and it decides the CI design.** The fast lane takes **11 min 06 s**
under coverage against ~3.5 min without — three times the cost of the check that
gates every push, against a job capped at 15 minutes. So coverage does not go in
the gating job. `.github/workflows/coverage.yml` runs it on demand and monthly
on `main`, and states in its own header what adding `pull_request:` would cost,
so the trade is visible rather than discovered.

This step is load-bearing and easy to get wrong: measuring the fast lane alone
reports every full-app-only module as dead, and acting on that list would delete
live code. Do not produce a report until both lanes are in it.

**B2 — Differential coverage on the diff.** Not "is the repo 80% covered" but
"did this branch add lines no test executes". `scripts/coverage.sh --diff` runs
`diff-cover` against `origin/main`, locally and free.

It is **advisory and local rather than a PR gate**, for two reasons that
compound. The slow lane does not run on PRs, so a PR whose code is genuinely
covered by a full-app test reads as uncovered — a check that cries wolf gets
muted. And per-PR coverage costs a second full lane run at three times the
normal price. Turning it into a CI check is one line in `coverage.yml` once
those two are addressed.

**B3 — The one-off inventory.** Produce, once, the list of application modules
with zero coverage from **either** lane, and triage each into: a test that
should exist, dead code to delete, or genuinely unreachable and worth a comment
saying so.

**The finding, from both lanes in one measurement.** Slow lane #36 at `463722f`
(six shards green, 54 batch data files) combined with the fast lane at the same
head: **76,964 statements, 79.8% covered, and five modules with no executed
lines.**

| module | stmt | verdict | why |
| --- | --- | --- | --- |
| `all_well_launcher.py` | 74 | genuinely unreachable | The PyInstaller entry point. Importing it installs a `setuptools._distutils.compilers` stub and runs the `_MEIPASS` bootstrap, which would reshape the interpreter for every later test in the process. Guarded at source level instead — `test_bootstrap_frozen_dispatch.py` and `test_profile_meipass.py` read its syntax tree through `_repo_scan`. The comment B3 asks for is now in its docstring. |
| `widgets/color_picker_popover.py` | 310 | **test written** | `tests/test_color_picker_popover.py`, 40 tests, 96.1%. Reached only from `ColorSwatchRow._open_picker`, so it was one level deeper than the row below. Writing it found a defect: `_on_recent_picked` went through `setColor`, which moves the committed baseline as well as the colour, so the `_commit` right after it compared the colour against itself and returned — clicking a recent could never emit `colorCommitted`, which its own docstring promises. Nineteen mutations watched failing. |
| `widgets/color_swatch_row.py` | 213 | **test written** | `tests/test_color_swatch_row.py`, 30 tests, 93.9%. Three live call sites — `cell_browser/tab_view._cb_pick_color`, `review_image/controller.pick_review_image_color`, `saved_selections_list._on_recolour_requested` — each opening a `Popover` from a button press: §7.6's shape exactly, offered everywhere and driven nowhere. The presses are real `QMouseEvent`s at coordinates the widget's own geometry decides, because which tile a click lands on *is* half the behaviour. Half the file reads pixels back, since the selection ring is the one piece of state a user has nothing but the screen to read. Fifteen mutations watched failing. |
| `widgets/chip_group.py` | 89 | **test written** | `tests/test_chip_group.py`, 20 tests, 98.9%. Presses go through `QToolButton.click()` rather than `setCurrentIndex`, so cutting the `idToggled` wiring fails nine of them instead of none. Seven mutations watched failing. **Still owed, and not this row's to fix:** its one call site, `saved_selections_list._build_readonly`, wraps the import in `try/except Exception` and falls back silently to a comma-joined `QLabel`, so a broken `ChipGroup` degrades to plain text with nothing said — §7's rule, in a module this row does not own. |
| `well_viewer/persistence/bar_groups.py` | 32 | **test written** | `tests/test_bar_groups_load.py`, 14 tests, 100%. It could not be reached however hard the suite tried — its only route is `runtime_app._bar_load_groups` → `load_via_dialog`, and the autouse `_no_modal_dialogs` makes every dialog raise — so the tests take ARCHITECTURE's own escape and replace the module's `QFileDialog` and `QMessageBox`. The state under test is the real `AppState`, not a namespace shaped to match what the code happens to read. Eight mutations watched failing; the last of them is why the redraw spy records *what the state held when it was called*, since a counter cannot tell "applied, then redrew" from "redrew, then applied". |

**Nothing on the list is dead code**, which was the dangerous reading and is now
settled twice over — by a static pass showing every module has a live
application importer, and by the measurement itself.

**The fast-lane-only list was wrong, and wrong in the direction that invents
dead code.** It named eleven modules and 1,860 statements. Six of those eleven
turn out to be reached: `core/redraw_coordinator`, `widgets/gradient_strip`,
`widgets/lut_selector`, `widgets/saved_selections_list` and
`widgets/search_input` fall out on adding a *single* slow-lane file
(`tests/test_app_smoke.py`, six tests), and `widgets/kbd_hint` on adding the
rest of the lane. Acting on the partial list would have deleted live code in
five of eleven cases. This is the trap B1 names, met in practice rather than in
theory.

**Getting both lanes into one measurement is itself two dispatches**, and how to
run them is in ARCHITECTURE.md's coverage section: dispatch **Slow lane** with
`coverage` ticked, then **Coverage inventory** with that run's id, and read the
list out of the job log.

**Pair with mutation testing, scoped — measured, and declined.** The proposal
was `mutmut` over `well_viewer/domain/` and `pipeline/` only, on the grounds
that both are Qt-free and pure "so mutants run in the fast lane's budget". That
premise is wrong, and by two orders of magnitude: the two trees are **6,979
statements** (4,042 across 36 files, 2,937 across 21), which is roughly
7,000–14,000 mutants at mutmut's usual one-to-three per mutable statement, each
needing its own test run — 10–19 hours at an optimistic 5 s per mutant, 39–78 at
a realistic 20. Per module is barely better where it would matter most:
`domain/fold_change.py` alone is 829 statements, about nine hours, and it is the
module §7.5 #8 names as most worth mutating. `mutmut` 3.7.0 installs cleanly
against the pins, so this is a cost decision rather than a feasibility one. Row
11 carries it, and C row 12's argument applies again: a lane nobody runs is this
repository's largest existing gap rather than a fix for one.

The need underneath it is real and is met elsewhere. §7.7 #2's ritual — name the
edit that would break it, make it, watch a test fail — is run at the point of
change, where the mutation is chosen by judgement rather than by enumeration;
`scripts/prove_guard.sh` mechanises the half that has a base branch to compare
against.

**Housekeeping found while reading.** `.github/workflows/ci.yml`'s header comment
said the fast lane is "~1,770 tests, ~60s". Re-measured when row 10 closed it:
**7,681 collected, ~5 min 50 s** — the lane grew more than fourfold underneath a
comment nobody re-read, and that comment is what a reader consults to decide
whether the 15-minute job cap still has headroom. The correction carries the
two-second command to re-measure, so the next reader can check rather than
trust.

---

## C — Pinning performance

**The problem.** The repository has fought and won a series of large performance
battles, every one of them recorded as a measurement in `ARCHITECTURE.md` and
none of them pinned by anything: the 70 s → 0.2 s stylesheet call, 1091 ms →
180 ms aggregation over 96 wells, 3.57 s → 1.06 s colony detection, 327 MiB →
135 MiB per frame, 1729 → 867 MiB peak on load. `test_the_fast_lane_stays_fast.py`
guards the harness, not the app. Each of those is free to regress in silence.

Three tiers, cheapest and most durable first.

**C1 — Assert the complexity, not the clock.** Most of the wins were complexity
fixes, and a complexity fix can be tested deterministically: run the function at
sizes *n* and 2*n*, count the operation the fix removed, and assert the ratio is
sub-quadratic. This is immune to machine speed, runs in the fast lane, and fails
loudly on precisely the regression that happened. The four to cover:

- `spatial_compute.detect_colonies` — the DBSCAN frontier set (was quadratic in
  cluster size);
- `stage_layout` first-visit extraction — the `c not in cells[:i]` shape;
- `pipeline.stardist.nucleus_seed_markers` — one whole-array pass, not one per
  nucleus (count array traversals with a wrapped ndarray);
- `data_loading.aggregate_with_threshold_df` — the string-timepoint fallback must
  not run when the numeric column is complete (assert the fallback is not called).

The last one is the model for the others: what regressed was not time but
*whether an expensive branch is entered*, and that is directly assertable.

**C2 — A benchmark lane with in-repo baselines. Declined (`⊘`).**

The proposal was `pytest-benchmark` with committed baselines, run deliberately
rather than on PRs. Building it surfaced the argument against, and it is the
repo's own: **a lane that only runs when someone remembers is the largest gap
already on this list.** §10.4 records that the slow lane's cron was removed and
that nothing now notices a full-app test going red except a person choosing to
look. A benchmark lane on the same footing would be a second one — and unlike
the slow lane it would have no correctness signal to make anyone open it.

It would also be the flakier kind of check. CI here is one Ubuntu runner shared
with everything else; a wall-clock band tight enough to catch a 1.5x regression
is loose enough to miss most real ones and tight enough to fire on contention,
and a check that fires on the weather gets muted.

What replaced it is C1 and C3, and the substitution is not a downgrade for the
regressions this repo has actually had. Every one of them — the quadratic
frontier, the list-prefix scan, the pass-per-nucleus, the unconditional string
strip, the string-per-cell read — is a *countable* change in what the code does,
not a change in how fast the machine is. Counting them is exact, identical on
every machine, and runs in the fast lane for free.

The gap this leaves is honest and worth stating, and it is wider than "a pure
constant factor". Two shapes get through:

A constant-factor slowdown that changes no operation count and no allocation
passes both files. Nothing in this repository's history is that shape, and if
one appears the answer is to find what it made more of and count that.

Worse, **a complexity regression is invisible to counting whenever the
complexity lives inside a single library call.** Both `_dbscan` and
`nearest_neighbour_distance` build a `cKDTree`; replacing that with
`scipy.spatial.distance.cdist` or a broadcast `np.linalg.norm` reads as a
simplification, keeps every Python-level count identical — the frontier check
still sees one `set()` call — and is O(n²) in time and memory. The memory guard
is scoped to `load_well_csv` and sees none of it. The same shape covers a
`df.apply(axis=1)` replacing a vectorised pandas expression, which in this
codebase is the likeliest performance regression there is.

**The middle option was not considered and should have been.** Between an
absolute wall-clock band and operation counting sits a self-normalising timing
check: time the same operation at *n* and *2n* in one process and assert the
*ratio*, which cancels machine speed and contention and is exactly the
two-input-size shape C1 already builds. It is passed over here, not overlooked
— a ratio is still a wall-clock measurement, and on a shared runner the two
halves can be scheduled differently enough that the ratio itself moves. It is
the right thing to reach for if a regression of either shape above ever lands,
and it would go in a lane of its own rather than into the fast lane.

**C3 — Memory has its own gauge.** `tracemalloc` peak around `load_well_csv` for
a fixed synthetic plate, asserted against a ceiling with headroom. This is the
1729 → 867 MiB win, and unlike timing it is stable across machines. It also
becomes the acceptance test for workstream D.

**Explicitly out of scope.** Do not try to pin the 70 s stylesheet number — that
one is guarded structurally by the widget reaper, which is the right level.

---

## D — Parquet storage (no DuckDB yet)

**The goal.** Remove the CSV parse from the load path and make it possible to
read a subset of columns, without changing the query engine, the aggregation
functions, or any collector. This is the half of the storage change that is
independently valuable and commits to nothing.

**Why it pays.** A plot reads roughly 7 of 37 columns (`Included`, `QC_Pass`,
`Crop_Pass`, `Gate_Pass`, `area_px`, `timepoint_hours`, the value column, plus
`fov` under per-FOV spread). Today all 37 are parsed from text and held resident
per well, for every well ever touched, for the life of the session. Parquet is
columnar and typed: the parse disappears, the dtypes and categories survive on
disk, and `pd.read_parquet(path, columns=[...])` reads only what is asked for.

**The design.**

- **The CSV stays the source of truth.** Parquet is a cache, written beside the
  dataset at `out/.aw_parquet/<well>.parquet`, dot-prefixed so every dataset scan
  in the app already skips it (the same convention as `.allwell-transit`).
- **Invalidation is a fingerprint, not a timestamp comparison alone.** Store the
  source CSV's `(size, mtime_ns)` in the Parquet file's own key-value metadata and
  compare on read. A mismatch re-converts. This is the same shape as
  `cell_overrides.json`'s `seg_fp` stamping and fails the same safe way.
- **Conversion is `load_well_csv`'s existing output**, written with
  `to_parquet`. That is what keeps the two paths byte-identical by construction:
  the typed read, the renames, the FOV normalisation, the categoricals and the
  circularity backfill all happen exactly once, in the function that already does
  them, and Parquet stores the result. Do not reimplement the read.
- **The write is atomic**, through the existing convention: a staging file beside
  the destination and `os.replace`. `tests/test_dataset_tool_integrity.py` scans
  for the truncating shapes and will fail a bare `to_parquet` onto the live path.
- **A conversion that fails is not fatal.** Log once per dataset and fall back to
  the CSV path for the session. A read-only output volume is an ordinary state
  (the run journal already handles it that way) and must not cost the user their
  dataset.

**The column-projection half is the actual win, and it is a separate row.**
Writing Parquet alone changes load time and nothing else, because
`WellRepository.rows(label)` returns a whole frame and every caller expects one.
To read fewer columns, the callers have to say what they need:

- `aggregate_with_threshold_df` already names its columns — `val_col`, `tp_col`,
  `area_px`, the gate columns, the four verdict columns — so the set is
  derivable from its arguments rather than guessed.
- Add `WellRepository.rows(label, columns=None)` and a small resolver that turns
  an aggregation request into a column list. `None` keeps today's behaviour.
- The cache must then be keyed on the column set, or a narrow read must not
  displace a wide one. Simplest correct rule: the cache holds the widest frame
  requested so far for a well, and a narrower request slices it. That keeps one
  entry per well and never re-reads for a subset.

**The trap.** The verdict columns are *written into* the cached frame by the
projectors after load, and by the gating worker later. A Parquet file must never
be written from a projected frame — it would bake one session's QC verdict into
the cache and serve it to the next session under different parameters. Convert
from the output of `load_well_csv`, before `load_and_project` runs its
projectors. This is the single most important rule in this workstream and is
worth its own guard test.

**Acceptance.** C3's memory gauge, plus a byte-equality harness: for a corpus of
real plates, every column of the CSV-loaded frame equals the Parquet-loaded frame
including dtype and category set, and `aggregate_with_threshold_df` returns
identical `AggPoint` tuples from both. Per §7.5 #8, get the corpus from real
datasets, not from fixtures written for this test — and say in the PR body which
plates it ran against.

**Explicitly not in scope.** DuckDB, any change to `aggregate_with_threshold_df`,
any change to the collectors, and the unbounded `StatsCacheState.cache` (worth
fixing, but it is a memo-eviction bug rather than a storage one — file it as its
own item when this lands).

---

## E — Recent datasets on Home

**Where the list lives.** `QSettings` (`allwell.py:1559` already has the handle
for window geometry). Not the dataset folder: a list *about* many datasets cannot
live inside one of them, and a dataset copied to a colleague must not carry the
author's history of other plates.

Store a capped list (10) of `(path, display_name, opened_at)`. The display name
is `domain/dataset_name.dataset_display_name`'s answer, stored rather than
re-derived — `collect.py` may not touch the filesystem, and re-deriving would
need the `in/` sibling check. Deduplicate by resolved path, most recent first.

**Where it is written.** `load_controller.load_path` (`:123`), on success only. A
folder that opened and yielded nothing readable is not a dataset the user wants
offered back to them.

**The design problem, and it is the whole feature.** Home's body is wrapped in
`ui_helpers.wrap_with_empty_state`, so before a dataset is open the user sees the
shared "Load a directory." placeholder and *not* Home. The one moment a recents
list matters most — launch, nothing loaded — is the one moment Home is hidden.

Two candidate answers:

1. **Put the recents on the placeholder.** `EmptyState` already supports one
   action (`setAction`, which Home is the only user of). Extend it to accept a
   list of secondary entries, and Home passes its recents. Contained, and the
   list appears exactly where the user is looking on launch.
2. **Give Home a no-dataset state of its own**, replacing the shared placeholder
   for this one page.

Prefer (1). (2) means Home stops sharing the wording and the swap moment with the
twelve other wrapped tabs, which is the property `core/empty_state_copy.py` exists
to protect, and re-introduces the drift that module was written to end.

**Where it is shown once a dataset is open.** A card on Home Overview, built
exactly like the saved-plots strip: `collect.recent_datasets(ctx)` returning
plain data, `_build_recents(ctx, page, reg)` in `tab_view.py`, tiles that are
`QPushButton`s with child labels. That precedent is already load-bearing and
tested; follow it rather than inventing a second card idiom.

**Two rules the strip must honour.**

- **A path that no longer exists is shown, not hidden.** An external drive that
  is not mounted is the commonest reason, and silently dropping the entry tells
  the user their history was lost. Show it disabled with the reason on the
  tooltip. Checking existence is a filesystem call, so it happens in the *view*,
  not in `collect.py` — the same split the saved-plot thumbnails already use
  (`collect` returns a path, the view loads the pixels).
- **Clicking one goes through the existing open path**, `run_home_action(ctx,
  "open")`'s sibling, so a recent open and a browsed open cannot diverge.

**Tests.** The store is pure and belongs in the fast lane: capping, dedupe by
resolved path, ordering, a malformed `QSettings` value read as empty rather than
raising. The card itself gets one full-app test asserting a click loads.

---

## F — Sample variables (the two-factor plate map)

**What this is.** Named annotation axes the user declares per plate — `Cell line`
with values `HeLa` / `U2OS`, `Dose` with values `0 … 10 µM` — assigned to wells.
It is the foundation for G (a numeric variable becomes a plot's x axis) and for
two of H's tests.

**Where it lives.** A new Home sub-tab, **Variables**, between Groups and Well
Labels: `well_viewer/features/home/variables/`, registered in
`features/registry.py` with `removable=True`, `reads_well_selection=False`, and
**its own plate in the tab body** rather than the rail plate (the
`features/home/plate_panel.py` precedent).

Home is the page where the user declares what the wells mean, so this belongs
there. It cannot extend Groups: `groups/panel_view._commit_plate_to_current`
guards on the current sub-tab being "Groups" precisely because a second meaning
on the rail plate silently rewrites memberships, and ARCHITECTURE already records
the "two plates meaning different things" hazard. It cannot extend Well Labels
either — that grid is free text feeding legends, and parsing `key: value` out of
free text is §7.4 #2's mistake, with no type and no value ordering.

**Values are objects, not typed strings.** A variable carries `name`, `type`
(`text` | `number`), `unit` (numbers only) and an **ordered list of values**.
That list is what makes renaming a value rewrite every well holding it, and what
stops `1uM` / `1 µM` / `1` accumulating as three distinct doses.

### The storage decision, and the two reviewers disagreed about it

The plotting reviewer proposed an `x_value` field on each `Selection` — one
number per group. The annotation reviewer proposed a per-well store with the
group acting as a brush. **Take the per-well store.** Both of the claims that
decide it were checked against the tree:

- **Groups partition the plate.** `selection_controller.sel_strip_wells`'s own
  docstring is "A well belongs to ≤1 group", and `sel_set_composition` calls it
  with `keep_id`. So a per-well value always rolls up to a group unambiguously,
  while the reverse fails — ungrouped wells exist and a group-keyed store cannot
  annotate them at all.
- **`make_selection` is closed.** Its signature is keyword-only and it builds a
  fresh dict; an `x_value` kwarg raises, and one smuggled into the dict is dropped
  the next time any of the five call sites rebuilds the selection. The
  forward-compat carry-through at `selections_model.py:390` belongs to
  `validate_repair`, not to `make_selection`. Adding the field properly would mean
  touching `make_selection`, `validate_repair`, `to_block` and `from_block` — and
  would still be the wrong key space.
- **`bar_pool_replicates` flips the key space** from set names to well tokens
  mid-plot. `plot_members`' own docstring records that as the reason
  `member_replicate_sets` exists: a mapping written against the sets is silently
  orphaned by a toggle that has nothing to do with it. A group-keyed variable
  store walks into exactly that trap.

So: `pipeline_info.json` gains a `variables` block holding the definitions plus
`values: {well_token: value_id}`. A group row in the Variables tab is an
**assignment target** that displays the rolled-up value of its wells.

**The trap, and it must not be softened.** A well added to a group *after* the
variables were filled has no value. It does not inherit one. The group reads
`Mixed (2)`, the well is ringed on the plate, and a plotted member whose wells
disagree is **excluded with a stated count** rather than resolved to its first
well's value. Fabricating an annotation on a real measurement is §7's cardinal
sin, and silent inheritance is exactly that.

### Assignment

One row above the plate, modelled on Quick Replicates' own row:
**`Fill [Dose ▾] along [Columns ▾] · repeat each [2] · [Apply Fill]`** — values
cycle along the chosen axis over the enabled wells. Any block, row or column
design is then one click.

Everything else is **select-then-chip**: the plate in the `"set"` model with
row/column headers selectable and rubber-band drag (as `groups/controller.rep_add`
already configures it), then a click on a value chip (`widgets/chip_group.py`)
assigns. Two steps, and it matches Well Labels' existing "select cells, then Add
Prefix". Deliberately **not** the `"paint"` model: paint latches an add/remove
polarity on press, so dragging over an assigned well *clears* it, and a chip is
still needed to say which value is being painted.

### Reading it back

Two chips above the plate name the defined variables. The selected one owns the
**fill colour** with a value legend; the other supplies a short **marker glyph**
(`setWellMarker` — `H` / `U`, `0` / `.1`). Clicking the other chip swaps them.
A well in the dataset but **unassigned for the active variable gets a ring**
(`setWellRing`) and a count line, *"8 wells have no Dose."* — never the empty
socket, which already means "not in the dataset". Three independent decoration
layers, no reliance on colour alone, per the rule the dashboards already follow.

Extend `core/ui_helpers.well_plate_tooltip_provider` so hover gives the whole
row: `A01 — shCtrl · Cell line: HeLa · Dose: 1 µM`.

### Reaching the plots

One `LabeledComboBox` in the Plotting band's **scope** zone reading
`Group by: Dose`, plus a popover carrying **Colour by** and **Split into panels**.
Both hidden entirely when no variable is defined — the band's stated rule is that
it only ever carries controls that do something.

The semantics live in `domain/plot_members.py` as `member_variable_values(ctx,
scope, name)` built on the existing `member_wells`, so one module answers "what
is this member" for every key space.

**Two flags, both real.** Grouping by a variable introduces a *third* member-key
shape, and `tests/test_plot_members.py` asserts those keys equal what the
collectors emit — so `plot_members` and the collectors change in **one commit** or
that guard fires. And per-sample fold-change `ControlRef`s are keyed on member
keys, so changing *Group by* orphans saved control references: decide what happens
explicitly rather than discovering it.

### Naming

**Variables**, each with **Values**. Hint: *"A variable is anything you changed
across the plate — cell line, dose, timepoint of treatment. Name it, list its
values, then fill them onto the plate."* Not "Factor" (jargon), not "Condition"
(taken — `make_selection` states a group *is* one condition, so the word would
mean two things on one page), not "Metadata" or "Dimension" (the latter means
image axes in the Image Table).

**The payoff worth stating in the hint:** a variable is not a group. A user with
variables defined need not paint twelve group names to get twelve series.

### Acceptance walkthrough — 2 cell lines × 6 doses × 8 replicates

Rows A–D HeLa, E–H U2OS; column pairs are the six doses.

1. Home → Variables → **+ Variable**, name `Cell line`, type Text, values `HeLa`, `U2OS`.
2. `Fill [Cell line] along [Rows] repeat each [4]` → Apply.
3. **+ Variable**, `Dose`, type Number, unit `µM`, values `0, 0.1, 0.3, 1, 3, 10`.
4. `Fill [Dose] along [Columns] repeat each [2]` → Apply.
5. Click the `Dose` chip to check the fill; click `Cell line` to check the other.
6. Plotting → Bar → `Group by: Dose`, popover `Colour by: Cell line`.

No groups are defined anywhere in that sequence. If it does not fit in a minute,
the fill row is wrong.

---

## G — Response curves (numeric x-axis plotting)

**What this is.** Response plotted against a numeric variable the experimenter
set — concentration, seeding density, MOI, temperature, pre-treatment duration —
with error bars across replicates and an optional fit. A serial dilution is one
case of it and the feature is not named for it.

**Blocked on F.** The x value is a Number variable from F's store. Do not build a
private numeric field for this tab; that was the plotting reviewer's proposal and
it is superseded for the reasons in F.

**A new Plotting sub-tab**, `well_viewer/features/response/`, registered like any
other leaf. Not a mode on Line Graphs: that tab's fourth panel is a CDF whose x is
intensity, so a tab-wide x-source control is already wrong for a quarter of the
figure, and a mode toggle would change the meaning of the timepoint and
fold-change controls beside it. A separate leaf keeps the one-line-removable
property `test_feature_removal.py` enforces.

**The x control is scope, not action.** An `X: Concentration` `LabeledComboBox`
in the band's left zone via `plotting_band_slot`, listing F's Number variables,
added before the trailing Threshold.

**The other axis defaults to one timepoint** — `ctx.state.plot.focus_timepoint`,
the shared cross-tab timepoint, *read without writing* (writing it moves Heat Map
and Distribution). Surface it with the Bar tab's existing multi-select button
idiom; picking more than one draws one curve per timepoint, coloured by
timepoint. Defaulting to all timepoints would open a twelve-timepoint plate as
twelve overlapping curves per condition.

**Log x, and the vehicle control.** Log is the existing Properties toggle
(`plot_style._DEFAULTS["x_log"]`) — no new control — but its default comes from
the data: on when the positive values span two decades or more. The zero-dose
control is drawn **off-scale to the left of an axis break**, one decade below the
lowest positive dose, its tick labelled `0`, excluded from the fit with the
caption saying so. Dropping it is matplotlib's default and a silent substitution;
squeezing it onto the axis is a lie about where it sits. Do **not** reuse the
`_aw_log_plus1` FuncScale — it exists for intensities where +1 is negligible and
would move every point on a nanomolar axis.

**Register this tab's `x_log` in the per-tab pref scoping** (`plot_style.py`'s
`FIGURE_SCOPED_PREF_KEYS`), or a log scale set on Distribution leaks onto a
temperature axis.

**Groups with no value are named in three places**, because the shortfall must
survive into a saved figure: a caption on the axes (`3 of 8 groups omitted: no
Concentration (Ctrl, Blank, Rep 7)`), a row per omitted group in the exported CSV
with an empty x and a reason column, and the status line. If *no* group has a
value, an empty state naming the fix rather than empty axes.

**The fit.** A `Fit ▾` popover in the action zone: `None | 4PL | Hill (bottom = 0)`,
constrain-top/bottom, and *shared across curves*. **Fit in log10(x) space** when x
is log-scaled — fitting in linear x on decade-spaced data weights the top dose
almost exclusively — so EC50 is `10**logEC50` with an asymmetric CI. Report it
**in the legend entry** and in the plot-data CSV, not only in a sidebar, because a
fitted number is meant to be quoted and must survive Save to the gallery.

**Non-convergence draws nothing.** The legend reads `EC50 — (no fit)` and the
caption states the fitter's reason: fewer than four distinct x values, no plateau,
a bound hit. When the optimum sits on a bound the covariance CI is meaningless —
print `—`, never a number.

**Fold change.** `vs t0` is meaningless on a dose axis; `vs control` is exactly
what is wanted. Install the shared controls with a scope of its own and enable the
t0 half only when more than one timepoint is drawn.

**No un-pooled mode.** `plot_members.bar_member_keys` emits well tokens rather
than set names when sets are active and pooling is off; a per-condition x value
cannot resolve per well. Say so in the tab rather than letting it fail quietly.

### Naming

Tab **Response Curves**, beside `Bar Plots` / `Line Graphs` / `Heat Map`.
Controls: `X:`, `Timepoints: n selected`, `Fit ▾`, `Display ▾`, `Save ▾`. No
control says "dose", "concentration" or "EC50" unless the user typed it — except
the fit's own readout, where EC50 is the term of art.

---

## H — Statistical tests

**Deferred, by decision, not by drift.** H is not started and is not next: the
rows below stay `☐` because that is what they are, but nobody should read that
glyph as "the thing to pick up". Written here because a scoping decision that
lives only in a conversation is gone after the next compaction, and the reader
who finds six untouched rows a month from now has no way to tell a deferral from
a body of work that was simply forgotten — which is the failure §6.1 of
`CLAUDE.md` exists to prevent, in its cheapest form.

Two things to hand whoever does start it. **The F blocker has mostly lifted**:
rows 47 and 48 (paired / repeated-measures tests, and two-way ANOVA over two
variables) were written when sample variables did not exist, and they now do —
`domain/plot_members.member_variable_values` answers "which value of this
variable does this member hold" for any scope, which is the pairing key both of
them need. **And the dependency check is already done**: `scipy.stats.dunnett`
exists at the pinned scipy 1.13.1, so H3 needs nothing new; `statsmodels` is
**not** installed, so H5 needs a pip dry-run against the numpy 1.24 pin before
its row can be estimated at all.

Today the Statistics tab offers Welch and Student t-tests, Wilcoxon rank-sum,
Mann-Whitney U, and a two-well KS test, all pairwise, all at one timepoint, with
Benjamini-Hochberg adjustment across the pairs (`stats/controller.py:158`). The
replicate unit is correctly per-well or per-FOV and never per-cell, with an
explicit refusal below n=2 — that part is right and must not regress.

Five additions, in the order they pay off.

**H1 — Effect size and a confidence interval, on every comparison.** Hedges' *g*
with a bootstrap CI on the difference of means. With n=3 wells a p-value alone
says very little, and this is the cheapest scientific upgrade available. It needs
no new UI: the results panel already prints a block per pair, and
`domain/significance.py` already decides what a bracket says. Add the columns
there and let the label mode choose whether the bracket shows stars, the adjusted
p, or the effect size.

**H2 — An omnibus gate.** One-way ANOVA or Kruskal-Wallis (matching the
parametric choice already in the Test combo) run before the pairwise pass, with
its result printed above them. Reporting fifteen pairwise tests without first
asking whether anything differs is the practice BH-adjustment partially
compensates for; stating the omnibus is more honest and costs one scipy call.
Do not *suppress* the pairwise results when the omnibus is non-significant —
that is a policy the app should not impose — but say so.

**H3 — Dunnett against a named control.** This is the test that matches what the
app already models. The fold-change machinery has a first-class control concept
(`domain/control_ref.ControlRef`, per-member controls, `resolve_control`), and
comparing k treatments to one control is both the common design and strictly more
powerful than all-pairs with BH. The control picker already exists and is already
shared between two tabs (`core/fold_change_controls_view.py`) — reuse it rather
than adding a second way to name a control, or the two will disagree.

`scipy.stats.dunnett` exists in modern scipy; check the pinned version before
committing to it, and be prepared to implement it against the studentised range
if the pin is too old. Note that pin in the row.

**H4 — Paired and repeated-measures tests.** Paired t and Wilcoxon signed-rank.
Replicate sets repeated across plates or days *are* a paired design and are
currently tested as independent, which is both less powerful and slightly wrong.

This one has a prerequisite that must not be skipped: pairing requires knowing
*which* replicate in group A corresponds to which in group B. That correspondence
does not exist in the data model today. It is exactly what workstream F's
annotations provide (a "replicate" or "batch" axis), so H4 is blocked on F and the
row says so. Shipping a paired test that pairs by list order would be a wrong
number with a plausible face.

**H5 — Two-way ANOVA.** Once F exists, "does the drug effect depend on cell
line" becomes expressible. Two factors plus interaction, printed as a small table.
This is the first test needing `statsmodels`; add the dependency here and not
before, and check it against the pins that hold numpy at 1.24 (`_Docs/requirements.txt`
documents that resolution — re-run the dry-run described there).

**A cross-cutting rule for all five.** Every one of these produces a number that
can be undefined — a CI that did not converge, an ANOVA with an empty cell, a
paired test with unmatched replicates. Each reports *undefined* distinctly from
*computed as zero*, in the results panel and in any CSV column, per §7. The
existing `err` slot in `_run_pair_tests`' tuple is the precedent.

**Where the UI grows.** The Test combo gains entries; the results panel gains
rows. `stats/tab_view.py:200` is the roster. Resist adding a second panel: the
tab is already five selectors and one action, and the band exists to keep it that
way.
