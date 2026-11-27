# One design language — plan and tracker for making the app look like the redraws

This is a tracker in the sense of CLAUDE.md §6.1, for one body of work: giving
All-Well a single design language and changing every page to follow it, so that
the running application looks like the redraws in
`_Docs/design/design_language_mockup.html`. It is written to be handed to another
assistant session that has not seen the conversation that produced it.

**All of this work is one pull request, and it is opened last.** The user
asked for the design language as a single pull request, with the menu and
organisation changes as another (`SURFACES_TRACKER.md`, on the branch
`claude/festive-albattani-9gzkrd`). This tracker lives on the branch
`claude/festive-albattani-9gzkrd-design-language` and is worked there. **No
pull request is open, and none is to be opened until the work is finished**:
CLAUDE.md §2 and §6 say to commit and push as the work goes, which costs
nothing while no pull request exists, and to open the pull request at the end,
which starts the one CI run that matters. A pull request was opened from this
branch while it held only the plan (#1373); that broke the rule, and it was
closed at the user's instruction. Do not reopen it; open a new one at the end.
The commit that closes the last row deletes this tracker, and the pull
request's body says so. Counts are derived from the rows (§10 gives the command); no
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
4. **The well colours change, and a colour already written to a file stays as
   it is.** The user's words: *"You can keep the old well colors if they are
   already written to a file; build the remaining new well colors."* So there
   is no migration. A selection whose colour is saved in a dataset's sample
   definitions keeps that colour; a selection made after the change is baked
   from the new palette. A dataset can therefore show old and new colours side
   by side, which the user has accepted.

   All 96 are designed. Ranks 0 to 7 are the eight the user approved; the other
   88 were chosen and placed by `_Docs/design/well_palette_build.py`, which
   reproduces every number below. Rank order, twelve to a plate row:

   `#338CED` `#E76624` `#00A888` `#D28E05` `#E24D8B` `#0AAC23` `#9887EF` `#EB6263` `#C01085` `#DF816E` `#447CBF` `#FF648B`
   `#1C6E80` `#C588B0` `#D302C5` `#2E8C7C` `#D40E14` `#A67284` `#857829` `#C6099F` `#F36AA9` `#C03DB3` `#878F5D` `#7C60DB`
   `#639F74` `#795C84` `#D38B5A` `#AF16B6` `#976371` `#7BA944` `#566394` `#5EA0E3` `#A43FAE` `#FC146F` `#7332FD` `#C35E74`
   `#A174F0` `#4AA7B7` `#3D68BC` `#915075` `#0F9CD0` `#C03A8F` `#FB5FCA` `#B6443A` `#FD449D` `#5F63CC` `#BA6C95` `#B639F0`
   `#D8006D` `#236FEA` `#3E7C6B` `#A765DB` `#ED214B` `#FD46F5` `#FC7145` `#D163CF` `#7C6135` `#AC3992` `#2258E5` `#BF5297`
   `#5A7BE2` `#26894C` `#9E4BFF` `#9A4F57` `#9C78B6` `#A0832A` `#4C869D` `#E14513` `#E87997` `#D56CFC` `#BC857C` `#C452FD`
   `#507C41` `#6F70AB` `#D14160` `#3694A6` `#B29B37` `#C27FDD` `#AB6647` `#DD63A7` `#8D4E9F` `#5C7704` `#9428DD` `#AE4565`
   `#EE27A6` `#2AA1FE` `#FB3DCC` `#FD171C` `#8552C2` `#61911C` `#8688C5` `#4DA64F` `#4C6D48` `#B33DD3` `#097463` `#DE49BD`

   | Measured | Current palette | New palette |
   |---|---|---|
   | Under 3 : 1 on the grey plot background | 19 of 96 (lowest 1.37) | 0 (lowest 3.34) |
   | Under 3 : 1 on the blue plot background | 22 (lowest 1.22) | 3 (lowest 2.99) |
   | Under 3 : 1 on white, for exported figures | 38 (lowest 1.12) | 18 (lowest 2.75) |
   | Closest pair of plate neighbours, normal vision | 5.2 | 14.0 |
   | Closest pair of plate neighbours, colour-blind | 1.2 | 10.1 |
   | Plate neighbours closer than 8 when colour-blind | 19 of 172 pairs | 0 |
   | Closest pair anywhere on the plate, normal vision | 4.1 | 4.0 |

   Distances are OKLab differences times 100; "colour-blind" is the worse of
   simulated deuteranopia and protanopia (Machado et al. 2009, full severity).
   A plate neighbour is the next well along a row or down a column, which is
   what a person usually selects together. Three limits, stated so they are
   not rediscovered as defects: on white, 18 colours sit between
   2.75 and 3.0, because the approved yellow (`#D28E05`, 2.76) set that
   floor; three colours measure 2.99 on the blue plot; and two wells far apart
   on the plate can still be as close as 4.0, as they can today, because 96
   colours that all pass contrast on both a dark and a white ground share a
   narrow band of lightness. The palette leans to blues, purples and pinks for
   the same reason: a bright yellow or green cannot reach 3 : 1 on white.
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
   mock-up's redraw**, kept for the pull request's body. The comparison is of
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

- **One pull request for the whole plan, opened last.** Commit on the branch
  and push as you go; with no pull request open a push starts no CI run and
  keeps the work safe. Local runs are the gate: run what §7's *Lanes* item
  lists before the push that will open the pull request. Open it only when
  every row is `done` or `declined`, label it `minor`, and subscribe to its
  activity in the same turn. From then on push by round, since each push to an
  open pull request is a billed run. Merge `origin/main` into the branch at
  the start of each phase and before opening the pull request; never rebase or
  force-push it. Read `git log origin/main -- <files>` for the files a phase
  touches first.
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
  `scripts/test.sh tests/test_app_smoke.py -m ""` at the end of each phase.
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

## 9.1 Where the work stands (keep this current, step by step)

This section is the hand-over: whoever picks the work up next — a new session, a compacted one, another
assistant — reads it first. It is updated in the same commit as each step.

- **Pull request:** #1375 against `main`, labelled `minor`, from `claude/festive-albattani-9gzkrd-design-language-850d9z`
  (the older `claude/festive-albattani-9gzkrd-design-language` is kept at the same commit). Each push to it runs CI.
- **Order the user asked for:** small rows first; long runs (the fast lane, `tests/test_every_page_renders.py`
  at ~14 min) once, at the end, not after each row. Per row: ruff and that row's own test files only.
- **Unverified so far:** D17 and D19 were marked `done` on ruff and `tests/test_labeled_combo.py` alone; their
  claim that no `label inside` / `empty band` remains on those pages rests on the every-page run still owed.
- **Unfinished D14 work, not merged:** an agent's partial retirement of `LabeledComboBox`'s prefix across the band
  sites and Plotting band changes, 32 files, untested — local commit `51e8d3a` on branch
  `worktree-agent-a79c6ce415759c893` in `.claude/worktrees/agent-a79c6ce415759c893`. Not pushed, so it is lost with
  this container; read it for ideas, do not merge it blind.
- **Known defect left:** Nanowells Identity Diagnostics for a partial well (A04) still collapses on very short cards.

Step log (newest last):

1. D17, D19: FOV/Timepoint boxes on smFISH and Review CSV through `widgets.labeled_combo.unlabelled`; Saved Cells
   builds no band. Commit `d36c539`.
2. D18 (part): Playground builds no band either — `widgets.page_body.build_bandless_body` (new), used by Saved Cells
   and Playground; `tests/test_control_band_adoption.py` counts it as a page's one control-row decision. Its
   `DESIGN_PENDING` entry removed. Still open in D18 after step 3: Segmentation's `band rows` and `label inside`. Also fixed: the `unlabelled` test crashed its xdist
   worker by calling `deleteLater()` on a widget the reaper fixture deletes too. Commit `791e292`.
3. D18 (part): Image Table builds no band (`build_bandless_body`, sides flush as before, top inset `Spacing.md`); the
   body is registered as `image_table.tab_view.PAGE_KEY` (`_image_table_page`), which the shelf's root-filter
   inspector pages now anchor on (`builder_view._open_root_filter`) in place of the band, and which
   `tests/test_surface_depth_app.py` (slow, not run) reads. Its `DESIGN_PENDING` entry removed. Ruff and the 384
   fast Image Table tests green.
4. D15/D18/D16 (part), labels outside the boxes: `unlabelled(...)` plus the band's caption for Statistics
   (Timepoint, Control, Pair by, and), Spatial and Multivariate (Timepoint), Segmentation (`preview/panel_view.py`:
   FOV, Timepoint) and the Nanowells Group by (`nanowell/variables_view.py`). `DESIGN_PENDING` `label inside`
   removed for Statistics, Spatial, Multivariate and Segmentation (to be confirmed by the every-page run; a label
   moved beside its box widens a band, so `band rows` may appear on a page not yet pending — check that first).
   Left for later: the Nanowells Timepoint (`nanowell/tab_view.py:380`, shares one scope with the well part),
   the Donor box inside the Compare scope (`statistics_view.py:99`), the decoration combos in a panel
   (`variables_view.py:77`), and the Plotting sites (D14). Commit `ea4c740`. Ruff and 402 fast tests of those pages green.
5. D14 (part), labels outside the boxes on the Plotting band: Heat Map and Distribution Timepoint, Response X /
   Channel / Property, the shared Population box (`review/core/population_band.py`), the nanowell Metric
   (`review/core/observation_band.py`) and the Scatter observation boxes X, Y, Subset, Baseline
   (`scatter/_observations.py`). All six Plotting `label inside` entries removed from `DESIGN_PENDING` — to be
   confirmed by the every-page run. Not converted, because they are inside Display / inspector panels rather than a
   band and no page tour opens them: Response's Model and Compare EC50 to (`response/tab_view.py:201,227`), Bar's
   significance Label (`barplot/tab_view.py:247`). D14's other half, each Plotting band on one row, is still open.
   Commit `f4b13a6`. Ruff and 914 fast Plotting tests green.
6. Small rows exhausted. Next: one round of long checks on the head — `ALLWELL_FULL_LANE=1 scripts/test.sh`, then
   `scripts/test.sh tests/test_every_page_renders.py -m ""` — and fix what they find: stale `DESIGN_PENDING`
   entries, new `band rows` from captions moved beside boxes. Still open after that: D14 and D15–D16 band rows,
   D16's two label sites, D18 Segmentation band rows, D28, D29, D31, D32, D33.
7. Fast lane on `830a3f9`: 14,542 passed, 15 skipped, 0 failed (5 min 07 s).
8. D16 (part), the last two label sites: the Nanowells Timepoint (`nanowell/tab_view.py`, now a `LabelledField`
   beside an `unlabelled` box inside the Where scope; `_show_where` shows and hides the field) and the Statistics
   view's Donor (`statistics_view.py`, `StatisticsControls.donor_field` is what is shown and hidden). The five
   Nanowells `label inside` entries removed from `DESIGN_PENDING` — to be confirmed by the every-page run. Ruff
   and 17 Nanowells test files (261 tests) green. No `LabeledComboBox` in a band is left painting its label. Commit `e1dae3e`.
9. CI on `830a3f9`: the nanowell-features job failed `test_nanowell_identity_diagnostics_layout.py::
   test_a_low_card_leaves_the_axis_labels_out_and_says_their_unit[A01, A02]` (pictures 26.9 pt against > 40; tick
   overlaps). Cause: the runner has no Inter installed and matplotlib fell back to DejaVu Sans (its glyph warning is
   in the log), which is wider; this container has Inter system-wide, so it passed here. Fix: the test file
   registers the bundled `assets/fonts/Inter*.ttf` (what the app registers at start-up) in a module fixture.
   Checked here that with the system Inter removed from matplotlib's list, `findfont("Inter")` falls back to
   DejaVu without the fixture and resolves to `assets/fonts/Inter-Regular.ttf` with it; the file's 34 tests pass.
   Whether CI then passes is for the next run. Every-page run on `e1dae3e` in progress. Pushed as `bb1c5cf`.
10. Every-page run on `e1dae3e`: 70 passed, the three desk cases failed. Phone cases clean, no stale entry.
    Two findings: (a) Plotting's shared Channel and Property boxes (`review/core/centre_view.py`, imported under the
    alias `_QComboBox`, which is why a search for `LabeledComboBox(` missed them) still painted their labels —
    now `unlabelled` with the band's caption; (b) smFISH's band needs 80 px at 1284 px wide (two rows) now that
    FOV and Timepoint carry captions beside them. D17 is reopened (`in progress`) with `("smFISH", "band rows")`
    back in `DESIGN_PENDING`; the fix is to move LUT min / LUT max and the secondary actions (This frame CDF,
    Refresh) off the band into the inspector, as §6 does for the plot pages. Commit `e7a246b`.
