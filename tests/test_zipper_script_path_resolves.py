"""The path the app spawns the grouping pre-pass from must resolve to the module.

``services.input_resolution_service`` reaches the loose-TIF grouper by *path*
rather than by import, because it runs as a subprocess:
``ZIPPER_SCRIPT = HERE / "pipeline" / "well_plate_zipper.py"``, handed to
``[sys.executable, str(zipper), ...]``. A path is not an import, so nothing in
Python notices when it stops describing the tree — ``find_zipper_script()``
simply answers ``None``, and ``run_zipper`` raises only at the moment a user
starts a run on a folder of loose TIFs. That is the worst moment to find out and
the only one: the failure is invisible until then, and invisible in CI for ever.

The move to ``pipeline/well_plate_zipper.py`` is what made this worth pinning.
The constant was edited by hand in the same commit as the move, and nothing in
the suite would have said so had it not been — the guards that cover the other
rosters naming this file (``services.remote_manifest``, the build script's
``REQUIRED`` array, the PyInstaller spec's package walk) all read *their own*
list against the tree and none of them reads this constant.

Identity, not existence. ``ZIPPER_SCRIPT`` pointing at ``pipeline/__init__.py``
would satisfy an ``.exists()`` check while spawning a subprocess that does
nothing, so the assertion is that the constant names the same file as the module
the rest of the repository refers to by its dotted name.
"""
from __future__ import annotations

from pathlib import Path

from services.input_resolution_service import ZIPPER_SCRIPT, find_zipper_script


def _module_file() -> Path:
    """Where ``pipeline.well_plate_zipper`` actually is, per the import system."""
    import pipeline.well_plate_zipper as wpz

    assert wpz.__file__ is not None, (
        "pipeline.well_plate_zipper has no __file__, so this test cannot "
        "compare the spawn path against it")
    return Path(wpz.__file__).resolve()


def test_the_spawn_path_resolves_to_the_zipper_module():
    """The constant, held against the module the import system finds."""
    assert ZIPPER_SCRIPT.resolve() == _module_file(), (
        f"ZIPPER_SCRIPT names {ZIPPER_SCRIPT}, but pipeline.well_plate_zipper "
        f"is at {_module_file()}. The app spawns the first of those as a "
        "subprocess, so a grouping run would execute the wrong file or none at "
        "all. Either the module moved and the constant did not follow, or "
        "`HERE` (services/ → repository root) no longer computes the root — "
        "both are silent until someone starts a run on loose TIFs.")


def test_the_resolver_the_caller_uses_finds_it():
    """The boundary rather than the constant behind it.

    ``run_zipper`` asks ``find_zipper_script()``, not ``ZIPPER_SCRIPT``, and it
    is the ``None`` from here that becomes the RuntimeError a user sees. A test
    of the constant alone would pass with this function returning ``None``
    unconditionally.
    """
    found = find_zipper_script()
    assert found is not None, (
        "find_zipper_script() answered None, so the app cannot spawn the "
        f"grouping pre-pass at all (ZIPPER_SCRIPT is {ZIPPER_SCRIPT}, which "
        f"{'exists' if ZIPPER_SCRIPT.exists() else 'does not exist'}). "
        "`run_zipper` turns that None into a RuntimeError at the moment a user "
        "starts grouping a plate of loose TIFs — the first and only time "
        "anything notices.")
    assert found.resolve() == _module_file(), (
        f"find_zipper_script() answered {found}, which is not the "
        f"pipeline.well_plate_zipper module at {_module_file()}")
