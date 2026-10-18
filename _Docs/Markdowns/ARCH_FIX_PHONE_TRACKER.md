# Architecture-audit fixes — phone layout (tracker)

Live state for the phone-layout items of the architecture audit. One row per
item; a row's state changes in the same commit as its fix. This file is deleted
by the commit that closes its last row.

Counts (derived from the rows below): todo 3 · done 3 · declined 0 — of 6.

| ID | Rule | Where | State | Evidence |
|---|---|---|---|---|
| F1 | ARCH §15 "A confirmation that could not be obtained is not consent" | `allwell.py` `_confirm_close_in_phone_layout` | done | `tests/test_phone_layout.py::test_a_close_confirmation_that_could_not_be_asked_keeps_the_window_open` failed on unfixed code with the AssertionError "a close confirmation that raised escaped closeEvent with the event accepted"; real-window twin in `tests/test_phone_layout_app.py` |
| F2 | CLAUDE.md §5 no hardcoded pixel sizes; floors read the named constants | `ui/theme.py` `_touch_qss`, `ui/phone_shell.py` | done | `tests/test_phone_layout.py::test_the_stylesheet_touch_floors_follow_the_named_constants` and `::test_the_text_size_buttons_follow_the_named_touch_sizes` patch the constants and failed on unfixed code (`'min-height: 50px' in 'min-height: 44px'`; `(98, 90) == (125, 103)`); the landscape 40 is `phone_shell.LANDSCAPE_BAR_BUTTON_PT` |
| F3 | CLAUDE.md §5 / §3 copied pixel offsets | `review/features/image_browser/image_table/_phone_fit.py` | done | Measured: the +16 / +8 were not copies of `facet_view` padding but a guessed reserve (the band's QSS is `padding:2px 6px`), 14 px too wide at the desk styling and 30 px too narrow once the band's padding grew. The fit now measures the labels `facet_view` builds (`label_columns_width`) and reads `facet_view.CELL_MARGIN_PX`, which the controller builds the cell host with. `tests/test_phone_layout.py::test_image_table_fitted_columns_fill_the_page_exactly` failed on unfixed code in both cases ("leaves 14 px of the page unused", "scrolls sideways by 30 px") |
| F4 | ARCH §3 Rule 1 placement; ARCH §5 `ctx.window` is not a behaviour dependency | `review/core/phone_drawer.py` | todo | |
| F5 | CLAUDE.md §3 one roster, guarded | `ui/phone_shell.py` `_phone_relevant` vs `PhoneShell.adapt` | todo | |
| F6 | CLAUDE.md §6 docs are a running description, consistent with the code | ARCHITECTURE.md restyle timing, touch-floor source, #1006 history prose | todo | |