11. CI on `e7a246b`: the same two Identity Diagnostics cases fail with identical numbers, so the font fix of step 9
    did not take there. Established here: with Inter removed from matplotlib's list A01 measures 26.89 pt (CI:
    26.93) — the cause is the face the figure is drawn in. Not the lookup cache (a probe passes without clearing
    it). Not established: why CI still draws in a fallback with the bundled faces registered — most likely the
    figure's style pass names its family from somewhere else (the Qt application font, or `ui/theme`'s family).
    Stated on the PR (comment 7227715674). Left open, not chased further, per the user's "no rabbit holes".
    Commit `df4421f`.
12. Desk-case every-page re-run on `e7a246b`: 12 passed — the Plotting Channel/Property fix holds, and every
    `DESIGN_PENDING` entry removed so far is confirmed gone. D19 stands `done`.
13. D17 closed: smFISH's LUT min / LUT max / Show overlays / This frame CDF on its inspector Display page; band one
    row at 1284 and 1100 px (two rows still at 916 px, below the every-page desk widths). Test and mutation in the
    row. Next smallest: D18 Segmentation `band rows`, then D15 (Statistics, Spatial, Multivariate band rows).
14. D18 Segmentation `band rows` — measured, not changed, waiting on the user. At 1284 px the band has 1142 px and
    needs two rows (69 px). Its scopes measure 94 (well), 141 (Channel), 74 (FOV), 105 (Timepoint), 160 and 150 (the
    Delete and Crop scope controls), 175 (ON threshold) px, plus the zoom group and the actions Delete cell, Crop
    cells, Edit and Display. Moving the zoom group under the image alone still left two rows (68 px at 1258), so it
    was reverted. The ON threshold field's comment records that it was put back on the band on purpose ("its original
    home"), so it was not moved. What would fit: the editing tools — Delete cell and Crop cells with their scope
    controls, and Edit — as a tool row under the image beside the zoom. That is a layout decision the plan and the
    prototype do not settle (no Segmentation mock-up), so it is the user's.
15. **The user's decisions (asked with measurements):** Segmentation — the editing tools (Delete cell, Crop cells,
    each with its scope control, and Edit) go in a tool row under the image beside the zoom group. Statistics — the
    "Replicates: …" readout becomes a help line under the band. Spatial and Multivariate — Lasso cells moves under
    the figure as a canvas tool, and Spatial's Recompute and Multivariate's Standardize go onto the Display page.
16. D15 (part) Statistics band one row: the "Replicates: …" readout (`_stats_fov_indicator`) is a help line at the top
    of the page body. New `tests/test_review_bands_are_one_row.py` (fast lane, through `tests/_minimal_shell`) measures
    the band at 1400 and 1284 px and finds the readout under it; putting it back on the band fails it (`{1400: ['needs
    …']}`). `DESIGN_PENDING` entry removed. 580 Statistics tests green. Commit `d7c4234`.
17. D15 Spatial and Multivariate bands one row (1400 and 1284 px): Lasso cells is a tool under each figure
    (`card.toolbar.add_group`); Spatial's Recompute and Multivariate's Standardize are on a new Display page each
    (`make_popover_button("Display", root=True)`, which `finish_band` now finds instead of a disabled placeholder).
    Their bands were still 49 px high after that: a `SegmentedControl` (Level, Mode) is 33 px at a form's height,
    against the band's 28 px row. **Shared change:** `ControlBand.add_scope` now makes every segmented control it
    holds compact (`SegmentedControl.setCompact`: 2 px padding, 1 px track), so a band is one height on every page —
    this touches every segmented control in any band (Nanowells' Along x, Positions, …). Tests:
    `tests/test_review_bands_are_one_row.py` (one row, Lasso under the figure and not on the band, the segmented
    control at the row height) and `tests/test_band_panels_open_in_the_inspector.py` (Spatial and Multivariate in
    its roster; the moved control is in the inspector after pressing Display). Mutations: Lasso back on Spatial's
    band fails the one-row case. Found on the way: `band_is_one_row(window)` walks every widget of the window and
    in the fast lane leaves wrappers the reaper deletes twice — a segfault in the next test — so the fast-lane test
    measures the bands `build_page` returns. `DESIGN_PENDING` entries removed. Commit `69f2700`.
18. Fast lane on `69f2700`: 14,549 passed, 2 failed — `test_a_page_has_the_three_fixed_actions.py` still held
    Spatial and Multivariate with a disabled Display; the roster now says they have one. Commit `f600e66`.
19. D18 closed: Segmentation's tool row under the image. Next: D14 Plotting bands (prototype: Channel, Property,
    Timepoints ›, Fold change ›, Color & split ›, then Display · Style · Share), then D16 Nanowells (§6). Commit `5c750e8`.
