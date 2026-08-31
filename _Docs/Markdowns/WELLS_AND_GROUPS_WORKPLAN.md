# Unifying how wells and groups are defined

There are four user-facing ways to say something about a well, and nothing in
the application states what each is for, which one takes precedence, or that the
word "group" means two different things depending on where it is written. This
file is the state of the work that fixes that; it is not a design essay and not
a changelog.

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
   document claiming work is owed. `tests/test_workplan_is_honest.py` goes with
   it.

Status glyphs: `☐` not started · `◐` in progress · `☑` done with evidence ·
`⊘` declined, reason in the note.

## What is actually there today

Four surfaces, four stores, one shared confusion. This table is the starting
state and is not edited as the work proceeds — the rows below are what moves.

| Surface | Stores | Shape | What it reaches |
|---|---|---|---|
| **Plot Groups** — the rail drawer under the plate map | `persistence.json::plot_groups`, mirrored into the `sample_definitions` block | a named saved *well selection* | recalling that selection, and nothing else |
| **Groups** — Home → Groups | `pipeline_info.json::sample_definitions.selections` | named well set + colour + hidden flag + drag order; a group **is** exactly one replicate set | pooling on every plot, Statistics, Batch Export, and the fold-change `ControlRef` key space |
| **Variables** — Home → Variables | `pipeline_info.json::variables` | a named axis, typed, with **ordered** values, assigned per well | pooling (Group by), ink (Colour by), bands (Split by), the Response x axis, the two-factor tests |
| **Well Labels** — Home → Well Labels | `sample_definitions.well_labels` | free text per well | display substitution only — legends, tick labels, exported names |

A fifth exists in a file format rather than on a tab: Batch Export's
`bar_groups.json` `BarGroup`, which — unlike a Home group — can hold several
member sets plus solo wells.

**The pooling engine is already unified and is not what is wrong.** Every tab
asks `domain/plot_members.plot_replicate_sets`, a chosen variable substitutes
for the user's groups there, and a variable that groups nothing falls back to
them. What is missing is entirely on the declaring side: nothing says what each
surface is *for*, and nothing states the substitution anywhere the user declares
things.

## The taxonomy the application never states

Three questions, one answer each. Every row below is in service of these three
sentences being true, visible, and contradicted nowhere:

- **Group** — *which wells are replicates of one condition?* Pooling unit,
  colour, plot order.
- **Variable** — *what did you change across the plate?* An axis with ordered
  values. You need no groups to use one.
- **Well label** — *what should this well be called on a figure?* Display only.

**The stores are not merged, and that is a decision rather than an omission.**
The per-well versus per-group split was settled against three facts checked in
the tree — groups partition the plate, `make_selection` is closed and drops a
smuggled field, and `bar_pool_replicates` flips the plot key space from set
names to well tokens mid-plot. Folding well labels into variables fails on its
own terms as well: ninety-six distinct values makes the ordered value list, which
is the whole reason a variable is not a set of strings, meaningless. The
confusion this work closes is comprehension and surface, not schema.

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
degraded result that looks like a good one.

**2. Does it behave as claimed?** Not "do the tests pass" — the reviewer names
the single edit that would break the feature, and checks a test would catch it
(§7.7). This work is unusually exposed to §7.6 and §7.8: most of its deliverables
are *sentences on screen* and *controls that switch a layer*, and a sentence
asserted by a test that only checks the module mentions it is exactly the
assertion §7.8 records passing against three silent surfaces. Every row that puts
text on screen is pinned at the widget, not at the function that composes it.

**3. Is it over-architected?** The repo's risk profile is a registry for two
things and a seam for a second implementation that never arrives. A layer switch
over three layers does not need a plugin protocol.

The reviewer reports findings; it does not apply them. Each finding becomes a
row here — fixed, or `⊘` with the reason written at the site as well as in the
row.

## Sequencing

```
A vocabulary ─────────► B signposting ─────────► C one plate ─────────► D precedence
```

Strictly ordered, unlike the previous tracker's independent workstreams, and for
a reason: B writes the sentences, so it must not write them in a vocabulary A is
about to change; C moves those sentences onto a merged tab, so writing them twice
is the cost of doing C first; and D's readout names the layer C introduces.

A and B together are the whole of what ships without users relearning the page.
C is the structural half and is a minor version bump on its own terms. Each
letter ends at its review gate, which is that workstream's last row and the
condition for opening its pull request.

## The tracker

