# Architecture-audit fixes — phone layout (tracker)

Live state for the phone-layout items of the architecture audit. One row per
item; a row's state changes in the same commit as its fix. This file is deleted
by the commit that closes its last row.

Counts (derived from the rows below): todo 6 · done 0 · declined 0 — of 6.

| ID | Rule | Where | State | Evidence |
|---|---|---|---|---|
| F1 | ARCH §15 "A confirmation that could not be obtained is not consent" | `allwell.py` `_confirm_close_in_phone_layout` | todo | |
| F2 | CLAUDE.md §5 no hardcoded pixel sizes; floors read the named constants | `ui/theme.py` `_touch_qss`, `ui/phone_shell.py` | todo | |
| F3 | CLAUDE.md §5 / §3 copied pixel offsets | `review/features/image_browser/image_table/_phone_fit.py` | todo | |
| F4 | ARCH §3 Rule 1 placement; ARCH §5 `ctx.window` is not a behaviour dependency | `review/core/phone_drawer.py` | todo | |
| F5 | CLAUDE.md §3 one roster, guarded | `ui/phone_shell.py` `_phone_relevant` vs `PhoneShell.adapt` | todo | |
| F6 | CLAUDE.md §6 docs are a running description, consistent with the code | ARCHITECTURE.md restyle timing, touch-floor source, #1006 history prose | todo | |
