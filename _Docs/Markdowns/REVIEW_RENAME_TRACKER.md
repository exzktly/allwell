# Tracker — rename `well_viewer/` to `review/`, and lift the shared core out of it

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

`well_viewer/` is the **Review pane's** package. It reads like the application's
package only because the application was once nothing but the Review pane. The
root now builds three peers — `AnalyzeTab`, `DatasetsTab`, `WellViewerApp` — and
of those, two live in a directory that says what it is and one does not.

The import direction settles that these are siblings rather than parent and
child: `analyze/` imports `well_viewer` eighteen times and `datasets/` sixteen,
while `well_viewer/` imports **neither, ever**. Review does not know the other
panes exist. So the hierarchy is right and only the name is wrong.

Underneath the name sits the substantive half. Every one of those thirty-four
cross-pane imports lands in `well_viewer/core/` — never in a feature — because
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

**7 rows — 0 done, 0 declined, 7 todo.**

| # | module | lines | external sites | destination | state |
|---|---|---|---|---|---|
| 1 | `core/safe.py` | 96 | 13 | `core/safe.py` | todo |
| 2 | `core/app_settings.py` | 42 | 5 | `core/app_settings.py` | todo |
| 3 | `core/profile.py` | 182 | 2 | `core/profile.py` | todo |
| 4 | `core/debug_flags.py` | 99 | 1 | `core/debug_flags.py` | todo |
| 5 | `core/worker_bridge.py` | 249 | 1 | `core/worker_bridge.py` | todo |
| 6 | `core/app_fonts.py` | 127 | 1 | `startup/app_fonts.py` | todo |
| 7 | `core/ui_helpers.build_hline_separator` | 19 | 3 | `widgets/separator.py` | todo |

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

---

## Phase 2 — rename the package

**4 rows — 0 done, 4 todo.**

| # | what | measured scope | state |
|---|---|---|---|
| 8 | `well_viewer/` → `review/` — the directory and every import | 865 files, 5,342 occurrences of the string | todo |
| 9 | `WellViewerApp` → `ReviewApp` | 98 files, 241 occurrences | todo |
| 10 | The rosters that name the package by string: `tests/_repo_scan.PACKAGE_ROOTS`, `ROOT_DIRS`, the PyInstaller spec's package loop, `build_all_well.sh`'s `REQUIRED`, `profiles/*.toml` | — | todo |
| 11 | The guards that name it in a *rule*: pipeline self-sufficiency, the layering test, "nothing imports the app class" | — | todo |

Row 9 is part of the same sweep rather than a follow-up. A `review/` package
whose entry class is still called `WellViewerApp` is the half-swept state that is
worse than either end, because one directory then holds two spellings of one
concept and neither looks wrong.

Row 11 is the one that cannot be done by substitution. Those guards do not merely
*mention* the package — they forbid importing it, so the string is the rule
itself. A blind rewrite leaves each guard passing while measuring a package name
that no longer exists, which reports as a greener suite.

---

## Verification owed before any of this is called done

- `scripts/prove_guard.sh` cannot prove a guard over code that is new on this
  branch, so anything new here is mutation-proved on HEAD instead.
- The rename is mechanical, which is exactly why it needs the **slow lane**: the
  fast lane never builds an application, so an import that only the real window
  reaches would survive it. `tests/test_app_smoke.py` at minimum.
- After the rename, `scripts/rename_sweep_check.sh well_viewer review well_viewer`
  for the prose half — a path substitution leaves the bare basename in directory
  trees and layer diagrams, and leaves sentences that now contradict themselves.
