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
python allwell.py [--data_dir <folder>]        # run the app from source
ALLWELL_PROFILE=thin python allwell.py         # run a feature subset (profiles/*.toml)
python process_microscopy.py --input_dir … --output_dir …   # pipeline only, no GUI deps

ruff check .                                   # lint — CI pins ruff==0.15.8, config in pyproject.toml
pytest tests/ -m "not slow"                    # THE default test command (see §2)
pytest tests/test_app_smoke.py                 # full-app tests: one or two files at a time

scripts/build_executable.sh --clean            # macOS .app (always pass --clean)
ALLWELL_PROFILE=<name> scripts/build_executable.sh   # feature-subset binary
```

Test deps: `pip install -r tests/requirements-dev.txt` (deliberately lightweight —
no TF/Torch/StarDist; those tests mock or degrade). Qt needs
`QT_QPA_PLATFORM=offscreen`, which `tests/conftest.py` sets for you.

## 2. Running tests — read this before typing `pytest`

The suite has two lanes and **running the wrong one hangs the run and OOM-kills
the container**:

- **Fast lane** (~800 tests, ~40s): `pytest tests/ -m "not slow"`. Pure-logic,
  guardrail, and per-feature tests. This is what CI runs.
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
tests/                  ~307 test files, incl. the guardrails
_Docs/Markdowns/        ARCHITECTURE.md + README_WellViewer.md (the only live docs)
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
- **`_Docs/Markdowns/` is small on purpose** — live reference docs only. Plans and
  status write-ups belong in git history / PRs, not new markdown files.

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
the default branch yourself.

**Scheduling your own check-ins is pre-authorized.** `send_later` (and the
equivalent self-wakeup tools) needs no permission — standing grant, never ask.
It is also allowlisted in `.claude/settings.json`, so the harness shouldn't
prompt for it either.
Use it freely to re-check a running CI job, a queued workflow, or an open PR,
and re-arm it as often as the work needs. A silent re-arm is the correct move
when nothing has changed: don't narrate a wake-up that found the job still
running.

**Every PR bumps the version.** No exceptions — a PR that ships anything ships
a new version. Patch (`8.8.24` → `8.8.25`) for fixes, docs, tooling, and small
features; minor for a new tab or a workflow change users must relearn; major is
a human's call, never yours. Bump once per PR, not once per commit: if the
branch already carries a bump that isn't on the default branch yet, edit that
number instead of adding another. Check with
`git diff origin/main -- version.py` before bumping.

Bumping means four sites, all in one commit — `version.py`, the two
ARCHITECTURE.md claims, and the README's `AllWell-X.Y.Z.app` names.
`tests/test_doc_version_sync.py` fails if they disagree.

**The only version claims in the docs** are the current-release ones:
`version.py::__version__` is where the app version lives (the title-bar pill and
the PyInstaller spec read it), and bumping it means updating the "Reflects
All-Well vX.Y.Z" header and the `version.py` directory-map entry in
ARCHITECTURE.md plus the `AllWell-X.Y.Z.app` names in README_WellViewer.md.
`tests/test_doc_version_sync.py` pins exactly those.

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
