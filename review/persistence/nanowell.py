"""Nanowells page configuration — the ``nanowell`` section of ``persistence.json`` (tracker D9).

Stores what the user chose on the page (view, metric, baseline, axis, well, timepoint, the nanowells
selected for the site detail) — never the tables or the metrics, which are recomputed from the dataset.

**Reset on dataset switch.** A dataset with no saved section lands on the page's defaults rather than
keeping the previous dataset's well, timepoint and selected nanowells, which name things this dataset need
not have.

The Nanowells feature registers the ``nanowell`` slice; ``persistence/registry.py`` registers these two
functions (when the build profile includes the page), and ``persistence/reset_scopes.py`` puts the section in
the view tier. Both take the slice from the context unless a state object is passed, and do nothing when the
feature is absent.
"""

from __future__ import annotations

from review.persistence import _doc

SECTION = "nanowell"


def save_to_data_dir(ctx, st=None) -> None:
    """Write the page's configuration into the dataset's ``persistence.json``."""
    st = st if st is not None else ctx.state.feature_slice(SECTION)
    if not ctx.state.dataset.data_dir or st is None:
        return
    _doc.set_section(ctx, SECTION, st.to_config())


def load_from_data_dir(ctx, st=None) -> None:
    """Put the dataset's saved configuration on the page, or the defaults when it has none."""
    st = st if st is not None else ctx.state.feature_slice(SECTION)
    if not ctx.state.dataset.data_dir or st is None:
        return
    data = _doc.get_section(ctx, SECTION)
    # The configuration only: the tables on the state belong to the dataset being loaded, not to this block.
    st.apply_config(data if isinstance(data, dict) else {})
