# CLAUDE.md — working notes for AI assistants

All-Well is a PySide6 desktop app for fluorescence-microscopy quantification: an
**Analyze** pane that runs a segmentation/quantification pipeline over a plate of
raw images, and a **Review** pane that explores the CSVs it produced. Both live
in one window; a dataset folder is the unit of work that flows between them.

**`_Docs/Markdowns/ARCHITECTURE.md` is the canonical map of this codebase**
(~8,700 lines, kept current). Read the section that covers what you're touching
before you change it — this file is the short operational layer on top, not a
replacement. `_Docs/Markdowns/README_WellViewer.md` is the user-facing manual.

## 0. Reply style

Chat replies follow the output style configured in `.claude/settings.json`
(`"outputStyle": "Concise"`) — lead with the result, skip preamble and closing
recaps, and keep the answer as short as it can be without losing anything.

**Never sacrifice precision for brevity.** Keep every technical specific: file
paths, line numbers, symbol names, exact numbers, error text, the failing case.
Brevity cuts *connective tissue*, not *content*. If a fact is load-bearing, say
it — long. Bad news, caveats, and "this could be wrong" always survive the
compression; a hedge dropped to save four tokens is a lie by omission.

**Applies to chat only — never plan docs, never anything written to disk or
GitHub.** Full normal prose, unabbreviated, for: the plan-mode plan file,
`_Docs/Markdowns/*`, code comments and docstrings, commit messages, PR titles
and bodies, review comments. Those are read by humans (or future you) who
lack this file's context, sometimes years later — do not compress them.

---

## 1. Commands

```bash
scripts/preflight.sh                           # lint + fast lane + tree check, before pushing
python allwell.py [--data_dir <folder>]        # run the app from source
ALLWELL_PROFILE=thin python allwell.py         # run a feature subset (profiles/*.toml)
python process_microscopy.py --input_dir … --output_dir …   # pipeline only, no GUI deps

ruff check .                                   # lint — CI pins ruff==0.15.8, config in pyproject.toml
scripts/test.sh                                # THE test command — CI runs this same script
scripts/test.sh tests/test_app_smoke.py -m ""  # full-app tests: one or two files at a time

scripts/build_executable.sh --clean            # macOS .app (always pass --clean)
ALLWELL_PROFILE=<name> scripts/build_executable.sh   # feature-subset binary
```

On Claude Code on the web, `.claude/hooks/session-start.sh` has already installed
the test deps and the Qt system libraries (`libegl1` and friends — Qt needs
GL/EGL even under the offscreen QPA), so `ruff check .` and `scripts/test.sh`
work from the first turn. It does **not** install TF/StarDist/Torch/Cellpose; the
`slow` segmentation tests still need those by hand. See ARCHITECTURE.md §10.6.

Test deps: `pip install -r tests/requirements-dev.txt` — pinned to the app's
runtime stack (numpy 1.24, pandas 2.2, PySide6 6.7.3) and deliberately without
TF/Torch/StarDist, so the `importorskip` tests for those **skip** in CI. Keep
the pins: as lower bounds, CI resolved numpy 2.x / pandas 3.x against an app
that ships numpy 1.24. pytest config lives in `pyproject.toml`, not
`tests/pytest.ini` — a config under `tests/` loads for `pytest tests/` but not
for a bare `python -m pytest`.

## 2. Running tests — read this before typing `pytest`

The suite has two lanes and **running the wrong one hangs the run and OOM-kills
the container**:

- **Fast lane** (~6,840 tests of ~7,280 collected, across ~690 files):
  `scripts/test.sh`. Pure-logic, guardrail, and per-feature tests. CI invokes
  this same script, so local and CI runs cannot drift in flags or config —
  don't hand-roll a `pytest` command.
- **Full-app tests** (~440 tests): anything using the `viewer_app`
  / `loaded_app` / `loaded_app_shape` / `image_table_app` fixtures, auto-marked
  `slow` by `conftest.py`. Each builds a real `WellViewerApp` (~1,800 QObjects)
  whose Qt↔Python reference web is **never reclaimed in-process** — RSS climbs
  ~40 MB per test, monotonically. It cannot be forced, either: deleting the
  app's widgets from a teardown fixture segfaults the interpreter after the
  tests have passed, which is why the fast lane's widget reaper exempts
  anything marked `slow`.

Three things keep the fast lane fast, all of them in the harness rather than in
any test, and all of them easy to undo without a single test going red.
`tests/test_the_fast_lane_stays_fast.py` is what notices; ARCHITECTURE.md §10.4
rules 7–9 explain each.

1. **A test does not keep the widgets it builds.** An autouse fixture deletes
   the top-level widgets it leaves behind. Without it the process carried
   93,446 live widgets by the end of the `test_a*.py` files alone, and each
   `QApplication.setStyleSheet` walked all of them — 70 s for one call.
2. **The source tree is parsed once and shared.** Go through
   `tests/_repo_scan.parse_source`, never `ast.parse(path.read_text())`.
   Parsing the tree is 2.8 s and sixty-odd guards want all of it. The trees are
   shared, so read them and never mutate one.
3. **No test spawns `ssh` / `rsync` / `scp` / `sftp`.** Use the
   `offline_transport` fixture. The remote layer's seams nest, so patching
   `Fetcher._rsync` still lets three other paths through to the real binary.

**How long it actually takes**, so a run that is merely slow is not mistaken for
one that is wedged:

| | Measured |
|---|---|
| Fast lane, local, quiet container | ~3.5 min |
| Fast lane, local, under contention from other work in the same container | roughly 2–3× that |
| Fast lane, CI job end to end | add ~2 min for checkout and installing the test stack. The job cap is 15 min (`ci.yml`) |
| One slow-lane file of ~15 full-app tests | ~3.5 min, i.e. ~14 s per app construction |

Treat a lane that has crept well past that as a harness regression, not as a
suite that has honestly grown. It read as contention once before — the same
~3.5 min figure sat in this table while the lane actually took 10 min 47 s, and
two thirds of that was three costs no test's own logic accounted for. Run
`scripts/test.sh --durations=40` and read where it went; a cost that lands in
`setup`, or the same number appearing against several unrelated tests, is the
shape of a harness problem.

Re-count rather than trusting these when it matters: `python -m pytest tests -m
"not slow" -q --collect-only | tail -1` prints the split in about two seconds.

Rules: never run a bare `pytest tests/` or `-m slow` across everything here. Run
full-app tests a file or two per process (budget ≲15–20 app constructions per
invocation). Prefer writing new behaviour so it's testable **without** the full
app — that's why the `collect.py` / `*_compute.py` / renderer split exists. Do
not try to "fix" the leak with `gc.collect()` in a fixture; it doesn't work.

CI also tolerates a PySide6 teardown SIGSEGV by gating on the JUnit report
instead of the exit code — a 139 after a green run is expected, a missing report
is a real failure.

**Local runs are the gate; CI is the receipt.** A CI run costs real money, and
`ci.yml` fires `on: pull_request` (plus pushes to `main`). Read that literally,
because it decides the whole cadence:

- Pushing to a session branch with **no PR open yet** starts nothing. It is free.
- Pushing to a branch whose **PR is already open** starts a run — every time.

So **commit and push as you go, and open the PR last.** Commits stay one per
coherent change and go to the branch as soon as they are made; nothing is held
back, and nothing is billed. Opening the PR at the end of the task is what
triggers the one CI run that matters. §6 has the sequence.

Once the PR is open the arithmetic inverts, so from that point batch by **round**
rather than by commit: finish a round of work (a review comment, a follow-up fix,
a second body of work stacking onto the branch), verify it locally, then push it
as one push. Five commits in a round is fine; five pushes is five billed runs,
most of them cancelled mid-flight by the next one — and a cancelled run still
costs.

That puts the verification burden on this machine, which is where it belongs
anyway. Before the push that opens the PR — and before each later round's push —
run what the change touches:

