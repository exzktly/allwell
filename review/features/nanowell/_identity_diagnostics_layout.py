"""Where the Identity view's **Diagnostics** puts everything, worked out from the figure's own size.

The figure has no fixed size: the plot card fills a splitter, so it is 8 × 6.4 in by default, narrower with the site
detail open beside it, short and wide in a low window, and a phone's width in phone layout. Fixed figure fractions
cannot hold all of those — at a narrow card the crops' keys landed on their axis labels, and at a short wide one the
pictures shrank to thumbnails with the empty space between them. So every row of text is measured in points with the
font it will be drawn in, broken at the words to the room it has, and stacked; the pictures get what is left, in
the arrangement that draws them largest:

* **top** — the title, then the verdict headline: ``Verdict: <verdict> —`` in the largest type on the figure, its
  reason running on after it a size smaller;
* **the pictures** — the three panels, either in a row or the two crops in a row over the match score, each a square
  image with its title and note above it and its ticks and axis label below it. Each panel's key sits under it while
  every key fits in two rows at the panel's width; otherwise all three keys share one block under the pictures. On
  a figure too small for the pictures at :data:`MIN_SIDE`, the panels' notes (which repeat what the titles and the
  footer say) are left out before the pictures shrink further;
* **the checks** — one entry per remaining check, under the pictures (in two columns when each column is at least
  :data:`MIN_CHECK_COLUMN` wide) or, on a short wide figure, in a column beside them;
* **bottom** — the footer, anchored to the figure's foot.

Room the pictures cannot use (their edge is set by the width) goes between them and the checks, up to
:data:`MAX_EXTRA_GAP`, and the rest above the footer — never between the verdict and the pictures.

**A figure too small for all of it** gives up the reference text before the pictures. When no arrangement reaches
:data:`MIN_SIDE` even without the panels' notes, the footer (the units and the zoom hint) is left out; when that is
still not enough, the per-check lines are folded into one :func:`summary_check` line that counts them by state and
says to enlarge the card to read them. The verdict and the three pictures are always drawn. Without this a phone-sized
card shrank the pictures towards nothing while every text kept its room, and the tick labels, checks and footer were
drawn over one another.

Everything here is arithmetic on points and measured text widths, no artist is made; ``_identity_diagnostics_render``
draws at the places this returns. Positions are in points from the figure's lower left corner.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: The type sizes, in points. The verdict is the largest text on the figure; the per-check detail and the footer the
#: smallest, because they are the reference a reader goes to after the verdict and the pictures.
TITLE_PT = 9.5
HEADLINE_PT = 10.5
REASON_PT = 8.5
HEADLINE_GLYPH_PT = 12.0
PANEL_TITLE_PT = 8.5
NOTE_PT = 7.0
TICK_PT = 6.5
KEY_PT = 7.0
BAR_PT = 6.5
CHECK_PT = 7.5
CHECK_GLYPH_PT = 8.5
FOOTER_PT = 7.0
#: A row's height against its type size.
LEADING = 1.32
#: The figure's margin at each edge, and the gap between two blocks stacked down it, in points.
MARGIN = 10.0
BLOCK_GAP = 9.0
#: The most extra room put between the pictures and the checks when the pictures cannot use it.
MAX_EXTRA_GAP = 18.0
#: The room before a check's sentence (its glyph's column), and the gap between two checks.
GLYPH_COLUMN = 13.0
HEADLINE_GLYPH_COLUMN = 16.0
CHECK_GAP = 2.5
#: A check column narrower than this, in points, reads as a ribbon: the checks then take one column under the
#: pictures. Beside the pictures they take a column between these two widths.
MIN_CHECK_COLUMN = 210.0
MAX_CHECK_COLUMN = 300.0
#: The narrowest gap between two panels, the widest one the slack is spread into (the rest centres the row), and
#: the gap between two rows of panels.
PANEL_GAP = 12.0
MAX_PANEL_GAP = 40.0
ROW_GAP = 8.0
#: The image edge, in points, under which the panels' notes are left out to give the pictures their room.
MIN_SIDE = 72.0
#: Room the measured widths are padded by: tick labels and axis labels take the plot font after the Properties pass,
#: which may set a little wider than the font measured here.
SLACK = 1.12
#: The gap between a panel and its colour bar, the bar's width, the space between two key entries, and the room a
#: key entry's mark takes before its words.
BAR_GAP = 5.0
BAR_WIDTH = 6.0
KEY_GAP = 9.0
KEY_SWATCH = 11.0
#: Below the image: the tick labels with their ticks and pad, and the gap between the axis label and the key.
_TICKS_BELOW = TICK_PT * LEADING + 5.5
_KEY_DROP = 5.0
#: The panel names in drawing order, and the two ways of arranging them.
PANELS = ("looked", "found", "surface")
GRIDS = {"row": [list(PANELS)], "stacked": [list(PANELS[:2]), [PANELS[2]]]}


class Typesetter:
    """Measures text with the fonts it will be drawn in (matplotlib's Agg renderer at 72 dpi, so a pixel is a point)
    and breaks it at the words to a width."""

    def __init__(self) -> None:
        from matplotlib.backends.backend_agg import RendererAgg

        self._renderer = RendererAgg(1, 1, 72)
        self._cache: dict = {}

    def width(self, text: str, size: float, weight: str = "normal") -> float:
        key = (text, size, weight)
        if key not in self._cache:
            from matplotlib.font_manager import FontProperties

            w, _h, _d = self._renderer.get_text_width_height_descent(
                text, FontProperties(size=size, weight=weight), ismath=False)
            self._cache[key] = float(w)
        return self._cache[key]

    def wrap(self, text: str, size: float, width: float, weight: str = "normal", *, first: float | None = None
             ) -> list[str]:
        """*text* broken at single spaces into rows no wider than *width* points — the first row no wider than
        *first*, when given — a word wider than its row alone taking a row of its own; the rows joined by one space
        give *text* back. A first row with no room is ``""``."""
        rows, row, room = [], "", width if first is None else first
        for word in text.split(" "):
            candidate = f"{row} {word}" if row else word
            if self.width(candidate, size, weight) > room and (row or (not rows and first is not None)):
                rows.append(row)
                row, room = word, width
            else:
                row = candidate
        rows.append(row)
        return rows


def rows_height(n: int, size: float) -> float:
    return n * size * LEADING


@dataclass
class KeyEntry:
    """One entry of a panel's key: the mark it names (``swatch``: what ``_identity_diagnostics_render`` draws for it)
    and the words, in the ink."""
    swatch: str
    colour: str
    words: str
    gid: str


@dataclass
class PanelText:
    """What a panel writes round its picture."""
    title: str
    note: str
    xlabel: str
    keys: list = field(default_factory=list)          # [KeyEntry]
    tick_text: str = "−000"                           # the widest y tick label it can draw; "" for none drawn
    bar_label: str = ""                               # the colour bar's label; "" for no colour bar


@dataclass
class PanelPlace:
    """A panel as placed: its square image (``x``, ``y`` its lower left corner, ``side`` its edge), the room over it
    its row gives the title and note, and the rows its note and axis label take at that width."""
    x: float
    y: float
    side: float
    above: float
    title_rows: list
    note_rows: list
    xlabel_rows: list
    bar_label: str = ""


@dataclass
class Headline:
    """The verdict as set: ``lead`` (``Verdict: <verdict> —``) in :data:`HEADLINE_PT`, the reason's rows after it in
    :data:`REASON_PT` — ``reason_rows[0]`` on the lead's row, ``""`` when it starts on the next. ``y`` is the first
    row's baseline."""
    lead: str
    reason_rows: list
    y: float
    lead_width: float

    def baselines(self) -> list[float]:
        """The baseline of each row: the lead's, then each further reason row's."""
        out = [self.y]
        for _ in self.reason_rows[1:]:
            out.append(out[-1] - rows_height(1, REASON_PT))
        return out


@dataclass
class CheckPlace:
    """One check as placed: its glyph at ``x``, its rows from ``x + GLYPH_COLUMN``, the first row's top at ``y``."""
    check: object
    rows: list
    x: float
    y: float


@dataclass
class Layout:
    """Every place :func:`layout` worked out, in points from the figure's lower left corner."""
    width: float
    height: float
    title_y: float
    headline: Headline
    panels: dict                                      # name -> PanelPlace
    keys: list                                        # [(KeyEntry, [rows], x, y top of its first row)]
    checks: list                                      # [CheckPlace]
    footer_rows: list
    footer_y: float                                   # the top of the footer's first row
    arrangement: str                                  # "<row|stacked>, <below|beside>[, shared key][, no notes]"


@dataclass
class _Plan:
    grid: str
    beside: bool
    shared: bool                                      # the keys in one block under the pictures
    notes: bool
    band: tuple                                       # (left, width, top, bottom) the pictures may use
    checks: tuple                                     # (columns, column width, height)
    side: float = 0.0


# ── the panels ───────────────────────────────────────────────────────────────
def _right_decor(ts: Typesetter, text: PanelText) -> float:
    """The width a panel needs right of its image: the colour bar, its tick labels and its label, if it has one."""
    if not text.bar_label:
        return 0.0
    return BAR_GAP + BAR_WIDTH + 3.5 + SLACK * ts.width("0.00", BAR_PT) + 4.0 + BAR_PT * LEADING


def _left_decor(ts: Typesetter, text: PanelText) -> float:
    """The width left of a panel's image its y tick labels take (a little room when it draws none)."""
    if text.tick_text:
        return SLACK * ts.width(text.tick_text, TICK_PT) + 7.5
    return 0.5 * SLACK * ts.width("−000", TICK_PT) + 3.0      # the overhang of its first x tick label


def _flow_keys(ts: Typesetter, keys: list, width: float, left: float, top: float) -> tuple[list, float, int]:
    """*keys* flowed left to right from (*left*, *top*) and onto a new row when the next does not fit, an entry too
    wide for a row of its own broken over rows: ``([(entry, rows, x, y)], height, rows)``."""
    placed, x, y, used, lines = [], left, top, 0.0, 0
    for entry in keys:
        words = ts.wrap(entry.words, KEY_PT, max(width - KEY_SWATCH, 1.0))
        need = KEY_SWATCH + max(ts.width(w, KEY_PT) for w in words)
        if x > left and (x - left) + need > width:
            x, y, lines = left, y - used, lines + round(used / rows_height(1, KEY_PT))
            used = 0.0
        placed.append((entry, words, x, y))
        used = max(used, rows_height(len(words), KEY_PT))
        x += need + KEY_GAP
    lines += round(used / rows_height(1, KEY_PT)) if keys else 0
    return placed, ((top - y) + used if keys else 0.0), lines


def _decor(ts: Typesetter, text: PanelText, side: float, plan: _Plan) -> tuple[float, float, list, list, list]:
    """``(above, below, title rows, note rows, xlabel rows)`` for a panel whose image is *side* points wide: the
    title and the note above it; the tick labels, the axis label and (unless the keys are shared) the key below."""
    width = side + _right_decor(ts, text)
    title = ts.wrap(text.title, PANEL_TITLE_PT, width, "semibold")
    note = ts.wrap(text.note, NOTE_PT, width) if plan.notes else []
    above = rows_height(len(title), PANEL_TITLE_PT) + rows_height(len(note), NOTE_PT) + 3.0
    xlabel = ts.wrap(text.xlabel, TICK_PT, max(side / SLACK, 1.0))
    below = _TICKS_BELOW + rows_height(len(xlabel), TICK_PT) * SLACK
    if not plan.shared and text.keys:
        below += _KEY_DROP + _flow_keys(ts, text.keys, width, 0.0, 0.0)[1]
    return above, below, title, note, xlabel


def _shared_keys(texts: dict) -> list:
    return [entry for name in PANELS for entry in texts[name].keys]


def _keys_fit_under_panels(ts: Typesetter, texts: dict, side: float) -> bool:
    """Whether each panel's key fits in two rows at the panel's width: otherwise the keys share a block."""
    return all(_flow_keys(ts, texts[n].keys, side + _right_decor(ts, texts[n]), 0.0, 0.0)[2] <= 2 for n in PANELS)


def _fit_side(ts: Typesetter, texts: dict, plan: _Plan) -> float:
    """The largest image edge at which *plan*'s panels fit the room it gives them."""
    left, width, top, bottom = plan.band
    rows = GRIDS[plan.grid]
    shared_h = _flow_keys(ts, _shared_keys(texts), width, 0.0, 0.0)[1] + _KEY_DROP if plan.shared else 0.0

    def across(row) -> float:
        fixed = sum(_left_decor(ts, texts[n]) + _right_decor(ts, texts[n]) for n in row)
        return (width - fixed - PANEL_GAP * (len(row) - 1)) / len(row)

    def fits(side: float) -> bool:
        decor = [max(sum(_decor(ts, texts[n], side, plan)[:2]) for n in row) for row in rows]
        return len(rows) * side + sum(decor) + shared_h + ROW_GAP * (len(rows) - 1) <= top - bottom

    # The decoration grows as the edge shrinks (its words break over more rows), so the room needed is not monotonic
    # in the edge: walk down from the widest edge the width allows, coarsely and then finely.
    side = min(across(row) for row in rows)
    for step in (4.0, 0.5):
        while side > 1.0 and not fits(side):
            side -= step
        side = min(side + step, min(across(row) for row in rows)) if step == 4.0 and side > 1.0 else side
    while side > 1.0 and not fits(side):
        side -= 0.5
    return max(side, 0.0)


def _place_panels(ts: Typesetter, texts: dict, plan: _Plan, top: float) -> tuple[dict, list, float]:
    """The panels at *plan*'s image edge, each row's slack spread into its gaps up to :data:`MAX_PANEL_GAP` and the
    row centred in the band — or, with the checks beside the pictures, set from the band's left edge, so the checks
    take the rest — and the keys under each panel or shared under them all; returns the panels' places, the keys'
    places and the lowest point they reach."""
    left, width, _top, _bottom = plan.band
    out, keys, y_top, side = {}, [], top, plan.side
    for row in GRIDS[plan.grid]:
        decor = {n: _decor(ts, texts[n], side, plan) for n in row}
        above = max(d[0] for d in decor.values())
        below = max(d[1] for d in decor.values())
        cells = [_left_decor(ts, texts[n]) + side + _right_decor(ts, texts[n]) for n in row]
        slack = max(0.0, width - sum(cells) - PANEL_GAP * (len(row) - 1))
        gap = PANEL_GAP + (min(slack / (len(row) - 1), MAX_PANEL_GAP - PANEL_GAP) if len(row) > 1 else 0.0)
        x = left + (0.0 if plan.beside else max(0.0, (width - sum(cells) - gap * (len(row) - 1)) / 2))
        y = y_top - above - side
        for n, cell in zip(row, cells):
            image_x = x + _left_decor(ts, texts[n])
            _above, _below, title, note, xlabel = decor[n]
            out[n] = PanelPlace(image_x, y, side, above, title, note, xlabel, texts[n].bar_label)
            if not plan.shared:
                key_top = y - _TICKS_BELOW - rows_height(len(xlabel), TICK_PT) * SLACK - _KEY_DROP
                keys += _flow_keys(ts, texts[n].keys, side + _right_decor(ts, texts[n]), image_x, key_top)[0]
            x += cell + gap
        y_top = y - below - ROW_GAP
    lowest = y_top + ROW_GAP
    if plan.shared:
        first = min(p.x for p in out.values()) - _left_decor(ts, texts["looked"])
        placed, height, _n = _flow_keys(ts, _shared_keys(texts), width - (first - left), first, lowest - _KEY_DROP)
        keys += placed
        lowest -= height + _KEY_DROP
    return out, keys, lowest


# ── the checks ───────────────────────────────────────────────────────────────
def _entry_height(rows: list) -> float:
    return rows_height(len(rows), CHECK_PT) + CHECK_GAP


def _checks_block(ts: Typesetter, checks: list, width: float, most: int) -> tuple[list, float, float]:
    """The checks across *width* in up to *most* columns (two only when each is at least :data:`MIN_CHECK_COLUMN`
    wide), split where the columns' heights are most even: ``(columns, column width, height)``."""
    n = 2 if most > 1 and (width - PANEL_GAP) / 2 >= MIN_CHECK_COLUMN and len(checks) > 2 else 1
    col_w = (width - PANEL_GAP * (n - 1)) / n
    entries = [(c, ts.wrap(c.text, CHECK_PT, col_w - GLYPH_COLUMN)) for c in checks]
    heights = [_entry_height(rows) for _c, rows in entries]
    if n == 1:
        columns = [entries]
    else:
        k = min(range(1, len(entries)), key=lambda i: max(sum(heights[:i]), sum(heights[i:])))
        columns = [entries[:k], entries[k:]]
    height = max(sum(_entry_height(rows) for _c, rows in col) for col in columns) - CHECK_GAP if checks else 0.0
    return columns, col_w, max(height, 0.0)


def _set_checks(columns: list, col_w: float, left: float, top: float) -> list[CheckPlace]:
    out = []
    for k, column in enumerate(columns):
        y = top
        for check, rows in column:
            out.append(CheckPlace(check, rows, left + k * (col_w + PANEL_GAP), y))
            y -= _entry_height(rows)
    return out


# ── choosing the arrangement ─────────────────────────────────────────────────
def _plans(ts: Typesetter, checks: list, *, inner: float, top: float, bottom: float) -> list[_Plan]:
    """Every arrangement the figure allows: the panels in a row or stacked; the checks under them, or beside them
    when the pictures keep at least 1.6 times the checks' column; the keys under each panel or shared; the notes
    kept or left out."""
    out = []
    below = _checks_block(ts, checks, inner, 2)
    beside_w = min(MAX_CHECK_COLUMN, max(MIN_CHECK_COLUMN, 0.34 * inner))
    beside = _checks_block(ts, checks, beside_w, 1)
    for grid in GRIDS:
        for shared in (False, True):
            for notes in (True, False):
                band_bottom = bottom + (below[2] + BLOCK_GAP if checks else 0.0)
                out.append(_Plan(grid, False, shared, notes, (MARGIN, inner, top, band_bottom), below))
                if checks and inner - beside_w - BLOCK_GAP >= 1.6 * beside_w and beside[2] <= top - bottom:
                    out.append(_Plan(grid, True, shared, notes, (MARGIN, inner - beside_w - BLOCK_GAP, top, bottom),
                                     beside))
    return out


def _choose(ts: Typesetter, texts: dict, plans: list[_Plan]) -> _Plan:
    """The plan that draws the pictures largest. Keys go under their panels where each fits in two rows there, and
    the notes are left out only when no plan with them reaches :data:`MIN_SIDE`."""
    for plan in plans:
        plan.side = _fit_side(ts, texts, plan)
    usable = [p for p in plans if p.shared != _keys_fit_under_panels(ts, texts, p.side)] or plans
    with_notes = [p for p in usable if p.notes]
    pool = with_notes if max((p.side for p in with_notes), default=0.0) >= MIN_SIDE else usable
    return max(pool, key=lambda p: (round(p.side, 1), p.notes))


def _headline(ts: Typesetter, text: str, inner: float, y: float) -> Headline:
    """The verdict sentence set as ``lead`` and reason (:class:`Headline`), *y* the top of its first row."""
    lead, sep, reason = text.partition(" — ")
    lead = lead + (" —" if sep else "")
    room = inner - HEADLINE_GLYPH_COLUMN
    lead_rows = ts.wrap(lead, HEADLINE_PT, room, "semibold")
    if len(lead_rows) > 1:                           # a lead too long for one row: the whole sentence as reason
        lead, reason = "", text
    lead_w = ts.width(lead, HEADLINE_PT, "semibold") if lead else 0.0
    first = room - lead_w - (ts.width(" ", REASON_PT) if lead else 0.0)
    reason_rows = ts.wrap(reason, REASON_PT, room, first=first) if reason else [""]
    return Headline(lead, reason_rows, y - HEADLINE_PT, lead_w)


#: The check :func:`summary_check` stands in for the per-check lines with, on a figure too small for them.
SUMMARY_KEY = "summary"
_STATE_WORDS = (("pass", "passed"), ("fail", "failed"), ("warn", "warned"), ("info", "noted"), ("skip", "not run"))


@dataclass
class SummaryCheck:
    """The one line the per-check detail folds into (:data:`SUMMARY_KEY`); drawn as a check of state ``info``."""
    text: str
    key: str = SUMMARY_KEY
    state: str = "info"


def summary_check(checks: list) -> SummaryCheck:
    """*checks* counted by state, in words, and where to read them: ``"Checks: 4 passed, 1 failed, 1 noted — make
    the card larger to read each one."``"""
    counts = [f"{sum(c.state == state for c in checks)} {word}" for state, word in _STATE_WORDS
              if any(c.state == state for c in checks)]
    return SummaryCheck(f"Checks: {', '.join(counts)} — make the card larger to read each one.")


def layout(ts: Typesetter, width: float, height: float, *, title: str, headline: str, texts: dict, checks: list,
           footer: list[str]) -> Layout:
    """Place everything on a *width* × *height* point figure (module docstring). *texts* is ``{panel name:
    PanelText}``, *checks* the checks under the headline, *footer* its lines."""
    inner = width - 2 * MARGIN
    title_y = height - MARGIN
    head = _headline(ts, headline, inner, title_y - rows_height(1, TITLE_PT) - 3.0)
    top = head.baselines()[-1] - REASON_PT * (LEADING - 1.0) - 2.0 - BLOCK_GAP
    # The reference text gives way before the pictures do (module docstring): the full figure, then without the
    # footer, then with the checks folded into one line — the first that lets a picture reach MIN_SIDE, or the last.
    folded = [summary_check(checks)] if checks else []
    for footer_lines_, checks_ in ((footer, checks), ([], checks), ([], folded)):
        footer_rows = [row for line in footer_lines_ for row in ts.wrap(line, FOOTER_PT, inner)]
        footer_y = MARGIN * 0.6 + rows_height(len(footer_rows), FOOTER_PT)
        bottom = footer_y + BLOCK_GAP if footer_rows else MARGIN
        plan = _choose(ts, texts, _plans(ts, checks_, inner=inner, top=top, bottom=bottom))
        if plan.side >= MIN_SIDE:
            break
    checks = checks_
    columns, col_w, checks_h = plan.checks
    panels, keys, lowest = _place_panels(ts, texts, plan, top)
    # Room the pictures cannot use (their edge set by the width) goes between them and the checks, up to
    # MAX_EXTRA_GAP, and the rest above the footer: never between the verdict and the pictures, which read together.
    spare = lowest - plan.band[3] if not plan.beside else 0.0
    extra = min(MAX_EXTRA_GAP, max(0.0, spare) / 2)
    if plan.beside:                                   # the checks take the room right of the pictures
        x = max(p.x + p.side + _right_decor(ts, texts[n]) for n, p in panels.items()) + 2 * BLOCK_GAP
        col_w = min(width - MARGIN - x, 1.6 * MAX_CHECK_COLUMN)
        columns, col_w, _h = _checks_block(ts, checks, col_w, 1)
        placed = _set_checks(columns, col_w, x, top)
    else:
        placed = _set_checks(columns, col_w, MARGIN, lowest - BLOCK_GAP - extra)
    name = ", ".join([plan.grid, "beside" if plan.beside else "below"] + (["shared key"] if plan.shared else [])
                     + ([] if plan.notes else ["no notes"]) + ([] if footer_rows else ["no footer"])
                     + (["checks folded"] if checks is folded and folded else []))
    return Layout(width, height, title_y, head, panels, keys, placed, footer_rows, footer_y, name)
