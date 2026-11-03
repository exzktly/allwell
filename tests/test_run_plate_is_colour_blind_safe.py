"""The run plate stays readable under red/green colour vision deficiency.

Analyze's run plate paints every well's state at once. Under red/green colour
vision deficiency — the commonest kind — red and green collapse onto one axis,
and what remains to tell colours apart is lightness and blue-against-yellow.
Three reports shaped what is held here:

* "complete" green and "failed" red read as one olive;
* a light green "complete" and an amber "processing" at the same lightness were
  hard to tell apart — CIEDE2000 ≈ 4 between a complete well and a
  just-started processing dial, whose unfinished share is the state colour
  blended toward white;
* a red fill for "failed" was set apart from its neighbours by nothing but its
  ✕.

So every *appearance* a well can take is held apart — each state's solid
colour, and the pale "remaining" tint of each state drawn as a dial — measured
by CIEDE2000 under normal vision and under deuteranopia and protanopia at full
and partial severity (Machado et al. 2009), and more loosely under
tritanopia. Problems are not a colour: a failed well and a partial one share
the white ``run_problem`` fill and say which problem with their glyph, and the
failed ✕ carries its own red ink, which must read on that white under every
vision. The tests then hold that the plate and its legend paint with exactly
this palette, by value, and put the ✕'s ink on the well.
"""
from __future__ import annotations

import itertools

import pytest

from tests._ciede import RED_GREEN
from tests._ciede import contrast as _contrast
from tests._ciede import de2000 as _de2000
from tests._ciede import lab as _lab
from tests._ciede import seen as _seen
from ui import theme

#: The fills the plate paints: one per state, problems sharing one.
FILLS = ("run_pending", "run_uploaded", "run_processing", "run_on_server", "run_done", "run_problem")
#: The states a well shows as a progress dial, so also as their pale "remaining" tint.
DIAL_STATES = ("run_uploaded", "run_processing", "run_done")
#: The least CIEDE2000 between any two appearances of different states. The
#: palette before this one reached ~4 (complete against a just-started
#: processing dial); this one's worst is 13.5. Tritanopia is far rarer and its
#: blue/yellow axis is the one the palette leans on, so its bar is lower.
_MIN_APART = {"normal": 13.0, **{v: 13.0 for v in RED_GREEN}, "tritanopia": 9.0}
#: A dial's two shares — done and remaining — must stay readable as two.
_MIN_DIAL = {"normal": 12.0, **{v: 12.0 for v in RED_GREEN}, "tritanopia": 11.0}
#: WCAG 2.1 SC 1.4.11's floor for graphical objects.
_MIN_CONTRAST = 3.0
_PANELS = {"blue": "#131A24", "grey": "#24272C"}


def _remaining(hex_colour: str) -> str:
    """The plate's own "remaining" tint of a dial in *hex_colour* — read from the painter, not re-derived."""
    from PySide6.QtGui import QColor

    from widgets.well_plate_selector import _PlateGrid

    return _PlateGrid._remaining_tone(QColor(hex_colour)).name()


def _appearances(palette: dict[str, str]) -> dict[str, str]:
    out = dict(palette)
    for name in DIAL_STATES:
        out[f"{name} (just started)"] = _remaining(palette[name])
    return out


def _state(appearance: str) -> str:
    return appearance.split(" (")[0]


def _closest(palette: dict[str, str], vision: str) -> tuple[float, str, str]:
    looks = _appearances(palette)
    return min(
        (_de2000(_lab(_seen(looks[a], vision)), _lab(_seen(looks[b], vision))), a, b)
        for a, b in itertools.combinations(looks, 2) if _state(a) != _state(b)
    )


def _palette() -> dict[str, str]:
    return {name: getattr(theme.Colors, name) for name in FILLS}


@pytest.mark.parametrize("vision", sorted(_MIN_APART))
def test_every_appearance_of_every_state_stays_apart(vision):
    d, a, b = _closest(_palette(), vision)
    assert d >= _MIN_APART[vision], (
        f"under {vision}, {a} and {b} are only CIEDE2000 {d:.1f} apart; the run plate needs ≥ {_MIN_APART[vision]}"
    )


@pytest.mark.parametrize("vision", sorted(_MIN_DIAL))
def test_each_dial_shows_its_two_shares(vision):
    palette = _palette()
    weak = {}
    for name in DIAL_STATES:
        d = _de2000(_lab(_seen(palette[name], vision)), _lab(_seen(_remaining(palette[name]), vision)))
        if d < _MIN_DIAL[vision]:
            weak[name] = round(d, 1)
    assert not weak, f"under {vision}, dials whose done and remaining shares merge: {weak}"


@pytest.mark.parametrize("panel", sorted(_PANELS))
def test_every_fill_reads_against_the_panel(panel):
    weak = {name: round(_contrast(h, _PANELS[panel]), 2) for name, h in _palette().items()
            if _contrast(h, _PANELS[panel]) < _MIN_CONTRAST}
    assert not weak, f"under {_MIN_CONTRAST}:1 against the {panel} theme's panel: {weak}"