- **The guard family, always.** `test_optional_returns_declared`,
  `test_no_unbound_names`, `test_no_silent_except`, `test_safe_helpers_used_right`,
  `test_architecture_contracts`, `test_layering`, `test_guardrails`,
  `test_complexity_ratchet`, `test_monolith_budgets`, `test_stale_app_attrs`,
  `test_doc_version_sync`, `test_ctx_app_burndown`. Thirty seconds for the lot,
  and they are the checks most likely to fail on something a feature test would
  never look at. Run them as a **set** — picking the four that feel relevant is
  how an unannotated `-> X | None` reached CI.
- **The test files covering what you changed**, plus the ones that import it.
- **`ruff check .`**, which is a second of it.
- **The full `scripts/test.sh` fast lane** when the change is broad, when it
  touches the harness itself (`conftest.py`, `scripts/test.sh`, the pytest
  config, `requirements-dev.txt`), or when you cannot enumerate what it could
  break. ~4 minutes on a quiet container; cheaper than a billed CI round trip
  and far cheaper than a red PR.

Then open the PR and read that one CI run. **Do not wait on CI before the PR
exists** — there is nothing to wait for, because no run has been started.

Report what you ran, honestly and specifically. "The guard family and the four
files this touches, green; the full lane not run" is the shape. Never let a
failure you already know about go unmentioned because the summary reads better
without it.

**A run is evidence only for the tree it ran against.** A lane backgrounded
while the work continues is measuring a tree that no longer exists by the time
it prints its total, and four commits later that total is a number about
nothing — I quoted one into a PR body before catching it. Guards in particular
read the source from disk *during* the run, so a lane straddling an edit is not
even internally consistent. Either hold off starting the lane until the last
commit is in, or re-run it afterwards; and when reporting, say which commit the
number belongs to.

### Waiting on a long run

Slow-lane runs take minutes to hours, so they go in the background — and the
background is where two mistakes cost real time here.

**Don't poll for completion.** A backgrounded run already notifies on exit; a
separate waiter buys nothing and can outlive what it watched.

**Never match processes by command-line string.** `pgrep -f PATTERN` /
`pkill -f PATTERN` match *any* process whose argv contains PATTERN — including
the shell running the command and any ancestor shell whose argv embeds it. So
`until ! pgrep -f "test_foo"; do sleep 20; done` never exits (it matches itself),
and `pkill -f "test_foo"` kills the run you just launched. The `[t]est_foo`
bracket trick drops the immediate shell but not ancestors, so it is not a fix.
Wait on a PID (`until ! kill -0 "$PID" 2>/dev/null`) or on an artifact the job
writes, bound the loop, and kill by PID.

**Keep scratch output out of the repo.** `SLOW_REPORT_DIR` and friends put JUnit
XML wherever you point them; a `git add -A` then sweeps it into the commit, and
adding a `.gitignore` rule afterwards cannot undo that. Read `git status` before
staging.

