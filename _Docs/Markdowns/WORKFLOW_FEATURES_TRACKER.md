# Workflow features — implementation tracker

**This document is temporary.** It exists because this body of work will be
carried across more than one session, and CLAUDE.md §6.1 requires that a plan
of this size live on disk rather than in conversation. It is deleted by the pull
request that closes its last row, and that pull request says in its body that it
is gone.

It plans four additions that turn the app's existing pieces into a repeatable
workflow:

- **Section P — Provenance log.** An append-only event log beside
  `pipeline_info.json`. No panel yet.
- **Section Q — QC triage with selective rerun.** One screen that joins what the
  app already knows about a plate's problems and turns each one into an action.
- **Section R — Recipes, kept small.** Apply one dataset's sample definitions,
  gates, ratios and saved plots to another dataset, with compatibility warnings.
  No environment fingerprint and no YAML import/export in the first cut.
- **Section H — Headless report CLI.** Batch Export without the GUI. It comes
  after recipes exist.

**No implementation has started.** No row is done; the counting command under
*How to read the rows* gives the current split. The plan was
written against the tree at `ecaae35` (the merge of #946), and every claim about
existing code names the file it was read from, so a later reader can check it
rather than trust it.

## This document is live, and it leads the work

It is not a record of what was decided. It is the instruction for what happens
next, and it is wrong the moment it stops matching the tree.

1. **Read it before touching anything.** The first act of any session working on
   these features is to open this file and find the first row, in the order
   given under *Sequencing*, that is not `done` or `declined`. That row is the
   work.
2. **The row and the code move in the same commit.** Never batched at the end of
   a session, and never "once CI is green".
3. **What a row learns goes into the row.** A measurement, a refuted assumption,
   a file that turned out to be somewhere else: write it in the row's evidence
   column, not only in a commit message.
4. **A finding is a row.** Anything a reviewer, a failing test or the tree turns
   up becomes a numbered row before it is acted on. Rows added that way go under
   the row that produced them and keep its letter (`Q4a`, `Q4b`, …).
5. **A row that turns out to be wrong is `declined` with the reason, never
   deleted.** A deleted row and a forgotten row look identical six months later.
6. **Each row is a hypothesis about the tree as it stood when it was written.**
   Reproduce the premise before building on it (CLAUDE.md §6.1, last paragraph).
   Several rows below begin with a *verify* step for exactly this reason.
7. **Report progress by reading this file, not by remembering.**

The prose above each row table is part of the same contract. When a measurement
changes what a section is doing, rewrite the prose to describe what is now true,
in the same commit. This file states what is true *now*; the history is in
`git log`.

## How to read the rows

Every row has a state, a place, and — once it is `done` — the test that pins it.

- **`todo`** — not started.
- **`wip`** — started, not finished. Never left across a session boundary
  without a note saying where it stopped.
- **`done`** — implemented **and** pinned by a test watched failing without the
  fix (CLAUDE.md §7.7 #2). "Implemented" alone is not `done`.
- **`blocked`** — cannot proceed until a named row closes, or cannot proceed in
  this environment. The blocker is named in the row.
- **`declined`** — examined and deliberately not done, with the reason written
  both in the row and at the site. Never silently dropped.

**Counts are derived from the rows, never typed.** The states reach `grep`
through a variable so the command cannot match itself, and the legend above is
prose rather than a table so it cannot match either:

```bash
F=_Docs/Markdowns/WORKFLOW_FEATURES_TRACKER.md
for s in todo wip done blocked declined; do
  printf '%-9s %s\n' "$s" "$(grep -c "| \`$s\` |" "$F")"
done
```

---

## 0. Ground rules

These restate the repository's existing contracts only where a section is at
risk of breaking one.

### 0.1 Two files on the likely path have zero line headroom

`tests/test_monolith_budgets.py` holds `process_microscopy.py` at 3733 lines
against a budget of 3733 (`PROCESS_MICROSCOPY_BUDGET`, line 113) and
`services/remote_runner.py` at 1183 against 1183 (`REMOTE_RUNNER_BUDGET`, line
182). Ratchets only move down. Any event emission the pipeline or the remote
client needs (Section P) goes into a new module that those files call in one
line, not into the files themselves. Re-measure both before starting P.

### 0.2 The pipeline stays self-sufficient, and the manifest is not optional

`tests/test_architecture_contracts.py::test_pipeline_is_self_sufficient` forbids
`pipeline/` from importing `review`, `widgets` or Qt. `tests/test_remote_manifest.py`
requires an entry in `services/remote_manifest.py` for every `.py` in
`pipeline/` and `util/`. A provenance writer the pipeline calls lives in
`pipeline/` or `util/` and joins the manifest in the same commit.

### 0.3 A module goes where its users are

CLAUDE.md §3 rule 1, enforced by `tests/test_a_module_lives_at_its_widest_use.py`.
Section Q wants Review to read what `datasets/validate/validate_compute.py`
computes, and Section H wants a command-line entry to drive Review's
collectors. Both cross a pane boundary. The answer is to move the shared part to
the narrowest root package that holds every user (`domain/` for Qt-free subject
matter), not to add an exemption. Q1 and H1 are where that is decided.

### 0.4 Undefined is not zero, and degraded output is visible in the artifact

CLAUDE.md §7 #3 and §7's design rule. Every path in this plan that can produce
less than was asked — a recipe that could not map a channel, a rerun that
finished three of five wells, a headless report that could not draw a plot —
puts the shortfall in the artifact itself (a marker, a count, a named
placeholder), not only in a log line.

### 0.5 Report; do not decide. Offer; do not apply.

QC triage proposes an exclusion or a rerun; the user presses the button. A
recipe shows its diff before it writes anything. Nothing in this plan changes a
dataset's scientific state without an explicit user action, and the headless
CLI changes none at all — it only reads and renders.

### 0.6 One test per feature goes through the real constructor

CLAUDE.md §7.8 #3. Each section has a row for a slow-lane test that builds the
real app (or, for H, runs the real command) and drives the real control. The
fast lane cannot see a builder it never calls, and every feature here has a
builder.

### 0.7 New persistence domains join the reset roster

`review/persistence/reset_scopes.py:285` defines `ANALYSIS_SCOPES`, and
`tests/test_reset_scopes.py` asserts that the scopes partition every persistence
domain exactly. Any new on-disk state this plan adds (a triage decision, a
per-field exclusion) must be placed in a scope, and placing it is a design
decision recorded in the row, not an afterthought to make that test pass.

### 0.8 The word "recipe" is already taken in ARCHITECTURE.md

ARCHITECTURE.md §11 is titled *Recipes* and holds developer how-tos ("Add a new
plotting tab", "Persist some state to disk"). The user-facing feature needs
either a different name or a rename of §11. R0 decides it before any user-facing
string is written.

---

## Sequencing

The four sections are listed in the order they were asked for. They should be
**built** in this order, because of what each depends on:

1. **P — Provenance log**, first and small. Q's exclusions and reruns and R's
   applications are exactly the scientific decisions the log exists to record.
   Building P first means they emit events from their first commit, rather than
   being retrofitted.
2. **Q — QC triage**, well-level rerun first (Q1–Q9). Field-level rerun (Q10–Q14)
   is a separate, riskier stage and can be deferred without blocking R.
3. **R — Recipes.**
4. **H — Headless report**, which needs R's "apply definitions without the GUI"
   path to be useful. H0 (the feasibility spike) can run any time.

Each section is intended to be one pull request. Per CLAUDE.md §6, each PR bumps
the version once, updates ARCHITECTURE.md and README_WellViewer.md, and runs the
guard family and the touched files locally before it is opened.

---

## Section P — Provenance log

### P.0 What exists, and what is missing

The app already records a great deal about how a dataset came to be. None of it
is a history.

| Record | Where | What it holds | Why it is not the log |
|---|---|---|---|
| `pipeline_info.json` | `process_microscopy.write_pipeline_info` (`process_microscopy.py:3146`) | Run settings, available timepoints/FOVs, plus the GUI-written curation blocks | Rewritten by every run; holds the latest state only |
| `run_report` block | `pipeline/run_report.py` | Per-well expected vs produced FOV-groups for the last run | Last run only; names only touched wells on an incremental run (ARCHITECTURE §9.7) |
| `run_check` block | `pipeline/completeness.py` (`scan_run`, `errors_since` at line 399) | End-of-run completeness for the whole plate, error-file counts | Last run only |
| `.aw_journal/run.json` | `services/run_journal.py` (`JOURNAL_DIRNAME`, line 74; `_RUN_FILENAME`, line 76) | What a run intended, where it sent work, how it ended | One file per output directory; P1 verifies whether the next run replaces it |
| `.aw_settings/` | `pipeline/settings_record.py` (`SETTINGS_DIRNAME`, line 25) | The settings digest that produced each well's output | Current state per well, not a sequence of changes |
| `errors/` | `pipeline/completeness.py:297` | One traceback file per failed FOV-group | Never cleared, but not linked to the run that wrote it except by timestamp |

What none of them records: **the order of events across runs**, and **anything
the user decided in Review** — a gate moved, a field excluded, a ratio defined,
a recipe applied. Those decisions live only as current state in
`persistence.json` and `pipeline_info.json`, so "why does this plate's number
differ from the figure I exported in March" has no answer on disk.

### P.1 The design, as far as it can be fixed before measuring

- **File:** `provenance.jsonl` in the dataset's output root, beside
  `pipeline_info.json`. One JSON object per line. Append-only: nothing in the
  app ever rewrites or truncates it.
- **Every line carries** `schema_version`, `ts` (UTC ISO-8601), `event` (a type
  from a closed roster), `source` (`pipeline`, `remote`, `review`, `datasets`),
  `app_version` (read from `version.py`, never written as a literal), and an
  event-specific `detail` object.
- **A damaged line is not a corrupt file.** A crash mid-append leaves a
  partial final line, and two processes appending at once on a network volume
  or on Windows can interleave mid-file. The reader skips every line it cannot
  parse, counts it, and reports the count; it never discards the lines around
  it, and the writer never "repairs" it.
- **It is evidence, never authority** — the same rule `services/run_journal.py`
  states for itself. No decision in the app reads the log. If it goes missing,
  nothing about the dataset changes.
- **Run events come from the Analyze runner, not the pipeline.**
  `PipelineRunner` writes `run_started` and `run_finished` at the moments it
  opens and closes the run journal, for local, remote and batched runs alike,
  so the pipeline's worker pool never appends and nothing is written on a
  remote host (where the fetch would never collect it). A pipeline launched
  directly from a terminal writes no run events.
- **Review-side events are recorded when a save is flushed, not per edit.**
  `schedule_save` debounces edits behind a 500 ms timer (ARCHITECTURE §5.7), so
  a dragged gate slider is one event, not two hundred. The event names the
  persistence domain and a digest of the block before and after, not the whole
  block.
- **No size cap in the first cut.** P9 measures a realistic year of use before
  anyone designs rotation.

### P.2 Rows

| ID | Item | State | Where | Evidence |
|---|---|---|---|---|
| P1 | Verify the premises in P.0 against the tree: does a second run replace `.aw_journal/run.json`; does any existing file already accumulate across runs; is `pipeline_info.json` always in the output root for every layout (in/out, folder mode, remote materialise) | `done` | `services/run_journal.py`, `services/remote_fetch.py`, `review/domain/viewer_state.py` | Measured: `run_journal.open_run` replaces `run.json` and clears `wells/` (`services/run_journal.py:313`), so nothing accumulates across runs. The remote fetch pulls named files only (`pipeline_info.json`, per-well CSVs and archives, `.aw_settings/`), so a log written by the remote pipeline would never come home. Review's data dir is not always the sidecar's directory: `resolve_pipeline_info_path` (`review/domain/viewer_state.py`) falls back one level up for the Analyze layout, so Review resolves the log's directory through it. |
| P2 | Event schema and roster: closed set of event types, required fields per type, `schema_version` | `done` | `util/provenance_log.py` (`EVENT_FIELDS`, `SOURCES`, `SCHEMA_VERSION`); manifest entry in `services/remote_manifest.py` | `tests/test_provenance_log.py::test_an_unknown_event_is_refused`, `::test_a_missing_detail_key_is_refused_by_name`, `::test_a_key_present_as_none_is_accepted` |
| P3 | Writer: atomic single-line append with fsync; reader that tolerates and counts a torn last line and refuses a future `schema_version` without discarding earlier lines | `done` | `util/provenance_log.py` (`append_event`, `read_events`) | `tests/test_provenance_log.py::test_a_torn_last_line_is_counted_and_hides_nothing_before_it`, `::test_appending_never_disturbs_the_bytes_already_there`, `::test_a_newer_schema_line_is_foreign_not_unreadable`, `::test_no_log_is_missing_and_an_empty_log_is_not`. Widened from a torn *last* line to any unreadable line: concurrent appends on a network volume or on Windows can interleave mid-file. |
| P4 | Run events. **Revised:** emitted by `PipelineRunner` at the journal's open and close, not by `process_microscopy.py` — one writer for local, remote and batched runs, and the pipeline file has zero headroom. Records `argv` and the scope options rather than a settings digest: the GUI's opts are not the pipeline's parsed args, so a digest computed client-side would not match `.aw_settings/`. Cost, stated in ARCHITECTURE: a pipeline invoked directly from a terminal writes no run events | `done` | `services/run_provenance.py`, `services/pipeline_runner.py` (`_open_journal`, `_close_journal`) | `tests/test_provenance_log.py::test_a_second_run_adds_to_the_history_while_the_journal_is_replaced` drives the real `PipelineRunner` twice; deleting the `finish_run` call fails it. |
| P4a | `run_finished` quoted the previous run's `run_check` when two runs fell in the same second: the block's timestamps are second-resolution. Found by the runner test. Replaced the timestamp comparison with a fingerprint of the block taken at `begin_run` | `done` | `services/run_provenance.py` (`run_check_summary`) | `tests/test_provenance_log.py::test_a_run_check_this_run_wrote_is_quoted_even_in_the_same_second`, `::test_a_run_check_left_from_before_this_run_is_not_quoted`; removing the fingerprint check fails the latter and the runner test. |
| P5 | Remote runs: written by the same two calls as local ones (P4), against the local output directory, with the run_check the fetch brought home | `done` | `services/pipeline_runner.py` (`_open_journal` / `_close_journal`) | `tests/test_provenance_log.py::test_a_remote_run_is_recorded_locally_with_the_fetched_run_check` (remote and remote-batched): open before upload, the fetched sidecar arriving locally, then close — the mode is named and the fetched `run_check` quoted. |
| P6 | Review records `analysis_changed` per analysis block, plus `reset` naming the scopes that completed. **Revised:** recorded at `_io._replace_with_text`, the one place a dataset JSON file is replaced, not at the registry flush — the gating block and smFISH thresholds are written inline and never pass the registry (`registry.py` docstring). The file is read just before the replace and diffed against the text just after it, so a failed write records nothing and a block the pipeline changed since load is not attributed to Review | `done` | `review/persistence/provenance.py`, hooked in `review/persistence/_io._replace_with_text`; reset in `reset_scopes.clear_analysis` | `tests/test_review_provenance.py` drives `_doc.set_section`, the real gating writer, a deferred write and `clear_analysis`. Mutations, each caught: dropping the `after_write` call (9 tests fail), never reading the file before the write (6), recording before the replace (`::test_a_failed_write_records_nothing`). |
| P6a | Guard: every domain in `ANALYSIS_SCOPES` reaches the event emitter, stated against the roster so a new domain is covered by being declared (CLAUDE.md §3, "derive the list from the registry") | `done` | `review/persistence/provenance.py` (`STORED_AT`, `RECORDED_UNDER`) | `tests/test_review_provenance.py::test_every_analysis_domain_reaches_the_log` (stated against `ANALYSIS_SCOPES`), `::test_the_roster_guard_bites`. |
| P7 | Subset, Merge Plates and Concatenate record `derived_from` naming their source dataset(s), after verification and the destination sidecar, with the detail their summary states (wells kept, well map, hour offset) | `done` | `datasets/core/provenance.py` (`record_derivation`), called from `datasets/{subset,merge_plates,concatenate}/apply.py` after the destination sidecar is written | `tests/test_datasets_provenance.py` runs each tool's real planner and executor; `::test_a_derivation_that_failed_verification_records_nothing`. |
| P7a | The importers (CZI, ND2, OME-TIFF, loose TIFFs) | `declined` | `datasets/core/provenance.py` module docstring | They write raw input with no sidecar, so no directory holds the dataset's history until its first Analyze run, which the runner records (P4). Reason also written at the site. |
| P8 | Validate & Repair emits `repaired` with what it changed | `done` | `datasets/validate/apply.py` (`apply_repairs`) | `tests/test_datasets_provenance.py::test_a_repair_that_changed_something_is_recorded_once` — recorded once, and the idempotent re-run adds nothing. |
| P9 | Measure log growth over a realistic session and a realistic year; decide whether rotation is needed | `done` | this row | Measured with `util.provenance_log.format_line` on realistic payloads: `run_started` 869 B (96-well remote run, 40-flag argv), `run_finished` 386 B, `analysis_changed` 240 B. Assumed load: 200 working sessions a year at 100 analysis writes each (the 500 ms debounce collapses a drag to one write) is 20,000 lines, 4.8 MB; `read_events` parses that in 0.12 s on this container, and one append with fsync costs 0.45 ms on local disk. **No rotation**: a year of heavy use is a few MB and reads in well under a second. Revisit if a reader ever needs it on every load. |
| P10 | Slow-lane test: build the real app, load a dataset, make an analysis edit, flush, assert the event line on disk | `done` | `tests/test_provenance_in_a_real_app.py` (slow lane) | Real `ReviewApp`, `_sel_add` queues the debounced save, `flush_all` lands it on the background writer; the `sample_definitions` event is read back. Removing the `after_write` call in `_io` fails it. |
| P11 | ARCHITECTURE.md (§5.7 persistence table, §6.2, §9.6) and README_WellViewer.md (what the file is, that it is safe to delete, that nothing reads it) | `done` | `_Docs/Markdowns/ARCHITECTURE.md` (§3 map, §5.7 *The provenance log*, §6.4 `run_provenance.py`, §6b `datasets/core/provenance.py`, §16 contract row); `_Docs/Markdowns/README_WellViewer.md` (*What the pipeline writes* → *The dataset's history*) | Docs only; `tests/test_doc_version_sync.py` green. |

---

## Section Q — QC triage with selective rerun

### Q.0 What exists

Almost every input this screen needs is already computed somewhere. The work is
joining them, not producing them.

| Piece | Where | What it gives triage |
|---|---|---|
| Validate & Repair scan | `datasets/validate/validate_compute.py` — `build_report` (line 1044), `Finding` (89), `WellReport` (97), `_scan_error_files` (425), `_check_stale_overrides` (976) | Per-well completeness against intent, error files per well, stale curation, findings with severity |
| Rerun scope from a report | `validate_compute.wells_needing_force` (148), `wells_needing_work` (178) | Which wells a completion run must force and which it must be limited to |
| Hand-off to Analyze | `datasets/validate/controller.py:223-225` | Opens Analyze on the dataset with settings pre-filled and `incremental`, `force_wells`, `only_wells` set — the rerun action already exists |
| Pipeline well scoping | `pipeline/cli_args.py:387` (`--force_wells`), `:401` (`--only_wells`); form fields in `analyze/well_scope.py` | Rerun named wells from scratch while leaving every other well's output alone |
| Field quality | `pipeline/field_quality.py`, scored by `review/domain/field_quality_scores.py`, read through `review/data/field_quality_cache.py` (`flagged_fields` 107, `flagged_detail_by_well` 243) | Per-field focus and quality flags with reasons |
| Field-quality exclusion | `field_quality_cache.apply_field_quality_to_cache` (349), `FIELD_PASS_COLUMN` (55) | An exclusion of *every* flagged field at once — all or nothing |
| QC debris gate | `review/features/qc/controller.py`, editing UI in `review/features/qc/tab_view.py` (embedded in Home → Cell Gating) | Per-cell `QC_Pass` |
| Per-cell curation | `review/persistence/cell_overrides.py` | Per-cell `Included` overrides |
| Run records | `run_report`, `run_check`, `errors/` (see P.0) | Partial wells and the exception that caused each loss |

### Q.1 What is missing

1. **One table.** Today a user who wants to know what is wrong with a plate
   reads Validate in the Datasets pane, the field-quality status in Home → Cell
   Gating, the Segmentation overlay in Image Browser, and the error files on
   disk. Triage puts one row per finding — plate, well, field, timepoint, kind,
   source (acquisition / processing / review), detail — in one sortable table.
2. **Per-field exclusion with a reason.** The field-quality exclusion is all or
   nothing; per-cell overrides are too fine. There is no way to say "exclude
   field 3 of B4, it is out of focus" and have that reason travel with the
   decision. This is a new persistence domain (§0.7) and a new layer at the
   inclusion chokepoint (`review/data/data_loading.df_included_mask`).
3. **A decision state per finding** — open, accepted, excluded, queued for
   rerun — so a triaged plate stays triaged across sessions.
4. **Jump-to-source.** Open the exact field in Image Browser with the mask and
   exclusion overlays on.

### Q.2 What "selective rerun" means, and the trap in field-level rerun

**Well-level rerun already works.** `--only_wells` plus `--force_wells`
re-segments named wells from scratch and leaves every other well alone, and
Validate already hands that scope to Analyze. Triage reuses that hand-off; it
does not build a second one.

**Field-level rerun does not exist, and incremental mode cannot provide it.**
`pipeline.io.merge_records_into_csv` (`pipeline/io.py:809`) keeps existing rows
and drops new rows whose `(well, fov, timepoint, nucleus_id)` already appears —
its docstring calls this idempotent. So re-running an already-processed field
through the incremental path is a no-op that reports success: the old numbers
stay, which is CLAUDE.md §7.7 #3's failure exactly. Field-level rerun needs a
*replace* merge: delete every row and every archive member for the
`(well, fov, timepoint)` being replaced, then add the new ones. It also
invalidates per-cell overrides for that field, because new segmentation mints
new nucleus ids — `_check_stale_overrides` already detects the result of that
after the fact. This is why field-level rerun is its own stage (Q10–Q14) and is
not required for the first PR.

**Reruns are not undoable.** A rerun overwrites output. Triage says so before
the run starts and records the before-state summary in the provenance log
(Section P), which is what makes "review before-and-after metrics" possible.
Exclusions and decision states are undoable; reruns are not, and the UI must not
suggest otherwise.

### Q.3 Rows

| ID | Item | State | Where | Evidence |
|---|---|---|---|---|
| Q1 | Decide where the screen lives (a Review page vs a Datasets tool) and where the shared scan lives. Review needs `validate_compute`'s findings; that module belongs to the Datasets pane (§0.3). Record the decision and the reason | `done` | this row; ARCHITECTURE *QC Triage* | **Decided:** a Home sub-tab (`features/home/triage/`), because everything it acts on — per-field exclusion, the inclusion chokepoint, the Segmentation jump — is Review state. It reads the completeness record (`run_check`) Review already holds rather than importing `validate_compute`: the pipeline and Validate both write that block, so the findings arrive without crossing the pane boundary, and Home already reports from it (`home/collect.py`). |
| Q2 | Move the Qt-free findings model and scan that both panes need to the root package that holds both users, with no behaviour change; prove it with the existing Validate tests unchanged | `declined` | this row | No move is needed: Q1 reads the `run_check` block both the pipeline and Validate write, so no Datasets-pane module is imported from Review. The one listing that was missing — the traceback files themselves — was added to `pipeline/completeness.py` (`error_files_since`), and `errors_since` now counts through it, so the two cannot disagree. |
| Q3 | Pure collector: join Validate findings, field-quality flags, run records and error files into one list of typed triage rows. A source that could not be read yields a row saying so, never an absent row (§0.4) | `done` | `review/features/home/triage/collect.py` | `tests/test_qc_triage.py` — `::test_no_record_is_a_row_not_an_empty_table`, `::test_an_unscorable_field_quality_block_is_a_row`, `::test_well_shortfalls_each_name_their_well_and_offer_a_rerun` (incl. the unlisted-groups row), `::test_only_this_run_s_traceback_files_are_listed`. |
| Q3a | Fixture with the mess of real output (CLAUDE.md §7.1 #4): a partial well, an error file for a well that later succeeded, a field flagged for focus, a stale override, `.DS_Store` in `errors/` | `done` | `tests/test_qc_triage.py` | Fixtures carry a partial well whose record lists fewer groups than it counts, a failed group named by file, a never-processed well, a stale traceback from a day earlier beside a current one, a field-quality plate that cannot be scored, and an untrusted well. `.DS_Store` is not relevant: `error_files_since` globs `*.txt`. |
| Q4 | Per-field exclusion with a reason: new persistence domain, placed in a reset scope (§0.7), composed at `df_included_mask` beside `QC_Pass` and `Field_Pass` | `done` | `review/data/field_exclusions.py` (`Excluded_Field_Pass`), `review/persistence/field_exclusions.py`, `DatasetState.field_exclusions`, `VERDICT_LAYERS`, the overlay roster and ink, the *Curation* reset scope | `tests/test_field_exclusions.py` (chokepoint, removal on restore, untrusted wells refused, registered whatever the profile, round trip); the overlay guards in `tests/test_exclusion_layers_overlay.py` forced the seventh ink, chosen by measurement (`ink_light`, ΔE 35.1). |
| Q5 | Persisted decision state per finding, keyed so that a rerun which resolves a finding closes it rather than orphaning it | `done` | `review/persistence/triage_decisions.py`, `DatasetState.triage_decisions` | `tests/test_qc_triage.py::test_accept_then_reopen`, `::test_a_decision_key_does_not_move_when_its_detail_does`. Decisions for findings no longer reported are kept, not pruned (reason in the module docstring). |
| Q6 | The table view and its actions: open in Image Browser, accept, exclude with reason, queue well rerun | `done` | `review/features/home/triage/tab_view.py`; `QC Triage` descriptor in `features/registry.py`; `profiles/full.toml` entry | `tests/test_qc_triage_in_a_real_app.py` (slow lane). Home sub-leaf, so the *Adding a Plotting leaf* table does not apply. |
| Q7 | "Queue well rerun" goes through the existing Validate → Analyze hand-off (`datasets/validate/controller.py:223`), not a second copy of it | `done` | `home.controller.open_in_analyze`, used by both *Finish this run* and triage | `tests/test_qc_triage.py::test_rerun_hands_off_exactly_the_selected_wells`. |
| Q8 | Every exclusion, decision change and rerun reaches the provenance log | `done` | `review/persistence/provenance.STORED_AT` | No new event type was needed: both new domains are analysis state, so every exclusion and decision is an `analysis_changed` event at its write, and a rerun appears as the `run_started` the runner writes with its `only_wells` / `force_wells` scope. Recording "rerun queued" would be false whenever the user opens Analyze and does not press Run. `tests/test_qc_triage.py::test_both_new_domains_are_curation_and_reach_the_provenance_log`. |
| Q9 | Slow-lane test: real app, real table, press *Exclude* on a row, assert the field's cells leave a plot's n (CLAUDE.md §7.6 #1 — press the button) | `done` | `tests/test_qc_triage_in_a_real_app.py` | Builds a three-FOV plate (the scorer declines below eight fields per timepoint, so the one-FOV mini plate could not flag anything), presses *Exclude field…*, and asserts B02 loses exactly its six cells. Disconnecting the button, or removing the projector registration, fails it. |
| Q10 | Field-level rerun, stage 2. Verify Q.2's premise first: run an incremental pass over an already-processed field and show the CSV unchanged | `todo` | `pipeline/io.py` | |
| Q11 | Replace-merge for named `(well, fov, timepoint)` keys in the CSV and in `<well>_out.zip`, atomic, with a test that mutating each part of the key alone changes the result (`tests/test_reuse_is_keyed_completely.py` is the shape) | `todo` | `pipeline/io.py` | |
| Q12 | Pipeline flag naming the fields to replace; remote staging must ship only those fields and the fetch must use the replace merge, not the append merge | `todo` | `pipeline/cli_args.py`, `services/remote_*` | |
| Q13 | Per-cell overrides on a replaced field: decide drop vs keep-and-flag, and make the outcome visible (§0.4) | `todo` | `review/persistence/cell_overrides.py` | |
| Q14 | Before/after metrics view for a rerun, read from the provenance event Q8 wrote | `todo` | triage feature | |
| Q15 | ARCHITECTURE.md (new feature section, §5.7 table row for each new domain, §9.7 note on replace vs append) and README_WellViewer.md (the triage workflow) | `done` | `ARCHITECTURE.md` (§3 map, Home → *QC Triage*, §5.7 rows, §6.6b, §6.7); `README_WellViewer.md` (Home → *QC Triage*, *Why removed*, *Starting over*) | Docs; `tests/test_doc_version_sync.py` green. |

---

## Section R — Recipes, kept small

### R.0 Scope of the first cut

"Apply this dataset's sample definitions, gates, ratios and saved plots to that
dataset", with compatibility warnings. The first cut copies **from one dataset
folder to another**; there is no standalone recipe file, no environment
fingerprint and no YAML. A portable JSON recipe file is a later row (R13) and is
not part of the first PR.

### R.1 What can be copied, measured against the reset roster

`review/persistence/reset_scopes.py:285` already names every piece of analysis
state and the storage domains it owns. A recipe is a selection of those scopes,
which also means a new analysis domain added later is automatically a recipe
candidate rather than silently left out.

| Scope (`ANALYSIS_SCOPES` key) | Domains | In the first cut | Why |
|---|---|---|---|
| `sample_variables` | `sample_variables` | yes | Plate layout variables; well-keyed |
| `sample_definitions` | `sample_definitions`, `notes`, `plot_groups` | yes | Asked for; well-keyed |
| `gating` | `cell_gating`, `qc_params` | yes | Asked for ("gates"); channel-keyed |
| `ratios` | `ratios` | yes | Asked for; channel-keyed |
| `smfish` | `smfish` | to decide in R1 | Channel-keyed thresholds tied to the source's spot counts |
| `export_groups` | `export_groups` | to decide in R1 | Batch Export groups; needed by H |
| `curation` | `crop_region`, `cell_overrides` | **no** | Per-cell overrides are keyed by the source's nucleus ids and a crop by its pixel geometry; neither means anything on another plate. Declined at the site too when R is built |

Saved plots are not an analysis scope; they live in `persistence.json` under
`"saved_plots"` with images in a `saved_plots/` directory
(`review/features/saved_plots/state.py:50-51`).

### R.2 The trap in copying saved plots

A saved plot is a parameter snapshot **and a rendered image of the source
plate's data** (`saved_plots/collect.capture_params`, line 35). Copying the
record as it stands would put the source plate's figure into the target's
gallery, under the target's name, looking exactly like a figure of the target's
data — a silent substitution of the kind CLAUDE.md §7 forbids. A copied saved
plot must arrive **without** the source image and visibly marked as not yet
drawn on this dataset, and redraw only through `restore_saved_plot`
(`saved_plots/controller.py:180`) against the target's data.

### R.3 Compatibility is a pure function, and it runs before anything is written

Each copied block is keyed by something the target may not have:

- **Wells** — sample definitions, variables and labels name well ids. A 96-well
  source onto a 384-well target, or a target missing wells, leaves entries that
  attach to nothing.
- **Channels** — gates, QC thresholds and ratios name channel tokens. A target
  imaged with different tokens has none of them.
- **Columns and metrics** — saved plots name metrics and shape features the
  target's CSVs may not carry.
- **Timepoints** — saved plots and heat-map timepoints name hours the target may
  not have.

The check returns, per scope, what will apply cleanly, what will apply with a
warning, and what cannot apply. The preview shows that before the user confirms,
and what could not be applied is listed in the result, not dropped.

The target's own state is the other half of the diff. The pipeline's
auto-threshold pass writes `cell_gating.thresh_frac_on` into every new dataset
(ARCHITECTURE §9.7 step 6), so "apply the source's gates" overwrites values the
target already has. The preview shows old against new per scope, and the user
chooses per scope.

### R.4 Rows

| ID | Item | State | Where | Evidence |
|---|---|---|---|---|
| R0 | Name the feature (§0.8). Record the decision and, if §11 of ARCHITECTURE.md is renamed instead, do it in the same commit | `done` | `_Docs/Markdowns/ARCHITECTURE.md` §11 | **Decided:** ARCHITECTURE §11 is renamed *How-to guides*; "recipe" is the code and doc name for this feature, and the user-facing label is *Copy analysis from another dataset*, which says what it does. |
| R1 | Decide `smfish` and `export_groups` (R.1). Record the reason in the table above | `done` | `review/domain/recipe_compute.DECLINED`, `RECIPE_SCOPES` | `export_groups` is copied (the headless report needs them, and they are plain well-keyed groups). `smfish` is **declined**: a saved threshold is the one the per-well spot counts were baked with by Apply Global Threshold, so copied alone it would describe counts the target was never computed at. |
| R2 | Read a source dataset's analysis blocks without loading it into the app: reuse the `review/persistence/` readers, and respect `persistence_doc_degraded` / `persistence_doc_foreign` (ARCHITECTURE §5.7) — a source the app refuses to read yields no recipe, and says why | `done` | `review/persistence/recipe.read_dataset_blocks` | `tests/test_recipes.py::test_a_dataset_root_is_read_from_its_out_folder`, `::test_an_unreadable_file_is_refused_not_read_as_empty`, `::test_a_foreign_persistence_document_is_refused`. Reads the files directly rather than through the app-state readers, so no dataset is loaded. |
| R3 | Pure compatibility check (R.3): per scope, clean / warning / cannot-apply, with the entries in each | `done` | `review/domain/recipe_compute.check_fit` | `tests/test_recipes.py::test_the_fit_names_what_the_target_lacks`, `::test_a_clean_scope_says_so`, `::test_column_names_count_as_their_channel` (removing the `<channel>_` prefix rule fails it). |
| R3a | Fixtures where right and wrong answers differ (CLAUDE.md §7.8 rule 2): a target missing one channel the source gates on, a 384-well target, a target with an existing auto-threshold value that differs from the source's | `done` | `tests/test_recipes.py` fixtures | Target lacks `cy5`, which the source gates and ratios on and names in a scatter-gate column; the source names `H12`, absent from the target; the target already holds its own `thresh_frac_on`. Each makes the right and wrong answers differ. |
| R4 | Write path. Decide whether the target may be the dataset currently open in the app (then it must go through live state and `flush_all`, or the next autosave overwrites the applied blocks) or must be closed. Record the decision | `done` | this row; `review/core/recipe_view.py` | **Decided:** the target is the dataset open in Review. Every pending debounced save is flushed first, the blocks are written to disk through the usual primitives, and the dataset is reloaded through the same shell reload the reset uses, so live state is re-read from disk rather than patched. Entry point: a title-bar button beside Reset analysis. |
| R5 | Apply: write the chosen scopes atomically, never into a foreign or degraded document | `done` | `review/persistence/recipe.apply_recipe` | `tests/test_recipes.py::test_apply_flushes_first_then_writes_through_the_recorded_paths`, `::test_a_failing_scope_does_not_stop_the_others`, `::test_a_foreign_target_document_is_reported_not_silently_skipped`. |
| R6 | Saved plots arrive without the source image and marked as not drawn on this dataset (R.2); the gallery and both exports (`_report.py`, `_deck.py`) show the marker rather than an empty or stale image | `done` | `SavedPlot.copied_from`, `SavedPlotsStore.image_path`, `saved_plots.state.undrawn_note`; gallery card, Home strip, `_report.py`, `_deck.py` | `tests/test_recipes.py::test_copied_plots_are_added_undrawn_under_fresh_ids` (keeping the source filenames fails it), `::test_a_copied_record_has_no_image_path_and_says_why`, `::test_the_pdf_report_pages_a_copied_plot_with_its_note`. **Not measured:** that *Restore* on a copied record draws a sensible figure from the target's data — it replays the source's parameters, which may name wells or channels the target lacks. Row R6a. |
| R6a | Drive *Restore* on a copied saved plot in the real app, against a target that lacks one of its wells, and record what is drawn and what is said | `done` | `saved_plots.controller.absent_wells` / `absent_wells_sentence`, `restore_saved_plot`, `_report_group_drift(also=…)`; `_render.RenderedPlot.note` → manifest `note` | **Measured** in the real app (mini plate, a copied record naming `H12`): Restore re-adopted group `G` with wells `A01, H12`, drew the figure from `A01` alone, and the status line said only "1 replicate set(s) · 1 well(s)" — a silent substitution. Restore now names the wells the record uses that this dataset lacks, on the same status line as any group drift, and the headless report puts the same sentence in the plot's manifest `note`. `tests/test_a_restore_does_not_rewrite_declarations.py::test_wells_the_dataset_lacks_are_named_with_the_drift`, `::test_absent_wells_alone_are_still_said`, `::test_nothing_is_said_when_every_named_well_is_here`. The figure itself is still drawn from the wells that are here; that is the right figure, now said. |
| R7 | Dialog: pick source, pick scopes, preview the diff and the warnings, confirm | `done` | `review/core/recipe_view.py`, `allwell._on_copy_analysis_clicked` | `tests/test_recipes.py::test_the_checklist_disables_what_cannot_be_copied`. |
| R8 | Every application emits a provenance event naming the source dataset, the scopes and what failed | `done` | `util/provenance_log.EVENT_FIELDS["recipe_applied"]`; `apply_recipe` | Each block written is an `analysis_changed`; the copy is one `recipe_applied`. `tests/test_recipes.py::test_apply_flushes_first_then_writes_through_the_recorded_paths`. |
| R9 | Guard: every `ANALYSIS_SCOPES` key is either in the recipe roster or declined with a written reason, so a new scope cannot be silently left out | `done` | `tests/test_recipes.py` | `::test_every_analysis_scope_is_copied_or_declined_with_a_reason`, `::test_the_roster_guard_bites`. |
| R10 | Slow-lane test: two datasets, apply sample definitions and gates from one to the other through the real dialog, reload the target, assert the values | `done` | `tests/test_recipes_in_a_real_app.py` (slow lane) | Real app, real prompt with the dialog accepted, real reload: the copied ratio is live and the copied gate is in `ctx.state.cell_gating`. |
| R11 | ARCHITECTURE.md (new section; §5.7 cross-reference) and README_WellViewer.md (the workflow, what is not copied and why) | `done` | `ARCHITECTURE.md` (§5.7 *Copying analysis*, §11 renamed); `README_WellViewer.md` (*Copying analysis from another dataset*) | Docs; `tests/test_doc_version_sync.py` to run with the guard family. |
| R12 | Portable recipe file (JSON, carrying `schema_version`) exported from a dataset and applied like a source dataset. Not in the first PR | `todo` | later | |
| R13 | Environment fingerprint and YAML import/export | `declined` | — | Out of scope for the first cut by request; revisit only after R12 ships |

---

## Section H — Headless report CLI

### H.0 What exists, and the real cost

The rendering half is already Qt-free. The orchestration half is not.

- **Qt-free:** each feature's `renderer.py` (pure matplotlib) and `collect.py`
  (pure data collection); `review/features/batch_export/service.py`
  (`save_export_figure`, no Qt, no app); `saved_plots/_report.py` (PDF) and
  `_deck.py` (PowerPoint), both pure builders over captured images.
- **Not Qt-free:** the Batch Export panels, which hold the per-job orchestration.
  A count of `ctx` references at `ecaae35`: `base_panel.py` 98, `scatter_panel.py`
  51, `bar_panel.py` 44, `_line_series_runner.py` 26, `tab_view.py` 26,
  `response_panel.py` 12. `base_panel.py` is 3574 lines.
- **Collectors take `ctx`.** They read `ctx.state.*` — the loaded dataset,
  selections, gating, QC — which today is only ever populated by loading a
  dataset into a running app.

So there are two ways to build this, and the plan does not choose between them
until H0 has measured both:

1. **Offscreen app.** Construct the real app under `QT_QPA_PLATFORM=offscreen`,
   load the dataset, drive Batch Export programmatically. Works with the code as
   it is. Costs a full app construction (~14 s measured in the slow lane,
   CLAUDE.md §2) and a `QApplication`.
2. **Headless context.** Populate `ctx.state` from the dataset without building
   widgets, and call collectors and renderers directly. No `QApplication`, but
   it needs the job orchestration lifted out of the panels, which is the large
   part.

The output contract is the same either way: a directory of figures and CSVs, an
optional PDF and PowerPoint, and a `manifest.json` listing every requested
output and what became of it. **A plot that could not be drawn is a manifest
entry with a reason, never an absence** (§0.4), and the exit code distinguishes
complete, partial and failed.

### H.1 Rows

| ID | Item | State | Where | Evidence |
|---|---|---|---|---|
| H0 | Spike: measure both routes on a real dataset — time to first figure, peak RSS, and whether route 1 can load and render with no window shown. Record the numbers here and choose | `done` | this row | Measured on the mini dataset, offscreen, in one process: imports 2.2 s, `ReviewApp()` 1.3 s, load 3.5 s, one Bar plot captured 1.4 s; peak RSS 396 MB; no window shown, no modal raised. **Chose route 1** (the real app, offscreen): it draws every plot exactly as the app does. Route 2 would need H4 first and would be a second definition of how each plot is drawn. |
| H1 | Entry point. A command a person types may sit at the root only with a reason in `tests/test_root_holds_only_entry_points.py::ROOT_MODULES`; the frozen build needs a dispatch in `all_well_launcher.py` beside `--run-pipeline`. Decide the spelling and record it | `done` | `allwell_report.py` (root); `all_well_launcher.py` `--run-report`; spec hidden import; `ROOT_MODULES` entry | **Decided, then revised by the guards:** it was first written as `review/report/`, and `test_nothing_imports_reviewapp_at_runtime` and `test_a_module_lives_at_its_widest_use` both failed on it — a module inside `review/` may not construct `ReviewApp`, and the launcher may not reach into a pane. Both say the same thing: the report is an entry point, like `allwell.py`. It is now the root module `allwell_report.py` with its reason in `ROOT_MODULES`. |
| H2 | Input contract: dataset folder; a definitions source (the dataset's own state, or a source dataset / recipe file from R); which outputs (Batch Export groups, saved plots, or both) | `done` | this row; `allwell_report.py` | **Decided for the first cut:** the input is a dataset folder, and the outputs are its saved plots drawn from its current data, plus a PDF and a manifest. Batch Export groups are not drawn (their orchestration is in the panels; see H4). Applying a recipe first is **not** offered, because it would change the dataset; copy the analysis in the app, then report. |
| H3 | Manifest schema with `schema_version`, one entry per requested output, status and reason; exit codes for complete / partial / failed / invalid input | `done` | `allwell_report.py` | `tests/test_headless_report.py::test_the_exit_code_follows_the_entries`, `::test_every_outcome_has_its_own_exit_code`, `::test_a_missing_dataset_is_invalid_input_and_still_writes_a_manifest`. |
| H4 | Lift Batch Export's per-job orchestration out of the panels into a Qt-free runner the panels call, with no behaviour change for the GUI. Only if H0 chooses route 2 | `declined` | this row | Only needed for route 2, which H0 did not choose. Drawing Batch Export groups headlessly remains future work and would start here. |
| H5 | The command itself, built on whichever route H0 chose | `done` | `allwell_report.py`, `review/features/saved_plots/_render.py` (services `saved_plots.gallery` / `.render` / `.report_pdf`), `persistence/_io.writes_suspended` | `tests/test_headless_report_command.py::test_the_command_draws_the_gallery_and_changes_nothing` runs the real command in a subprocess: one drawn plot (PNG + SVG non-empty), one Image Table record not drawn with its reason, exit 3, PDF written, dataset byte-identical. **Measured:** replays that re-adopt groups or move gating schedule no save, so the write suspension is defence in depth that this run does not exercise; it is pinned by `tests/test_headless_report.py::test_writes_under_a_suspended_root_are_dropped_and_counted` (removing the check fails it). |
| H6 | Profiles: a feature profiled out of the build (`ALLWELL_PROFILE`) yields a manifest entry saying so, not a crash and not a silent gap | `done` | `allwell_report.py` | `tests/test_headless_report_command.py::test_a_build_without_saved_plots_says_so` (`ALLWELL_PROFILE=thin`: exit 2, the reason in the manifest). |
| H7 | Each report run emits a provenance event naming the output and its status | `done` | `util/provenance_log.EVENT_FIELDS["report_generated"]`; `runner._record` | `tests/test_headless_report_command.py::test_the_command_draws_the_gallery_and_changes_nothing` reads the `report_generated` line back. |
| H8 | Test through the real command: run it on the `mini_dataset` fixture, assert the manifest and that each listed file exists and is non-empty (CLAUDE.md §7.3 #1) | `done` | `tests/test_headless_report_command.py` | Runs on the `mini_dataset` fixture, asserts every listed file exists and is non-empty, and that the dataset is unchanged. |
| H9 | ARCHITECTURE.md (§6.3 launcher dispatch, new section) and README_WellViewer.md (the command, its flags, its exit codes) | `done` | `ARCHITECTURE.md` (§3 map, Saved Plots → *The headless report*, §6.3); `README_WellViewer.md` (Saved Plots → *Drawing the gallery without opening the app*) | Docs. |

---

## What would make this plan wrong

These are the premises most likely to fail when measured. Each has a row that
checks it before anything is built on it.

- **The run journal may already accumulate history** (P1). If it does, P extends
  it rather than adding a second file.
- **Validate's scan may be too slow to run on every triage open** — it opens
  every container (`validate_compute.py` module docstring, "two container opens
  and one CSV parse per well"). Triage may need to run it in a worker and cache
  the result, which changes Q3.
- **The incremental merge may not be the no-op Q.2 predicts** for a re-processed
  field (Q10). If it is not, the plan's account of the risk is wrong and Q11
  changes.
- **The offscreen-app route may not load without showing a window, or may hit a
  modal dialog** (H0). CLAUDE.md §2 records that a modal dialog under the
  offscreen platform blocks forever and cannot be interrupted.
- **Saved plots may not be redrawable on another dataset at all** without the
  source's selections (R6). `restore_saved_plot` reports group drift
  (`_report_group_drift`, `saved_plots/controller.py:392`), which suggests
  partial support; R6 finds out.
