# Review fix tracker: phone layout

Live state for the phone-layout findings of the adversarial review of PRs
#992–#1006 (the phone-layout report, plus L2 of the #1003–#1004 report). One row
per finding. A row moves to `done` only with a regression test that was watched
failing on the unfixed code. This file is deleted by the commit that closes its
last row; the pull request body records the outcome.

| ID | Defect | State | Test |
|---|---|---|---|
| M12 | A fixed size or explicit font set while a UI scale is in force has no recorded desk value, so it is kept as a literal when leaving phone layout: a window launched in phone layout keeps phone sizes after switching back (run log 31 px vs 13, empty-state glyphs 111 vs 48, plate caps 781×497 vs 330×210). | open | — |
| M13 | `set_text_size` and `_on_geometry_changed` restyle without first giving touch-mode steppers and stacked form rows their desk form, so the rescale pass sees a touch-mode Stepper and its pinned width comes back 227 instead of 96. | open | — |
| L16 | The sweep decides from the widget count alone, so a widget replaced in place between two ticks is never watched. | open | — |
| L17 | `rescale_widgets` delivers every posted event (`singleShot(0)` calls, queued signals), while its docstring and ARCHITECTURE say no timers run inside the restyle; the test uses an interval-0 QTimer object that never fires there anyway; `_phone_fit._refit`'s `after_switch` branch is unreachable. | open | — |
| L18 | The touch-floor `min-height` rules reset code-set minimum heights to 0 after a round trip (96 Home › Labels cells, 26 → 0). | open | — |
| L19 | A page change does not sweep, although ARCHITECTURE and a comment in `phone_shell.py` say it does. | open | — |
| L20 | A rail section's glyph is measured off the unpolished font at construction and off the polished one after a rescale, so it does not round-trip (15 → 16 px). | open | — |
