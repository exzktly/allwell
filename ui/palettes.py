"""The palettes the application offers, as data: the role each colour plays and its value in each palette.

``ui/theme.py`` holds the token classes (``Colors`` and friends), whose values are the *blue* palette, and the
machinery that applies a palette onto them. This module holds what varies between palettes and nothing else,
so a palette can be read, compared and measured without the stylesheet builder beside it.
``tests/test_theme_contrast.py`` measures every palette registered here against the floors the design
language sets.

**Roles.** Every colour the interface chrome draws is one of a small set of roles
(``_Docs/Markdowns/DESIGN_LANGUAGE_TRACKER.md`` §2.9):

- three surfaces — ``canvas`` (the page), ``card`` (a card on it) and ``control`` (a button's fill);
- three inks — ``text_primary``, ``text_secondary`` (labels, help, anything quieter) and ``text_disabled``
  (a disabled control's label, a placeholder, a value that reads *not set*);
- three edges — ``hairline`` (a card's outline and dividers inside it), ``button_edge`` and ``field_edge``
  (a field's outline, at least 3 : 1 on a card so a field can be found without reading its label);
- ``accent`` (actionable or selected) with ``on_accent``, the label drawn on an accent fill;
- ``selected`` and ``selected_fg``, the fill and the text of a selected item.

There is one quieter ink, ``text_secondary``. The two quieter still that the palettes once carried measured
2.5 : 1 and below on a card in the grey palette, which is not readable; their readers now take
``text_secondary``, or ``text_disabled`` where what they drew was a disabled control.
"""

from __future__ import annotations

#: Grey — the palette the application starts in (``ui.theme.DEFAULT_THEME``). Neutral charcoal chrome with a
#: restrained azure accent and a near-black plot canvas; designed against Nikon NIS-Elements AR screenshots,
#: then measured against the design language's contrast floors. Overrides colours only: radii and padding are
#: shared by every palette.
GREY: dict = {
    "Colors": {
        "titlebar": "#191B1E", "rail": "#1F2226", "frame": "#0B0D0F",
        # The three surfaces.
        "canvas": "#141618", "card": "#24272C", "control": "#2D3137",
        "hover": "#373C43", "active": "#3E444D",
        # Structural dividers between regions (the top bar's rule, the splitter handle) read
        # ``border_subtle``, a touch darker than the surfaces so a seam is a dark groove rather than a line.
        "border_subtle": "#101214", "border_strong": "#50565E",
        # The three edges: 1.5, 2.6 and 3.7 : 1 on a card.
        "hairline": "#3F444B", "button_edge": "#60666E", "field_edge": "#787E86",
        # The three inks: 12.7, 6.2 and 4.0 : 1 on a card.
        "text_primary": "#EAECEE", "text_secondary": "#A1A7AF", "text_disabled": "#7F858D",
        "accent": "#2D8CEB", "accent_hover": "#43A0FF", "accent_pressed": "#1668B8",
        # A dark label on the accent fill: 5.6 : 1, where white measured 3.5.
        "on_accent": "#0B0D10",
        "selected": "#22415E", "selected_fg": "#D6E6F8",
        # ``accent2`` has to move with ``accent``. It is the *neighbouring* hue — the second series colour, the
        # dial's other half — and the relationship, not the absolute value, is what makes the pair read as one
        # family. Derived by carrying the blue palette's own hue offset (+38°) and saturation/value ratios onto
        # this accent; left at blue's, the gap went from 38° to 55° and the two stopped looking related.
        "accent2": "#6D5BE5",
        # ``danger`` is also a destructive button's label, on the control fill: 4.7 : 1 (the earlier
        # ``#F04747`` measured 3.5).
        "success": "#3FB950", "warn": "#FFB300", "danger": "#FF6B6B",
        "plot_bg": "#0A0C10", "plot_grid": "#1D2128", "plot_spine": "#50565E",
        "focus_ring": "rgba(45, 140, 235, 0.35)",
    },
}
