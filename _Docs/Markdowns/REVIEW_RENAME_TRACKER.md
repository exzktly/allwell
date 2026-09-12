# Tracker — rename `review/` to `review/`, and lift the shared core out of it

This is a live tracking document, not a write-up. It exists because the work
below is larger than one session can hold in its head, and because progress
reported from memory is progress reported from the parts of the work that were
memorable. Every row is updated **in the same commit as the change it
describes**. The counts in each table header are read off the rows, never typed
from recollection.

**Delete this file in the pull request that closes its last row**, and say in
the body that it is gone.

---

## Why

`review/` is the **Review pane's** package. It reads like the application's
package only because the application was once nothing but the Review pane. The
root now builds three peers — `AnalyzeTab`, `DatasetsTab`, `ReviewApp` — and
of those, two live in a directory that says what it is and one does not.

The import direction settles that these are siblings rather than parent and
child: `analyze/` imports `review` eighteen times and `datasets/` sixteen,
while `review/` imports **neither, ever**. Review does not know the other
panes exist. So the hierarchy is right and only the name is wrong.

Underneath the name sits the substantive half. Every one of those thirty-four
cross-pane imports lands in `review/core/` — never in a feature — because
`core/` is not Review's at all. It is application infrastructure that happens to
live inside one pane's package: the logged-swallow helpers, the settings
wrapper, the profile reader, the debug flags, the font registration, the
cross-thread log relay. That is the same shape the root cleanup removed when the
design-token module left the root for `ui/`: something every pane shares and none
owns, sitting in a place that claims an owner.

Renaming without lifting would bury that infrastructure one directory deeper
under a better name, so the two changes belong to one piece of work and are
sequenced accordingly: **lift first, rename second**, so the lifted modules move
exactly once.

---

## Where lifted code goes, and why there

A new top-level `core/` package: **cross-cutting application infrastructure that
every pane shares and no pane owns.** It is deliberately distinguished from its
three neighbours, because "shared" alone does not place a module:

| package | what belongs there |
|---|---|
| `core/` | shared app infrastructure — knows the application, is not UI, belongs to no pane |
| `ui/` | app-level **UI** every pane shares and none owns (the theme, the help map) |
| `widgets/` | reusable Qt widgets with **no** All-Well knowledge |
| `util/` | Qt-free general helpers, uploaded wholesale to a remote worker |
| `startup/` | what has to run **before the application exists** |

The `util/` boundary is the one worth stating twice: everything under `util/` is
rsynced to a remote worker for a headless pipeline run, so a Qt-importing helper
cannot go there however general it looks.

---

## Phase 1 — lift the shared core out of Review

Selection rule: a module qualifies only if it has **no import of Review's
internals**, so lifting it cannot drag a pane's guts with it. Every candidate was
measured rather than judged by name; the entangled ones are declined below with
the measurement that declined them.

**7 rows — 7 done, 0 todo.** (The declined table below is separate, and none of it was reopened.)

| # | was | lines | external sites | is now | state |
|---|---|---|---|---|---|
| 1 | `review/core/safe.py` | 96 | 13 | `core/safe.py` | done |
| 2 | `review/core/app_settings.py` | 42 | 5 | `core/app_settings.py` | done |
| 3 | `review/core/profile.py` | 182 | 2 | `core/profile.py` | done |
| 4 | `review/core/debug_flags.py` | 99 | 1 | `core/debug_flags.py` | done |
| 5 | `review/core/worker_bridge.py` | 249 | 1 | `core/worker_bridge.py` | done |
| 6 | `review/core/app_fonts.py` | 127 | 1 | `startup/app_fonts.py` | done |
| 7 | `ui_helpers.build_hline_separator` + its vertical twin | 30 | 3 | `widgets/separator.py` | done |

Notes on the two that do not go to `core/`:

* **`app_fonts`** registers the bundled Inter / JetBrains Mono faces with Qt and
  is called once from `allwell.main` before the window exists, beside
  `startup.mpl_bootstrap.configure()`. That is the definition `startup/` already
  carries, so it goes there rather than to `core/`.
