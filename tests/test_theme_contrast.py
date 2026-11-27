"""Every palette the application offers meets the design language's contrast floors.

Pure arithmetic on the tokens (WCAG 2 relative luminance), for every theme in ``ui.theme.THEMES``: apply it,
read the role tokens, compute each pair. The floors are the design language's
(``_Docs/Markdowns/DESIGN_LANGUAGE_TRACKER.md`` §2.9 and §5.2):

- primary and secondary text at least 4.5 : 1 on the canvas, a card and a control — the WCAG AA floor for
  body text, which a label or a line of help is;
- the disabled ink at least 3.5 : 1 on the same three: it is what a placeholder and an unset value
  (*not set*) are drawn in, and those are information, not decoration;
- the label on an accent fill (the primary button) and the text of a selected item at least 4.5 : 1;
- a destructive button's red label at least 4.5 : 1 on the control fill;
- a field's edge at least 3.0 : 1 on a card and on the canvas — the WCAG floor for the boundary of a control
  a person has to find.

A palette that fails is named with every pair it fails, the measured ratio and the floor.
"""

from __future__ import annotations

import pytest

from ui import theme

SURFACES = ("canvas", "card", "control")

#: (what it is, ink token, ground tokens, floor)
FLOORS = (
    ("primary text", "text_primary", SURFACES, 4.5),
    ("secondary text", "text_secondary", SURFACES, 4.5),
    ("disabled ink (placeholder, not set)", "text_disabled", SURFACES, 3.5),
    ("label on the primary button", "on_accent", ("accent",), 4.5),
    ("selected item's text", "selected_fg", ("selected",), 4.5),
    ("destructive button's label", "danger", ("control",), 4.5),
    ("field edge", "field_edge", ("card", "canvas"), 3.0),
)


def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    assert len(h) == 6, f"{hex_colour!r} is not a #RRGGBB colour"

    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_the_arithmetic_matches_known_wcag_ratios():
    """The measuring stick, checked on pairs with published values before it measures anything."""
    assert contrast("#FFFFFF", "#000000") == pytest.approx(21.0)
    assert contrast("#777777", "#FFFFFF") == pytest.approx(4.48, abs=0.01)
    assert contrast("#767676", "#FFFFFF") == pytest.approx(4.54, abs=0.01)


def failures(name: str) -> list[str]:
    theme.apply_theme(name)
    try:
        c = theme.Colors
        out = []
        for what, ink, grounds, floor in FLOORS:
            for ground in grounds:
                ratio = contrast(getattr(c, ink), getattr(c, ground))
                if ratio < floor:
                    out.append(f"{what} ({ink} {getattr(c, ink)}) on {ground} {getattr(c, ground)}: "
                               f"{ratio:.2f} : 1, floor {floor}")
        return out
    finally:
        theme.apply_theme("blue")


@pytest.mark.parametrize("name", theme.available_themes())
def test_every_palette_meets_the_contrast_floors(name):
    bad = failures(name)
    assert not bad, f"the {name} palette fails:\n  " + "\n  ".join(bad)


def test_a_failing_palette_is_named_with_the_pair_it_fails(monkeypatch):
    """The check can fail: a palette with the white label the grey primary button once carried is caught."""
    monkeypatch.setitem(theme.THEMES, "probe", {"Colors": {**theme.THEMES["grey"]["Colors"],
                                                           "on_accent": "#FFFFFF"}})
    bad = failures("probe")
    assert bad == ["label on the primary button (on_accent #FFFFFF) on accent #2D8CEB: 3.46 : 1, floor 4.5"]
