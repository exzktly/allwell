# Variables tab overhaul — remediation tracker

Live state for one body of work, per CLAUDE.md §6.1. Every row is worked to
`done` only with the test that pins it, watched failing without the fix. This
file is deleted by the pull request that closes its last row.

## What this is fixing

A UX review of `well_viewer/features/home/variables/` found the tab built as a
form with a picture beside it, when the job — getting values onto ninety-six
wells and being able to trust the result — is a canvas with a form beside it.
The specific failures below all follow from that inversion.

Four findings were reproduced against the tree before anything was planned, so
that the plan is answering the defect rather than the report:

- The overlay glyph rule really does render the dose series `0, 0.1, 0.3, 1, 3,
  10` as `0, 02, 03, 1, 3, 10`, where `10` is a real dose and `02` is a code for
  `0.1`, with nothing distinguishing the two kinds of two-character glyph. Run,
  not read: `overlay_glyphs` was called on a real six-value numeric variable.
- `_PlateGrid._paint_lit` in `widgets/well_plate_selector.py` takes no
  `selected` argument, and the paint path sets `base = group_color`
  unconditionally when a colour override exists. An assigned well therefore
  renders pixel-identically whether or not the user has it selected.
- The first entry of the selection's rank-cycled trace palette and the first
  entry of `variable_series.value_colors` are both `#6AB2FB`. Selecting an
  unassigned well paints it the exact colour of the variable's first value.
- `home/controller.sync_home_sidebar` shows the rail's sample frame for every
  definition sub-tab, so the Variables tab carries a second, greyed, ninety-six
  well plate coloured by group membership beside its own plate coloured by
  variable value.

## Rows

State is one of `todo`, `doing`, `done`, `declined`. A `declined` row carries
its reason here and at the site.

### Must — the tab is unclear or unusable without these

| # | Item | Place | Pinned by | State |
|---|---|---|---|---|
| M1 | The user's selection must be visible on a well that already carries a value | `widgets/well_plate_selector.py` | `test_plate_selection_rim.py` | done |
| M2 | A persistent selection readout, and `Select: all / none / unassigned / same value` | `variables/plate_view.py` | `test_variables_tab.py::test_the_tab_says_what_is_selected…`, `…::test_select_all_takes_the_dataset_not_ninety_six` | done |
| M3 | A fill honours the plate selection, and the scope line states what it will overwrite | `variables/controller.py` | `test_variables_tab.py::test_a_fill_with_wells_selected_touches_only_those`, `…::test_the_scope_line_follows_the_selection_rather_than_the_button` | done |
| M4 | One-deep undo over every commit | `variables/controller.py` | `test_variables_tab.py::test_undo_puts_back_what_a_fill_replaced` and four siblings | done |
| M5 | `Preview` before `Apply fill`, and a report that also fires when nothing changed | `variables/controller.py` | `test_variables_tab.py::test_preview_paints_the_result_and_stores_nothing`, `…::test_a_fill_that_changed_nothing_says_that…` | done |
| M6 | A header naming the variable the plate is showing and the one overlaid on it | `variables/tab_view.py` | `test_variables_tab.py::test_the_header_names_the_variable_the_plate_is_showing` | done |
| M7 | Coverage (`88/96`) in the rail rather than a value count | `domain/sample_variables.py`, `variables/editor_view.py` | `test_variables_domain_readouts.py`, `test_variables_tab.py::test_the_rail_says_how_much_of_the_plate_each_variable_covers` | done |
| M8 | The glyph rule is all-or-nothing and lives in the domain | `domain/variable_series.py` | `test_variables_domain_readouts.py`, `test_variables_tab.py::test_a_colliding_series_is_drawn_as_a_numbering…` | done |
| M9 | Every silent no-op says what happened, and controls that cannot act are disabled | `variables/controller.py` | `test_variables_tab.py::test_a_duplicate_value_says_it_was_not_added` and four siblings | done |
| M10 | One label, one job — status stops doubling as instruction and as policy | `variables/tab_view.py` | `test_variables_tab.py::test_the_shortfall_is_counted_on_the_control_that_selects_it` | done |
| M11 | The destructive verbs name their scope | `variables/editor_view.py` | `test_variables_tab.py::test_a_chip_is_offered_per_value_plus_a_no_value` | done |
| M12 | The rail's second plate is hidden on the one tab that has its own | `core/feature_registry.py`, `features/home/controller.py` | `test_home_page.py::test_a_subtab_with_its_own_plate_hides_the_rails` | done |

