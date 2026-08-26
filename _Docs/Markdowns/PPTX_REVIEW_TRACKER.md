# PowerPoint export — adversarial review remediation

A live tracker, not a write-up. It exists because the review that produced it
found fifteen items across four files, and progress reported from memory is
progress reported from the parts of the work that were memorable. Every row
carries a state and a place; the row moves to `done` **in the same commit as its
fix**, and only with the test that pins it, watched failing without the fix.

**Delete this file in the pull request that closes its last row**, and say in the
body that it is gone.

The counts below are derived from the rows, never typed from memory.

## State

| State | Rows |
| --- | --- |
| `todo` | 0 |
| `doing` | 0 |
| `done` | 15 |
| `declined` | 2 |

## Rows

Severity is the review's, kept so the order of work is visible rather than
inferred. "Silent" marks a defect whose output is indistinguishable from correct
output — this repository's cardinal design rule, and the reason those rows are
first.

| # | Sev | State | Defect | Where | Pinned by |
| --- | --- | --- | --- | --- | --- |
| R1 | silent | done | A filled **and** stroked shape clipped at the axes boundary is closed as a polygon and then stroked, drawing an edge Matplotlib never drew. A bar of value 9 on a `ylim` of 5 gets a solid cap exactly at 5 and reads as a bar of value 5. A wrong number on the slide. | `_svg_shapes.clip_subpaths` / `_emit_path` | `test_a_clipped_bar_does_not_gain_an_edge_along_the_axes` |
| R2 | high | done | A control character in a plot title (`\x0b`, from a Word paste) makes the slide part unparseable XML. LibreOffice renders the slide blank; PowerPoint offers to repair the file. The export reports success. | `_drawingml` text emission | `test_a_control_character_in_a_title_cannot_break_the_deck` |
| R3 | silent | done | `DeckResult` counts neither `vector` nor `raster` for a plot that reached the slide with no figure at all, so a deck that dropped a figure reports "all editable". The existing test **asserted the bug** by pinning `(0, 0)`. | `_deck.DeckResult` / `build_saved_plots_deck` | `test_a_deck_that_lost_a_figure_never_reports_itself_complete` |
| R4 | silent | done | Every dash pattern is written as `prstDash val="dash"`. Dashed, dotted and dash-dot series become three identical curves with three identical legend keys. | `_drawingml._line_xml` | `test_each_dash_pattern_survives_as_a_different_dash` |
| R5 | silent | done | A hatched fill (`fill: url(#…)`) parses to "no paint", so hatched bars become empty outlines. Nothing is recorded and nothing is said. | `_svg_shapes._paint_from` | `test_a_hatched_fill_is_reported_rather_than_dropped` |
| R6 | silent | done | Mathtext (`$\mu$m`, subscripts, Greek) is written by Matplotlib as several `<tspan>`s ordered by font rather than by reading order, each with its own positions. `itertext()` concatenated them and threw the positions away, so a µm axis label came out scrambled across two lines. | `_svg_shapes._emit_text` | `test_a_mathtext_label_keeps_its_reading_order` |
| R7 | silent | done | `Drawing.unsupported` reached neither the slide nor the status line, so a figure that lost a construct said nothing. Also collected *after* the shape-cap check, so a capped figure's findings were discarded. | `_deck._place_vector` / `_note`, `controller.export_deck_pptx` | `test_a_construct_the_reader_could_not_translate_is_named_on_the_slide` |
| R8 | high | done | The vector capture is unbounded and synchronous on the UI thread: 300k points is a 4.2 s freeze, a 32 MB file written beside a 0.17 MB PNG, and then discarded at export because it is past the shape cap. The cap was applied after both expensive steps. | `widgets/plot_card.py`, `_deck._place_vector` | `test_a_figure_past_the_cap_is_not_captured_at_all` |
| R9 | latent | done | `parse_svg`'s viewBox mapping is applied to geometry but not to clip rectangles, so a non-identity root matrix clips against a rectangle in the wrong space. Matplotlib never emits such an SVG, but the docstring advertised the opposite. | `_svg_shapes._clip_rect` / `_clip_for` | `test_a_viewbox_scales_the_clip_rectangle_with_the_geometry` |
| R10 | low | done | `docProps/app.xml` emits `<Application>` before `<Slides>`; the schema's sequence is the other way round. | `_pptx.PptxBuilder._app_props` | `test_the_app_properties_follow_the_schema_order` |
| R11 | low | done | `text_box_xml`'s `color` default was a bare hex literal — dead, since every caller passes a theme token, but exactly the shape the colour guard exists to stop and invisible to it because it is a DrawingML string. | `_drawingml.text_box_xml` | `test_hardcoded_tokens::test_no_inline_hex_colours` — the guard itself, which the literal defeated by being a DrawingML string rather than a Qt one; the default is now a required argument so there is nothing to default to |
| R12 | low | done | `Slide.add_image`'s docstring claimed cross-slide deduplication of a shared colour bar; the dedup is per slide, which is what the test actually asserted. | `_pptx.Slide.add_image` | `test_one_image_shared_by_two_slides_is_stored_once_per_slide`, whose name already stated the per-slide scope the docstring contradicted |
| R13 | low | done | Home's strip decoded up to twelve full-resolution PNGs (~1500×960) on every `refresh_home`, on the UI thread, to draw 152 px thumbnails. | `home/tab_view.build_saved_plot_tile` | `test_a_thumbnail_is_decoded_at_thumbnail_size` |
| R14 | test | done | No test drove `clip_subpaths(..., filled=True)` at all — the branch R1 lives in. | `tests/test_saved_plots_pptx_export.py` | covered by R1's test |
| R15 | test | done | Every reader fixture was a clean hand-built figure: no hatch, no mathtext, no non-default dash, no clipped patch. A corpus of clean inputs encodes whatever assumption the bug is made of. | `tests/test_saved_plots_pptx_export.py` | `_messy_svg` fixture, used by R1/R4/R5/R6 |

## Declined

| # | Item | Why |
| --- | --- | --- |
| D1 | Map `DejaVu Sans` to `Arial` so labels do not font-substitute on stock Windows/macOS. | Declined at the site and here: the export's job is to say what the figure says. Substituting the family would make the deck's text a different typeface from the PNG in the PDF export of the same plot, which is a second inconsistency traded for the first. PowerPoint's own substitution is visible to the user and reversible by them; ours would not be. Recorded rather than dropped so the next reader knows it was considered. |
| D2 | `_MIN_EXTENT_EMU = 1` on a degenerate `custGeom` (a vertical spine becomes `cx=1`). | No failing case: LibreOffice draws it correctly and no PowerPoint was available to test against. Left as it is rather than changed on a guess — a change here would be unverifiable in this container either way. |
