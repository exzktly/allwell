# One design language — plan and tracker for making the app look like the redraws

This is a tracker in the sense of CLAUDE.md §6.1, for one body of work: giving
All-Well a single design language and changing every page to follow it, so that
the running application looks like the redraws in
`_Docs/design/design_language_mockup.html`. It is written to be handed to another
assistant session that has not seen the conversation that produced it.

**All of this work is one pull request**, the one this file arrived in: the
user asked for the design language as a single pull request, with the menu and
organisation changes as another (`SURFACES_TRACKER.md`, on its own branch). So
this tracker lives on that pull request's branch and is worked there; the
commit that closes the last row deletes it, before the pull request merges, and
the body says so. Counts are derived from the rows (§10 gives the command); no
total is typed anywhere in this file.

**The user's instruction, verbatim:** *"I want more than token changes. I want to
mirror your new style language, for example what you showed in this image"* — the
image being the redraw of Nanowells › Distribution. A change of colour tokens was
prototyped on the real application and shown to the user; it gave fields and
buttons visible edges and made dim text readable, and changed nothing else. That
is not what is being asked for. What is being asked for is the structure of the
redraws: one-row bands, labels above fields, sentence-case headings, one
accent-filled button, switches and segmented controls in place of buttons that
carry state, neutral status chips, and the inspector.

