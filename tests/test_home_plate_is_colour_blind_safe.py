"""Home's plate stays readable under colour-vision deficiency, as Analyze's does.

The report: "Home tab well color scheme is not color blind friendly like the
well colors in analyze pipeline." It was not. Home painted complete, partial and
failed in ``success``, ``warn`` and ``danger`` — green, amber and red, the three
hues red/green deficiency folds onto one olive-to-yellow axis. Measured on the
Machado et al. (2009) simulation with CIEDE2000, in the default grey theme a
complete well and a failed one were 4.4 apart under deuteranopia; in the blue
theme a complete well and the pale unfinished share of a partial dial were 2.8
apart under protanopia. Either is one colour to that reader.

The run plate's guard (``test_run_plate_is_colour_blind_safe.py``) is the
standard, and this file holds Home to the same bars by the same arithmetic
(``tests/_ciede.py``): every *appearance* a Home well can take — each painted
state's solid colour, and the pale "remaining" share of a partial well's
coverage dial — at CIEDE2000 ≥ 13 from every appearance of every other state
under normal vision and under deuteranopia and protanopia at full and partial
severity, ≥ 9 under tritanopia (rarer, and the blue/yellow axis it loses is the
one a red/green-safe palette leans on). Measured in **every registered theme**,
because two of the six tokens are theme tokens: a theme that re-tunes
``accent`` or ``stale`` is a theme that can bring two states back together.

The uncoloured cell (``unchecked`` / ``no_output``) and the colours as *drawn*
— after the sphere shading, which blends toward white and so compresses every
pair toward each other — are measured on rendered pixels in
``test_home_plate_render.py``.
"""
from __future__ import annotations

import itertools

import pytest

from review.features.home import collect as hc
from tests._ciede import RED_GREEN, VISIONS, contrast, de2000, lab, seen
from ui import theme

pytestmark = pytest.mark.qt

#: The run plate's bars, so "as readable as Analyze" is a measured claim.
MIN_APART = {"normal": 13.0, **{v: 13.0 for v in RED_GREEN}, "tritanopia": 9.0}
#: A partial dial's covered and uncovered shares must read as two.
MIN_DIAL = {"normal": 12.0, **{v: 12.0 for v in RED_GREEN}, "tritanopia": 11.0}
#: WCAG 2.1 SC 1.4.11's floor for a graphical object against its background.
MIN_CONTRAST = 3.0

#: The one state drawn as a dial (``collect.plate_fractions`` only gives
#: part-covered wells a fraction), so the one with a "remaining" appearance.
DIAL_STATES = (hc.CELL_PARTIAL,)


@pytest.fixture(scope="module")
def qapp():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _restore_theme(qapp):
    """``apply_theme`` is global state; put the baseline back afterwards."""
    yield
    theme.apply_theme("blue")


def _remaining(hex_colour: str) -> str:
    """The plate's own "remaining" tint — read from the painter, not re-derived."""
    from PySide6.QtGui import QColor

    from widgets.well_plate_selector import _PlateGrid

    return _PlateGrid._remaining_tone(QColor(hex_colour)).name()


def _appearances(palette: dict[str, str]) -> dict[str, str]:
    out = dict(palette)
    for state in DIAL_STATES:
        out[f"{state} (remaining)"] = _remaining(palette[state])
    return out


def _state(appearance: str) -> str:
    return appearance.split(" (")[0]


def _too_close(palette: dict[str, str], vision: str) -> list[tuple[str, str, float]]:
    looks = _appearances(palette)
    out = []
    for a, b in itertools.combinations(sorted(looks), 2):
        if _state(a) == _state(b):
            continue
        d = de2000(lab(seen(looks[a], vision)), lab(seen(looks[b], vision)))
        if d < MIN_APART[vision]:
            out.append((a, b, round(d, 1)))
    return out


def _palette() -> dict[str, str]:
    from review.features.home.plate_panel import state_colors

    return state_colors()


@pytest.mark.parametrize("theme_name", theme.available_themes())
@pytest.mark.parametrize("vision", VISIONS)
def test_every_appearance_of_every_state_stays_apart(theme_name, vision):
    theme.apply_theme(theme_name)
    close = _too_close(_palette(), vision)
    assert not close, (
        f"{theme_name} theme, {vision}: states Home's plate cannot keep apart "
        f"(CIEDE2000 < {MIN_APART[vision]}): {close}")


@pytest.mark.parametrize("theme_name", theme.available_themes())
@pytest.mark.parametrize("vision", VISIONS)
def test_a_partial_dial_shows_its_two_shares(theme_name, vision):
    theme.apply_theme(theme_name)
    palette = _palette()
    weak = {}
    for state in DIAL_STATES:
        d = de2000(lab(seen(palette[state], vision)), lab(seen(_remaining(palette[state]), vision)))
        if d < MIN_DIAL[vision]:
            weak[state] = round(d, 1)
    assert not weak, f"{theme_name} theme, {vision}: dials whose covered and uncovered shares merge: {weak}"


@pytest.mark.parametrize("theme_name", theme.available_themes())
def test_every_fill_reads_against_the_panel(theme_name):
    theme.apply_theme(theme_name)
    panel = theme.Colors.panel
    weak = {s: round(contrast(h, panel), 2) for s, h in _palette().items() if contrast(h, panel) < MIN_CONTRAST}
    assert not weak, f"under {MIN_CONTRAST}:1 against the {theme_name} theme's panel: {weak}"