* **`build_hline_separator`** returns a `QFrame` with an object name the theme
  paints. It knows nothing about All-Well, which is the stated test for
  `widgets/`. Only this one function moves; the remaining ~1,500 lines of
  `ui_helpers` are Review's.

### Declined, with the measurement

Each of these is reachable from another pane, and each is declined for the same
measured reason: it imports Review's internals, so "lifting" it would either drag
those internals along or leave a module in `core/` that reaches back down into a
pane. A row here is a decision, not an oversight.

| module | what another pane takes | internal imports | why it stays |
|---|---|---|---|
| `core/app_shell_view.py` | `RAIL_W` | 7 (`rail_plate`, `recent_view`, `sidebar_view`, `split_view`, `status_view`, `ui_helpers`, `domain.dataset_name`) | it is Review's shell; hosting the other panes is a fact about the window, not shared infrastructure |
| `core/centre_view.py` | `NamedPageStack` | 15 (`channel_state_controller`, `group_by_controller`, `plot_card_actions`, `tab_controller`, `metric_labels`, …) | same |
| `core/tab_controller.py` | `PANE_MODES`, `current_centre_tab` | 13 (`fold_change_scopes`, `persistence`, `dnd`, `lazy`, `rail_plate`, …) | same |
| `core/plot_card_actions.py` | `PLOT_CARD_ATTRS` | 2 (`plot_orchestrator`, `safe`) | plotting is Review's subject outright |
| `core/reset_scopes_view.py` | `prompt_reset_view` / `prompt_reset_analysis` | 1 (`persistence.reset_scopes`) | the scopes it resets are Review's |
| `core/ui_helpers.commit_focused_notes` | the function | — | the shell asking the Review pane to flush an in-progress edit before saving. A deliberate call into a pane, not infrastructure the pane happens to hold |

The three constants in the first rows (`RAIL_W`, `NamedPageStack`, `PANE_MODES`)
are worth revisiting separately: a layout constant reads like `ui/theme` material
and a page stack like `widgets/` material. Both are out of scope here, and
neither is blocked by this work.


### Two silent breakages the lift caused, both found by looking rather than by a test

Each is a module resolving a repository-level path against its own location, and
each was correct at its old depth and wrong at its new one. Neither raises.

| module | was | is | what the wrong value does |
|---|---|---|---|
| `core/profile.py::_repo_root` | `parents[2]` | `parents[1]` | `profiles_dir()` names a directory above the checkout, no profile resolves, and every build silently falls back to the full application — the exact failure the function's own comment is written to prevent |
| `startup/app_fonts.py::fonts_dir` | `parents[2]` | `parents[1]` | no bundled face registers; the application draws in a system font, which reads as a styling regression rather than a broken path |

Both are pinned now — `tests/test_profile_repo_root.py` and
`tests/test_app_fonts_dir.py`, anchored on `tests._repo_scan.REPO_ROOT` rather
than re-derived from the module's own `__file__`, since a test that computes the
answer the same way the module does accepts whatever the module says. Both were
mutation-proved on HEAD by restoring the pre-lift count and watching them fail
with the wrong path named: three of the five tests fail on the first, two on the
second, so no test in either file is passing vacuously.

This is the third time this exact shape has appeared in this area of the
repository — `startup/mpl_bootstrap.py` and `ui/theme._icon_path` were the first
two, in the root reorganisation. **Any module that resolves a repository-level
path against `__file__` has its address hard-coded**, and moving it is therefore
never only a move.

### One near-miss worth recording

The sweep that repointed `build_hline_separator`'s importers used a regular
expression whose `\s*` matched across a newline, so on the two call sites with a
*combined* import it swallowed the following line and produced syntactically
invalid code. `ruff` caught it immediately; nothing about the sweep's own output
did. A mechanical rewrite over 900 files needs a syntax check as part of the
sweep, not after it.