**Measure before attributing a cause.** A run killed by a timeout with no output
looks the same whether it was slow, starved, or blocked forever on a modal
dialog under the offscreen QPA (`ask_name_dialog` and friends — pytest's
signal-based `--timeout` cannot interrupt Qt's C++ exec loop). Re-run it alone
before explaining it.

## 3. Layout

```
allwell.py              app entry + title-bar shell (two-mode: Review / Analyze)
version.py              SINGLE source of truth for the app version
process_microscopy.py   pipeline orchestrator + CLI (Qt-free, self-sufficient)
pipeline/               Qt-free analysis modules (background, stardist, cellpose,
                        flatfield, shape_features, auto_threshold, io) — re-exported by pm
util/                   Qt-free general helpers (well_token, aw_safe)
services/               Analyze-side services (pipeline/remote runners, config) — Qt-free
analyze/                Analyze pane — AnalyzeTab (tab.py) + the mixins it is
                        assembled from (form, input, preflight, progress,
                        suspend, run_spec, …) + the Remotes dialogs
well_viewer/            Review pane — the bulk of the app
  core/                 cross-cutting infra (state, event bus, controllers, shell views)
  data/                 IO + decode (data_loading, image_identity, zipfile_cache, …)
  domain/               Qt-free models + pure math (stats_compute, fold_change, …)
  features/<name>/      one package per tab — controller/renderer/collect/state/tab_view
  persistence/          JSON read/write, one module per on-disk block
widgets/                reusable Qt widgets, zero app coupling
ui/                     app-level UI shared by every pane — theme.py (design
                        tokens + QSS, never hardcode colours), help_topics.py
profiles/*.toml         build profiles (feature subsets)
tests/                  ~490 test files, incl. the guardrails
_Docs/Markdowns/        the three live docs — ARCHITECTURE.md, README_WellViewer.md,
                        GOD_OBJECT_REPOSITORY_ROADMAP.md
```

Where new code goes: pipeline work → `process_microscopy.py` / `pipeline/`; a new
Review tab → a new `well_viewer/features/<name>/` package registered in
`features/registry.py`; behaviour for an existing feature → that feature's
`controller.py`; Analyze-form behaviour → the `analyze/` module that owns that
part of the form; cross-cutting behaviour → `well_viewer/core/`; a reusable widget
with no All-Well knowledge → `widgets/`.

A new top-level package must be added to `PACKAGE_ROOTS` in
`tests/_repo_scan.py` in the same commit that creates it. Every AST guard
takes its file list from there, so a package the roster omits is invisible to
all of them at once — and because the omission removes checks rather than
adding failures, it reports as a *greener* suite.
`tests/test_scan_surface_is_complete.py` is what notices.

**A new tab is not one edit, and the missing edits do not fail loudly.** A tab
builds itself, and then a dozen pieces of *shared* chrome have to find it —
redraw fan-outs, the plate's replicate-mode behaviour, the card roster the
Screen/Publication toggle walks, the band's slot, the restore router. Most of
those found a tab by iterating a list somebody wrote when there were fewer
tabs, so the failure is always the same and always quiet: the control is built,
it is wired, it writes its state, and the tab it should have reached is not in
the list. Nothing raises. Response Curves shipped missing from **eight** of
them, which is where the guard and the checklist came from:
ARCHITECTURE.md's *Adding a Plotting leaf* table says what to join and what
each omission costs, and `tests/test_a_plot_leaf_is_wired_everywhere.py`
enforces the half that is decidable, stated against the feature registry so the
next leaf is covered by having been declared.

Two habits behind it, both worth applying beyond plot tabs:

- **When shared chrome needs to know about a tab, derive the list from the
  registry** (`features.sub_leaves("Plotting")`) rather than writing one. Where
  it truly cannot be derived, write the roster once and guard it *against the
  thing it is a list of* — a guard that holds one roster against another has
  moved the problem up a level, not solved it.
- **Repaints go through the one fan-out**
  (`plot_orchestrator.redraw_every_scope`), never through a fresh list of tab
  titles. A named tab is a roster of one, and it is always written at a moment
  when it happens to be complete.

An exemption is a row with a written reason, never an absence: a tab left out
on purpose and a tab nobody remembered look identical in code six months later.

File-name suffixes carry meaning (`controller.py` = orchestration, `renderer.py`
= pure matplotlib, `collect.py` = pure data collection, `tab_view.py` / `*_view.py`
= Qt builders, `state.py` = dataclass only, `worker.py` = QThread). The full table
plus its documented exceptions is ARCHITECTURE.md §3; `test_architecture_contracts.py::test_feature_module_naming`
enforces it.

## 4. Architectural contracts — these are tested, not advisory

| Rule | Guard |
|---|---|
| The pipeline imports **no** `well_viewer` / `widgets` / Qt, at any nesting | `test_architecture_contracts.py::test_pipeline_is_self_sufficient` |
| Every repo-root module the pipeline imports is in the remote upload manifest | `test_remote_manifest.py`, `test_remote_runner.py` |
| `services/` stay Qt-free; `widgets/` carry no app coupling | `test_architecture_contracts.py` |
| Nothing imports `WellViewerApp` (controllers take `app` / `ctx` as an arg) | `test_architecture_contracts.py` |
| `domain/` and `data/` never import `features/` | `test_layering.py` |
| Core never imports a feature at module scope — `features/registry.py` is the sole importer | `test_architecture_contracts.py`, `test_guardrails.py` |
| No `ctx.app`; features reach the shell only via frozen `ViewerContext` accessors | `test_ctx_app_burndown.py` (`CTX_APP_BUDGET = 0`) |
| No silent `except Exception: pass` anywhere | `test_no_silent_except.py` |
| A test never names an `app._attr` that exists nowhere in the app | `test_stale_app_attrs.py` |
| No modal dialog reached from a test (it blocks forever offscreen, and pytest's signal timeout can't interrupt Qt's C++ exec loop) | autouse `_no_modal_dialogs` in `tests/conftest.py` |
| The docs state no version literal — `version.py` is the only one | `test_doc_version_sync.py` |
| File line budgets + per-function cyclomatic complexity (threshold 40) | `test_monolith_budgets.py`, `test_complexity_ratchet.py` |
| `runtime_app.py` line/attr budgets, no new loose `self._*` | `test_guardrails.py` |
| Any feature is one-line removable (and deletable from disk) | `test_feature_removal.py`, `test_feature_profile.py` |

**All ratchets only move down.** If a budget test fails, extract a cohesive
module — do not raise the number. If you decompose an allowlisted complex
function, remove its stale allowlist entry (the test fails until you do).

## 5. Conventions and gotchas

- **Never swallow exceptions silently.** Use `well_viewer.core.safe.swallow()` /
  `swallowed()` on the GUI side, `util.aw_safe` equivalents on the pipeline /
  `services` side. Two near-identical helpers exist because the pipeline may not
  import `well_viewer`.
- **Never hardcode colours or pixel sizes.** `ui.theme.Colors.*`, `ui.theme.get_color`,
  or QSS object names.
- **Never block the UI thread.** Heavy work goes to a worker (`features/smfish/worker.py`,
  `services/pipeline_runner.py` are the patterns). Cross-thread callbacks must go
  through a queued Qt signal — a worker thread has no event loop, so
  `QTimer.singleShot(0, …)` from one silently drops the call.
- **State lives on typed objects**, not loose attributes: `app._app_state.<domain>`
  or `ctx.state.<domain>`. Add cross-cutting state to `core/<domain>_state.py`,
  feature-local state to `features/<name>/state.py`. Register widgets via
  `ctx.widgets.register(...)` rather than assigning new `app._*` attributes.
- **Tab bodies build lazily.** Guard with `hasattr(app, "_heatmap_canvas")`;
  never assume a figure exists.
- **Plate selections are well-ID strings** (`set[str]`), app-wide state — a
  tab-local handler that clobbers `_selected_wells` leaks across tabs.
- **End a redraw with** `core.plot_style.apply_export_style_to_current(ctx, fig, canvas)`
  or the Properties sidebar's toggles get lost.
- **`_Docs/Markdowns/` is small on purpose** — three permanent documents:
  `ARCHITECTURE.md` (the structural map), `README_WellViewer.md` (the user manual),
  and `GOD_OBJECT_REPOSITORY_ROADMAP.md` (the teardown plan, which is a live
  tracking document because its ratchet numbers are asserted by
  `tests/test_guardrails.py`). One-off plans, status write-ups and post-hoc
  summaries belong in git history / PRs, not new markdown files. The one
  sanctioned exception is a **remediation tracker** for a body of work too large
  to hold in one session's head — see §6.1. A tracker is live state, not a
  write-up, and it is deleted by the PR that closes its last row.

## 6. Session defaults — docs and PRs

These are defaults, not optional extras. Do them without being asked; only skip
one if the user says so.

**Update the docs with every change.** A session that changes behaviour or
structure updates, in the same commit series:

- **`_Docs/Markdowns/ARCHITECTURE.md`** — whenever you add, move, remove, or
  rewire anything structural: a module, a feature package, a contract, a
  convention, a data flow, a guardrail. Edit the section that already covers the
  area; add a section only for genuinely new ground.
- **`_Docs/Markdowns/README_WellViewer.md`** — whenever user-visible behaviour
  changes: a new tab or control, a changed workflow, new install/run steps, new
  CLI flags, changed outputs.

If a change touches neither, say so in the PR body rather than silently skipping.

**ARCHITECTURE.md is a running description, never a changelog.** It states what
is true *now*. Do not write "new in vX.Y.Z", `(v8.7.2)` markers, "was changed
to", "previously did X", or any other note of *when* something arrived — that
history lives in `git log`, `git blame`, and the PRs. When behaviour changes,
**rewrite the prose to describe the new behaviour** instead of appending a note
beside the old one. `tests/test_doc_version_sync.py::test_no_introduced_in_version_markers`
fails on any version reference outside the two sanctioned current-release sites.

**Open a PR by default — at the end, not at the start.** Commit and push to the
session branch as the work goes (`git push -u origin <branch>`); that costs
nothing while no PR is open, and it means the work survives this container. Then,
once the task is actually done: run the checks §2 lists, push the last of it, and
open the pull request — don't wait to be asked. That is the one CI run, and the
one result to read. Follow the repo's PR template if one exists.

Opening the PR early, then pushing to it a dozen times, is the specific thing
this ordering exists to prevent: each push bills a run, and each new push cancels
the previous mid-flight, so most of what is paid for is never even reported.

Then keep the PR green: read that run, fix what your diff broke, and resolve
merge conflicts against the default branch yourself — batching each round of
follow-up into a single push, per §2. Resolve conflicts by editing the conflict
markers —
`git checkout --ours/--theirs <file>` takes the *whole* file from one side, so on
a doc that conflicts only on the version line it silently discards your prose
edits elsewhere in it. If you do use it, re-apply and verify what it dropped.

**Keep the PR summary current.** The body describes the branch as it stands
now, not what you set out to do. Every push that adds, drops, or changes scope
means editing the description in the same breath — new commits, a reverted
approach, a follow-up fix, a fresh test, a version bump. Treat a stale summary
as a broken build: a reviewer who reads it and finds different code in the diff
has lost the ability to trust any of it. Same voice rule as the docs — normal
prose, written for someone who has not read this conversation. Rewrite the
affected section rather than appending "EDIT:" notes; the body is a description,
not a log.

**Scheduling your own check-ins is pre-authorized.** `send_later` (and the
equivalent self-wakeup tools) needs no permission — standing grant, never ask.
Cancelling one you no longer need (`delete_trigger`) is part of the same grant.
Both are allowlisted in `.claude/settings.json`, under every spelling of the
MCP server name the session may use, so the harness shouldn't prompt either.
Use it freely to re-check a running CI job, a queued workflow, or an open PR,
and re-arm it as often as the work needs. A silent re-arm is the correct move
when nothing has changed: don't narrate a wake-up that found the job still
running. Delete the trigger once the work it watched is finished — a merged PR
does not need an hourly wake-up.

Project settings only apply once the harness trusts them; a remote session that
still prompts is loading none of the file. The fix there is the same allowlist in
the user-level `~/.claude/settings.json`, which needs no trust step.

**Every PR bumps the version.** No exceptions — a PR that ships anything ships
a new version. Patch (`8.8.24` → `8.8.25`) for fixes, docs, tooling, and small
features; minor for a new tab or a workflow change users must relearn; major is
a human's call, never yours. Bump once per PR, not once per commit: if the
branch already carries a bump that isn't on the default branch yet, edit that
number instead of adding another. Check with
`git diff origin/main -- version.py` before bumping. A second body of planned
work that stacks onto a branch whose PR is still open shares that PR, and
therefore that one bump — the failure mode is holding a two-PR model in your
head while the branch carries one. `scripts/preflight.sh` fails when the net
delta against `origin/main` is more than a single increment.

Bumping means **one line** — `version.py::__version__`, which the title-bar pill
and the PyInstaller spec read. The docs state no version at all; they write
`<version>` and point at `version.py`, and
`tests/test_doc_version_sync.py` fails on any version literal outside a short
allowlist of third-party pins. That is deliberate: with more than one branch
open at a time — the normal state here — a version repeated in four files
collides in four files on every rebase, and resolving those conflicts is itself
a way to lose unrelated prose from the same file.

ARCHITECTURE.md still must not say *when* something landed, either — no
`(vX.Y.Z)` markers, no "new in". Same test.

**Another session may be in your files.** Twice in one session a PR landed on
`main` touching the same widget being edited here. Git merged both cleanly and
CI then went red on *semantics*, not conflicts: the other change made an
uploaded well hold a full dial, and a test written here asserted the previous
empty one. Fetch `main` and read `git log origin/main -- <the files you are
about to touch>` when you start **and** again before you push. A clean automatic
merge is not evidence that two changes agree. The version number collides the
same way — that is what the `git diff origin/main -- version.py` check above is
for, and it belongs immediately before the push, not at the start of the work.

**Never `checkout -B` a branch whose PR is still open.** Restarting the branch
from `main` is the right move only once its PR has *merged*. Doing it with the
PR still open silently drops the commit under review; it survived only because
it had already been pushed. Check the PR's state first, and while it is still
open, stack onto the branch and update the PR description instead.

**A failing test may be asserting the bug.** Two tests broke in one session
because the behaviour they pinned *was* the defect being reported — a dial that
reverted to "computed" the moment a download finished, and a dial shaded
identically to a plain well. Before adjusting a tolerance or an expected value,
read what the test was *for*: if it encodes the reported symptom, rewrite it
around the report and say so in the commit. Loosening it would have left the
next regression unguarded.

## 6.1 A plan is a document on disk, or it does not exist

This one has cost more than any bug in this file. A 177-finding review was
worked across eight pull requests, and at the end I reported the tail complete.
A reconciliation pass against the tree found 42 items still owed — a quarter of
the review — including three rated High. Nothing had gone wrong in the code.
What went wrong is that the state of the work lived in conversation, and
conversation is compacted, summarised and eventually lost, while my confidence
that the work was finished survived all three.

The failure mode is specific and it recurs: **progress reported from memory is
progress reported from the parts of the work that were memorable.** The items
that get fixed are vivid; the items skipped because a file looked untouched, or
because a theme was reached at the end of a long session, leave no trace at all.
There is nothing to notice their absence with, so the summary reads complete and
is believed.

So, for any body of work with more than a handful of items — an audit, a review,
a migration, a sweep:

1. **Write the tracker before writing any code.** One row per item, with a state
   and a place. It goes in `_Docs/Markdowns/` (§5's sanctioned exception) so it
   survives a compaction, a new session and a different assistant. A plan that
   exists only in a reply is not a plan.
2. **Update the row in the same commit as the fix.** Not at the end of the
   session, not in a batch, not "once CI is green". A tracker updated in
   arrears is a summary, and summaries are what failed here.
3. **The tracker's counts are derived from its own rows, never typed.** If the
   header says 12 done, twelve rows say `done`. A hand-maintained total drifts
   from the rows within a day and is then the most confidently wrong line in the
   file.
4. **A row goes to `done` only with evidence** — the test that pins it, watched
   failing without the fix (§7.7 #2). "Implemented" is not `done`; `done` means
   the defect can no longer recur.
5. **Report completion from the tracker, not from recollection.** When asked
   whether the work is finished, read the file and answer from it. If the answer
   is "I believe so", the honest answer is "I have not checked" — say that
   instead, then go and check.
6. **Reconcile before claiming the whole body is closed.** Re-read every original
   item against the tree, not against the tracker and not against the report's
   line numbers, which will all have moved. The reconciliation is cheap and it
   is the only thing that catches an item that was never started.
7. **Delete the tracker in the PR that closes its last row**, and say in the body
   that it is gone. A tracker left behind at zero outstanding becomes a stale
   document claiming work is owed.

An item that turns out not to be a defect is `declined` with the reason written
at the site *and* in the row — never silently dropped, because a dropped row is
indistinguishable from a forgotten one, which is exactly the confusion this
section exists to prevent.

**Each finding is a hypothesis about the tree as it stood when it was written,
and the tree has moved since.** Rows are worked over days while the branch keeps
changing, including by the fixes to earlier rows. One row here read "this module
has one importer and four functions with no user"; measured against the tree it
had two importers, and blanking each public function in turn took twenty-four
tests down between them. Two of its six claims were true and were fixed. So
reproduce a finding before fixing it — the same rule §7.3 #3 applies to
"did I break this?" — and when a row is part right, record what was measured
alongside what was fixed. Writing "declined, the module has two importers" is
worth more than a closed row, because the next reader can tell a finding that was
answered from one that was waved away.

## 7. Diagnosing a wrong number

A plot that draws the wrong value — or quietly draws nothing — is almost never a
plotting bug. Work the chain from the data outward, in this order, before
proposing a cause:

1. **Read the run's own failure records first.** The pipeline persists a full
   traceback per failed FOV-group to `<output_dir>/errors/<well>_<fov>_pid<N>.txt`
   and shouts PARTIAL warnings in the run log. When wells or fields are missing
   from the results, that directory usually names the reason outright — exception
   type, well, field, timepoint. Check it before reading any application code.
2. **Verify which input the code actually reads.** For the in/out layout the
   pipeline segments from `in/<well>.zip`, never the sibling `in/<well>/` folder
   (`process_microscopy._process_one_well_zip` → `find_image_groups_in_zip`).
   Reasoning about files the run never opened wastes a whole cycle.
3. **Separate "not computed" from "computed as zero".** Most silent-wrong-number
   bugs here are that conflation: a spread with one replicate, a mask that could
   not be resolved, a scan that came up empty. Zero is a measurement; undefined
   is not. If the code stores `0.0` for something it never computed, that is the
   bug, whatever else is also true.
4. **Say what is established and what is inferred.** A mechanism found by reading
   code is a hypothesis until the data confirms it. State which is which in every
   reply — a plausible cause presented as a conclusion sends the user off to
   re-run a plate for nothing.

**The design rule these keep pointing at: degraded output must never be
indistinguishable from good output.** A partial run that writes a complete-looking
CSV, a cell that renders a different image than the one requested, a bar with no
whisker because nothing was computable — each was a silent substitution, and in
each case the software knew and did not say. When you add a path that can produce
less than it was asked for, make the shortfall visible in the artifact itself
(a marker, a count, a named placeholder), not only in a log line.

## 7.1 When a fix doesn't take

A fix that ships, passes its tests, and changes nothing the user can see is a
different failure from a wrong diagnosis, and it has its own habits behind it.
The staging rewrite took three attempts — the first fixed a neighbouring case,
the second added a fast path that could never be reached, and only the third
found the cause — and the same four mistakes produced all three.

1. **Run the code path; do not read it.** Every one of those attempts came from
   reading a function and reasoning about what it would decide. Not one ran the
   decision against a realistic input and printed the answer. A ten-line script
   that builds a fixture, calls the planner, and prints what it chose would have
   ended it at the first attempt. Reading tells you what the code can do; only
   running tells you what it does.
2. **Read your own instrumentation.** The counter that named the bug — a log
   line reporting how many members of each archive survived the filter — was
   added by the *first* attempt and went unread through the second. When a
   change adds a count, a ratio, or a per-item tick, that output is evidence:
   look at it on a realistic input before claiming the change works.
3. **Verify which branch the app takes, not which branch exists.** A fast path
   guarded by `if fluor_tokens is None` is worthless if the caller always passes
   a list — and the caller here is a GUI that hardcodes it. Trace inward from
   the real entry point (`analyze.run_spec._collect_run_spec` for an Analyze run)
   rather than outward from the function under repair. Describing an unreachable
   branch as the app's behaviour is worse than saying nothing.
4. **Fixtures must carry the mess of real data.** Well over a hundred remote
   tests passed at every step because every archive in them was built by the
   test, holding nothing but well-formed TIFs. Real archives carry `.DS_Store`,
   directory entries, AppleDouble sidecars, and files whose names parse to
   nothing. A corpus of clean inputs validates the happy path and tells you
   nothing else, and it will encode whatever assumption the bug is made of.

The bug class underneath it: **a domain predicate applied outside its domain.**
"Is this the requested channel?" was asked of every zip member, including the
ones that are not images at all. Everything unclassifiable answers "no", so the
whole set looks filtered. When a predicate partitions a collection, first decide
which items it is entitled to judge. Expect siblings — the same misapplication
sat in the zero-match guard and the incremental up-to-date check, so fixing one
in isolation would have traded a slow run for a silently wrong one.

## 7.2 The report usually contains the answer

A bug report from someone who has watched the software do the thing is primary
evidence. It outranks anything you infer by reading code, and it is the cheapest
evidence you will ever get — reading costs tokens the user pays for; asking costs
one turn.

The Image Table LUT bug took four rounds and three wrong diagnoses. The first
message said: *"showing 10 columns and four rows, changing the lut only updated
the first 24."* In a 10-wide grid, 24 is not a whole number of rows or columns,
which excludes every per-row, per-column and per-channel mechanism outright and
leaves a row-major prefix — the answer. That arithmetic took ten seconds and was
done on the fourth round.

1. **Do arithmetic on the reported numbers before opening a file.** A count, a
   grid shape, a ratio, a duration: factor it, divide it by the dimensions you
   were given, ask what it is a multiple of. Most reported numbers are structural
   and rule out whole families of cause for free. A mechanism that cannot produce
   the reported number is refuted before you read a line of it.
2. **Treat the reporter's word choice as load-bearing.** "Redraw" is not "draw";
   "updated" is not "rendered"; "sometimes" is not "always". When your diagnosis
   requires reading their word as a looser synonym, your diagnosis is probably
   wrong. Say which word you are relying on, so they can correct it early rather
   than after you have shipped.
3. **Ask about the artifact, not about the code.** They can see the screen; you
   cannot. Which axis holds what, what is actually inside the cells that misbehave,
   whether the affected region is contiguous or scattered, what the status line
   says — four questions like that closed this bug in one turn after three rounds
   of investigation had not. Ask them *first*, in parallel with the work that does
   not depend on the answers, not after the cheap explanations are exhausted.
4. **Never argue with an observation.** When the user says the cells were there,
   they were there. Re-deriving from code why they might not have been is not
   evidence, and making someone re-assert what they already told you is the most
   expensive thing you can do with their turn.
5. **Reproduce the reported number before changing anything.** The fix that
   finally worked began with a script that built the described grid and printed
   which cells changed: `24 / 40`, in the bottom-right block, matching the report
   exactly. Until your harness reproduces their number, you are fixing a bug you
   have not found.

**Subagents are billed to the user, and they start from zero.** A subagent
re-reads a codebase the parent already has in context, so dispatching one for
work in files you have already opened is pure waste with someone else's money.
Default to inline. Reach for an agent when the work genuinely spans files you have
not read, or when two independent lines of investigation can run at once — not as
a reflex, and not to feel thorough.

Two ways that went wrong here, both worth naming:

- **Never state a subagent's uncommitted working tree as fact.** I told the user
  two helpers "already exist" in the codebase; they had been written minutes
  earlier by an agent and were not on HEAD. Check `git show HEAD:<file>` before
  reporting that something is already there.
- **Overcorrection costs as much as the original mistake.** After being burned by
  CI failures I instructed agents to run every test file matching a grep, which
  pulled in **slow-lane full-app files** at minutes apiece. §2 asks for the guard
  family, the files covering the change, and the fast lane when the change is
  broad — a grep-wide sweep that drags in `loaded_app` tests is not a thorough
  version of that, it is the wrong lane.

**The habit underneath all of this:** when a user reports a symptom, the first
move is to restate their observation as constraints — this number, this shape,
this word — and check each candidate mechanism against those constraints before
investigating any of them. Investigation that has not been narrowed by the report
is just reading, and reading is what produced all three wrong answers.

## 7.3 Verifying the fix, and verifying the verification

§7.1 says run the code rather than read it. This is the next layer: the thing
you run to prove the fix can itself be wrong, and a check that cannot fail is
worse than no check because it is reported as evidence.

1. **A comparison that printed PASS on two empty files is not a pass.** An A/B
   script rendered the Review rail picker on this branch and on a worktree of
   `origin/main`, diffed the JSON, and printed `IDENTICAL`. Both runs had died on
   a `sizePolicy` repr before writing a byte, so the diff compared nothing to
   nothing. Check the exit status and that the artifact is non-empty *before*
   reading the verdict — a harness that fails open will happily confirm anything.
2. **Prove a new regression test fails against the pre-fix code.** Copy it into
   a `git worktree` of `origin/main` and run it there. The dial-colour test came
   back failing for `accent2` and `accent`, passing for `warn` and `success`,
   which did double duty: it proved the test bites, and it named exactly which
   state tokens the bug touched. A regression test written after the fix and
   never seen red is a hypothesis, not a guard.
3. **"Did I break this?" is a question with an experiment.** Two Batch Export
   bugs surfaced while checking a refactor and looked exactly like its
   regressions. A probe run inside a worktree of `origin/main` reproduced both,
   so they predated it by a long way. Reproduce on the base branch before
   calling something yours *or* pre-existing; the diff of the one function
   involved (`_apply_drag` was byte-identical) is corroboration, not proof.
4. **Check the platform API's semantics instead of recalling them.** The
   white-well bug was `QColor.lighter()`, which does not merely raise HSV value —
   once value saturates it *drops saturation*, lightening toward white. Two lines
   in a REPL settle that. I had reasoned confidently from the wrong memory in the
   same session I then measured it.
5. **A shared painter or helper has a blast radius: enumerate the hosts and
   measure one.** Fixing the dial's tone meant touching `_sphere_gradient`, which
   every lit well in the app goes through — fine, but it belongs in the PR body,
   not discovered later. The same change picked `role="rail"` for a panel plate;
   `"rail"` is the one role exempt from the size caps, and measuring both
   branches showed the plate reserving 389px against the old grid's 212, taking
   164px from the group list beneath it.
6. **Do not contradict the doc you wrote in the same commit.** That `role="rail"`
   choice sat a few hundred lines from a new ARCHITECTURE.md row explaining that
   the rail exemption exists *because* the rail picker is Review's primary
   interaction surface. When a change adds a rule, re-read the rule against the
   rest of the change. This one recurred: a module move updated a path inside a
   paragraph and left "It lives in `domain/`" four lines below it, because a
   string substitution feels like it cannot have semantic consequences.
   `scripts/rename_sweep_check.sh` runs the query that finds it (§7.5 #6).
7. **Search the history before building a remembered feature.** A report that
   Batch Export's Quick Setup "used to take an N for serial rows" turned out to
   be Sample Definitions' *Quick Replicates* (`Group along` / `Replicates per
   group` / `Order`, backed by `home.groups.controller.make_replicate_pairs`).
   `git log -S` across the plausible strings found no such control in Batch
   Export at any point. One question beat implementing the wrong thing.

**On self-review.** A fresh agent pointed at the pushed diff — told what the
change claims to do and asked to be adversarial — found six real defects in one
pass, including the `role="rail"` contradiction above and a stale-dial bug that
shipped invisible because no test looked at the plate after a run ended. Worth
doing by default on a diff that touches shared code, and worth reading its
"cleared" section too: it also proved the parts I was unsure of (a threading
question I had flagged to myself turned out not to be a race, because the wells
are partitioned disjointly).

**One refinement to §7.2.** That section says do arithmetic on the reported
numbers, and it is right — but a reported number can be incidental. "Stuck at
`Transferring… 92%`" pointed straight at the label, and 92 was then a dead end:
rsync's last progress line need not be 100%, so guaranteeing a terminal 100%
would have frozen the display at a rounder number. The number located the bug;
the *structure* around it (six writers, both clears inside end-of-run handling)
was the bug. Use the number to narrow, then ask what owns the thing.

## 7.4 What the code computed is not what the screen shows

§7.1 says run the code rather than read it. This is the gap in that: you can run
the code, read the value it returns, and still be looking at something the user
never saw.

1. **Assert the outbound call, not the computed value.** A well downloading on
   the run plate was reported as a pie that "filled to blue". `_well_color`
   returns green for a transfer in flight, and I checked it — twice — and twice
   told the user something that was not on their screen. The defect was that
   `set_well_transfer` pushed the *fraction* to the plate and never the colour,
   so the correct green was recomputed and discarded on every tick. Nothing
   inside the widget was wrong; the call to the outside was missing. For
   anything that renders, sends or writes, put the assertion at the boundary —
   spy on `setWellColor`, on the emitted line, on the argv — not on the
   derivation behind it. A test that reads the internal value passes happily
   while the screen stays wrong, which is exactly what the existing dial tests
   did: they asserted fractions, never the tones or colours drawn with them.
2. **A text heuristic reads paths, not just prose.** `classify_log_line` painted
   a benign note red because the *directory it named* is called `errors/`. The
   same function's docstring already recorded the mirror-image version — a clean
   run's summary reads "wells failed : 0", so a substring test for "failed"
   reddened it — and that lesson had been written down without being
   generalised. When a heuristic scans a whole line, enumerate what else lives
   in that line: paths, well ids, filenames, hostnames, a user's own text. Then
   let a line that states its own severity say so, and keep the guessing for the
   lines that declare nothing.
3. **A transform applied to both sides can erase the difference between them.**
   The progress dial draws two tones in the same pixels. `_lighten` blends
   toward white, which preserves the hue — the whole reason it exists — but it
   also compresses two tones *toward each other*, so the sphere's highlight
   quietly removed the distinction the dial is for. §7.3 says check what an API
   does; the extension is to check what it does to a **pair** of things that
   must stay distinguishable. Wherever one function is applied to both halves of
   a contrast, the contrast is an output of that function too.
4. **Prove the guard fails for the reason you claim.** §7.3 says run a new
   regression test against a worktree of `origin/main`. Mine failed there — with
   an `AttributeError` on a constant the fix introduces, having measured nothing
   at all. That is a false proof of exactly the kind §7.3 opens by warning
   about. Re-run against the old code's *own* values, and read the failure
   message to check it says what you expected it to say.

## 7.5 Sweeping, and the guards that hold a sweep

§7 through §7.4 are about one defect. This is the other job: changing the same
thing in a hundred places and writing the test that holds it. The test failing
quietly is the worse half, because it gets reported as proof.

Everything here came from one branch, all of it found by reviewers after the work
had been called verified — and four of the mistakes were rules already in this
file, broken in the session's second half on the Nth repeat of an operation.
§7.4 #4 was not forgotten: every guard *was* run against `origin/main`, but
somewhere around the sixth one "1 failed" started being accepted as proof
without reading the failure type. A rule you have to remember decays exactly
when you need it, and the decayed version looks identical from the outside.
Three of these are commands now. Run them instead of recalling them.

1. **Prove a guard with `scripts/prove_guard.sh <test_file>`, not by eye.** It
   builds a worktree of the base ref, carries across any `tests/` support module
   the guard imports, and fails unless the result was an `AssertionError` naming
   sites — reporting how many. Two of eight hand-made proofs were an
   `ImportError` and a `NameError` raised while formatting the guard's own
   failure message; both look like "it failed on main" in a terminal and both
   measured nothing. It also reports *passed on the base*, which means the rule
   already held and the test is a hypothesis rather than a guard.

   **Read the site count it prints against your own.** The proof measures the
   *guard*, not only the code: a sweep that converted three sites, proved by a
   guard that names two, has found a blind guard rather than an over-eager
   sweep. That is exactly how the CSV-dialog guard was caught reading only the
   string literals *inside* each call and walking past the one site that held
   its filter in a module constant — the site that had actually shipped. Any
   mismatch between the two numbers is a finding.

   **It cannot prove a guard over code that is new on this branch.** The base
   worktree has no such module, so it reports `NOT PROVEN (ImportError)` and has
   measured nothing — twice in one session I reached for it anyway. Before
   running it, ask whether the symbol the guard names exists on the base ref;
   when it does not, the mutation goes on HEAD instead, per §7.7 #2.

   **That proof expires, so it cannot be a CI check.** Once the fix merges, the
   rule holds on the base and the script correctly reports "passed on the base"
   for ever after — a CI step doing this would go red the day it merged. The
   durable half is `tests/test_guards_bite.py`: every guard is handed a
   synthetic file containing the violation it exists to prevent and must fire,
   then a clean one and must not. Add a case there when you add a guard, and add
   one for each evasion you find; the shapes already in it are the ones that
   really slipped past — the conditional `setFrameShape`, the aliased `QFrame`,
   the pixel literal wrapped in a `max()`, the `[0-9]` spelling of `\d`, the
   roster with a stray string in it.
2. **A hardened guard that passes on the first run has probably not been
   hardened.** A helper added to make the pixel guard see wrapped literals left
   the suite green, and green was read as success; the replacement string had
   not matched, so the helper was dead code and the guard still measured what it
   always had. Make it red on the case it used to miss, *then* fix the sites,
   *then* watch it go green.
3. **The scan surface is half the rule.** Three AST guards landed in one branch
   with three different ideas of which files to read, each blind exactly where
   another could see: the divider guard could not see `analyze/form_chrome.py`,
   which builds dividers, and the well-token guard could not see `pipeline/` or
   `process_microscopy.py` — the call sites `util/well_token.py`'s own docstring
   names as the reason the rule exists. Roots come from `tests/_repo_scan.py`;
   a guard that needs its own says why in the file.
4. **Report the count the guard produces, not the count your grep produced.**
   "Three copies of the plot-card roster" became five once the guard could see
   every module, and two of the missed ones carried the same six-of-eight
   staleness the commit claimed to be fixing. A headline number that came from
   your own search describes your search, not the codebase.
5. **Convert every sibling that answers the same question, including the ones
   spelled differently.** Five "is this directory a well?" predicates in
   `services/remote_runner.py` were converted and the six
   `glob("[A-H][0-9]*.zip")` calls beside them — several in the same function —
   were not. A glob is a predicate. The two then disagreed in both directions,
   so FOLDER-mode staging zipped `a1/` into `a1.zip` and could not find it
   again: source folder deleted, plate uploaded one well short, and the error
   blamed the filename schema. **Half-swept is worse than unswept**, because one
   function now holds two definitions of the same word and neither looks wrong.

   **Find the remaining siblings by searching for the concept, not for the
   symbol you just deleted.** Deleting a private helper and converting its
   callers leaves nothing to grep for, so the sweep reports itself complete: the
   CSV Save-As sweep converted the seven sites that called
   `export/service._ask_save_csv` and stopped, and grepping for the *string*
   `"CSV files ("` then found two more dialogs in two other features that had
   never gone near that helper. Search for what the code does — the API call,
   the literal, the shape — because the sites that most need converting are the
   ones that never shared a name with the thing being replaced.
6. **After a move, run `scripts/rename_sweep_check.sh <old> <new> [<old-dir>]`.**
   A path substitution leaves the bare basename in directory trees and layer
   diagrams, and leaves the sentence that now contradicts itself — §7.3 #6,
   broken again by exactly the mechanism it describes. The script runs both
   queries: the basename alone, and the old directory name within a few lines of
   the new symbol.

   **A rename with no symbol in it needs the same sweep and has no script.**
   When a function starts cycling over *lines* of wells rather than over wells,
   its name, its module and its call sites can all be untouched while every
   sentence describing it is now false. ARCHITECTURE went on saying `fill`
   "cycles values over the wells in the order given" for the rest of the
   session, in two separate rows, and only the final documentation pass caught
   it. After changing what a function *means*, grep the docs for the old
   concept word rather than for anything you edited.
7. **Measure the thing you are about to name.** An unnamed divider was reported
   as "3 px of near-white, three times the intended weight" and that went into
   ARCHITECTURE. It is a **1 px** line inside a frame that reserves 3 px for it —
   widget geometry described as ink — and several converted sites already pinned
   a height, so for those only the colour was ever wrong. Keep the method beside
   the number, especially before promoting it into the canonical map.
8. **Your own differential is the weakest evidence you can offer.** For the
   `fold_change` extraction the harness was mine, the fixtures were mine, the
   result was zero differences, and that was reported as equivalence. A reviewer
   with different fixtures found a real divergence: a shared helper had added an
   `isinstance` check the original site never had, which turns a `numpy.float32`
   spread into "no error bar" — §7's computed-as-zero-versus-not-computed, in
   the most numerically sensitive module in the app. Say what the fixtures were
   when you report it, and for numeric code get them from somewhere other than
   your own imagination. **No command fixes this one.**
9. **Measure member identity before planning a consolidation.** "These six
   controllers look alike" became a plan to share seven methods. Hashing each
   method's normalised AST across the six showed `_on_queue_requested` had six
   distinct bodies, `shutdown` six, `_on_finished` five, and one `_on_succeeded`
   a different signature — and `shutdown` was the one a naive base would have
   regressed silently, because the tool whose worker blocks in a subprocess read
   needs a different stop call. Estimating a consolidation by eye is the
   reading-not-running mistake wearing a planning hat. **No command for this one
   either.**
10. **Allowlist the site, not the file**, and pair every allowlist with a
    liveness test that fails when an entry stops describing a real violation. A
    colour allowlist justified by one file's icon-painter palette exempted every
    literal in that whole file.

    **Needing an exemption is first a claim about where the code lives.** A
    helper that reports which wells a variable grouping adds has to name the
    pooling function every tab is forbidden to reach for, because comparing the
    two poolings is its entire job — so it belongs in the module that owns the
    seam, next to the substitution, rather than in the controller that prints
    its sentence. Moving it made the exemption defensible and one line long.
    Before widening a guard, ask whether the guard is telling you the code is
    in the wrong place; when the answer really is "this site is entitled",
    exempt it by function name and write the reason twice — at the site and in
    the roster — because a bare name in an exemption list is indistinguishable
    from one somebody added to make a failure go away.
11. **Ruff's F821 is enforced, but it does not see everything.** Stripping an
    `app = ctx.window` bridge left `app` undefined in three functions and lint
    passed, because F821 was in the ignore list; it no longer is, and
    `tests/test_guards_bite.py` keeps it that way. Annotations still slip past
    it — every module carries `from __future__ import annotations`, so an
    annotation is a string that is never compiled — and so does anything only
    `symtable` can see. `tests/test_no_unbound_names.py` covers those. After
    deleting a binding, still AST-scan the touched files for names loaded but
    never bound in scope.

## 7.6 A control that exists is not a control that works

§7.4 #1 says put the assertion at the boundary. This is what happens when you
write that rule down, apply it to the values a figure draws, and never once
apply it to the widget the user has to touch to get there.

The per-sample control editor shipped unopenable. `_open_editor` wrapped its one
call in `with swallowed():` — `swallowed` is the call for *inside* an `except`
block, the context manager is `swallow` — so the `with` raised before its body.
Both routes into the feature go through that closure, so selecting
`Per-sample control…` left the plot in per-member mode with nothing to configure
it, and the `Edit…` link did nothing at all. It survived a lane of ~4,950 tests,
three adversarial review rounds, and a reviewer whose half of the diff contained
the line.

1. **Test that the button does something, not that the button is there.** Two
   tests came within one line: they built a `QComboBox`, called
   `_repopulate_control_combo`, and asserted the per-sample entry was present.
   Neither ever selected it, so `currentIndexChanged` never fired and the
   callback was never reached. Eighteen test files import that module and not one
   drives a widget a user drives. Asserting a control is *offered* is not
   asserting it *works*, and the gap between those two is where every callback
   bug in a Qt app lives.
2. **Testability bias picks the wrong seam, silently.** `open_pairing_editor(ctx,
   scope)` is one line to call; reaching it through the combo needs a built
   popover, a real widget registry and a stub bus — about forty lines of fixture.
   Every editor test took the cheap route, so every editor test entered one step
   past the bug. When the convenient seam is *underneath* the thing that is
   actually wired up, convenience is choosing what you cannot see. Pay the
   fixture cost once, in a file that drives the real control, and let the rest
   stay cheap.
3. **Guards get written for the last war.** Four AST guards landed on that branch
   — unbound names, tuple unpacking, Events-total isolation, the menu seam — and
   every one of them guards a class that had already bitten. The guard that would
   have caught this took two seconds to run and twenty minutes to write, and
   nothing prompted it because nothing had gone wrong that way *yet*. After
   fixing a defect, ask what *else* is decidable from the syntax tree in the same
   file, not only what just broke.
4. **Two helpers with near-identical names and opposite call shapes will get
   swapped.** `swallow` / `swallowed` here; `util/aw_safe` carries the same pair
   for the pipeline side. Nothing catches it — ruff does not type-check, and the
   failure is a runtime `AttributeError: __enter__` (a `TypeError` on 3.11) on a
   line that reads as correct code. `tests/test_safe_helpers_used_right.py` is
   the guard; it also caught a second live site nobody had reported, where
   `swallowed()` sat outside any handler and logged a suppressed failure, with
   `exc_info` and no exception in flight, every time a channel simply had no gate
   to draw.
5. **A reviewer reading for contracts will not find a typo in a wrapper.** Three
   rounds found twenty-two real defects between them — wrong denominators,
   half-swept predicates, labels that lied, layering, dead code — because that is
   what they were pointed at. None of them was a two-token substitution inside a
   suppression wrapper, because such a line is *plausible*: it parses, it reads
   right, and it is not where a reader hunting wrong numbers slows down. More
   reviewers would not have found this one. The remedy is mechanical (a guard) or
   behavioural (a test that presses the button), never another pair of eyes.

## 7.7 A branch that exists is not a branch the app takes

§7.6 is about a control that is offered and does nothing. This is its sibling
one layer down: a *decision* that is written, tested, documented and dead — and
then, once revived, one that reuses work it had no right to reuse. Both shipped
in the same branch, both survived a full local suite, and a reviewer found them
in one pass.

The re-attach feature had a module docstring, a section of ARCHITECTURE, a
measurement justifying it, and fifteen green tests. It could not fire.
`PipelineRunner` opened the journal before `RemoteRunner` read it, and opening
reset the field being read. Fixing that made a worse defect live: the address
was keyed on the pipeline arguments alone, so a plate re-exported with corrected
images — no setting touched, Force not ticked — re-attached to the previous
run's directory, was found complete by the pre-flight check, never ran the
pipeline, and had the old numbers fetched back and reported as success.

Neither is an exotic mistake. Each has a mechanical cause and a mechanical
answer.

1. **Test the caller's sequence, not the unit's API.** Both ends were correct in
   isolation; the *order* was the defect, and an order is invisible to a unit
   test of either end. The test that should have caught it recorded an address
   and read it straight back inside one run — a sequence the app never performs.
   Whenever two modules touch one piece of state, write down the order the app
   runs them in and drive a test through it. If you cannot name which function
   writes and which reads, you do not yet know whether the feature works.
   `tests/test_run_journal_reattach.py::test_the_stored_address_survives_the_app_s_real_call_order`
   is the shape.
2. **Kill the feature and check a test notices.** The one question that separates
   a tested feature from a decorated one: *what edit would make this fail?* Make
   that edit in a `git worktree` of HEAD, run the suite, and require a real
   `AssertionError`. `scripts/prove_guard.sh` does this for guards against the
   base branch; for a feature the base branch is useless, because the feature is
   new there — mutate HEAD instead. Reverting one line of the fix made three
   tests fail with the reasons in their own messages. Before that fix, all
   fifteen passed against code where the feature was unreachable.
3. **A reuse decision must be keyed on everything that invalidates the reuse.**
   "We already did this, use the old result" is the highest-stakes predicate in
   any pipeline: too narrow a key returns stale numbers and reports success —
   §7's cardinal sin, arrived at by omission rather than by a bug. Enumerate what
   makes the old result wrong *before* choosing the key, and write the reason
   beside each key. `tests/test_reuse_is_keyed_completely.py` holds the half that
   is decidable: every declared key, mutated alone, must decline. The other half
   — that nothing outside the keys can invalidate the result — no test can
   decide, which is exactly why the roster there carries a written justification
   per key. That file caught a live gap in the fix that introduced it, within a
   minute of being written.
4. **A measurement of one cause does not license a feature that fires on all of
   them.** I measured that the upload cache key churns when staging rewrites the
   archives, and then built a feature triggered by the key churning *at all* —
   and the same key also churns when the user replaces the images. When a signal
   triggers an action, enumerate every reason that signal can appear and say
   what the action does in each. One measured cause is one row of that table.
5. **Writing confident documentation is not evidence.** The ARCHITECTURE prose,
   the module docstring and the PR body all asserted the behaviour, and writing
   them raised my confidence rather than testing it. Prose describing a branch
   is worth exactly what the branch is worth; §7.1 #3 already says an unreachable
   branch described as the app's behaviour is worse than saying nothing, and
   documenting it well is how that gets worse still.

**The check that costs nothing and would have caught all of it:** before
reporting a behaviour-changing feature as done, state out loud the single edit
that would break it, then make that edit and watch a test fail. If no edit
comes to mind, or the suite stays green, the feature is not tested — however
many tests are passing.

## 7.8 My tests agree with me, which is why they pass

Every section from §7.3 onward is a variation on "the thing you ran to prove the
fix can itself be wrong", and I have gone on making the same mistake anyway —
about ten times in one session, on the code I had just written each time. Not
ten different mistakes. **One mistake with two faces**, and the reason it keeps
winning is that in both faces the test is *green*, so nothing asks a second
question.

**Face one: the stub is mine, so it cannot disagree with me.** A
`SimpleNamespace` ctx has exactly the attributes my code happens to read,
because I added them when my code raised. It can therefore never catch the two
things that matter most — that the *real* object has no such attribute, and
that the real caller never runs this code at all.

- The Response tab raised `TypeError` on **every real app construction from the
  moment the package existed**: `build_plot_save_menu`'s required `cells_cb` was
  never passed. About 130 of its own tests were green, because every one drove a
  control through my stub and **not one called `build_response_tab`**.
- `draw_response` called `apply_ax_style(ax)` against a three-argument function.
  Invisible because my own fixture stubbed that helper.
- A Recent Datasets test wrote `monkeypatch.setattr(..., raising=False)`, which
  **created** the `_qsettings` attribute the shipped app does not have.

**Face two: the assertion is weaker than the claim.** I assert a property that
the broken implementation also satisfies, so the test passes both ways.

- "The two colours differ" — true of the palette strategy I was replacing *and*
  the one replacing it. Four mutations, including deleting the whole decoration
  call, left the file green.
- "`focus_timepoint` still reads 24.0" — on a fixture whose seed *was* 24.0, so
  a tab that wrote the field wrote the same number back.
- "The readout says `ringed`" — passed with `setWellRings` deleted.
- "The module mentions the clause somewhere" — passed with three of four
  surfaces silent.
- `ResponseData(placed=3)` built by hand — deleting the line that fills `placed`
  left the lane green.

### The check, at the moment of writing the test

Both faces die to one question, asked before the test is kept:

> **Name the wrong implementation that still passes this.**

If one comes to mind, the test is not finished. "The colours differ" → name it:
*the palette strategy I just replaced*. "The control is offered" → *a combo
connected to nothing*. Naming it takes seconds and is the whole of the fix.

Three rules follow, and they are mechanical rather than a matter of remembering:

1. **Assert the value, never a property of it.** Not "they differ" but "it is
   `palette["C1"]`". Not "a sentence was drawn" but the sentence. A property is
   satisfied by a set of implementations and only one of them is right.
2. **Build the fixture so the right and wrong answers differ.** This is the one
   I keep skipping. The batch collector inks members from the same
   `WELL_COLORS` in member order, so with values assigned *in* member order the
   decorated and undecorated figures are byte-identical — no assertion of any
   strength could tell them apart. I had to assign the values *against* the
   member order before the test could measure anything. Ask: *what does the
   broken version produce here?* If the answer is "the same bytes", change the
   fixture, not the assertion.

   Two shapes of this keep recurring, and both feel like careful test-writing
   at the time. **The fixture is more varied than reality**, so the collision
   the defect is made of cannot occur in it: a test written to reject ids
   minted from a fixed placeholder named its two variables `Dose` and
   `Cell line`, which mint two *different* slot ids — while the editor creates
   every variable in the application with the placeholder `Variable`. The test
   passed against the exact code it was written to fail. Before building an
   input, go and read what the caller actually passes; a realistic-looking
   value is not the same thing as the real one, and where the real one is
   uniform, variety in the fixture hides the bug. And **the fixture's ordering
   makes two answers agree**: a fallback that says "when two members disagree,
   use the parsed number" was tested with the labels in the order where "keep
   whichever arrived first" produces that same number. Deleting the collision
   check left it green. When a rule picks between candidates, order the
   candidates so that every wrong rule picks something else.
3. **One test per feature goes through the real constructor.** Not the seam I
   just wrote — the entry point the app calls: `build_<feature>_tab`, the
   registry hook, the real widget. Everything else may stay cheap and stubbed.
   §7.6 #2 said this about controls; it is just as true of builders, and the
   builder is worse, because `centre_view` **catches** a failing builder, logs
   it, and drops the title from the pending map — so the tab renders as an empty
   page and `test_drain_builds_all_centre_tabs` goes on passing.
   `test_app_smoke.py::test_no_tab_builder_raised_while_draining` reads that log
   instead and costs two seconds. It covers **centre pages**; Home's definition
   editors build through a different path with a swallow of their own, and are
   covered by `test_home_page.py::test_every_definition_tab_is_built_after_a_load`,
   because `built[name] = True` is stamped only on success. Two guards, two
   mechanisms, neither covering the other's half.

   That last sentence is only accurate because writing this section caught me
   getting it wrong. I first wrote "covers every tab", then widened the guard's
   pattern to make that true, then — applying rule 2 above — broke a Home
   sub-tab to see the widened guard catch it. **It did not.** The section's own
   check, run against the section's own claim, one paragraph after writing it.
   Which is the honest measure of how strong this habit is: knowing the rule is
   not the same as applying it, and the only thing that reliably applies it is
   building the case where right and wrong differ and *looking*.

### What actually catches it, and what does not

**Mutation catches it. Review and the lane do not.** Three times in one session
a mutation either made a *failing* test pass or left everything green, and each
time that was the only signal. A reviewer reading for contracts does not slow
down at a plausible-looking assertion, and the fast lane cannot see a builder it
never calls. So when §7.7 #2 says make the edit and watch a test fail: watch
*which* test fails and *why*. A mutation that changes nothing has told you the
test measures nothing — that is a finding, not a nuisance to work around.

**And the lane you did not run is where this lives.** The fast lane never builds
an app, so every defect above was invisible to the check that gates every push.
The slow lane found the Response builder within one dispatch. Before calling a
feature done, ask which lane would notice if it were completely broken — and if
the answer is "none of the ones I ran", that is the work, not a formality.

## 7.9 Debugging entry points

- Widget you can't find: `grep -rn '_line_fig\b' well_viewer/ widgets/` — every
  Review widget is stored as `_<noun>`. Rail pages → `core/centre_view.py`;
  title bar → `allwell.py`.
- Tab body doesn't appear: its lazy builder raised. Check the log drawer or run
  from a terminal.
- Native crash with no traceback: `startup.boot_diag.arm_faulthandler()` runs at import
  and writes the C stack to the **boot log**, so it survives a crash that left
  nothing in the terminal. `~/.cache/allwell/allwell-boot.log`
  (`%LOCALAPPDATA%\allwell` on Windows, the temp dir if neither is writable),
  truncated per launch with the last three crashes kept beside it as `.1` / `.2`
  / `.3`. Ask for that file before theorising: the cause is usually a widget
  touched mid-event-dispatch or after C++ deletion, and no amount of reading
  narrows that down the way one stack does.
- Review can't see channels: check `pipeline_info.json` (`fluor_tokens`, `schema`,
  `available_timepoints`) — the viewer reads it in `domain/viewer_state.read_pipeline_info`.
- More: ARCHITECTURE.md §12 (debugging recipes) and §17 (build/environment).
