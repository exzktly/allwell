"""`startup.app_fonts.fonts_dir` must find the bundled faces, and it fails quietly.

`fonts_dir()` counts parents from its own `__file__`, so the count is a
statement about where the module sits. Get it wrong and nothing raises:
`_register_qt_group` looks for each `.ttf`, finds none, returns an empty family
name, and the application renders in whatever the system supplies. That reads as
a styling regression rather than a broken path, and on a developer's machine —
where Inter may well be installed system-wide — it may not read as anything at
all.

The count was `parents[2]` while the module lived at
`review/core/app_fonts.py`; lifting it to `startup/app_fonts.py` left it
pointing one directory above the checkout until this test was written. Its
sibling `startup/mpl_bootstrap.py` carries the same shape and the same kind of
test, for the same reason: both resolve a repository-level asset path against
their own location.

Anchored on `tests._repo_scan.REPO_ROOT` rather than re-derived from
`app_fonts.__file__`, because a test that computes the answer the same way the
module does accepts whatever the module says.
"""
from __future__ import annotations

from tests._repo_scan import REPO_ROOT


def test_the_fonts_directory_is_the_one_in_the_repository():
    from startup.app_fonts import fonts_dir

    assert fonts_dir() == REPO_ROOT / "assets" / "fonts", (
        f"fonts_dir() answered {fonts_dir()}, not {REPO_ROOT / 'assets' / 'fonts'}. "
        "The module moved and its parent count did not follow; no face will "
        "register and the application will silently draw in a system font.")


def test_every_face_the_module_names_is_actually_there():
    """The directory existing is not the same as the faces being in it.

    A `fonts_dir()` that happened to resolve to some other real directory would
    satisfy the test above on its own.
    """
    from startup.app_fonts import (MONO_FONT_FILES, UI_FONT_FILES, fonts_dir)

    d = fonts_dir()
    missing = [f for f in (*UI_FONT_FILES, *MONO_FONT_FILES) if not (d / f).exists()]
    assert not missing, (
        f"app_fonts names faces that are not in {d}: {missing}. Each one is a "
        "weight that silently falls back at runtime.")
