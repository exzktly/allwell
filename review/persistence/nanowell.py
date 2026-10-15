"""Nanowells page configuration — the ``nanowell`` section of ``persistence.json`` (tracker D9).

Stores what the user chose on the page (view, metric, baseline, axis, well, timepoint, the nanowells
selected for the site detail) — never the tables or the metrics, which are recomputed from the dataset.

**Reset on dataset switch.** A dataset with no saved section lands on the page's defaults rather than
keeping the previous dataset's well, timepoint and selected nanowells, which name things this dataset need
not have.

The page's state object is passed in. The Nanowells page (tracker Section V) registers the feature slice
and wires these two functions into ``persistence/registry.py`` and the view tier of
``persistence/reset_scopes.py``; until a feature registers the slice, nothing calls them.
"""

from __future__ import annotations

from review.persistence import _doc

SECTION = "nanowell"


def save_to_data_dir(ctx, st) -> None:
    """Write *st*'s configuration into the dataset's ``persistence.json``."""
    if not ctx.state.dataset.data_dir or st is None:
        return
    _doc.set_section(ctx, SECTION, st.to_config())


def load_from_data_dir(ctx, st) -> None:
    """Put the dataset's saved configuration on *st*, or the defaults when it has none."""
    if not ctx.state.dataset.data_dir or st is None:
        return
    data = _doc.get_section(ctx, SECTION)
    # The configuration only: the tables on the state belong to the dataset being loaded, not to this block.
    st.apply_config(data if isinstance(data, dict) else {})
