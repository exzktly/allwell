"""Custom widgets for the All-Well UI.

Every widget in this package is styled exclusively from the tokens in the
repo-root ``theme`` module, is DPI-aware (sizes derive from font metrics /
logical units, never hardcoded device pixels), and ships a ``__main__`` block
that opens it in a small standalone window for visual QA.

Import widgets directly from their modules, e.g.::

    from widgets.toggle_switch import ToggleSwitch

or run a module directly to preview it::

    python widgets/toggle_switch.py
"""

from __future__ import annotations
