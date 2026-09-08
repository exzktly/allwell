# PR #907 — adversarial review findings

An adversarial reviewer was pointed at the pushed diff of #907 and found eleven
defects, three of them serious. This tracks them to closure.

The review's own summary of why they were not caught: three of the four surfaces
the pull request claims to correct are tested only by handing the rescale to the
function under test, so the shipped renderer can stop reading the correction
entirely and the fast lane stays green. That is CLAUDE.md §7.8 face two, and it
had already been caught once on this branch for the *titles* — the same check
was not then applied to the renderer.

Counts are read off the rows, never typed.

| # | Severity | Finding | Decision | State |
|---|---|---|---|---|
| D1 | HIGH | Deleting the one line in `renderer.draw_response` that reads the correction leaves the whole fast lane green (measured: 8904 passed). All four surface tests pass the rescale in themselves; `test_the_title_and_the_legend_agree_on_one_figure` hands both halves the same rescale by hand, so it can never observe a caller that fails to thread one. | Drive `draw_response` on the real `tab` fixture and assert on `ax.get_legend_handles_labels()` and the caption `Text` — the boundary (§7.4 #1). Then delete the read and watch it go red. | **done** — four tests read the drawn artists. The review's mutation (`_rescale_for(None)`) now fails 3 of them, including the caption showing raw `t=6`; the narrower legend-only mutation fails 2. |
| D2 | HIGH | `domain/fold_change.py:1197` writes `f"t={base_tp:g}h"`, reaching the Response title via `data.normalised`. Measured output under ×4: `… (fold change vs t=6h (ratio)) — t=[24, 48] h, filename value × 4 — vs Dose` — the same timepoints, one clause apart, one corrected and one not. `origin/main` was raw throughout and internally consistent, so this branch made that title worse. | **Blocked on the user.** `controller.py:891` states this suffix is the same string Bar and Line use, so correcting it is a three-tab change beyond this PR's scope. Either widen the PR or decline it with the reason written at the site and here. | blocked |
| D3 | MED-HIGH | The branch splits the app's spelling: Response now reads `t=24 h` while Bar/Line still read `t = 24 h`, for the same plate with no correction set. `test_the_off_state_matches_what_the_sites_used_to_print` asserts the *new* spelling, so the wrong implementation that passes it is the one that shipped. | **Blocked on the user.** Two ways out: give `figure_timepoints_label` the spaced spelling (Response matches Bar again, Heat Map/Distribution stay unspaced), or move the whole app to unspaced (wider, and touches merged work). | blocked |
| D4 | MED | `_KEEPS_FILENAME_HOURS` exempts nothing: the guard's regex requires a `t=` prefix and none of the three allowlisted lines has one. Deleting all three rows leaves 19/19 passing. The liveness test asserts `":g}" in line`, a weaker predicate than the guard's own, so it confirms three exemptions the guard has never used. | Drop the dead rows, or re-key the guard so they are real. Exempt by function name rather than line number either way (§7.5 #10). | todo |
| D5 | MED | The guard misses five shapes (`format(t,'g')`, `"t=" + str(t)`, `f"t={t}"`, an hour named without a `t=` prefix, `%`-format); cannot see `batch_export/response_panel.py`, one of the two files this PR converted; cannot see `domain/fold_change.py`, which holds D2 and which its regex *would* match; and has no `tests/test_guards_bite.py` case. | Widen the scan surface to the files the sweep actually touched, broaden the pattern, add the bite case. | todo |
| D6 | MED | `test_a_figure_with_no_timepoint_drawn_says_nothing_about_when` asserts `"t =" not in title` — a substring the app can no longer produce after D3, so the assertion is unconditionally true. Measured: a mutant naming an undrawn timepoint passes 10/10. | Re-pin it on the spelling the app now produces. Depends on D3's outcome. | blocked |
| D7 | LOW | `figure_timepoints_label` drops non-finite values while `_scaled_tp` and `collect._tp` fall back to the raw number, so `[nan, 6.0]` gives a title of `t=24 h` (singular) over a legend of `['t=nan', 't=24']` — a two-curve figure whose title names one, in the form the docstring reserves for a genuine single. `origin/main` printed `t = [nan, 6] h`: honest about the count. Reachability of a non-finite timepoint measured as possible, not observed in a real dataset. | Make the two agree. Prefer the honest count over the tidy string. | todo |
| D8 | LOW | ARCHITECTURE says `renderer._scaled_tp` is "the single formatter"; `collect.py:234` defines a byte-identical private copy, and `figure_timepoints_label` calls `format_scaled` directly — and per D7 disagrees with `_scaled_tp` exactly where the fallback matters. The guard's failure message names two helpers, neither of which `collect.py` uses. | Reconcile the doc with the code, or the code with the doc. | todo |
| D9 | LOW | Five new `rescale=None` defaults are the anti-pattern `time_rescale.py:80-86` names in its own docstring: they let a call site keep compiling while silently dropping the correction, which is precisely the D1 mechanism. | Consider making `rescale` required on the internal helpers. | todo |
| D10 | LOW | `figure_timepoints_label` raises `ValueError` on a numpy array via `(values or ())`. No live caller passes one; it is a `domain/` helper others will reach for. | `list(values or ())`. | todo |
| D11 | LOW | `_curve_name` always appends an hour while `_curve_label` appends one only when several timepoints are drawn, so on a single-timepoint multi-series figure the caption names `HeLa t=96` against a legend entry reading `HeLa`. Pre-existing; the new docstring overstates the correspondence. | Fix the correspondence or correct the docstring. | todo |

## Also owed

- **The pull request body overstates its own coverage.** It presents the mutation
  table as though the gap it found was closed, when the same gap remains open for
  three of the four surfaces. Rewrite it once the rows above are resolved, and do
  not leave it claiming coverage the tests do not provide.

Delete this file in the pull request that closes its last row.