| # | Workstream | Item | Status | Note |
|---|---|---|---|---|
| 1 | A | "Plot Groups" becomes "Saved selections" in every user-visible string | ☑ | Seventeen call sites across `core/sidebar_view.py`, `core/ui_helpers.py`, `domain/plot_groups.py`, `home/collect.py`, `persistence/reset_scopes.py`, `persistence/sample_definitions.py`, `persistence/plot_groups.py` and the Image Table's copy of the drawer. The default name a new one takes is `Selection N` rather than `Plot Group N`; existing saved names are stored and are untouched. `persistence.json::plot_groups` and every `plot_group` symbol keep their spelling — renaming an on-disk key is a migration with a compatibility window, and no user reads it. One test pinned the old copy (`test_plot_groups_image_table_source`, asserting the status line a user reads) and was updated rather than loosened. |
| 2 | A | The glossary carries one entry per concept, each saying what it reaches | ☑ | `ARCHITECTURE.md` §14 gains a row per concept — group, sample variable, well label, saved selection — and each states **what it reaches**, which is the distinction the four were indistinguishable on. The README gains a **Which one do I use?** table under *Telling the app what your wells mean*, keyed on the tab a user clicks rather than the model's names. Both are pinned: the glossary test fails when an entry is missing *or* when one never says whether it pools, and both were watched failing on `origin/main` naming all four. |
| 3 | A | Guard: no user-visible string calls a saved selection a "plot group" | ☑ | `tests/test_one_word_per_concept.py`. `scripts/prove_guard.sh` reports PROVEN against `origin/main` naming 17 unique sites; the sweep converted 18 literals on 17 lines (two share `sidebar_view.py:450`), so the guard's count and the sweep's reconcile. The pattern ends at a word boundary after `group`/`groups` so *"a plot grouped by Dose"* — a plot pooled by a sample variable, live in `home/variables/controller.py` — is not claimed, and it requires whitespace so the `plot_groups` symbol and key are not either; docstrings are excluded because a docstring naming the persistence key is documentation. Nine bite cases in `tests/test_guards_bite.py`, three of which must **not** fire. The first version scanned through `parsed_sources` and so ignored the harness's redirection entirely: every bite case passed against the real tree while measuring nothing. |
| 4 | A | Review gate: one word, one meaning | ☐ | |
| 5 | B | A role sentence on each definition tab, present always rather than only when empty | ☑ | `features/home/roles_view.add_role_strip`, from the `collect.DECLARATION_ROLES` roster. Always visible, not only in an empty state: Variables said the right thing and said it in `EMPTY_HINT`, so the only statement of what a variable *is* vanished the moment somebody defined one. That hint is now the payoff alone, with no second copy of the definition. Driven through each tab's **real builder** rather than the strip helper — a strip nothing calls is §7.6's control that ships invisible — and three mutations, one per tab, watched failing. |
| 6 | B | Each definition tab points at the sibling that answers the other question | ☑ | The cross-link is the roster's second field and rides in the same strip. A sentence rather than a link: the reader is mid-decision, not lost, and a click that teleported them to a sibling tab would lose the plate they were looking at. Cell Gating, Ratios and Pipeline Info resolve to `None` through `role_for_tab` and take no strip at all, which is pinned — a gate is a threshold and a ratio is a derived channel, and neither says what a well is. |
| 7 | B | `your_work` reports variables, and says what each declaration does | ☑ | `your_work` gains a **Variables** row and an `effect` on each of the four declaration rows, from the same roster. The Overview report had listed groups, saved selections, labelled wells, smFISH thresholds, ratios and notes, and said nothing about the annotation the plots were pooling by. Asserted **at the widget** as well as at the dict: `_row_widget` draws the effect chip, and a row without one draws no blank column — the dict half alone would pass against a painter that had stopped rendering it. Two mutations watched failing. The `Replicate sets` row is now labelled `Groups`, which is what the tab that edits it is called. |
| 8 | B | The well-label → group-name substitution is visible where it happens | ☑ | `selections_model.groups_relabelled_on_figures` and its `relabelled_groups_clause`, painted under the group list by `selections_view._paint_relabel_note` on every panel refresh — the substitution depends on a group's name, its membership *and* the labels on a different tab, so a note written at build time describes a plate that has moved. Visible groups only: a hidden one is not drawn, so nothing relabels it. The rule lives next to `replicate_display_label`, which owns the substitution. Four mutations watched failing, including one that reported the group's own name in place of the drawn label — the test asserts the **exact** sentence, because "a sentence was produced" is satisfied by a sentence naming the wrong thing. |
| 9 | B | ARCHITECTURE and README describe the page that exists | ☑ | ARCHITECTURE's Home section listed five sub-tabs where there are six, said "five builders" and "five editors", and described the rail plate's three states without Variables — which is the tab where the defect actually shows, since it carries a plate of its own and so puts two on screen with one greyed. All corrected, and the roster documented where the Home page is. The manual's Well Labels section claimed labels "give wells / groups human-readable names"; a group is renamed on the Groups tab, and the one place a label *does* reach a group — the auto-generated-name substitution — is now written down there instead of implied. |
| 10 | B | Review gate: three roles stated once each, contradicted nowhere | ☐ | |
| 11 | C | A single **Plate map** tab with a layer switch replaces the three editors | ☐ | Layers: Groups, one per defined variable, and Labels. One tab in the registry where three were, with the sub-packages kept as the layer bodies rather than flattened — the removability contract (`test_feature_removal.py`, `test_feature_profile.py`) is per feature package and the profiles name these tabs. |
| 12 | C | One editor plate in the page body; the rail plate is hidden across Home's definition tabs | ☐ | Today Groups edits through the **rail** plate, Variables carries its **own** `role="editor"` plate while the rail plate sits beside it greyed (`home/controller.py` sets `plate.setEnabled(desc.reads_well_selection)`, and Variables declares `False`), and Well Labels is 96 `QLineEdit`s. Three interaction models for "point at some wells", two of them on screen at once. The Variables plate is the precedent to keep. |
| 13 | C | Per-layer actions, on one selection: group chips, value chips + fill row, label entry | ☐ | The selection is the plate's, shared across layers, so switching layer keeps the wells you had picked. Cell Gating still reads the app-wide well selection and is **not** folded in — it declares a threshold, not an annotation, and belongs where it is. |
| 14 | C | Profiles, `removable` flags and `reads_well_selection` follow the merge | ☐ | `Groups` and `Well Labels` are `removable=False` because `centre_view.build_centre` builds the rail plate that edits groups at startup with no descriptor check; row 12 removes that reason, so the flags are re-derived rather than carried across. `reads_well_selection` is retired for any tab that no longer asks the rail plate anything, and `test_stale_app_attrs` / the guard family are run as a set. |
| 15 | C | Guard: at most one live plate on any Home tab | ☐ | The defect row 12 fixes, made undoable. Two plates where one is greyed is the state that shipped; a guard that only counts *visible* plates would pass on it, so it counts enabled ones. |
| 16 | C | Review gate: one editor, no affordance lost | ☐ | Enumerate every control the three tabs offered — Quick Replicates, + Add's plate picker, group composition, Add Prefix / Add Suffix, the fill row, value chips, the 96 free-text cells — and confirm each has a home. A merge that quietly drops one is the §7.5 #5 half-sweep in a new shape. |
| 17 | D | A persistent readout naming what `plot_replicate_sets` actually decided | ☐ | Derived from the seam, never recomputed: "Plots are grouped by **Dose** (6 values); your 4 groups are not pooling", or the plain "Plots are grouped by your 4 groups". `group_by_controller.group_by_shortfall` already says what a grouping *left out* and reaches the figure title; this says what it *is*, which is the half nothing states. |
| 18 | D | The readout reaches Home, not only the figure | ☐ | The precedence question is asked while declaring, on the page where groups and variables are defined, and answered three clicks away on a figure title. Both surfaces read the one function. |
| 19 | D | Guard: the readout is derived from the seam | ☐ | `tests/test_group_by_variable.py` already fails a module that reaches past `plot_replicate_sets` to `active_replicate_sets` with a ctx in hand; extend it to the readout rather than writing a second rule. A sentence that recomputes the decision can disagree with the plot it describes. |
| 20 | D | Review gate: one pooling decision, stated once | ☐ | |

