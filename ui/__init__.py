"""App-level UI concerns that belong to no single pane.

Three panes — Analyze (:mod:`analyze`), Datasets (:mod:`datasets`) and Review
(:mod:`well_viewer`) — share a look and a reference manual, and what they share
lives here:

* :mod:`ui.theme` is the single source of truth for colour, type, spacing and
  radii, plus the base Qt stylesheet built from them and the registry of
  selectable themes. Every widget in the application reads its tokens from
  here rather than hardcoding a value, which is what
  ``tests/test_hardcoded_tokens.py`` enforces.
* :mod:`ui.help_topics` maps each page of each pane to the heading in
  ``_Docs/Markdowns/README_WellViewer.md`` that documents it, which is how the
  help drawer opens at the right place.

The distinction from :mod:`widgets` is ownership, not subject: a module in
``widgets/`` is a reusable Qt component with no knowledge of All-Well, while a
module here knows the application — its palette, its panes, its pages — but
belongs to none of them in particular. Nothing here may import a pane.
"""

from __future__ import annotations
