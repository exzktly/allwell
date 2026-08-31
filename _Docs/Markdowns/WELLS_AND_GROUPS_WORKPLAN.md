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
| 11 | C | A single **Sample map** tab with a layer switch replaces the three editors | ☑ | `features/home/sample_map/`. **Named Sample map, not Plate map**: "plate map" already names the rail's well picker in the manual, and coining a second meaning for it in the same branch that cut "group" down to one would have been the exact mistake workstream A exists to fix. Three pages in a `QStackedWidget`, each built by its own package's **unchanged** builder, and `LAYERS` derived from `DECLARATION_ROLES` so a declaration with no editor is impossible. **Deviates from the plan in one way**: the plan said one plate, and it is three taking turns in one slot. A single selector painting group colours, value colours and free text is the "two plates meaning different things" hazard turned inward, with two painters racing to say it — and a label cell takes free text, which a `WellPlateSelector` cannot show at all. What the user sees is one plate; what stays separate is the painters. |
| 12 | C | One editor plate in the page body; the rail plate is gone from Home entirely | ☑ | **The plan said hide it; the measurement said remove it.** Home's rail plate was documented as the picker Cell Gating drew its CDFs from and Add Prefix applied to. Probed rather than read (§7.1 #1): it never wrote `ctx.state.selection.selected_wells` at all — it composed a group and nothing else, so both of those ran off a selection only Overview's plate could set. So it moves into the Groups layer at `role="editor"`, `centre_view` stops mounting a Home rail panel, and `sync_home_sidebar` keeps `_sidebar_sample_frame` hidden — which finally makes `activate_home`'s own docstring ("Home is the one page that shows no rail frame") true. The commit guard now asks two questions where it asked one: the sub-tab must be Sample map **and** the layer must be Groups. |
| 13 | C | Per-layer actions, and the selection travels where it means the same thing | ☑ | Variables and Well Labels share it — on both it is "the wells I am about to act on" — and the tab publishes it to `selected_wells`, which is what gives Cell Gating and Add Prefix a working picker for the first time. Groups is excluded: its plate is the current group's **membership** and a drag on it is an edit, so carrying a selection in would hand a group wells nobody put in it. Both directions are pinned; a test asserting only "the selection is shared" would pass against exactly that bug. `LabelGrid` gained `setSelectedTokens`, the counterpart of the getter it already had. |
| 14 | C | Profiles, `removable` flags and `reads_well_selection` follow the merge | ☑ | One `removable=False` descriptor where there were three, since the layers are built together from one roster and Overview reports all three counts — a profile that wants none of them drops the tab whole. `reads_well_selection` is **retired**, not carried across: with no rail plate on Home it had no reader, and a descriptor field nothing reads is §7.6's control that does nothing one level down. `help_topics` gains a **Sample map** entry and the manual the heading it names; the three layer headings stay as its sub-headings, so the three existing `#groups` / `#variables` cross-references still resolve. |
| 15 | C | Guard: exactly one plate on screen, on every Home tab | ☑ | `tests/test_one_plate_on_the_sample_map.py` in the fast lane, over the tab's own widgets, and `test_home_page.py::test_one_plate_is_on_screen_on_every_home_tab` against the real application — Overview's report plate, or the current layer's, or none. Counting *visible* plates rather than enabled ones is deliberate: the state that shipped was two plates with one greyed, and a guard that only counted the live one would have passed on it. Ten mutations watched failing across rows 11–15. One of them found a real defect and a second found a bad test: switching to the Well Labels layer left its cells disabled, because the dispatch called `grid.refresh()` (text only) rather than the feature's `label_grid_refresh` (which also sets the enabled wells), so a selection carried in from Variables was silently dropped; and the switch-publishes test was written against the Variables layer, where the refresh repaints the plate and publishes by a second route — it passed against code with the feature deleted, and now runs on both layers. |
| 16 | C | Review gate: one editor, no affordance lost | ☐ | Enumerate every control the three tabs offered — Quick Replicates, + Add's plate picker, group composition, Add Prefix / Add Suffix, the fill row, value chips, the 96 free-text cells — and confirm each has a home. A merge that quietly drops one is the §7.5 #5 half-sweep in a new shape. |
| 17 | D | A persistent readout naming what `plot_replicate_sets` actually decided | ☑ | `group_by_controller.grouping_summary`. Five sentences, one per state the seam can be in, and the exact string is asserted for each — "a readout was produced" is satisfied by one naming the wrong pooling. Two of the states are the ones that matter: a variable in force says **and your N groups are not pooling**, and a variable that matched no wells reports the *fallback* rather than the setting, because the seam hands the question back to the user's groups and a sentence naming the setting there would describe bars that are not drawn. |
| 18 | D | The readout reaches Home and the band, from one function | ☑ | `GROUPING_READOUTS` names both labels and one loop paints them, so the two cannot describe different figures — the hand-written pair is the pair that grows a difference, which `DECORATION_CONTROLS` already records next door. Repainted on a Group-by change, on a variable being defined or deleted, on any change to the groups, and on a layer switch. In a label rather than the status bar, for the reason `_announce` gives beside it: the bar is one persistent slot and a redraw with nothing to report writes nothing, so a sentence put there outlives its condition. |
| 19 | D | Guard: the readout is derived from the seam | ☑ | `test_group_by_variable`'s existing rule covers it — the first version of `grouping_summary` called `active_replicate_sets` directly and the guard fired on it. The comparison moved to `plot_members` beside `wells_the_grouping_adds`, as **two** functions: `variable_grouping_in_force` (did the substitution happen) and `groups_the_variable_replaced` (how many groups it displaced). Splitting them was not tidiness — the version that read the empty list as "no substitution" reported the fallback sentence over a plate that really was grouped by dose, which a test caught. Plus a test that stubs the seam and requires the sentence to follow it. Seven mutations watched failing; the first round missed a hidden group being counted, because the only hidden-group case ran down the branch that reads the seam's own answer and never reached the displaced count. |
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
