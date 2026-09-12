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

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_image_table_state_suffixes_match_frame_load():
    from review.data import frame_load
    from review.features.image_browser.image_table import state

    assert state._BINARY_SUFFIX == frame_load.BINARY_SUFFIX
    assert state._MASK_SUFFIX == frame_load.MASK_SUFFIX
    assert state._RAW_SUFFIX == frame_load.RAW_SUFFIX
    assert set(state._RENDER_SUFFIXES.values()) == set(frame_load.VARIANT_SUFFIXES)


def test_image_table_state_stays_importable_without_numpy_or_qt():
    """The duplication above is only worth its cost while this holds.

    `state.py` restates the suffixes instead of importing them because it is
    documented stdlib-only and `data/frame_load.py` reaches numpy transitively
    (94 modules at import, measured, against zero for `state.py`). Nothing
    checked that: `tests/test_guardrails.py`'s state-boundary guard names this
    module, but it only forbids importing `runtime_app`.

    So a future reader can delete the duplication in good faith -- the parity
    test still passes, since the values are then the same object -- and quietly
    drag numpy into the module that was written to avoid it. This is what says
    no.
    """
    import subprocess
    import sys

    probe = (
        "import sys, importlib\n"
        "importlib.import_module('review.features.image_browser.image_table.state')\n"
        "print(','.join(m for m in ('numpy', 'PySide6', 'matplotlib', 'pandas')\n"
        "                if m in sys.modules))\n"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, cwd=REPO_ROOT, check=True).stdout.strip()
    assert not out, (
        "image_table/state.py now pulls heavy modules at import: "
        f"{out}. It is contractually stdlib-only -- the drag-and-drop shelf's "
        "spec mapping must stay importable without them, which is the whole "
        "reason the render suffixes are duplicated rather than imported.")