20. D14 measured in a full app (mini dataset, 1400 px window, band 801 px): every page carries Channel 129,
    Property 193, a help sentence 238 (the Group-by note / "Observations: cells — no nanowell view…"), ON threshold
    175 and the fixed actions 173 before its own controls. **The user's decisions:** ON threshold becomes a row of
    the inspector's Data tab (as the prototype has it), the band's help sentences become a help line under the band,
    and every page-specific control — Heat Map's Aggregation, Timepoint, Layout; Distribution's Mode, Bins, Layout,
    log x, log y, Pool replicates; Scatter's axis channels and properties, Subset, Baseline; Response's X and
    Series — becomes an inspector row, leaving Channel, Property, Timepoints ›, Fold change ›, Color & split › and
    Display · Style · Share on the band.
21. D14 shared step 1: the Plotting band's two sentences — the Group-by note (`_plotting_grouping_note`) and the
    "Observations: cells — no nanowell view" line (`_plotting_observations_static`) — are in a help line under the
    band (`_plotting_notes`, built in `centre_view`; `observation_band` falls back to the band only where a harness
    builds no notes line). 64 tests of those controls green. Commit `e8a01c1`.
22. D14 shared step 2: ON threshold is a Data row on the Plotting band (`_plotting_thresh_frac_btn`, "ON
    threshold: 50 ›", `make_popover_button(tab="data")`); its page holds the caption (`_plotting_thresh_frac_lbl`,
    still rewritten to "ON spots" for a count Property) and the box. `channel_state_controller` refreshes the row
    after it sets the value with signals blocked. `tests/test_thresh_frac_quick_edit.py` (slow file, 20 passed):
    `test_on_threshold_is_a_data_row_whose_page_edits_the_cutoff` presses the row, types into the box and reads the
    cutoff and the row back — the box back on the band fails it ("the box is not on the row's page");
    `test_channel_and_property_combos_fold_their_own_label` pinned the retired painted label and is rewritten as
    `test_channel_and_property_are_labelled_outside_their_boxes`. Commit `a8e0e79`.
23. D14 page steps (commits after `a8e0e79`, each with that page's fast tests green): Distribution's Mode, Bins,
    Layout, log x, log y and Pool replicates and Heat Map's Aggregation on their Display pages; Response's X and
    Series one "Curves" data row (`_response_curves_btn`); Scatter's axis pickers (both modes, including the
    nanowell X / Y / Subset / Baseline) one "Axes" data row (`review/features/scatter/_axes_page.py`, each picker in
    a `LabelledField` so `sync_controls` still hides it through its parent); Scatter's Points switch a data row
    (`_plotting_scatter_points_btn`). Rosters updated (`test_band_panels_open_in_the_inspector.py`,
    `test_a_page_has_the_three_fixed_actions.py`), and one test that read the Subset picker as visible in the
    window now reads it as shown on its page.
24. **Measured after step 23 (full app, mini dataset):** at a 1400 px window (band 801 px) only Line Graphs is one
    row; at 1600 (band 888 px) Line Graphs and Distribution are. Still two rows: Heat Map (Channel 129 + Property
    193 + Timepoint 119 + Layout › 107 + ON threshold › 125 + the fixed actions 173), Bar Plots (adds Color &
    split, Fold change, Timepoints and the Reset order action), Response Curves (its Curves row reads 434 px — the
    summary is too long — and the fit hint "Leave a plateau blank to fit it." is still a band item) and Scatter
    (the Axes row reads 319 px). **What is left for D14, and why it is structural:** the prototype's set
    (Channel, Property, Timepoints ›, Fold change ›, Color & split ›, the fixed actions) fits its band only because
    its labels sit *above* their controls, so an item is as wide as the wider of the two rather than their sum;
    here they sit beside. Putting band labels above makes a band taller than today's `ControlBand.band_height()`
    (44 px) on every page, so it needs a new band height, the `band rows` check moved with it, and every page's
    alignment re-measured — the shared step row D06 deferred to this row. Also owed: shorter summaries for the
    Curves and Axes rows (or eliding them), the Response fit hint off the band, Bar's Reset order to the Display
    page, and ON threshold's summary kept short. An unfinished agent attempt at labels-above is in local commit
    `51e8d3a` (branch `worktree-agent-a79c6ce415759c893`), untested.

## 10. Rows

States: `todo`, `doing`, `done`, `declined` (reason in the row and at the site).

```bash
awk -F'|' '/^\| D[0-9]+ /{gsub(/ /,"",$4); n[$4]++} END{for(s in n) print s, n[s]}' \
    _Docs/Markdowns/DESIGN_LANGUAGE_TRACKER.md
```

