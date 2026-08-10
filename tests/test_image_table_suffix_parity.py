"""The render-suffix vocabulary is written twice, and the two copies must agree.

``data/frame_load.py`` owns it: a channel token's ` (binary)` / ` (mask)` /
` (raw)` suffix is what the loader strips to find the base channel.
``image_table/state.py`` restates the same three strings rather than importing
them, because the drag-and-drop shelf's spec mapping is contractually
stdlib-only and the loader reaches numpy transitively.

That is a fair trade only while the copies match. A token the shelf writes and
the loader cannot decode resolves to no channel at all, so the cell renders
empty with nothing on screen saying why — the silent-substitution failure the
architecture rules out. Nothing checked it until this test.
"""

from __future__ import annotations


def test_image_table_state_suffixes_match_frame_load():
    from well_viewer.data import frame_load
    from well_viewer.features.image_browser.image_table import state

    assert state._BINARY_SUFFIX == frame_load.BINARY_SUFFIX
    assert state._MASK_SUFFIX == frame_load.MASK_SUFFIX
    assert state._RAW_SUFFIX == frame_load.RAW_SUFFIX
    assert set(state._RENDER_SUFFIXES.values()) == set(frame_load.VARIANT_SUFFIXES)