---

## Phase 2 — rename the package

**4 rows — 4 done, 0 todo.**

| # | what | measured scope | state |
|---|---|---|---|
| 8 | `well_viewer/` → `review/` — the directory and every reference | 866 files, 5,128 rewrites | done |
| 9 | `WellViewerApp` → `ReviewApp` | 247 rewrites | done |
| 10 | The rosters naming the package by string: `PACKAGE_ROOTS`, `ROOT_DIRS`, the spec's package walk, `build_all_well.sh`'s `REQUIRED`, `profiles/*.toml` | 5 rosters | done |
| 11 | The guards naming it in a *rule*, each proven to still bite | 4 guards | done |

Row 9 is part of the same sweep rather than a follow-up. A `review/` package
whose entry class is still called `ReviewApp` is the half-swept state that is
worse than either end, because one directory then holds two spellings of one
concept and neither looks wrong.

Row 11 is the one that cannot be done by substitution. Those guards do not merely
*mention* the package — they forbid importing it, so the string is the rule
itself. A blind rewrite leaves each guard passing while measuring a package name
that no longer exists, which reports as a greener suite.


### How row 11 was proven, since a rewritten rule is the one that fails open

These four guards do not merely *mention* the package — they forbid importing
it, so the string is the rule itself, and a blind substitution leaves each
passing while measuring a name that no longer exists. Each was therefore mutated
on HEAD and watched failing with the violation named:

| guard | mutation | result |
|---|---|---|
| `test_services_do_not_import_review` | `services/env.py` imports `review.domain.stats_compute` | fired, naming the file |
| `test_pipeline_is_self_sufficient` | `pipeline/io.py` imports the same | fired, naming the file |
| `test_layering` | a throwaway `review/domain/` module imports `review.features.barplot` | fired, naming the module and line |
| `test_nothing_imports_reviewapp_at_runtime` | a throwaway `review/core/` module imports `ReviewApp` | fired |

The layering mutation took two attempts, and the first one is the more useful
record: importing a feature from `stats_compute` created a genuine circular
import that killed `conftest` before collection, so pytest printed an
`ImportError` and **no verdict at all**. Grepping that output for "failed"
matched nothing, which is indistinguishable at a glance from a guard that did
not fire. A mutation that prevents the suite from running has measured nothing;
the replacement adds a throwaway module in the scanned directory instead, which
cannot cycle.

### The four spellings a package rename leaves behind

`\bwell_viewer\b` and `\bWellViewerApp\b` between them missed twenty-three
sites, every one of them a *different casing of the same word*, and none of them
would have failed anything:

* `WELL_VIEWER` — the module-level constant three guards use to root their walk;
* `test_..._well_viewer` / `..._wellviewerapp_...` — four test function names;
* `well_viewer3` — a predecessor project named in four module docstrings
  ("extracted from well_viewer3 for incremental migration"), which is a
  changelog note in a docstring and is now simply a description of the module;
* `WellViewer` in one prose sentence in `allwell.py`.

`scripts/rename_sweep_check.sh well_viewer review well_viewer` reports clean.

`_Docs/Markdowns/README_WellViewer.md` keeps its name deliberately: it is the
user manual for the whole application rather than for this package, CLAUDE.md
§5 names it as one of three permanent documents, and `ui/help_topics.py` and two
tests resolve headings inside it. Renaming it is a separate decision about a
document, not part of this package rename.

---

## Verification owed before any of this is called done

- `scripts/prove_guard.sh` cannot prove a guard over code that is new on this
  branch, so anything new here is mutation-proved on HEAD instead.
- The rename is mechanical, which is exactly why it needs the **slow lane**: the
  fast lane never builds an application, so an import that only the real window
  reaches would survive it. `tests/test_app_smoke.py` at minimum.
- After the rename, `scripts/rename_sweep_check.sh review review review`
  for the prose half — a path substitution leaves the bare basename in directory
  trees and layer diagrams, and leaves sentences that now contradict themselves.
