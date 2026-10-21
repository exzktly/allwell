# Phone layout: Datasets and Analyze forms — tracker

Live state for one body of work (CLAUDE.md §6.1). Delete this file in the PR that
closes its last row.

Reported: in phone layout the Analyze and Datasets tools are "basically
unusable". Audited in the real window on a 432×936 phone-shaped screen: seven of
the nine Datasets tools are wider than the phone (controls up to 479 px past the
right edge, description paragraphs cut), the tool list is reachable only from ☰,
and Analyze opens on an idle dashboard with its whole form behind ☰ and its Run
button at the foot of a ~3,500 px column.

Counts are derived from the rows below.

| # | Item | State | Where | Evidence |
|---|------|-------|-------|----------|
| 1 | Every Datasets tool page fits the phone's width: no sideways scroll, no control past the edge | done | `ui/phone_fit.py`, `ui/phone_shell.py` (`_FitOnShow`, `fit_roots`) | Real window, 432 px screen: 0 of 9 tools overflow (was 7). `test_phone_fit.py::test_every_datasets_tool_fits_a_phone` fails with fitting disabled (8 tools too wide), without row stacking (6), without label breaks (2); the own-form step is pinned by `test_a_widget_with_its_own_phone_form_takes_it`; the shell watcher by `test_phone_datasets_shell.py` (each watched failing under a mutation) |
| 2 | Datasets has a "Tool ▾" picker on its page, not only in ☰ | done | `ui/phone_shell.py` (`_fill_tool_menu`), `datasets/tab.py`, `allwell.py` | Real window: the button reads the tool on screen and follows picks from it and from ☰. `test_phone_datasets_shell.py::test_the_page_button_lists_the_tools_in_datasets_mode` fails when Datasets mode does not use the tool menu |
| 3 | Analyze opens on its form in phone layout, with run progress as a compact strip | open | `ui/phone_shell.py`, `analyze/` | — |
| 4 | Analyze's Run / + Queue stay in reach at the bottom of the screen | open | `analyze/`, `ui/phone_shell.py` | — |

Items 1–2 ship in the PR that carries this file's first commit; 3–4 in the next.
