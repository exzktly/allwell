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
| M1 | The user's selection must be visible on a well that already carries a value | `widgets/well_plate_selector.py` | `tests/test_plate_selection_rim.py` | done |
| M2 | A persistent selection readout, and `Select: all / none / unassigned / same value` | `variables/plate_view.py` | | todo |
| M3 | A fill honours the plate selection, and the scope line states what it will overwrite | `variables/controller.py` | | todo |
| M4 | One-deep undo over every commit | `variables/controller.py` | | todo |
| M5 | `Preview` before `Apply fill`, and a report that also fires when nothing changed | `variables/controller.py` | | todo |
| M6 | A header naming the variable the plate is showing and the one overlaid on it | `variables/tab_view.py` | | todo |
| M7 | Coverage (`88/96`) in the rail rather than a value count | `domain/sample_variables.py` | `tests/test_variables_domain_readouts.py` | doing — the fact exists; the rail still shows the value count |
| M8 | The glyph rule is all-or-nothing and lives in the domain | `domain/variable_series.py` | `tests/test_variables_domain_readouts.py` | doing — the rule is moved and fixed; the tab still calls its own copy |
| M9 | Every silent no-op says what happened, and controls that cannot act are disabled | `variables/controller.py` | | todo |
| M10 | One label, one job — status stops doubling as instruction and as policy | `variables/controller.py` | | todo |
| M11 | The destructive verbs name their scope | `variables/tab_view.py` | | todo |
| M12 | The rail's second plate is hidden on the one tab that has its own | `features/registry.py` | | todo |

### Should

| # | Item | Place | Pinned by | State |
|---|---|---|---|---|
| S1 | The value rows carry their colour, so the definition and the plate share one vocabulary | `variables/editor_view.py` | | todo |
| S2 | A `By group` row selects the wells it is complaining about | `variables/plate_view.py` | | todo |
| S3 | A chip can select every well holding its value | `variables/plate_view.py` | | todo |
| S4 | The plate is sized as the page's primary surface | `widgets/plate_geometry.py` | | todo |
| S5 | First run offers the explanation and the one action, not disabled chrome | `variables/tab_view.py` | | todo |
| S6 | The chip row wraps rather than running off the page | `variables/plate_view.py` | | todo |

### Declined

| # | Item | Reason | State |
|---|---|---|---|

## Reconciliation

Not started. Per §6.1 #6 the whole body is closed only after re-reading every
row against the tree rather than against this table.