| ID | Item | State | Where | Evidence |
|---|---|---|---|---|
| D01 | The render harness applies the shipped theme before building the application, as `allwell.main` does; the phone snapshot fixture too. Check every other test that builds `AllWellApp` or `ReviewApp` and asserts a colour | done | `tests/_page_render_case.py` (`start_window`), `tests/test_phone_layout_snapshot.py` | `tests/test_the_render_harness_shows_the_shipped_theme.py` reads the painted window background from a grab. Mutation: dropping `theme.select_theme_from_env()` from `start_window` fails it with *painted #0B0F17; the default theme (grey) paints #141618 and the unthemed blue baseline #0B0F17*. The harness honours `ALLWELL_THEME`, so both palettes render (second test). Phone baseline regenerated: written without the theme and with it, the two files are byte-identical (a theme bundle moves colours only); the one change is the scenario list, already stale on `origin/main` (*only in the baseline: Job Queue; only now: Automation, Watch Folder*). Audit: the seven other tests that build `AllWellApp` assert no colour; the `ReviewApp`-fixture tests that read colours compare against live `Colors.*`, so they hold in whichever theme is applied. `conftest.py` needed no change |
| D02 | Tokens of §2.8 for every theme: three surface roles, three text roles, three edges, selected pair, label-on-accent, radii, spacing without 32, type sizes 12, 13, 15 and 17 only. Audit every reader of each changed token first | done | `ui/theme.py` (`Colors`, `Typography`, `Radii`, `Spacing`), `ui/palettes.py` (new: the grey bundle), 86 reader files | **No aliases, at the user's instruction**: the ten retired names (`surface`, `panel`, `panel_elevated`, `text_muted`, `text_faint`, `placeholder`, `border`, `control_border`, `accent_dim`, `accent_fg`) are deleted and every reader moved to a role. An AST scan resolving every name bound to `Colors` found 340 reads; `text_muted`/`text_faint` went to `text_disabled` at the 21 sites inside a `:disabled` rule or a disabled branch and to `text_secondary` at the other 91; `border` went to `field_edge` (14: field outlines and a button's hover edge), `button_edge` (11: resting buttons, menus, popovers, toasts, scroll handle) or `hairline` (26). Missed by the scan and fixed: the Image Table builder's `$token` template fed by `vars(Colors)` (would have raised `KeyError`), and seven string-named tokens. Fields fill with `canvas` and lift to `text_secondary` on hover; a disabled button keeps the control fill with a hairline. Blue's disabled ink measured 3.23 on a card and was raised to `#717D90`; grey's `danger` to `#FF6B6B` (destructive label 3.55 → 4.71). Renders of all 48 `mini` desk pages before (D01 tree) and after in both palettes, kept for the PR; the bar plots checked in both. Moving caption text 11 → 12 exposed a latent layout loop that killed the Review window on its way to Plotting (stack overflow): fixed in `widgets/well_plate_selector.py` (floor not re-applied from inside its own relayout; `tests/test_plate_floor_settles_in_a_scroll_area.py`, 206 floor changes without the guard) and the new `widgets/settling_scroll_area.py` for the rail (`tests/test_settling_scroll_area.py`, nests to the probe's stop of 60 without the guard). Phone baseline regenerated: 1,556 records moved, all from the 11 → 12 caption size (316 labels' font, the rest geometry below them) |
| D03 | The contrast test | done | `tests/test_theme_contrast.py` | Green for grey and blue. Run against `origin/main`'s tokens (each role mapped to the token main's stylesheet drew it with) it fails naming, in grey: placeholder 3.15 on a card, primary label 3.46, `danger` 3.55, field edge 1.53, muted 4.48, faint 2.51; in blue: placeholder 3.35, primary label 2.86, field edge 1.38, muted 3.23, faint 1.98. A probe palette with a white primary label is caught by name in the test itself |
| D04 | Text roles and `NOT_SET` | done | `ui/text_roles.py` (new; the property is `textRole`, because `QLabel[role="section"]` already exists), its rules included by `ui.theme.qss` | `tests/test_text_roles.py` measures each role's polished `pixelSize`, weight and `WindowText` ink in both palettes, the eyebrow's capitals and tracking, the number's monospace face, and `show_value` drawing *not set* in the disabled ink. Mutation: removing the role rules from the application sheet fails 20 of its 23 cases |
| D05 | Four button kinds; the sheet's base button weight; the one-off variants mapped or retired | done | `ui/buttons.py` (new: builders, `set_kind`, the four kinds' rules), `ui/theme.py` (the old primary/secondary/danger/muted rules removed), `review/core/ui_helpers.py` (`_btn` builds through `ui.buttons`), 60 files of call sites | Retired by renaming every site: `secondary`→`default` (132), `danger`→`destructive` (10), `muted`→`quiet` (1, plus one QLabel that carried it, now a text role), `card`→`default` (1) — 144 on the base by the new guard's own count, matching the sweep (140 call sites, 4 rows of Job Queue's recipe table) plus one helper default (`qc_view._button`) that only a parameter-default check finds. The base button weight was already medium. `tests/test_button_kinds.py` measures each kind's drawn fill, label ink and weight, and the disabled ink, in both palettes; without the four rules 8 of 19 fail. `tests/test_buttons_have_one_of_four_kinds.py` (four bite shapes and a clean case in `test_guards_bite.py`; on the base it fails naming the 144 sites) holds the rest; its roster holds the 26 state-carrying `toggle*`/`sem*` sites, which D13 converts, and `SelectionChip`'s own `variant` parameter, which D07 replaces |
| D06 | Labelled field and form grid; `LabeledComboBox`'s painted prefix retired | done | `widgets/labelled_field.py`, `widgets/form_grid.py` (new), `widgets/control_band_form.py`, `ui/theme.py` (`Metrics.form_label_w`) | `tests/test_labelled_field_and_form_grid.py`: the label drawn above (or beside) its control in the label role's size and ink, every form row's control starting at one x past the label column, help and eyebrow rows in their roles, and `BandForm` moving a label set above its control into its column and back — reverting `BandForm` to horizontal captions only fails that case (*[] == ['Metric']*). **The prefix's retirement moved to row D14**: every one of its 30 sites is a control band, and taking the label out of the combo puts it above every band control, which reshapes all the band pages — the work §5.3 holds for phase 5, where the controls that no longer fit have the inspector to go to |
| D07 | Notice and status chip | done | `widgets/notice.py`, `widgets/status_chip.py` (new), `widgets/context_banner.py` (now an information `Notice`; Cancel is the close symbol, §3.6), `ui/theme.py` (the banner's own rules removed, the two components' rules included) | `tests/test_notice_and_status_chip.py` reads the left rule's and the dot's colour off a grab for each severity and each chip tone, in both palettes, and checks the bold lead, one button and the close symbol; dropping the per-severity and per-tone rules fails 18 of its 24 cases. The banner's own fit tests (`tests/test_context_banner_fits.py`) pass unchanged on the notice. **Not folded here:** the seven labels that switch between `QLabel#Notice` and `Muted` by object name (Home's QC panels, Analyze's QC section, the schema picker) — `Notice` is one of the retired text object names, so they go with row D10's sweep, as notices |
| D08 | Page header and action row | done | `ui/page_chrome.py` (new: `PageHeader`, `ActionRow`) | `tests/test_page_chrome.py`: through the real primary button, the reason shows while it is disabled, goes when it is enabled and comes back when disabled again; an enabled action shows none; the primary action is last with its neighbours before it; the header's title and detail carry their roles. Mutation: not following the button's `EnabledChange` fails the first case |
| D09 | The guards and measured checks of §5.2, with rosters holding today's sites | done | `tests/_design_scan.py`, `tests/_design_rosters.py`, seven guard files, `tests/_layout_checks.py` (four checks), `tests/test_every_page_renders.py` (`DESIGN_PENDING`) | Guards and their rosters on the tree they were written against: text roles 382 sites in 122 files, hand-built buttons 289/92, style sheets 166/65, spacing literals 278/76, sentence case 246/85, state in a label 34/14, unset values 212/101 (plus the variant guard of D05). Each catches a violating case and clears a clean one in `tests/test_guards_bite.py` (11 shapes); a roster entry larger than what remains fails until lowered. They are new on this branch, so the bite cases are the proof (§7.5 #1). The four page checks are each shown catching their case and clearing the right one in `tests/test_design_layout_checks.py`; on every dataset and screen they report 62 (page, check) findings, each held by `DESIGN_PENDING` with its row, and the every-page suite is green with them held (73 passed). Writing them found two bugs of the checks' own — `scope_host` called as a function, and a band's given height read where a layout can stretch it — both fixed before the roster was taken |
| D10 | Sweep: text roles replace the retired object names and inline font settings | done | 312 `setObjectName` sites in 110 files rewritten to their role (`Muted`/`InfoHint`→help, `Title`→page title, `Heading`/`SectionTitle`→section heading, `Secondary`/`Caption`/`MutedCaption`→label, `Mono`→number); 34 coloured or conditional sites by hand (import verdicts, path-field hints, form status lines, QC status lines, Home values, the Nanowells subset and details lines) through `text_roles.set_role` with a tone; three `role="section"` headers to section heading; the retired rules and the unused `notice_wash` token deleted from `ui/theme.py` | `tests/test_text_has_a_role.py` roster 382 → 13 sites in 5 files, each entry with its reason and page row (Batch Export's bold row labels D26, Pipeline Info's bold tree rows D27, two dashboards' bold active row D22/D24); ten painter and measuring fonts exempted per function, each with a reason and a liveness check. The sweep found the guard blind to a name carried by a variable, a default or a helper's parameter (`form_chrome._set_status(quiet="Muted")`, `path_field._set_hint_style("Muted")`, OME's `tone`): it now reads all three, with three new bite cases; on the pre-sweep tree the extended guard counts 394 where the first counted 382. Choice to check: a status line's alert colour is a *tone on a text role* (amber or red ink), not a notice card — §2.6's notice is kept for standalone notices. Phone baseline regenerated: 196 widget paths renamed (the object name dropped), 141 records moved, mostly labels whose role changed size (`Secondary` 13 → label 12) |
| D11 | Sweep: every button through `ui/buttons.py`; one primary per page decided and written in a table in the pull request | done | 289 hand-built `QPushButton`/`QToolButton` sites in 92 files → `buttons.default` / the new `buttons.tool_button`; 120 trailing `setProperty("variant", …)` lines folded into the builder call; four extra primaries demoted (Batch Export's + Add, Statistics' + Add group, Sample Map's + Add and Make replicates from variables, Heat Map's Auto-populate from selection) | `tests/test_buttons_have_one_of_four_kinds.py` hand-built roster 289 → 0, its variant roster unchanged (the 26 state-carrying toggles are D13's). `one_primary` holds on every page: its `DESIGN_PENDING` entries are gone and a tour of all three datasets read each page's visible primaries — **the table for the pull request:** Analyze form: Run pipeline; Batch Export (each tab): Run batch export; every Datasets tool: its own action (Concatenate, Convert ×3, Merge, Subset, Check, Zip folder, Start watching, Start queue); Statistics: Run test; Home › Sample Map: Apply Quick Replicates; Image Table: Generate; Cell Browser: Generate (it had drawn as a primary through a retired `#Primary` object name, which the review after phase 4 found and `test_buttons_have_one_of_four_kinds.py::test_no_button_takes_a_look_from_its_object_name` now refuses); Nanowells › QC: Resolve; every other page (the plot pages, which redraw as their controls change, and the rest): none |
| D12 | Sweep: spacing and margins from tokens; one-off style sheets moved into the theme or removed | done | Spacing: 554 literals at 269 sites to `Spacing` tokens (on-scale values exact; off-scale snapped to the nearest step, ties down: 2/3/5/6→4, 7/9/10→8, 11/14→12, 20→16; a 1 px border offset → `Metrics.hairline`). Style sheets: the guard now passes the theme applying its own sheet and a sheet being cleared (with a bite case for a module's own `qss(…)`); 16 reusable widgets' token-only sheets and 15 named pieces (status bar, version pill, help manual, app log, docks, Cell Browser thumbs, Pipeline Info tree, focus chip, phone sheets, transparent widgets) moved into the application sheet through the new `ui/component_qss.py`, included last but for the text roles so a component wins ties as its own sheet did; text-colour sheets on dashboards and labels became text roles | `tests/test_spacing_comes_from_tokens.py` roster 278 → 9 sites in 4 files; `tests/test_no_style_sheet_outside_the_theme.py` 166 → 63 in 33 files; every remaining entry carries its reason in `tests/_design_rosters.py` — data colours (well, group, channel and state swatches and dots), menu-indicator hides (CLAUDE.md §7.12), per-instance state (drawer side, badge tone, selection-chip variant), figure layout, phone layout's rescale, and the pieces whose page row restyles them (D14, D18, D20–D22, D24, D26–D28). The phone form's flat icon button is now drawn as a button by a `sheetButton` property and a touch-sheet rule instead of wiping its own sheet (`tests/test_control_band_form.py` reads the drawn fill). Phone baseline regenerated: 741 records moved, almost all y/x offsets from snapped gaps. **D34 stays startup-only**: the per-instance sheets on the roster still cache tokens, so a live palette switch is not yet safe |
| D13 | Sweep: wording — sentence case, no colons, "…", glyphs to icons, state-in-label toggles to switches or segmented controls, unset values to `NOT_SET`, "Color" as the one spelling in what users read, an ellipsis only where §3.6 allows one, Close and Back as symbols, switches and checkboxes sorted by §2.5 | done | app-wide (≈190 files); `ui/buttons.py` (`mode_tool`, `icon_button`, `set_icon`), `review/core/plot_toggles.py` (`make_spread_choice`, `make_fov_switch`), `widgets/icons.py` (`minus`, `pencil`, `arrow-left-right`), `widgets/segmented_control.py` (`setSegmentToolTip`), `tests/test_color_is_the_one_spelling.py` (new); `widgets/reserved_width_button.py` deleted | Every label renamed at each quoted place its text lives — code, tests, the manual — rather than only where the guard reads it. **The guards, made to see what they missed, then at zero:** sentence case 243 → 0 (the guard now strips markup, treats the registries' page and tool names as names — they are keys of restore state, help topics and manual headings, so their own capitals are not this row's to change — reads a breadcrumb's arrow, an f-string's glyphs, a lone ✕ / − / + / ◀ / ⋯ as an icon stand-in, and follows the app's own one-level label helpers such as `_add_bold_label` and `self._row`, which found 41 more sites); state in a label 34 → 0; unset values 212 → 0 (narrowed to text handed to a widget: the old count read every "—" in figures, CSVs and logs, which are not a control's unset value, and the "—" an *empty* combo shows is the Review combos' no-options sentinel compared in about forty places, recorded in the guard as not an unset value); the variant guard's 26 state-carrying `toggle*`/`sem*` sites → 0, and their theme rules deleted; "colour" in interface text 46 → 0. Each refinement has an example in its guard's own test and a bite case in `tests/test_guards_bite.py` (helper-passed label, f-string glyph, page name, a dash in a table cell, "colour" in a tooltip). **Controls converted:** on/off settings that act at once became switches — Bar Plots' *Pool replicates* and *Show significance*, Segmentation's *Show outlines*, *Show as a binary mask* and *Show why cells were removed*, Scatter's *Color by time* and *Plot only the gated cells*, smFISH's *Show overlays*, Nanowells' *Show overlays*, *Show notes* and *One per well*, and the shared per-FOV spread (*Spread across FOVs*); choices of two became segmented controls — Bar Plots' *Group by* (Well / Timepoint), the shared SEM / SD error band, Cell Browser's sort direction (drawn chevrons); a button that arms a pointer mode became a pressed-in tool (`ui.buttons.mode_tool`) — *Delete cell*, *Crop cells*, *Lasso cells*, Nanowells' *Set anchor*, Heat Map's *Layout* and the crop tool; *Reset order* became a quiet command enabled while there is an order to reset. Glyph-only buttons (✕ ✎ − + ◀ ▶ ▲ ▼ ⇄ ⋯ ☰ ⚙, 34 sites) draw icons with tooltips; ✓ / ✗ / ⚠ in status lines became words in the failure or attention tone (Manage remotes and its key wizard, Analyze's input planner, the OME verdict, Watch Folder, Batch Export's result lines) and Validate's findings carry drawn severity icons. The phone bar's Controls and Wells sheets close with the ✕ symbol; the queue's Back is the arrow. "Delete…" (a confirming alert follows, no further input) lost its ellipsis. Evidence: the full fast lane on `d408dcd`, 14,315 passed (an earlier run crashed a worker in `tests/test_sample_map_phone_layout.py`, which looked for a button reading "Variable" and aborted before its own cleanup once it read "Add variable"; and the Variables tab's row delete was looked up by push-button type after it became a tool button, so it found nothing and deleted nothing — the same silent miss a widget-type change makes anywhere a lookup names the old class); each converted toggle's own tests drive the real control (the significance switch through restore and a lazy build, the per-FOV and SEM banks through `make_band_controls`, the anchor and overlays switches through the Nanowells page); `tests/test_button_kinds.py` reads a pressed-in tool's fill (`selected`) and fails in both palettes when its rule is removed. **Read, not converted:** a dialog's own footer buttons keep the words *Close* and *Cancel*, as its OK does — §3.6's symbols are for the panels and sheets a page lays over itself; and the §2.5 sort of the remaining checkboxes and switches cannot be finished before the inspector exists, since a switch belongs "only in an inspector row": the switches made here sit in band popovers until D14–D21 move them, and those rows carry the sort |
| D14 | Plotting pages: Bar Plots, Line Graphs, Response Curves, Heat Map, Scatter, Distribution; and, first, `LabeledComboBox`'s painted prefix retired across all 30 band sites (moved here from D06), each label set above its control with `widgets.labelled_field.LabelledField` | todo | `review/features/{barplot,lineplot,response,heatmap,scatter,distribution}/tab_view.py` | §4 for each page |
| D15 | Statistics (results as a formatted table, not preformatted text), Multivariate, Spatial | todo | `review/features/{stats,multivariate,spatial}/` | §4 |
| D16 | Nanowells: all eight views, to the redraw of §6 | todo | `review/features/nanowell/` | §4; `scripts/test.sh --nanowell-features` |
| D17 | smFISH | done | `review/features/smfish/tab_view.py`, `widgets/labeled_combo.py` (`unlabelled`) | **Labels outside the controls.** FOV and Timepoint were `LabeledComboBox`es painting "FOV: …" inside the box; they are `unlabelled("FOV")` / `unlabelled("Timepoint")` — the same box, still named, drawing only its value — captioned by the band (`add_scope("FOV", box)`) like Channel, Threshold and the LUT fields beside them. **One row:** with those captions the band needed two rows (80 px at 1284 px wide), so how the frame is drawn — LUT min, LUT max, Show overlays and This frame CDF — moved onto the inspector's Display page, opened by a Display button at the band's right (`make_popover_button("Display", root=True)`); nothing they compute changed. At 1284 and 1100 px the band is one row (measured with `band_is_one_row` on the page built by `tests/_minimal_shell`). **Evidence:** `tests/test_every_page_renders.py` (desk cases on `e7a246b`) finds no `label inside` on smFISH; its `band rows` entry is removed too; `tests/test_band_panels_open_in_the_inspector.py` presses the real Display button and finds the four controls in the inspector, and flipping Show overlays there flips `state.smfish.show_overlays` — putting LUT min back on the band fails it (`_smfish_lut_min_edit is not on the Display page`); `tests/test_labeled_combo.py::test_unlabelled_keeps_its_name_and_paints_no_prefix` pins the box, and deleting `setLabelShown(False)` from `unlabelled` fails it (`assert not True`). |
| D18 | Image Browser: Image Table (the builder shelf and action strip), Segmentation, Cell Browser, Playground | done | `review/features/image_browser/` (`image_table/tab_view.py`, `image_table/builder_view.py`, `playground/tab_view.py`), `review/features/preview/panel_view.py`, `widgets/page_body.py` (`build_bandless_body`), `widgets/labeled_combo.py` (`unlabelled`) | **Image Table and Playground** build no band: neither has a scoping control (the shelf and the action strip, and the Playground dock, hold theirs), so the empty strip is gone (`build_bandless_body`); the shelf's root-filter inspector pages anchor on the page body (`_image_table_page`) instead. **Segmentation:** FOV and Timepoint labelled outside their boxes; the band is one row — the zoom, Delete cell and Crop cells with their scope controls, and Edit are a tool row under the image (the user's choice among three, with measurements). **Cell Browser** had no finding. **Evidence:** every-page desk cases on `e7a246b` clean for Image Table, Playground and Segmentation's labels; `tests/test_review_bands_are_one_row.py::test_segmentation_band_is_one_row_with_its_editing_tools_under_the_image` (one row at 1400 and 1284 px, the tools in the row and not on the band) — putting Edit back on the band fails it; `tests/test_control_band_adoption.py` counts the band-less bodies; 240 Segmentation tests and 384 Image Table tests green. The every-page run on the final head is still owed for Segmentation's band. |
| D19 | Review CSV, Saved Cells, Saved Plots | done | `review/features/review_csv/tab_view.py`, `review/features/saved_cells/tab_view.py`, `widgets/labeled_combo.py` | **Review CSV:** FOV and Timepoint labelled by the band outside their boxes, as on smFISH (D17). **Saved Cells:** the page has no control that scopes what it shows, so it no longer builds a band at all — it showed an empty strip; it is built with the new `widgets.page_body.build_bandless_body`, whose content takes the page's inset on all four sides instead. **Saved Plots** had no finding of any check, so nothing changed there. **Evidence:** `tests/test_every_page_renders.py` with the `Review CSV`/`label inside` and `Saved Cells`/`empty band` entries of `DESIGN_PENDING` removed finds neither in any case (the stale-entry test fails if an entry outlives its finding, and the main test fails if the finding returns); the `unlabelled` mutation of D17 covers the Review CSV boxes too. |
| D20 | The shell, in window style W1 (§3.1): the window background and the two panels, no dividing lines; the header (five commands as words, after `SURFACES_TRACKER.md` S23) with the application's name; the mode switch as a pill in the selected look; the sidebar with the dataset button at its head; the status bar | done | `ui/shell_frame.py` (new), `ui/palettes.py` / `ui/theme.py` (`Colors.frame`), `ui/component_qss.py`, `allwell.py`, `review/core/app_shell_view.py`, `widgets/mode_bar.py`, `widgets/rail_nav.py` | **Done.** The header, the mode band, the sidebar and the status bar sit on the window's ground, a new token `frame` (`#0B0D0F` grey, `#06090F` blue), with the hairlines under the header and the band removed; the page stack is a panel in the canvas colour clipped to 12-pixel corners with an 8-pixel gap to the sidebar and to the window's right and bottom edges (`shell_frame.install`; in phone layout the gaps and the rounding go). The regions are named where they are built (`ShellHeader`, `ShellBand`, `ShellRail`, `ShellSplit`, `ShellPage`) — named afterwards, they kept the look of their first polish. The mode switch is a pill: a 3-pixel track in the canvas colour, the current segment in the selected fill and its ink, the others in the secondary ink; its per-segment style sheets became a rule keyed on an `active` property (style roster `widgets/mode_bar.py` 3 → 0). The dataset button at the sidebar's head is raised, inset and rounded. **Evidence.** Renders of Review in both palettes. `tests/test_window_style_w1.py` (new, slow lane) reads the frame off the rail's foot and the band, the gap and the rounded clip of the page, the mode switch's selected segment and the rail's selected row; dropping the rail and band from the frame rule fails the first, putting the accent bar back fails the second. Phone baseline regenerated: 5,486 records moved on 56 pages, almost all a y offset from the hairline under the band being gone. **Decided by the user, after the menu-and-organisation pull request merged (S23 left six header commands — Split View, Refresh, Presentation, the remote-node button, Phone layout, Help):** they stay icons with their tooltips rather than becoming quiet words as the mock-up draws them. After that merge the inspector's dock takes the canvas colour as W1's second panel (its own style sheet moved into the application sheet). **One top bar, as the mock-up draws it** (chosen by the user from renders of both): at the desk the mode pill and the ‹ › history arrows sit in the header beside the name, the band they live in for phone layout is hidden, and the page tabs are the first row of Review's page panel (`shell_frame.lend_to_header`, which hands them back to the band when phone layout turns on, each to the place a hidden placeholder kept, so phone layout's own insertions still count from them). The pill is as wide as its segments need and stays when the rail collapses (`AllWellApp._sync_mode_bar`; `tests/test_mode_bar_collapse.py` rewritten to that). `tests/test_window_style_w1.py` holds the header, the hidden band, the tabs in the page and phone layout's round trip; skipping the move fails two of its four. The dataset button carries its second line, wells and timepoints in the help role (`app_shell_view.dataset_detail_text`, `tests/test_dataset_button_label.py`). Phone baseline regenerated again: 186 records on 56 pages. **Found by the slow-lane sweep after phase 4 and fixed here:** `widgets.form_registry` kept every `FormGrid` alive for the process — the grid registers its own bound method, which held its key in the registry's weak dictionary — so Batch Export's two grids outlived a closed window (`test_a_closed_review_app_is_freed`, failing since D26; bound methods of the widget are held weakly now, with a fast test in `tests/test_phone_form_registry.py`); and `tests/test_page_alignment.py` still required every page to open with a band — it names the form pages now (`FORM_PAGES`, each with its row) and holds that they carry none |
| D21 | The rail's plate footer, saved selections and per-page rail frames | done | `widgets/rail_nav.py`, `widgets/selection_footer.py`, `review/core/rail_plate.py`, `review/core/app_shell_view.py`; `widgets/selection_chip.py` deleted | Rail rows have 7-pixel corners; the selected one is the selected fill in its ink with no accent bar (the strip it was drawn in is kept, empty, so no row moved); hover is the control fill. The divider between the nav and the plate is gone. The plate slot and every per-page rail frame (the main frame with the footer and Saved selections, and each feature's own) are inset by the rows' 8 pixels, so the plate card and the Saved selections drawer sit inside the rail rather than against its edges; the footer's All and None are quiet. A row's count is plain text in the number role rather than a chip — `SelectionChip`, whose only user it was, is deleted, and with it the button-kind roster's last entry. **Evidence.** Renders of Plotting and Cell Browser's rail in grey (both palettes with the window, D20). `tests/test_rail_nav_label_follows_selection.py` reads the selected ink as `selected_fg`; `tests/test_window_style_w1.py` reads the empty strip; `tests/test_buttons_have_one_of_four_kinds.py`'s pending roster is empty. **Not restyled:** the Saved selections drawer keeps the collapsible section's own header — it is a `CollapsibleSection` like every other, and restyling that is the shared widget's, not the rail's |
| D22 | Analyze: the form as cards with a form grid in a wider column; cautions as notices; the foot row; the Run card; the log behind a button until a run starts | done | `analyze/` (`form.py`, `form_chrome.py`, `run_footer.py`, `run_spec.py`, `log_pane.py`, `seg_params.py`, `tab.py`), `widgets/card.py` (new), `widgets/plate_progress.py`, `widgets/run_dashboard.py`, `allwell.py` (`_sync_mode_bar`), `ui/theme.py` (`Metrics.form_column_w`) | **The page as the redraw has it.** The form is a 430 px column (`Metrics.form_column_w`) under a "New run" title with quiet "Copy from a run…" and Presets (`ui.page_chrome.PageHeader`); its sections are `CollapsibleSection` cards (D23) whose rows are a form grid — the label in a `Metrics.form_label_w` column beside the field, turning label-above on a phone through `widgets.form_registry` (`form_chrome._row`); the segmentation channel's caution is an attention `Notice`; the foot is a card (the new `widgets.card.Card`) with Run on and a quiet Manage…, then Add to queue and the primary Run pipeline right-aligned, primary last, the reason under them; the output pane is the Run card — title, quiet Clear, "Show log" / "Hide log" — around the dashboard, which no longer folds and whose legends read in sentence case in every dashboard (one place, `plate_progress._sentence`). The log is behind "Show log" until a run starts, shown by a run's start and put back by a clean end. **Evidence.** Renders idle and mid-run (a 96-well run driven through the dashboard and the log) in both palettes, kept for the pull request. `tests/test_every_page_renders.py` all 73 cases on `7b992d3` and `4b06367` (the run found Analyze's phone-form label clipped on five screens: aligned left, a word-wrapped label got one line's height; fixed by giving it the row). Pinned: `tests/test_card.py` (fill and hairline read from a grab in both palettes; without its transparency rule a bare row paints the canvas, failing both); `test_analyze_sidebar_ux.py::test_a_row_label_sits_beside_its_field_at_the_desk_and_above_it_on_a_phone`; `test_analyze_run_failure_visible.py::test_the_raw_log_is_behind_show_log_until_a_run_starts` (pressed through the real button); `test_analyze_narrow_sidebar_fit.py::test_run_controls_fit_the_form_column_with_the_queue_button_shown` (one line, primary last); `test_segmentation_channel_dropdown.py` (the caution's severity); `test_mode_bar_collapse.py::test_the_bar_stays_at_the_rail_width_over_analyze_s_wider_column`. Fast lane green. **Decided against an earlier rule, reported to the user:** the three modes' left panels no longer share one width (Analyze's column is wider; the mode bar stays at the rail's width so the switch does not move); "Copy from a run…" moved above the banners into the title row, where an earlier request had it just under them; and the log starts hidden where it used to start open. `tests/test_control_band_adoption.py` drops `analyze/log_pane.py`, its docstring naming this row as the reason. **Not done:** the run's status is still the dashboard's own status line and headline, not a status chip in the Run card's heading
| D23 | Analyze's own collapsible sections replaced by `widgets.CollapsibleSection` or by cards | done | `analyze/form_chrome.py`, `analyze/tab.py`, `widgets/collapsible_section.py`, `widgets/plate_progress.py`, `tests/test_one_collapse_implementation.py` (new) | Every Analyze section is a `CollapsibleSection` card: the widget gained `collapsible=False` (a titled card with no chevron whose header ignores clicks), `animated=False` (the form reads a body's visibility straight after a toggle) and `addHeaderWidget` (the info marker, a section's switch, the folded summary); the hand-built caret, rule and monkey-patched header press are gone. The one other fold in the app, the run dashboard's own ▸/▾ beside its header, is gone with D22's Run card, which does not fold (the log is what hides). `tests/test_one_collapse_implementation.py` at zero: its guard rejects a caret flipped between ▸ and ▾ by hand (bite case `hand_built_fold`), and its test drives the real form — every section is the shared widget, a folding one opens from its header, a fixed one does not fold; making the header click do nothing fails it (*Filename schema did not open from its header*). `tests/test_run_dashboard.py::test_the_dashboard_does_not_fold` replaces the fold's own test. Tests that found folded sections by the caret's markup now press each card's header
| D24 | Datasets: the tool list grouped; every tool page to the skeleton of §6; no empty band strip; fields capped in width | done | `datasets/core/tool_page.py` (new), `datasets/tab.py`, the nine `datasets/<tool>/view.py`, `datasets/job_queue/{view,recipes_view,runs_view}.py`, `datasets/core/{converter_dashboard,equation_row,channel_roles_row}.py`, `widgets/form_grid.py`, `widgets/path_field.py`, `ui/page_chrome.py`, `ui/theme.py` (`Metrics.tool_page_w`) | **The tool page.** `tool_page()` lays each page out as a column capped at 720 px (`Metrics.tool_page_w`) with the title and one line of help; `cards()` / `into_card()` move the rows into titled cards — *Folders*, then what the tool does (*What to copy*, *How to combine*, *Placement*, *Plate*, *Channels*, *Filenames*, *Dataset*, *When the folder has settled*) — keeping each row's stretch, alignment and hidden state, and a card of hidden rows hides; `foot_row()` is the foot (`ActionRow`): the reason, any run setting (OME-TIFF's Workers), Add to queue, the tool's follow-ups (Validate's Apply repairs), the primary last. The reason is read off the page's own folder fields — the first empty one (*Choose a folder for “Source dataset”.*), else the first refused one in its own words — re-read on each field change and each change of the action's state. The tool list is grouped under Combine, Convert and Maintain (`_Feature.group`, `RailNav.addCaption`); the empty band over every tool page is gone (the eight `DESIGN_PENDING` *empty band* entries cleared). The converters' one-line option rows are form grids, one setting a row; `FormGrid`'s labels ask for the column without pinning it and the grid declares a label-above phone form. The sentence-case guard learned two more shapes — a label handed over as a module constant and as a parameter's default — which found the run buttons' remaining ▶ glyphs. **Evidence.** Renders of all ten pages in both palettes, kept for the pull request. `tests/test_every_page_renders.py` all 73 (on `fac13d8` and, after the overflow fixes, `c79d58f`: in the capped column the converters' option rows, the converter dashboards' pick row and Merge Plates' swap note had held four cases wider than their screens). `tests/test_datasets_tool_pages.py` (new) reads the skeleton off every real page — capped column and one page title, the primary last in the foot, the cards and a card of hidden rows hiding (removing the hide-sync fails it), the reason following the fields through Browse (removing the per-field re-read fails it), the three eyebrows; `tests/test_labelled_field_and_form_grid.py::test_a_form_grid_puts_each_label_above_its_controls_on_a_phone`; `tests/test_phone_fit.py` green. Fast lane 14,336 passed. `tests/test_control_band_adoption.py` drops `datasets/tab.py`, its docstring naming this row. **Not done:** the prototype's quiet header actions on a tool page (none of the nine has one today)
| D25 | Datasets › Job Queue: header actions, the empty state, Stop only while running, the row's kinds of button | done | `datasets/job_queue/view.py` | The header's commands run quiet first (Clear completed, Clear all), then Recipes, Add job, Stop job and the primary Start queue last — the order of every tool page's foot row; a flow, so they wrap on a phone. **Stop job is shown only while a job runs** (`set_job_running`). The empty queue is a card — the heading *No jobs queued*, two lines, one *Add job* button (which opens the same menu as the header's). The rows already used the drawn ⋯ icon for their actions (D13). The page builds through the tool-page skeleton (D24), so the Automation tab's empty band is gone with the rest. Renders, empty, in both palettes. `tests/test_job_queue_view.py::test_start_queue_is_the_last_header_button_after_add_and_stop` and `::test_stop_appears_only_while_a_job_runs` (removing the visibility line fails it); `tests/test_queue_ux_review.py::test_p7…` reads the empty state's lines and button. **Not done:** the header is left-aligned under the page's help rather than right of the title, kept as a flow so it wraps on a phone; no render with jobs queued
| D26 | Batch Export, all six pages | done | `review/features/batch_export/` (`_page.py` new, `base_panel.py`, `bar_panel.py`, `scatter_panel.py`, `response_panel.py`, `nanowell_panel.py`, `tab_view.py`), `widgets/card.py` | **The page as the redraw has it.** Every mode is a title, *Batch Export · <mode>*, with a quiet *Sync from app* and the primary *Export* (renamed from *Run batch export*) at its right (`ui.page_chrome.PageHeader`); then a *Groups* card — Clear all and Quick setup quiet, Add group, a line of help, the variable picker, the plate and the group list, and an information notice *No export groups yet.* while there are none — beside one card holding two form grids, *Output* (Write to, Folder, Format, Style preset, Also write) and *Plot* (Channel, Property, Fold change, Error band, Points, then each mode's own rows, the Axes block and the Timepoints list under eyebrows). The cards stack when the viewport is too narrow for both, as the two columns did; the divider between them is gone, and with it the one divider that turned at run time. A group's row has quiet *Rename…* and *Clear*. The empty band over all six modes is gone (the six `DESIGN_PENDING` *empty band* entries cleared). Each mode's rows keep their builders: `_page.form_row` / `form_wide` hand a row to the grid under its label, so the bold, colon-ended row labels are gone (the text-role roster drops `base_panel.py` 7 → 0 and `scatter_panel.py` 1 → 0) and the axis labels lose their colons; the result line takes the body role, in the failure tone on errors, instead of a green or red sheet, and Quick setup is the shared menu button (one arrow, §7.12) — the style-sheet roster for `base_panel.py` 9 → 5, the rest each group's own colour. A scroll area inside a card is transparent (`widgets/card.py`). **Evidence.** Renders of all six modes in both palettes, kept for the pull request. `tests/test_batch_export_page.py` (new, slow lane) reads the skeleton off every real mode — the title, the header's two commands in order and kind, the Groups heading's three, the Output form's rows, the two section headings — and the empty-state notice giving way to a group's row; swapping the header's order fails the first and building the empty state as a plain label fails the second. `tests/test_narrow_page_widgets.py::test_batch_export_stacks_when_the_explicit_left_minimum_does_not_fit` offers a width that fits only if the gap between the cards is not counted. Slow-lane Batch Export files (`panel_shots`, `ctx_characterization`, `empty_groups`, `plate_map`, `csv_batch_export_eval`, `export_groups_persistence`, `line_color_mode`, `no_view_holds_the_plate`) green. Phone baseline regenerated — it had not been since D12, so it records D13 and D22–D25 too: the Analyze form and Progress pages (95–188 records each), every Datasets tool page (48–168), Playground (88), two or three records on most other pages; Batch Export is not among its scenarios. **Not done, for the user to decide:** §6 draws *Error band* as SEM / SD / **Off** and *Points* as a **checkbox**, "because nothing happens until Export". Both controls here are the plots' own shared ones (`plot_toggles.make_spread_choice` / `make_fov_switch`): choosing one redraws every plot at once, and an export reads the shared state. *Off* would be a new choice the plots do not have, and a checkbox that acts only at Export would have to stop sharing the plots' state — each changes what the control computes, so both are left as the shared SEM / SD control and the shared switch |
| D27 | Home: Overview, Cell Gating, Sample Map, Ratios, Pipeline Info, QC Triage; the empty band strip removed | done | `review/features/home/` (`tab_view.py`, `controller.py`, `collect.py`, `sample_map/tab_view.py`, `groups/tab_view.py`, `variables/`), `review/core/selection_controller.py`, `ui/text_roles.py` (`NOT_RECORDED`, `show_not_recorded`), `ui/component_qss.py`, `ui/theme.py`, `review/features/home/import_view.py` (new) | **Overview as the redraw has it.** The dataset's name is the page title, its path in the number role, Reload quiet at the right. What most needs doing is one `Notice` with one button: `collect.next_step` gives each step a bold lead (*No completeness record.*, *Failures recorded.*, …) and its severity maps to the notice's — amber for what the reader should look at, red for failures, information for the rest — so "Nothing needs attention" is never amber. Its sentence wraps and its button moves under it on a phone by itself, so it is no longer one of the page's side-by-side rows. The cards are the shared `widgets.card.Card`; tile and receipt labels take the label role; a tile or run parameter the record does not hold reads *not recorded* in the quiet ink (`text_roles.NOT_RECORDED`; `run_parameters` gives `None` for a missing segmentation method or background method rather than a dash); a receipt value's warning is a text tone, not a colour sheet. The Health card's re-check is quiet, the notice's button being the page's one. **The band is gone from every Home tab.** The one command it held, Import (groups from a JSON file), is a quiet *Import…* beside Sample Map's layer switch, still hidden on the Variables layer — so it is no longer offered on Cell Gating, Ratios, Pipeline Info or QC Triage, where it imported groups those tabs do not show. The theme's Home-only rules for a title, a card, a card title, the next-step frame, a tile label and an action button are deleted (the last overrode the four button kinds on eight Home buttons, which now draw as their own kinds). Sample Map's active-group value is styled by a theme rule and an `active` property instead of two per-state sheets (the style roster drops `home/groups/tab_view.py` 1 → 0, `selection_controller.py` 2 → 0, `home/controller.py` 5 → 3, the rest the legend's and health dots' state colours). **Evidence.** Renders of all six tabs in both palettes, kept for the pull request. `tests/test_home_overview_page.py` (new, fast lane) builds the real Overview: the title's roles and quiet Reload; the notice's severity, lead, text and button for a dataset with no record, and information for nothing to do (mapping *warn* to information fails it); *not recorded* for a missing parameter (writing an empty string fails it). `tests/test_home_phone_layout.py` counts four side-by-side rows. `tests/test_control_band_adoption.py` drops `home/tab_view.py`, its docstring naming this row. Full-app Home files (`test_home_page`, `test_a_load_reads_cell_gating_once`, `test_load_resets_sample_definitions`, `test_opening_placeholder_app`, `test_selection_refresh`, `test_group_edit_plot_redraw_visibility`) green. **Not done:** the five editor tabs keep their own layouts — §6 redraws only Overview, and turning Sample Map's, Cell Gating's, Ratios' and QC Triage's controls into cards and form grids is a redesign of each that this row did not attempt; Pipeline Info's parameter tree keeps its bold group rows (the text-role roster's three entries) rather than becoming label/value rows, because it shows the whole raw `pipeline_info.json` as a tree; Well Labels' own sheet stays (`ui/` may not import a pane to include its rules) |
| D28 | Dialogs and windows: Manage Remote Servers and those under it, Storage, Reset analysis, Copy analysis, the curve windows, Test detection, the message-box wording | todo | `analyze/remote_dialog.py` and the others in `SURFACES_TRACKER.md` §11.5 | A render of each, before and after |
| D29 | Phone layout: the touch sizes re-derived from the new tokens; every page on all six screens | todo | `core/phone_layout.py`, `ui/theme.py` (`_touch_qss`), `ui/phone_shell.py` | `tests/test_every_page_renders.py` green; shots reviewed |
| D30 | The well colours (§3.4). The 96 values are designed; put them in `domain/well_palette.py` in the order given, keeping the length at 96 for the reason that module's docstring gives. **No migration**: a colour already saved in a dataset's sample definitions is left alone. Find every place a saved colour is *re-baked* from rank and make sure none of them replaces a saved colour with a new-palette one unasked: the module's docstring names one, Datasets' `merge_plates`, which re-bakes when a condition moves to another part of the plate; report what you find there to the user rather than choosing. Update the docstring's account of how the palette was generated | done | `domain/well_palette.py`, `domain/selections_format.py` (`rank_color`, `well_colors`), `domain/plot_groups.py`, `datasets/merge_plates/`, `ui/theme.py` (which re-exports the palette) | The 96 values in `domain/well_palette.py` in the tracker's order, the docstring's account rewritten. `tests/test_well_palette.py` recomputes §3.4's table and matches every figure; on the old palette it fails four of six, naming the 19 colours under 3 : 1 on the grey plot background, and making the load path re-bake a stored colour fails its saved-colour case (driven through `from_block` and `make_selection`, the functions the load path calls — not a full-app load). The user's answers: **Merge Plates keeps re-baking** a remapped condition from the wells it landed on (unchanged; every other path keeps a saved colour); **colour by variable's floors are lowered** to what the palette reaches — `VALUE_PICKS` re-derived by the same greedy method (0, 16, 9, 57, 72, 37, 58, 76, 10, 35, 1, 85, 39, 2, 91, 14), worst-view separation 229/118/87/61/49/25 at 2/3/4/6/8/16 values, trichromat 298/146/65 at 2/3/6, the test floors set just under those. `tests/test_iter_plot_groups_ctx.py`'s pinned rank colours updated to the new palette |
| D34 | The palette is the user's choice, in the application: a Grey / Blue setting, remembered between sessions, honoured at start-up ahead of `DEFAULT_THEME` and behind an explicit `ALLWELL_THEME` or `--theme`. Applies at the next start and says so beside the control; make it live once D12 has removed the cached style sheets | done | `ui/palette_setting.py` (new), `allwell.py` (`main`), `all_well_launcher.py`, `review/core/app_shell_view.py`, `tests/_page_render_case.py` | A **Palette** submenu at the foot of the rail head's dataset menu (Grey, Blue, and a disabled line *Applies the next time All-Well starts*); the choice is stored as `appearance/palette`. One start-up function, `apply_startup_palette`, called by `main` and by the render harness: `--theme`, else `ALLWELL_THEME`, else the stored choice, else a build's baked palette, else `DEFAULT_THEME`. The tracker missed that a baked build palette was injected as `ALLWELL_THEME`, which would have outranked the user's choice in every themed build: it now arrives as `ALLWELL_BUILD_THEME`, ranked below the choice. `tests/test_the_stored_palette_is_the_one_the_window_starts_in.py` (slow) reads the painted window: ignoring the stored setting fails the first case (*stored blue, started painted #141618*), ignoring the variable the second (*ALLWELL_THEME=grey over a stored blue painted #0B0F17*). `tests/test_palette_setting.py` holds the order, the launcher and the menu pressed through `_fill_dataset_menu`. Still open, by the row's own terms: the setting becomes live after D12; and the control moves to the title bar's ⋯ menu when `SURFACES_TRACKER.md` S23 lands — today it is reachable only from Review, not from Analyze or Datasets |
| D35 | Close as the ✕ symbol on the inspector and the phone sheets (the user's decision, after the menu-and-organisation pull request had made it the word *Close*) | done | `widgets/inspector.py` (`CLOSE_NAME`), `ui/phone_shell.py`, `tests/test_one_way_to_close_a_panel.py` | The inspector's header and every phone sheet (Wells, Controls) close by the ✕ symbol at their top right, a drawn icon named *Close* in its tooltip and accessible name, at the touch size on a phone; the guard's rule now reads one way to close a panel, the ✕. A dialog's footer and a window's own foot in phone layout keep the word (D13). `tests/test_inspector.py`, `tests/test_phone_layout.py` and `tests/test_phone_layout_app.py` read the accessible name and an empty label; reverting the inspector to the worded button fails the first |
| D31 | Every roster from D09 is empty or holds only reasoned entries; the per-widget names in the theme's sheet that only restated a role are gone | todo | `tests/`, `ui/theme.py` | The rosters; the count of named widgets in the sheet, from a script, before and after |
| D32 | Reconcile: re-render every page and compare each against §2's rules, not against this table; list anything that does not follow them | todo | this file | The reconciliation, in the pull request body |
| D33 | Docs and help; delete this tracker | todo | `_Docs/Markdowns/`, `ui/help_topics.py` | The docs lane green; the pull request body says the tracker is gone |
