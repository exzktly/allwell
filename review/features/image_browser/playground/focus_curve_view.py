"""The Playground's handle on the shared focus-distribution window.

The window and its renderer live in :mod:`review.core.focus_curve_view`,
because Cell Gating draws the same curve at well scope and this package is a
removable unit -- a build with the Playground profiled out must not take that
button down with it. Re-exported here so this package's own call sites read
the same as they always did.
"""

from __future__ import annotations

from review.core.focus_curve_view import (  # noqa: F401
    draw_focus_distribution,
    show_focus_distribution,
)