@pytest.mark.parametrize("vision", ("normal", *RED_GREEN, "tritanopia"))
def test_the_failed_mark_reads_on_the_problem_fill(vision):
    """The ✕ is the only thing telling a failed well from a partial one, so its ink must stand off the white under
    every vision — measured on the simulated colours, not only on the screen's."""
    def shown(h: str) -> str:
        r, g, b = (round(v * 255) for v in _seen(h, vision))
        return f"#{r:02X}{g:02X}{b:02X}"

    ratio = _contrast(shown(theme.Colors.run_failed_mark), shown(theme.Colors.run_problem))
    assert ratio >= _MIN_CONTRAST, f"the failed ✕ is only {ratio:.2f}:1 on the problem fill under {vision}"


def test_the_palettes_this_replaced_are_what_this_guard_rejects():
    """The bar is not one any palette clears. The light-green / amber palette fails it on the reported pair —
    a complete well against a just-started processing dial — and the emerald / light-yellow one that followed it
    fails it on a just-started complete dial against the white a problem well is now."""
    first = {"run_pending": "#5F6B7C", "run_uploaded": "#C4B5FD", "run_processing": "#F5B700",
             "run_on_server": "#3B6FE8", "run_done": "#4ADE80", "run_problem": "#F5F5F5"}
    d, a, b = _closest(first, "protanopia")
    assert {a, b} == {"run_done", "run_processing (just started)"} and d < 5, (a, b, d)
    second = dict(first, run_processing="#FDE047", run_done="#10B981", run_pending="#6B7280")
    d, a, b = _closest(second, "deuteranopia")
    assert d < _MIN_APART["deuteranopia"], (a, b, d)


@pytest.mark.parametrize("remote", [False, True])
def test_the_legend_paints_with_the_run_palette_and_shows_the_problem_glyphs(qapp, remote):
    from widgets.run_dashboard import RunDashboard

    dash = RunDashboard()
    dash._remote = remote
    _title, items = dash._legend_items()
    expected = {getattr(theme.Colors, n) for n in FILLS}
    if not remote:
        expected.discard(theme.Colors.run_uploaded)
    assert {item[0] for item in items} == expected
    problems = {item[1]: item[2:] for item in items if item[0] == theme.Colors.run_problem}
    assert problems == {"partial / unfinished": ("!", None), "failed": ("✕", theme.Colors.run_failed_mark)}


def test_every_well_state_paints_with_the_run_palette(qapp):
    """Drive the plate through each state and collect what ``_well_color`` returns; every colour reached must be a
    ``run_*`` fill, and every fill must be reached, so a branch reverting to a general status colour fails by
    value. Failed and partial both reach the problem fill."""
    from widgets.run_dashboard import RunDashboard

    palette = {getattr(theme.Colors, n): n for n in FILLS}
    reached: dict[str, str] = {}

    dash = RunDashboard()
    dash.reset(remote=True, total=6)
    steps = {
        "A01": ["group"],                  # pending
        "A02": ["upload"],                 # uploaded, waiting its turn
        "A03": ["upload", "active"],       # processing
        "A04": ["process"],                # on the server
        "A05": ["process", "download"],    # downloaded
        "A06": ["process", "error"],       # failed
    }
    for w, stages in steps.items():
        for stage in stages:
            dash.set_well_stage(w, stage)
        reached[w] = dash._well_color(w)

    local = RunDashboard()
    local.reset(remote=False, total=2)
    local.set_well_stage("B01", "threshold")              # complete
    local.set_well_partial("B02", "2 FOV-groups failed")
    local.set_well_stage("B02", "process")                # partial
    for w in ("B01", "B02"):
        reached[w] = local._well_color(w)

    stray = {c for c in reached.values() if c not in palette}
    assert not stray, f"well colours outside the run palette: {sorted(stray)}"
    assert set(palette.values()) == {palette[c] for c in reached.values()}
    assert reached["A06"] == reached["B02"] == theme.Colors.run_problem


def test_a_failed_well_s_cross_reaches_the_plate_in_its_red(qapp, monkeypatch):
    """At the boundary: the ✕ is pushed to the plate with the failed mark's ink, and the "!" with none (it takes the
    ink the painter picks for white). A dashboard that set the glyph and dropped its ink would draw a dark ✕ that no
    longer says "failed" in any colour."""
    from widgets.run_dashboard import RunDashboard

    dash = RunDashboard()
    dash.reset(remote=False, total=2)
    pushed = []
    monkeypatch.setattr(dash._plate, "setWellMarker", lambda w, g, ink=None: pushed.append((w, g, ink)))
    dash.set_well_stage("A01", "process")
    dash.set_well_stage("A01", "error")
    dash.set_well_partial("A02", "2 FOV-groups failed")
    dash.set_well_stage("A02", "process")
    last = {w: (g, ink) for w, g, ink in pushed}
    assert last["A01"] == ("✕", theme.Colors.run_failed_mark)
    assert last["A02"] == ("!", None)
