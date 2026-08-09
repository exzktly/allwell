"""Which section of the reference manual documents which page of the app.

The help drawer (``AllWellApp._toggle_help_drawer`` in ``allwell.py``) renders
``_Docs/Markdowns/README_WellViewer.md`` into one scrolling browser and, on
open, jumps to the heading that documents whatever the user is currently looking
at. These are the maps that decide where it jumps.

They live here rather than in ``allwell.py`` for two reasons. The module is
Qt-free, so ``tests/test_help_topics.py`` can check every entry against the real
manual in the fast lane without building a window. And the three panes whose
pages are mapped -- Review's centre tabs, the Analyze form's sections, and the
Datasets rail -- are maintained in three different places, so a single import
site makes it obvious that adding a page means adding a topic.

Keys are what the app calls a page; values are the **exact heading text** in the
manual. The two differ often enough that none of these can be an identity
lookup: the Datasets rail says "Validate & Repair" where the manual says
"Validate & Repair Dataset", and the Analyze form's "Scratch Disk" is the
manual's "10. Scratch disk". ``allwell._gh_slug`` turns the value into the
GitHub-style anchor that ``allwell._render_manual_html`` injected at that
heading.
"""

from __future__ import annotations

from pathlib import Path

#: Where the manual can be found, in the order the drawer tries them: the source
#: tree first, then the path PyInstaller bundles it to at the app root.
README_RELPATHS: tuple[tuple[str, ...], ...] = (
    ("_Docs", "Markdowns", "README_WellViewer.md"),
    ("Markdowns", "README_WellViewer.md"),
)


def readme_candidates(base: Path) -> tuple[Path, ...]:
    """The manual's possible locations under *base* (the app root)."""
    return tuple(base.joinpath(*parts) for parts in README_RELPATHS)


#: The heading that introduces each top-level mode. Used when a pane cannot say
#: which of its own pages is showing, and as the Review ladder's last resort.
MODE_OVERVIEW: dict[str, str] = {
    "review": "Review mode — exploring results",
    "analyze": "Analyze mode — running the pipeline",
    "datasets": "Datasets mode — dataset-level tools",
}

#: Review leaf tab (as returned by ``well_viewer.core.tab_controller.
#: current_centre_tab``) -> manual heading. Every Review leaf has its own
#: per-feature heading; a leaf missing from here falls back to the Review
#: overview, and a renamed or prefixed leaf ("Bar Plots (split)") is caught by
#: the loose substring match in ``AllWellApp._help_section_for_current_page``.
SECTION_BY_PAGE: dict[str, str] = {
    "Bar Plots": "Bar Plots",
    "Line Graphs": "Line Graphs",
    "Scatter Plot": "Scatter Plot",
    "Distribution": "Distribution",
    "Heat Map": "Heat Map",
    "Statistics": "Statistics",
    "Multivariate": "Multivariate analysis",
    "Spatial": "Spatial analysis",
    "Spatial Map": "Spatial analysis",
    "Image Table": "Image Table",
    "Segmentation": "Segmentation",
    "Review Image": "Segmentation",
    "Cell Browser": "Cell Browser",
    "Playground": "Playground",
    "smFISH": "smFISH",
    "Review CSV": "Review CSV",
    "Cell Gating": "Cell Gating",
    "Sample Definitions": "Sample Definitions",
    "Saved Plots": "Saved Plots",
    "Batch Export": "Batch export",
}

#: Analyze form section title (the ``_section(...)`` titles in
#: ``analyze_tab_form.py``) -> manual heading. The manual's Analyze block is
#: numbered in the order the form builds its sections, so these read in step.
ANALYZE_SECTION_HELP: dict[str, str] = {
    "Filename Schema": "1. Filename schema",
    "Channel Tokens": "2. Channel tokens",
    "Folders": "3. Folders",
    "Quality Control": "5. Quality control — field flatness & background correction",
    "Auto-threshold": '6. Auto-threshold — picking each channel\'s "on" cut',
    "Shape Metrics": "7. Shape metrics",
    "Output Options": "8. Output options",
    "Compute Options": "9. Compute options",
    "Scratch Disk": "10. Scratch disk",
}

#: Datasets rail label (the ``_Feature.name`` values in ``datasets_tab.py``,
#: which are also the ``RailNav`` keys) -> manual heading.
DATASETS_PAGE_HELP: dict[str, str] = {
    "Concatenate Datasets": "Concatenate datasets",
    "Convert OME-TIFF": "Convert OME-TIFF",
    "Validate & Repair": "Validate & Repair Dataset",
    "Subset / Split": "Subset / Split Dataset",
    "Zip Loose Folder": "Zip Loose Folder",
    "Merge Plates": "Merge Plates",
    "Job Queue": "Job Queue",
}
