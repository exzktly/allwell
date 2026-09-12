"""Image Browser — the unified image-review area.

A single rail entry hosting the disparate image-review surfaces as sibling
sub-tabs: **Segmentation** (the review-image canvas), **Image Table** (the
per-cell grid), **Cell Browser** (the filtered single-cell gallery), and
**Playground** (the single-frame correction testbed). The four feature
packages live underneath this one (``review_image`` / ``image_table`` /
``cell_browser`` / ``playground``); ``tab_view.build_image_browser_tab`` is the
container that composes them and ``controller`` owns sub-tab routing.
"""