#: What Home painted before, per theme — the ``success`` / ``warn`` / ``danger``
#: / ``accent`` / ``stale`` / ``text_muted`` values each theme carried.
_REPLACED = {
    "grey": {hc.CELL_COMPLETE: "#3FB950", hc.CELL_PARTIAL: "#FFB300", hc.CELL_FAILED: "#F04747",
             hc.CELL_NO_MEASUREMENTS: "#2D8CEB", hc.CELL_CSV_ONLY: "#22D3EE", hc.CELL_INPUT_ONLY: "#878D95"},
    "blue": {hc.CELL_COMPLETE: "#4ADE80", hc.CELL_PARTIAL: "#F59E0B", hc.CELL_FAILED: "#F87171",
             hc.CELL_NO_MEASUREMENTS: "#6B8AFD", hc.CELL_CSV_ONLY: "#22D3EE", hc.CELL_INPUT_ONLY: "#5F6B7C"},
}


def test_the_palette_this_replaced_is_what_this_guard_rejects():
    """The bar is not one any palette clears. The reported palette fails it on
    exactly the pairs red/green deficiency merges: complete against failed in
    the default theme, complete against a barely-started partial well in blue."""
    grey = {(a, b): d for a, b, d in _too_close(_REPLACED["grey"], "deuteranopia")}
    assert grey[(hc.CELL_COMPLETE, hc.CELL_FAILED)] < 5, grey
    blue = {(a, b): d for a, b, d in _too_close(_REPLACED["blue"], "protanopia")}
    assert blue[(hc.CELL_COMPLETE, f"{hc.CELL_PARTIAL} (remaining)")] < 3, blue


# ── what reaches the widget ─────────────────────────────────────────────────

#: Stated here independently of ``plate_panel.STATE_TOKENS`` — the assertion
#: below is that the plate *receives* these, so restating the mapping by
#: reading the module under test would let it agree with itself.
_EXPECTED_TOKEN = {
    hc.CELL_COMPLETE: "run_done",       # Analyze's "downloaded / thresholded"
    hc.CELL_PARTIAL: "home_partial",
    hc.CELL_FAILED: "run_problem",      # Analyze's problem fill
    hc.CELL_NO_MEASUREMENTS: "accent",
    hc.CELL_CSV_ONLY: "stale",
    hc.CELL_INPUT_ONLY: "run_pending",  # Analyze's "grouped, queued for processing"
}


def _record_well(health, *, intent=8, covered=8, failed=()):
    return {"intent": intent, "covered_any": covered, "covered_csv": covered,
            "missing_count": intent - covered, "missing": [], "csv_state": "ok",
            "has_in": True, "has_out_zip": covered > 0, "has_out_folder": False,
            "failed": list(failed), "health": health}


#: One well per painted state, in the record shape ``plate_cells`` classifies —
#: so the state each well lands in is decided by the real classifier.
_RECORD = {
    "A01": _record_well("complete"),
    "A02": _record_well("partial", covered=4),
    "A03": _record_well("partial", covered=6, failed=["f1"]),
    "A04": _record_well("missing", covered=0),
    "A05": _record_well("csv_only"),
    "A06": _record_well("partial", covered=0),
    "A07": _record_well("no_output", covered=0),
}


@pytest.mark.parametrize("theme_name", theme.available_themes())
def test_home_s_repaint_pushes_each_state_s_token_to_the_plate(theme_name, qapp, monkeypatch):
    """At the boundary (CLAUDE.md §7.4 #1): drive Home's own repaint
    (``controller._paint_plate``) against the real plate ``build_home_plate``
    makes, and record what reached ``setWellState``. A plate that computed the
    right colour and pushed another — or a palette captured at import under a
    different theme — fails by value here."""
    from types import SimpleNamespace

    from PySide6.QtWidgets import QGridLayout, QLabel, QWidget

    from review.features.home import controller as hcon
    from review.features.home.plate_panel import build_home_plate

    theme.apply_theme(theme_name)
    plate = build_home_plate(None)
    pushed: dict[str, str] = {}
    real = plate.setWellState
    monkeypatch.setattr(plate, "setWellState",
                        lambda w, s: (pushed.__setitem__(w, s), real(w, s)))
    legend = QWidget()
    QGridLayout(legend)
    widgets = {"_home_plate": plate, "_home_legend": legend, "_home_plate_summary": QLabel()}
    monkeypatch.setattr(hcon, "_widget", lambda _c, name: widgets.get(name))
    # Field quality is a separate channel (the "!" marker) with its own tests;
    # scanning it needs a loaded dataset this fixture does not carry.
    monkeypatch.setattr(hcon, "wells_needing_review", lambda _c: {})
    ctx = SimpleNamespace(state=SimpleNamespace(
        dataset=SimpleNamespace(pipeline_info={"run_check": {"wells": _RECORD}},
                                well_paths=dict.fromkeys(_RECORD), cache={}, summaries={},
                                fov_integrity={}),
        qc=SimpleNamespace(params=None)))

    hcon._paint_plate(ctx)

    cells = hc.plate_cells(ctx)
    assert set(cells.values()) == {*_EXPECTED_TOKEN, hc.CELL_NO_OUTPUT}, "premise: one well per state"
    for well, state in cells.items():
        want = ("neutral" if state == hc.CELL_NO_OUTPUT
                else f"color:{getattr(theme.Colors, _EXPECTED_TOKEN[state])}")
        assert pushed.get(well) == want, f"{well} ({state}) reached the plate as {pushed.get(well)!r}, not {want!r}"
    # And the legend swatch shows the same fill the well got.
    from review.features.home.tab_view import legend_swatch_style
    for state, token in _EXPECTED_TOKEN.items():
        assert f"background:{getattr(theme.Colors, token)};" in legend_swatch_style(state)
    plate.deleteLater()
    legend.deleteLater()