Read before starting: `CLAUDE.md` (all of it), `_Docs/Markdowns/SURFACES_TRACKER.md`
(the companion plan for the inspector, which several rows here depend on; it is
on the other pull request's branch until that merges),
ARCHITECTURE.md §8 (the theme, the UI scale) and the *Phone layout* section, and
open `_Docs/design/design_language_prototype.html` in a browser: it is the working
example the user confirmed, and it opens on the choices they made (window style
W1, grey palette, the proposed well colours, the spelling "Color"); its panel
names the published rule behind each choice on the page being viewed.
`_Docs/design/design_language_mockup.html` is the earlier audit and static
redraws, kept for the audit; where the two differ, the prototype is right. Line numbers below
were read on the tree this plan was written against; re-find each by its symbol.

---

## 1. Why

All-Well already defines its colours, type sizes and spacing in one file,
`ui/theme.py`. What it lacks is a rule saying which of them a heading, a field
label, a button or a selected item gets, so each page answered that for itself.
Measured on the non-test source and on screenshots of every page:

| What | Measured |
|---|---|
| Ways a section heading is drawn | 6: white ("Plate"), accent ("Cell inclusion"), bold capitals ("EXPORT GROUPS"), muted capitals ("ANALYSIS"), a collapsible bold line, inline capitals ("AXES"); Analyze adds a seventh, large and underlined |
| Ways a field is labelled | 3: a muted caption beside the field, the label painted inside the control ("Channel: GFP"), bold with a colon ("Folder:") |
| Ways "selected" is shown | 5: solid accent (mode switch), dim accent fill (rail), underline (tabs), lighter grey (segmented), a lit well |
| Ways an on/off setting is drawn | 4: a checkbox (97 sites), a button that turns accent, a button whose label carries its state ("Pooled: Off", 14 sites), a button reading "SEM" or "FOV" that looks like a command |
| Button variants in use | 116 `secondary`, 19 `primary`, 19 `toggle`, 6 `danger`, and four one-off variants; which action is primary is not consistent |
| Spacing | 177 uses of a spacing token against about 180 literal values in twelve sizes; 67 distinct margin tuples |
| Style sheets set outside the theme | 169 `setStyleSheet` calls in 66 files |
| Per-widget names in the theme's sheet | 141, in a 2,455-line module |
| Label capitalisation | 135 sentence case, 24 Title Case, 15 mixed |
| How an unset value reads | "(not set)", "none", "auto", "—", depending on the page |
| Text glyphs used as icons | ✎ ✕ ▶ ⇄ ＋ ■ ⏸ beside a drawn icon set (`widgets/icons.py`) |

Legibility, in the grey theme the application starts in (`DEFAULT_THEME`):
"muted" text on a card 4.5 : 1, "faint" 2.5, placeholder 3.2, the primary
button's label 3.5, field and button edges 1.3 to 1.5, and 19 of the 96 well
colours under 3 : 1 on the plot background.

One finding is about the tests rather than the application, and row D01 fixes it
first: **the page-render harness never applies a theme.**
`tests/_page_render_case.py` builds `AllWellApp` directly, and
`theme.select_theme_from_env` is called only from `allwell.main`, so
`tests/test_every_page_renders.py` and every screenshot it saves show the *blue*
baseline, which no user sees. The first version of the design audit was drawn
from those screenshots and had to be redone.

## 2. Decisions already made with the user

Settled; do not reopen. If one cannot be met, stop and report.

1. **The goal is the redraws, not a recolour.** Every page is restructured to the
   language. A page is not done because its colours changed.
2. **Seven rules.**
   1. *One role, one look.* Every piece of text is one of six roles; every
      button one of four kinds; every on/off setting a checkbox or a switch.
   2. *Weight means rank.* Semibold is for headings and the selected item.
      Labels are regular, buttons medium, the primary button semibold.
   3. *Colour means one thing.* Accent: actionable or selected. Amber: needs
      attention. Red: destructive or failed. Green: done. Information is never
      coloured.
   4. *One accent-filled button per view* — the action that produces the page's
      result (Run, Generate, Export, Apply).
   5. *Three surfaces, visible edges.* Canvas, card, control. A field has an
      edge of at least 3 : 1; a card has a hairline; nothing else draws a box.
   6. *A four-point grid.* Gaps of 4, 8, 12, 16 or 24. A band is one row; what
      does not fit lives in the inspector.
   7. *Plain words, one spelling.* Sentence case. No colon after a label. "…"
      only when a button asks for more before acting. Icons from the drawn set,
      never a text glyph. An unset value reads **not set**, in the disabled ink.
3. **The six text roles**

   | Role | Size, weight | Ink | Used for |
   |---|---|---|---|
   | Page title | 17 semibold | primary | one per page, top left |
   | Section heading | 15 semibold, sentence case | primary | a card's title |
   | Eyebrow | 12 semibold, capitals, tracked | secondary | a group inside a card or the inspector; rail and tool-list groups |
   | Body / value | 13 regular | primary | text, and the value in a field |
   | Label / help | 12 regular | secondary | a field's label (outside the control, no colon) and help text |
   | Number | 12 monospace, tabular | primary | counts, readouts, paths |

4. **The four button kinds**: *primary* (accent fill, dark label), *default*
   (control fill and edge), *quiet* (no fill; toolbars and secondary commands),
   *destructive* (red label; a confirming alert follows). A disabled button says
   why, in help text beside it, when the reason is not obvious.
5. **Choosing a control.** A **switch** (`widgets/toggle_switch.py`) only where
   the change takes effect at once and only in an inspector row; a **checkbox**
   wherever the page has a button that applies it (Run, Export, Create), because
   nothing happens until that button is pressed. A switch's label names what is
   true when it is on ("Show significance", not "Significance"); a checkbox's
   label is positive and says what will happen. A **segmented control**
   (`widgets/segmented_control.py`) for two to about five closely related
   choices, labelled with nouns; a pop-up (combo box) for more. Views of a page
   are **tabs**, never a segmented control. No button carries its state in its
   label, and no toggle looks like a command. Sources: Nielsen Norman Group,
   *Toggle-Switch Guidelines* (a switch "should take immediate effect"); Apple
   HIG, *Toggles* ("In general, don't replace a checkbox with a switch") and
   *Segmented controls*.
6. **One notice component**, three severities (information, attention,
   failure): a coloured left rule and dot, a bold lead, plain text, at most one
   button. Amber is never used for plain information, and never for "in
   progress": a running job is the accent colour, done is green, each with its
   word beside the dot (Apple HIG, *Color*: "Avoid using the same color to mean
   different things"; "Avoid relying solely on color").
7. **Page skeleton.** Tabs; a one-row band of the page's most-used Data
   controls, each with its label above it, ending in the fixed Display · Style ·
   Share buttons; then the content in cards; conditions that apply shown as
   neutral chips, amber only for an alert. A form page is a title, cards with a
   label column, and one row of actions at the foot.
8. **Both palettes are offered: grey and blue.** The user was shown the two
   side by side in a working example and asked for both as options. Grey stays
   the one the application starts in. Every rule, role and building block in
   this plan is palette-independent, both palettes must pass the contrast test
   of row D03, and every page row is judged in both. The choice is the user's,
   in the application, and remembered (row D34); today it is a developer-facing
   environment variable, `ALLWELL_THEME`.
9. **Tokens.** Grey's values are below. Blue takes the same roles with its own
   hues: canvas / card / control `#0B0F17` / `#131A24` / `#1D2633`; text
   `#E6E9EF` / `#98A2B3` / `#5F6B7C`; hairline / button edge / field edge
   `#2A3343` / `#3B475C` / `#5E6B82`; accent `#6B8AFD` with a `#0B0F17` label;
   selected `#24315C` with `#C9D4FF` text. Blue's values were drawn for the
   mock-up and have not all been measured: row D02 measures each against D03
   and adjusts any that fail.

   | Role | Value | Contrast on a card |
   |---|---|---|
   | canvas / card / control | `#141618` / `#24272C` / `#2D3137` | — |
   | text primary / secondary / disabled | `#EAECEE` / `#A1A7AF` / `#7F858D` | 12.7 / 6.2 / 4.0 |
   | hairline / button edge / field edge | `#3F444B` / `#60666E` / `#787E86` | 1.5 / 2.6 / 3.7 |
   | accent, label on accent | `#2D8CEB`, `#0B0D10` | label 5.6 on the fill |
   | selected fill, selected text | `#22415E`, `#D6E6F8` | text 8.3 on the fill |
   | radius | 4 controls, 6 cards, 10 overlays | — |
   | spacing | 4, 8, 12, 16, 24 | — |
   | type sizes | 12, 13, 15, 17 (and a large numeral for Home's stat tiles) | — |

   The seven surface tokens collapse to three roles and the five text tokens to
   three; "muted" and "faint" both become secondary.

## 3. Decisions from the working example

The user was given a working example with each choice numbered and answered
*"W1, p1 default with p2 offered, c2, s2"*.

1. **Window style W1, "Panels".** The sidebar and the top bar sit directly on a
   window background darker than the canvas (`#0B0D0F` grey, `#06090F` blue),
   with no dividing lines between them. The page and the inspector are separate
   panels in the canvas colour with 12-pixel corners and an 8-pixel gap between
   them and the window's edge. The mode switch is a pill in the top bar; sidebar
   items have 7-pixel corners and the selected one is the selected fill with no
   accent bar. The user rejected the first version's hairlines between flat
   regions. Row D20.
2. **The window keeps the application's name, and the dataset's name stays the
   button at the head of the sidebar** that opens the recent datasets and the
   dataset tools. A version that moved the dataset into the window title was
   shown and the user asked for it to be undone. The button may carry a second
   line (wells and timepoints).
3. **Grey is the default palette and blue is offered** (decision 8 of §2).
4. **The well colours change** to a palette that passes contrast and
   colour-blind separation. Only the **first eight** were designed and shown:
   `#338ced`, `#e76624`, `#00a888`, `#d28e05`, `#e24d8b`, `#0aac23`, `#9887ef`,
   `#eb6263`, which pass on both plot backgrounds (worst adjacent colour-blind
   separation 10.1). The other 88 of the 96 are not designed. Row D30 designs
   them, validates all 96, and migrates saved datasets, whose colours are
   written into their sample definitions; it reports to the user before it
   changes any saved file.
5. **Spelling: "Color"**, in everything a user reads in the application. This
   file, the code's identifiers and the other documents keep whatever spelling
   they have; the rule is about the interface. Row D13.
6. **The rules were revised against the published guidance** and the user chose
   among options drawn in that revision, which is taken as confirming it:
   - Close and Back are symbols with tooltips, always visible, not the words
     (Apple HIG, *Toolbars*; Nielsen Norman Group on bottom sheets).
   - Four text sizes, not five (§2.3, §2.9).
   - An ellipsis only on a command that opens another window or needs more
     input before it acts ("Rename…", "Browse…"; not "Remove").
   - A chip does a job or is not a chip: a pressable **filter** chip with a
     selected look, a removable **input** chip with an ✕, and anything that only
     reports is plain text (Material's chip types).
   - An alert only for an uncommon action that cannot be undone. Its title is a
     full question in sentence style with its punctuation; its buttons are
     "Cancel" and a verb, never OK, Yes or No; the destructive button is red
     text and is not the default (Apple HIG, *Alerts*). A destructive menu item
     is red and set apart by a divider. The toolkit orders an alert's buttons
     for the system it is running on.
   - A band holds only what fits one row *with the sidebar and the inspector
     both open*; on Nanowells that is Metric, Channel and Subset.
   - Sentence-style capitals stand, on Microsoft's Windows guidance
     ("Capitalize the start of every string of text in your app"); Apple's
     title-style for buttons was considered and not taken.

**Still open — ask before the row that depends on it:** where the well plate
lives. Apple's guidance is to "avoid putting critical information or actions at
the bottom of a sidebar", which is where it is today. Nothing in this plan moves
it; the menu-and-organisation plan leaves it in the sidebar.

## 4. How "done" is judged

A page's row is `done` when all of these hold, and the evidence cell says so:

1. **A render of the real application, in each of the two palettes, beside the
   mock-up's redraw**, added to the pull request's body. The comparison is of
   structure: the same regions, the same roles, the same controls in the same
   places. Exact pixel sizes are settled in the application.
2. **The page passes the measured checks of §5.2** with no entry for it on a
   pending roster.
3. **Nothing the page could do before is lost.** A control removed from the
   band is reachable in the inspector; a test presses it there (CLAUDE.md §7.6).
4. **Phone layout still fits** (`tests/test_every_page_renders.py`, all six
   screens), and the phone snapshot baseline is regenerated in the same commit.

Render a page with the harness, after row D01:

```bash
ALLWELL_PAGE_SHOTS=<dir> QT_QPA_PLATFORM=offscreen ALLWELL_DESKTOP=1 \
  ALLWELL_STATE_DIR=<dir>/state ALLWELL_SCRATCH_DIR=<dir>/scratch PYTHONPATH=$PWD \
  python -m tests._page_render_case mini desk <dir>/out.json
```

`mini` covers every Review page plus Analyze and the nine Datasets tools;
`nanowell` and `images` fill the Nanowells and Image Browser pages. One case
takes about four minutes.

## 5. Design

### 5.1 The building blocks (rows D02–D09)

Each is a small module that the page rows then use. They go where CLAUDE.md §3
says: no application knowledge → `widgets/`; shared by every pane → `ui/`.

| Block | Where | What it is |
|---|---|---|
| Tokens | `ui/theme.py` (`Colors`, `Radii`, `Spacing`, `Typography`, the `_GREY` bundle) | The values of §2.8. Old token names stay as aliases of the new roles until the last reader is gone, then are deleted |
| Text roles | `ui/text_roles.py` (new) | `page_title(text)`, `section_heading(text)`, `eyebrow(text)`, `field_label(text)`, `help_text(text)`, `number(text)`: each returns a `QLabel` carrying a `role` property the theme's sheet styles. Replaces `setObjectName("Muted")` (207 sites), `"Title"`, `"Heading"`, `"SectionTitle"`, `"Caption"` and the per-page heading names |
| Buttons | `ui/buttons.py` (new), replacing `btn_primary` / `btn_secondary` / `btn_card` / `btn_danger` in `review/core/ui_helpers.py` | `primary`, `default`, `quiet`, `destructive`; each sets the `variant` property. The theme's sheet styles exactly four variants. The base `QPushButton` rule loses its bold weight (a prototype found a more specific rule wins over an appended one, so the edit is in the sheet itself) |
| Field with a label | `widgets/labelled_field.py` (new) | A label above or beside any control, never inside it. `widgets/labeled_combo.py`'s painted prefix ("Channel: GFP", 30 sites) is retired in favour of it |
| Form grid | `widgets/form_grid.py` (new) | A two-column layout: labels in one column of fixed width, controls in the other. For form pages and inspector pages |
| Notice | `widgets/notice.py` (new; `widgets/context_banner.py` and the theme's Notice panel fold into it) | Decision 6 |
| Status chip | `widgets/status_chip.py` (new, or `widgets/chip_group.py` if it fits) | A neutral pill with an optional dot; amber or red only by severity |
| Page header and action row | `ui/page_chrome.py` (new) | A page title with optional quiet commands; a foot row holding the one primary action, its neighbours, and the reason it is disabled |
| Unset value | `ui/text_roles.py` | One constant, `NOT_SET = "not set"`, and one way to show it |

### 5.2 Tests that hold the language

Guards read the source through `tests/_repo_scan`; each has a per-site roster
that only shrinks, a liveness check, a case in `tests/test_guards_bite.py`, and a
proof by `scripts/prove_guard.sh` or by mutation on HEAD (CLAUDE.md §7.5, §7.7).
The measured checks and the page renders of §4 run once per palette.

| Test | Rule | Ends at |
|---|---|---|
| `tests/test_theme_contrast.py` | For every registered theme, computed from the tokens: primary and secondary text at least 4.5 : 1 on canvas, card and control; the label on the primary button at least 4.5; the field edge at least 3.0 on a card. Pure arithmetic, fast lane, runs in CI | Passing for every theme |
| `tests/test_the_render_harness_shows_the_shipped_theme.py` | A page rendered by the harness has the default theme's card colour, not the baseline's | Row D01 |
| `tests/test_text_has_a_role.py` | No `setObjectName` of a retired text name; no `QLabel` given an inline font size or weight | Zero outside a roster |
| `tests/test_buttons_have_one_of_four_kinds.py` | No `setProperty("variant", …)` outside the four; no `QPushButton(` or `QToolButton(` in a pane that bypasses `ui/buttons.py` | Rosters per file, shrinking |
| `tests/test_no_style_sheet_outside_the_theme.py` | `setStyleSheet(` appears only in `ui/theme.py` and a roster | The 169 sites, shrinking to those with a written reason (the menu-indicator rule of CLAUDE.md §7.12 is one) |
| `tests/test_spacing_comes_from_tokens.py` | No integer literal other than 0 in `setSpacing` or `setContentsMargins` | Zero outside a roster |
| `tests/test_labels_are_sentence_case.py` | String literals passed as a button, checkbox, heading or label text: no Title Case, no trailing colon, no "..." in place of "…", none of the glyphs ✎ ✕ ▶ ⇄ ■ ⏸ ＋ and no leading "+ " or "▶ " | Zero outside a roster of proper nouns (StarDist, Cellpose, OME-TIFF, …) |
| `tests/test_no_state_in_a_button_label.py` | No button text matching `": Off"` / `": On"`; no checkable `QPushButton` | Zero |
| `tests/test_unset_reads_not_set.py` | `"(not set)"`, `"none"` as a placeholder, and "—" as a value appear nowhere but through `NOT_SET` | Zero |

Checks measured on the built application, added to `tests/_layout_checks.py`
and run by the page tour on every page and screen:

- `one_primary`: at most one visible button with the primary variant per page.
- `band_is_one_row`: at the desk, a page's `ControlBand` is one row high at the
  window's width.
- `labels_outside_controls`: no visible combo box whose own text begins with a
  label and a colon.
- `no_empty_band`: a page with no band controls shows no band strip.

A page not yet converted is on that check's pending roster with its row's ID.

### 5.3 What depends on the inspector

A one-row band is only possible once the controls that leave it have somewhere
to go. The menu-and-organisation pull request (`SURFACES_TRACKER.md`) builds
that place, and it is a separate pull request, so the two meet through `main`:

1. Phases 0 to 4 of this plan (D01–D13 and the form pages D22–D27) need nothing
   from the other pull request and can be done at any time.
2. Phase 5, the pages with a band (D14–D21), starts only after the
   menu-and-organisation pull request has **merged**. Merge `origin/main` into
   this branch then, and restyle the inspector it brought (it is built with the
   application's existing buttons and labels) as part of row D20.
3. If this pull request is to merge before the other one, it stops at the end
   of phase 4 with rows D14–D21 `declined` for that reason, and those rows move
   to whichever pull request follows. Say so to the user rather than choosing.

## 6. The redraws, in words

For a session that cannot open the mock-up.

- **Nanowells › Distribution.** Tabs for the eight views. One band row: Metric,
  Channel, Subset (ending in ›, opening its inspector page), then Display ·
  Style · Share at the right. Below, a card titled "Cell count by nanowell"
  holding the plot, with "22 of 24 values used" as plain text at its top right
  beside two filter chips ("2 alerts" with an amber dot, "3 notes"), and one
  line of help at its foot. Beside it the inspector: eyebrow *Plot* (Along x,
  segmented: Time, Wells; Show, segmented: Auto, Beeswarm, Violin, Histogram),
  eyebrow *Cohort* ("Use rim focus" with its number and a switch; "Fix cohort"
  switch), eyebrow *Baseline* (Baseline pop-up; Average over), eyebrow
  *Smoothing* (Moving average; Responding above, reading "not set"). Today the
  same sixteen controls are four unlabelled band rows.
- **Batch Export.** A page title with a quiet "Sync from app" and the primary
  "Export". Two cards. *Groups*: quiet "Clear all", "Quick setup ▾", "Add
  group", a line of help, an information notice when there are none. *Output*
  and *Plot*, each a form grid: Write to (three checkboxes), Folder (field and
  "Browse…"), Format, Style preset, Also write; Channel, Property, Error band
  (segmented: SEM, SD, Off), Points (a checkbox, "Draw one point per field of
  view": a checkbox because nothing happens until Export).
- **Home › Overview.** Title and path with a quiet "Reload". One attention
  notice with one button. A row of stat tiles (label, large number, help).
  Cards for the plate legend and the run parameters as label/value rows, an
  unrecorded value reading "not recorded" in the disabled ink.
- **Analyze.** A form column about 400 wide: title "New run" with quiet
  "Presets ▾" and "Copy from a run…"; cards *Input*, *Channels*,
  *Segmentation* (and the rest), each a form grid; a caution as an attention
  notice, not grey prose; a foot card with Run on, quiet "Manage…", "Add to
  queue" and the primary "Run pipeline", with the reason it cannot run yet. On
  the right a *Run* card: status chip, progress bar, stages, the plate, a
  sentence-case legend; the log behind "Show log" until a run starts.
- **Datasets.** The tool list grouped under eyebrows *Combine*, *Convert*,
  *Maintain*. A tool page no wider than about 720: title, one line of help,
  cards *Folders* and *What to copy*, a foot row with the reason, "Add to queue"
  and the primary action. The Job Queue's empty state is a heading, two lines
  and one button; Stop appears only while a job runs.

## 7. How to work this plan

- **One pull request for the whole plan**, already open: commit on its branch
  and stack every phase there. Because the pull request is open, every push
  starts a billed CI run (CLAUDE.md §2), so push by round: finish a phase,
  verify it locally, push it once. Keep the body describing the branch as it
  stands, and label it `minor`. Merge `origin/main` into the branch at the
  start of each phase and before each push; never rebase or force-push it.
  Read `git log origin/main -- <files>` for the files a phase touches first.
- **Update the row in the same commit as the change**; `done` only with the
  evidence of §4.
- **Reproduce before sweeping.** The counts in §1 were measured on one tree.
  Re-measure with the guard, and report the guard's count, not this file's
  (CLAUDE.md §7.5 #4).
- **A sweep converts every sibling** (CLAUDE.md §7.5 #5): search for what the
  code does, not for the helper being replaced.
- **Every restyle moves the phone.** Phone layout scales the same tokens
  (ARCHITECTURE.md, *The UI scale*). Regenerate
  `tests/fixtures/phone_snapshot_baseline.json` in the commit that moves it and
  say which widgets moved.
- **Lanes:** the guard family and `ruff check .` always; the full fast lane
  (it holds the UI tests CI never runs) for every phase;
  `scripts/test.sh tests/test_every_page_renders.py -m ""` and
  `scripts/test.sh tests/test_app_smoke.py -m ""` before each push.
- **Budgets:** `ui/theme.py` is 2,455 lines. This work should shrink it; read
  `tests/test_monolith_budgets.py` and `tests/test_guardrails.py` first.
- **Docs:** ARCHITECTURE.md §8 and the manual's screenshots and control names
  change with almost every phase. Rewrite; do not append.

## 8. Risks

- **A token is read where it was not meant to be.** `Colors.border` is the
  field edge today and may also be drawn as a divider somewhere; raising it to
  3 : 1 would make those dividers loud. Row D02 audits every reader of each
  token it changes before changing it.
- **Matplotlib reads the theme too** (`plot_bg`, `plot_grid`, `plot_spine`,
  `core/plot_style.py`). Figures are out of scope except where a token they
  share moves; check a rendered plot after D02.
- **The mode switch and several widgets paint from tokens at construction**
  (`widgets/mode_bar.py` sets its own sheet), so they do not follow a sheet
  change; they are page work.
- **Muscle memory and the manual.** Every band and form changes.
- **Size of the sweep.** Roughly 450 button sites, 207 muted labels, 180
  literal spacings, 169 style sheets. The rosters make it safe to do in pieces;
  they also make it easy to stop half way, which CLAUDE.md §7.5 #5 warns is
  worse than not starting. A phase is finished only when its roster is empty or
  every remaining entry has a written reason.

## 9. Order of work

- **Phase 0 — measure the right thing (D01).**
- **Phase 1 — tokens, the contrast test and the palette choice (D02, D03, D34).** The application looks
  slightly different everywhere and no page's structure has changed.
- **Phase 2 — building blocks (D04–D09).** They exist and nothing uses them.
- **Phase 3 — sweeps (D10–D13).** Mechanical, app-wide, each held by a guard.
- **Phase 4 — form pages (D22–D27).** Analyze, Datasets, Batch Export, Home.
  These need no inspector.
- **Phase 5 — pages with a band (D14–D21),** after the menu-and-organisation
  pull request has merged (§5.3).
- **Phase 6 — dialogs and phone (D28, D29).**
- **Phase 7 — the well colours, then close (D30, D31–D33).**

## 10. Rows

States: `todo`, `doing`, `done`, `declined` (reason in the row and at the site).

```bash
awk -F'|' '/^\| D[0-9]+ /{gsub(/ /,"",$4); n[$4]++} END{for(s in n) print s, n[s]}' \
    _Docs/Markdowns/DESIGN_LANGUAGE_TRACKER.md
```

| ID | Item | State | Where | Evidence |
|---|---|---|---|---|
| D01 | The render harness applies the shipped theme before building the application, as `allwell.main` does; the phone snapshot fixture too. Check every other test that builds `AllWellApp` or `ReviewApp` and asserts a colour | todo | `tests/_page_render_case.py`, `tests/phone_snapshot.py`, `tests/conftest.py` | `tests/test_the_render_harness_shows_the_shipped_theme.py`, failing before the fix with the baseline's colour in its message; phone baseline regenerated |
| D02 | Tokens of §2.8 for every theme: three surface roles, three text roles, three edges, selected pair, label-on-accent, radii, spacing without 32, type sizes 12, 13, 15 and 17 only. Audit every reader of each changed token first; old names kept as aliases | todo | `ui/theme.py` | A render of five pages before and after; `tests/test_theme_contrast.py` green; a rendered plot checked |
| D03 | The contrast test | todo | `tests/test_theme_contrast.py` | Run against `origin/main`'s tokens it fails naming muted, faint, placeholder, the primary label and the field edge |
| D04 | Text roles and `NOT_SET` | todo | `ui/text_roles.py` (new), the theme's sheet | A test that each role's label draws at its size, weight and ink (assert the computed font and palette, not that a property was set) |
| D05 | Four button kinds; the sheet's base button weight; the four one-off variants (`toggle`, `toggle_muted`, `toggle_accent`, `sem`, `muted`) mapped or retired | todo | `ui/buttons.py` (new), `ui/theme.py`, `review/core/ui_helpers.py` | A rendered button of each kind measured for weight and fill; `test_buttons_have_one_of_four_kinds` roster created |
| D06 | Labelled field and form grid; `LabeledComboBox`'s painted prefix retired | todo | `widgets/labelled_field.py`, `widgets/form_grid.py` (new), `widgets/labeled_combo.py` | Component tests; phone form (`BandForm`) still finds the label, which it moves out of the control today |
| D07 | Notice and status chip | todo | `widgets/notice.py`, `widgets/status_chip.py` (new), `widgets/context_banner.py` | Component tests asserting the severity's colour reaches the painted rule |
| D08 | Page header and action row | todo | `ui/page_chrome.py` (new) | Component test: the disabled reason is shown while the action is disabled and gone when it is not |
| D09 | The guards and measured checks of §5.2, with rosters holding today's sites | todo | `tests/` | Each proved and given a `test_guards_bite` case |
| D10 | Sweep: text roles replace the retired object names and inline font settings | todo | app-wide; 207 `Muted` sites and the rest | `test_text_has_a_role` roster empty or reasoned |
| D11 | Sweep: every button through `ui/buttons.py`; one primary per page decided and written in a table in the pull request | todo | app-wide | `test_buttons_have_one_of_four_kinds` roster; `one_primary` on every page |
| D12 | Sweep: spacing and margins from tokens; one-off style sheets moved into the theme or removed | todo | app-wide | `test_spacing_comes_from_tokens`, `test_no_style_sheet_outside_the_theme` rosters |
| D13 | Sweep: wording — sentence case, no colons, "…", glyphs to icons, state-in-label toggles to switches or segmented controls, unset values to `NOT_SET`, "Color" as the one spelling in what users read, an ellipsis only where §3.6 allows one, Close and Back as symbols, switches and checkboxes sorted by §2.5 | todo | app-wide | The three wording guards at zero; each converted toggle pressed through its real control and reaching the same state as before |
| D14 | Plotting pages: Bar Plots, Line Graphs, Response Curves, Heat Map, Scatter, Distribution | todo | `review/features/{barplot,lineplot,response,heatmap,scatter,distribution}/tab_view.py` | §4 for each page |
| D15 | Statistics (results as a formatted table, not preformatted text), Multivariate, Spatial | todo | `review/features/{stats,multivariate,spatial}/` | §4 |
| D16 | Nanowells: all eight views, to the redraw of §6 | todo | `review/features/nanowell/` | §4; `scripts/test.sh --nanowell-features` |
| D17 | smFISH | todo | `review/features/smfish/` | §4 |
| D18 | Image Browser: Image Table (the builder shelf and action strip), Segmentation, Cell Browser, Playground | todo | `review/features/image_browser/`, `review/features/preview/`, `review/features/playground/` | §4 |
| D19 | Review CSV, Saved Cells, Saved Plots | todo | their feature packages | §4 |
| D20 | The shell, in window style W1 (§3.1): the window background and the two panels, no dividing lines; the header (five commands as words, after `SURFACES_TRACKER.md` S23) with the application's name; the mode switch as a pill in the selected look; the sidebar with the dataset button at its head; the status bar | todo | `allwell.py`, `widgets/mode_bar.py`, `widgets/rail_nav.py`, `review/core/app_shell_view.py` | §4; the mode switch no longer the only solid accent |
| D21 | The rail's plate footer, saved selections and per-page rail frames | todo | `review/core/sidebar_view.py`, `widgets/saved_selections_list.py` | §4 |
| D22 | Analyze: the form as cards with a form grid in a wider column; cautions as notices; the foot row; the Run card; the log behind a button until a run starts | todo | `analyze/` (`form_chrome.py`, `form.py`, `run_footer.py`, `tab.py`, the section modules), `widgets/run_dashboard.py`, `widgets/plate_progress.py` | §4, idle **and** during a run (drive a run with the test fixtures; the audit only saw Analyze idle) |
| D23 | Analyze's own collapsible sections replaced by `widgets.CollapsibleSection` or by cards | todo | `analyze/form_chrome.py` | One collapse implementation in the application |
| D24 | Datasets: the tool list grouped; every tool page to the skeleton of §6; no empty band strip; fields capped in width | todo | `datasets/tab.py`, each `datasets/<tool>/view.py`, `datasets/core/` | §4 for each of the nine tools; `tests/test_phone_fit.py` still green |
| D25 | Datasets › Job Queue: header actions, the empty state, Stop only while running, the row's kinds of button | todo | `datasets/job_queue/` | §4, empty and with jobs |
| D26 | Batch Export, all six pages | todo | `review/features/batch_export/` | §4 |
| D27 | Home: Overview, Cell Gating, Sample Map, Ratios, Pipeline Info, QC Triage; the empty band strip removed | todo | `review/features/home/` | §4; `tests/test_home_phone_layout.py`, `tests/test_sample_map_phone_layout.py` |
| D28 | Dialogs and windows: Manage Remote Servers and those under it, Storage, Reset analysis, Copy analysis, the curve windows, Test detection, the message-box wording | todo | `analyze/remote_dialog.py` and the others in `SURFACES_TRACKER.md` §11.5 | A render of each, before and after |
| D29 | Phone layout: the touch sizes re-derived from the new tokens; every page on all six screens | todo | `core/phone_layout.py`, `ui/theme.py` (`_touch_qss`), `ui/phone_shell.py` | `tests/test_every_page_renders.py` green; shots reviewed |
| D30 | The well colours (§3.4). Design all 96: the eight shown are the first eight, and the remaining 88 must be derived so that every colour is at least 3 : 1 on both palettes' plot backgrounds and on white (exported figures), and neighbouring ranks stay apart under colour-blind simulation. Read how the 96 are generated today before choosing how to extend eight. Then migrate: sample definitions store colours per well, so decide with the user, before changing any saved file, whether an existing dataset keeps its saved colours, is offered the new ones, or is converted | todo | `domain/well_palette.py`, `domain/plot_groups.py`, `ui/theme.py` (which re-exports the palette), `review/persistence/` | A test that computes contrast and simulated separation for all 96 on each background, failing on today's palette for the 19 it should; a round-trip test that a dataset saved with the old colours opens as the user decided |
| D34 | The palette is the user's choice, in the application: a Grey / Blue setting, remembered between sessions (`core/app_settings.py`), honoured at start-up ahead of `DEFAULT_THEME` and behind an explicit `ALLWELL_THEME` or `--theme`. `ui/theme.py` says a live switch is not possible while widgets cache inline style sheets built from the tokens, so the setting applies at the next start and says so beside the control; once row D12 has removed those style sheets, make it live and delete that sentence. Where the control lives is the surfaces plan's question: the dataset-tools / `⋯` menu of `SURFACES_TRACKER.md` S23 unless a settings page exists by then | todo | `ui/theme.py` (`select_theme_from_env`), `core/app_settings.py`, `allwell.py` | A test through the real start-up path: with the setting stored as blue and no environment variable, the built window's card colour is blue's; with `ALLWELL_THEME=grey` set, grey's. Mutation: ignoring the stored setting fails the first, ignoring the variable the second |
| D31 | Every roster from D09 is empty or holds only reasoned entries; the per-widget names in the theme's sheet that only restated a role are gone | todo | `tests/`, `ui/theme.py` | The rosters; the count of named widgets in the sheet, from a script, before and after |
| D32 | Reconcile: re-render every page and compare each against §2's rules, not against this table; list anything that does not follow them | todo | this file | The reconciliation, in the pull request body |
| D33 | Docs and help; delete this tracker | todo | `_Docs/Markdowns/`, `ui/help_topics.py` | The docs lane green; the pull request body says the tracker is gone |
