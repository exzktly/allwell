# CLAUDE.md — working notes for AI assistants

All-Well is a PySide6 desktop app for fluorescence-microscopy quantification: an
**Analyze** pane that runs a segmentation/quantification pipeline over a plate of
raw images, and a **Review** pane that explores the CSVs it produced. Both live
in one window; a dataset folder is the unit of work that flows between them.

**`_Docs/Markdowns/ARCHITECTURE.md` is the canonical map of this codebase**
(~2600 lines, kept current). Read the section that covers what you're touching
before you change it — this file is the short operational layer on top, not a
replacement. `_Docs/Markdowns/README_WellViewer.md` is the user-facing manual.

## 0. Reply style — caveman voice

**Chat replies default to caveman voice.** Drop articles, copulas, and filler.
Short declaratives. No preamble, no "I'll now…", no restating the question, no
summary of what you just said. Fragments fine. Bullets over paragraphs.

```
BAD   I've gone ahead and updated the controller so that it now reads the
      threshold from the typed state object rather than the loose attribute.
GOOD  Controller reads threshold from typed state now. Was loose attr.
```

**Never sacrifice precision for brevity.** Keep every technical specific: file
paths, line numbers, symbol names, exact numbers, error text, the failing case.
Caveman voice cuts *connective tissue*, not *content*. If a fact is load-bearing,
say it — long. Bad news, caveats, and "this could be wrong" always survive the
compression; a hedge you dropped to save four tokens is a lie by omission.

**Applies to chat only.** Anything written to disk or to GitHub keeps normal
prose: `_Docs/Markdowns/*`, code comments and docstrings, commit messages, PR
titles and bodies, review comments. Those are read by humans who lack this
file's context, sometimes years later.

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

- **Fast lane** (~1,770 tests, ~55s): `scripts/test.sh`. Pure-logic, guardrail,
  and per-feature tests. CI invokes this same script, so local and CI runs
  cannot drift in flags or config — don't hand-roll a `pytest` command.
- **Full-app tests**: anything using the `viewer_app` / `loaded_app` /
  `loaded_app_shape` / `image_table_app` fixtures (~70 files, auto-marked `slow`
  by `conftest.py`). Each builds a real `WellViewerApp` (~1,800 QObjects) whose
  Qt↔Python reference web is **never reclaimed in-process** — RSS climbs ~40 MB
  per test, monotonically.

Rules: never run a bare `pytest tests/` or `-m slow` across everything here. Run
full-app tests a file or two per process (budget ≲15–20 app constructions per
invocation). Prefer writing new behaviour so it's testable **without** the full
app — that's why the `collect.py` / `*_compute.py` / renderer split exists. Do
not try to "fix" the leak with `gc.collect()` in a fixture; it doesn't work.

CI also tolerates a PySide6 teardown SIGSEGV by gating on the JUnit report
instead of the exit code — a 139 after a green run is expected, a missing report
is a real failure.

**Run the suite only when asked.** Do not run `scripts/test.sh` — or install the
test stack — as a reflex before each commit. Work lands as a series of commits on
one PR, and a full lane per commit is wasted minutes. Commit, push, move on.

Run it when: the user asks; you changed the test harness itself (`conftest.py`,
`scripts/test.sh`, the pytest config, `requirements-dev.txt`); or CI went red and
you are reproducing the failure. Otherwise **CI is the gate** — it runs on every
push and supersedes its own in-flight runs, so a broken intermediate commit costs
nothing but a red check you then fix.

This trades away local pre-commit signal on purpose. Say plainly what you did and
did not verify — "not run locally, CI will tell us" is the honest line, and it is
never a substitute for reporting a failure you already know about.

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
analyze_tab.py + analyze_tab_form.py   Analyze pane
well_viewer/            Review pane — the bulk of the app
  core/                 cross-cutting infra (state, event bus, controllers, shell views)
  data/                 IO + decode (data_loading, image_identity, zipfile_cache, …)
  domain/               Qt-free models + pure math (stats_compute, fold_change, …)
  features/<name>/      one package per tab — controller/renderer/collect/state/tab_view
  persistence/          JSON read/write, one module per on-disk block
widgets/                reusable Qt widgets, zero app coupling
theme.py, ui/theme/     design tokens + QSS (never hardcode colours)
profiles/*.toml         build profiles (feature subsets)
tests/                  ~315 test files, incl. the guardrails
_Docs/Markdowns/        the three live docs — ARCHITECTURE.md, README_WellViewer.md,
                        GOD_OBJECT_REPOSITORY_ROADMAP.md
```

Where new code goes: pipeline work → `process_microscopy.py` / `pipeline/`; a new
Review tab → a new `well_viewer/features/<name>/` package registered in
`features/registry.py`; behaviour for an existing feature → that feature's
`controller.py`; cross-cutting behaviour → `well_viewer/core/`; a reusable widget
with no All-Well knowledge → `widgets/`.

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
- **Never hardcode colours or pixel sizes.** `theme.Colors.*`, `ui/theme/styles.get_color`,
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
- **`_Docs/Markdowns/` is small on purpose** — three live documents and no more:
  `ARCHITECTURE.md` (the structural map), `README_WellViewer.md` (the user manual),
  and `GOD_OBJECT_REPOSITORY_ROADMAP.md` (the teardown plan, which is a live
  tracking document because its ratchet numbers are asserted by
  `tests/test_guardrails.py`). One-off plans and status write-ups belong in git
  history / PRs, not new markdown files.

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

**Open a PR by default.** Finish a task by committing to the session branch,
pushing with `git push -u origin <branch>`, and opening a pull request — don't
wait to be asked. Follow the repo's PR template if one exists. Then keep the PR
green: watch CI, fix what your diff broke, and resolve merge conflicts against
the default branch yourself. Resolve them by editing the conflict markers —
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
`git diff origin/main -- version.py` before bumping.

Re-check that diff immediately before the merge, not just when you first bump.
Two branches that independently pick the same next number do **not** conflict on
`version.py` — the two sides are byte-identical, so git merges them silently and
both pull requests ship under one version. The number being free when you chose
it is no guarantee it is still free when you land, so a branch that has been open
while anything else merged needs the value re-read against `origin/main` and
bumped again if it has been taken.

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

## 7. Debugging entry points

- Widget you can't find: `grep -rn '_line_fig\b' well_viewer/ widgets/` — every
  Review widget is stored as `_<noun>`. Rail pages → `core/centre_view.py`;
  title bar → `allwell.py`.
- Tab body doesn't appear: its lazy builder raised. Check the log drawer or run
  from a terminal.
- Native crash with no traceback: `allwell.main()` enables `faulthandler`; run
  from a terminal to capture the C stack. Usually a widget touched
  mid-event-dispatch or after C++ deletion.
- Review can't see channels: check `pipeline_info.json` (`fluor_tokens`, `schema`,
  `available_timepoints`) — the viewer reads it in `domain/viewer_state.read_pipeline_info`.
- More: ARCHITECTURE.md §12 (debugging recipes) and §17 (build/environment).