---

## A — One word for one thing

The rail drawer's **Plot Groups** and Home's **Groups** are different objects
with different stores, different lifetimes and different consequences, and the
README has to spend a paragraph saying so ("Groups are *plotting selections
only* — not replicate sets or stats groups"). A paragraph that exists to
disambiguate a name is the name telling you it is wrong.

**Saved selections**, because it says what the thing is — a set of lit wells you
can get back — and because the widget behind it is already
`widgets/saved_selections_list.SavedSelectionsList`. "Well sets" was considered
and rejected: "set" already means replicate set in the glossary, so it trades
one collision for another.

The on-disk key stays `plot_groups`. Renaming a persistence section is a
migration with a compatibility window, and no user reads it.

## B — Say what each declaration is for

The three role sentences are the deliverable, and the risk they carry is §7.8's:
a sentence is easy to assert weakly. "The module mentions the clause somewhere"
is the assertion that passed while three of four surfaces stayed silent. Every
row here is pinned at the widget that renders the text.

Row 8 is the one with a real defect under it rather than an absence. A group
named `1` draws its member well labels on the axis instead of its name, so a
label typed on the Well Labels tab silently becomes a group's identity on every
figure — the two mechanisms the user cannot tell apart are, in this one place,
genuinely wired together, and nothing says so.

## C — One plate, one selection

The three editors ask the same question — *which wells?* — through three
different controls, and on the Variables tab two plates are on screen at once
with only one of them live. That is the mechanical half of "hard to know when to
use which": the page looks like three unrelated tools because it is built as
three unrelated tools.

The merge keeps the three sub-packages as layer bodies. Flattening them into one
module would put the group rules, the variable rules and the label grid in one
file and lose the removability each descriptor currently carries.

Cell Gating stays out. It reads the well selection, so it looks like a fourth
annotation editor, but it declares a *threshold* — a property of the analysis,
not of what the wells mean — and folding it in would make the taxonomy above
false on its first day.

## D — Precedence, stated where it takes effect

`plot_replicate_sets` already decides this for every tab at once, and the band
combo already spells the choice (`Wells / groups` against a variable name). What
is absent is any statement of the *consequence* at the moment it matters: a user
who has painted four groups and then picks `Group by: Dose` has had those groups
stop pooling anything, everywhere, and the only thing that mentions it is a
shortfall sentence about wells the grouping left out.

The readout is derived from the seam. A second computation of "what are we
pooling by" is a second answer, and the failure mode is a sentence that
confidently describes a figure that is not on screen.
