"""The glyph beside each rail entry of the Review pane, by the feature's name.

A feature with no entry here gets no glyph. The names are icon keys of :mod:`widgets.icons`.
"""

from __future__ import annotations

SECTION_ICONS = {
    "Home": "home",
    "Plotting":           "line-chart",
    "Statistics":         "sigma",
    "Multivariate":       "scatter-chart",
    "Spatial":            "waypoints",
    "Nanowells":          "hexagon",
    "Image Browser":      "layout-grid",
    "smFISH":             "dna",
    "Review CSV":         "file-spreadsheet",
    "Saved Plots":        "image",
    "Batch Export":       "boxes",
}