### Should

| # | Item | Place | Pinned by | State |
|---|---|---|---|---|
| S1 | The value rows carry their colour, so the definition and the plate share one vocabulary | `variables/editor_view.py` | `test_variables_tab.py::test_a_value_row_wears_the_ink_its_wells_will_take` | done |
| S2 | A `By group` row selects the wells it is complaining about | `variables/plate_view.py` | `test_variables_tab.py::test_a_group_row_selects_the_wells_it_is_complaining_about` | done |
| S3 | A chip can select every well holding its value | `variables/plate_view.py` | `test_variables_tab.py::test_select_same_value_finds_the_rest…` | declined — done as a `Select: same value` link instead |
| S4 | The plate is sized as the page's primary surface | `widgets/plate_geometry.py` | `test_plate_geometry.py::test_a_canvas_plate_is_larger_than_an_editor_one` | done |
| S5 | First run offers the explanation and the one action, not disabled chrome | `variables/controller.py` | `test_variables_tab.py::test_with_no_variable_there_is_no_plate_to_stare_at` | done |
| S6 | The chip row wraps rather than running off the page | `variables/plate_view.py` | `test_variables_tab.py::test_the_chip_row_wraps_rather_than_running_off_the_page` | done |

### Declined

| # | Item | Reason | State |
|---|---|---|---|
| S3 | A chip alt-click selects every well holding its value | The need is real and is met by the `Select: same value` link, which is discoverable, has a tooltip, and works from the keyboard. A modifier-click on a chip is a second, hidden way to do the same thing, and a chip whose plain click *assigns* and whose alt-click *selects* is two verbs on one control — the failure `Also show` was made a combo to avoid. | declined |
| D2b | Give a `number` variable a sequential colour ramp on the plate instead of the categorical palette | Reported below rather than changed. `variable_series.value_colors` is shared with the plots' *Colour by*, and its `VALUE_PICKS` roster was chosen by measuring separation under four colour-vision simulations. A ramp reads better for an ordered dose on a plate and worse for six values a dichromat has to tell apart in a legend, and that trade is a decision about every figure in the app rather than about this tab. | declined |

## Reconciliation

**Done.** Every row above was re-read against the tree rather than against this
table: each named symbol was grepped for at its stated place, the old
controller-local glyph rule was confirmed gone rather than shadowed, and no
retired widget key (`_var_readout`) survives anywhere in `well_viewer/` or
`tests/`.

Each `done` row's guard was watched failing with its fix removed. That was done
by mutating HEAD rather than with `scripts/prove_guard.sh`, because every symbol
involved is new on this branch and the script correctly reports
`NOT PROVEN (ImportError)` against a base that does not have it. The mutations
run were: the fill dropping the selection (M3), `_commit` not stashing (M4),
`Preview` committing (M5), the plate's `selectionChanged` left unconnected (M2),
the refusal checks removed (M9), the value buttons always enabled (M9), the rail
reverted to a value count (M7), `owns_plate` set to `False` (M12), the rim call
made unreachable (M1), and the old truncating glyph rule substituted back (M8).
Every one was caught by the rows' own guards, with an `AssertionError` naming the
defect rather than an import or attribute error.

Two guards deliberately pass under their mutation and are recorded here so a
future reader does not read that as a gap:
`test_the_value_colour_survives_the_rim` guards against a *wrong* fix that
repaints the fill rather than against the original defect, and
`test_two_cell_lines_keep_their_initials` pins the case the glyph rule change
deliberately leaves alone.

**One row came back after the reconciliation, and it is worth recording how.**
S5 was marked `done` and its guard was watched failing — and the fix was still
half a fix. `_clear_plate` hid the plate, the fill row and the chips by name, and
left the selection links, the Preview / Apply fill buttons, the overlay combo and
the standing policy note live on a tab with nothing to act on. The test asserted
exactly the three widgets the fix named, so it passed: §7.8's second face, an
assertion no stronger than the implementation it was written beside. Neither the
grep-based reconciliation nor the mutation caught it, because both were asking
about the three names. **Rendering the tab and looking at it caught it**, which is
the only check in this whole body of work that could have. The column is now one
registered widget hidden as a unit, and the guard reads a roster with
`isVisibleTo` — `isHidden` answers False for every child of a hidden parent, so
the obvious spelling of that assertion would have measured nothing.

This tracker is deleted by the pull request that carries this line.
