"""The Heat Map's Layout panel in phone layout: it never opens by itself.

At the desk the Layout panel (``layout_view``) opens with the page while the
grid is still empty, because an empty grid is the one state in which the page
has nothing to draw and the panel is the only way forward. Its home is the
plot's right dock, beside the figure.

In phone layout that dock is a full-width sheet over the page
(``review/core/phone_panel_sheet.py``). Opened by itself it would cover the
page the user had just navigated to — and the placeholder under it that says
what the page needs — before they had seen either. The interaction differs, not
just the size, so the panel waits for its **Layout** toggle in the ⚙ Controls
sheet there, which is where every other panel of the page is opened from.
"""

from __future__ import annotations

from core import phone_layout


def layout_opens_with_page() -> bool:
    """Whether the Layout panel may open itself with the page (at the desk only)."""
    return not phone_layout.is_on()
