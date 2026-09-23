"""A spawned scan worker must not load numpy.

``pipeline.scan_pool`` runs a plate's header scan across up to eight processes,
and a spawned child is told a module and a qualified name: it imports that
module from scratch in a fresh interpreter. So whatever the worker's module
pulls in at import time is paid **once per worker**, in memory and in startup,
before a single byte of the plate is read.

It was paying for numpy and tifffile. ``pipeline.czi_set`` took one name from
``pipeline.explode_common`` — ``elapsed_hour_tokens`` — and ``explode_common``
also holds the TIFF encoder, so it imports both at module scope;
``pipeline.czi_read`` imported numpy directly for the half of itself that
decodes pixels. Measured in a fresh interpreter: ``import pipeline.czi_set``
was 40 MB of peak RSS, 281 modules and 510 ms, against 19 MB, 160 modules and
71 ms with neither.

On Windows that was not merely wasteful. Eight children loading numpy at once
exhausted the machine's paging file and each died inside numpy's
``_umath_linalg`` DLL load — ``ImportError: DLL load failed while importing
_umath_linalg: The paging file is too small for this operation to complete`` —
so the pool broke, the scan fell back to threads, and the terminal filled with
tracebacks. A header scan needs no arrays at all.

Asserted in a **fresh interpreter**, because pytest has already imported numpy
by the time any test runs: an in-process ``"numpy" not in sys.modules`` is
false for reasons that have nothing to do with the module under test, and the
inverted version passes no matter what the module imports.

The roster is derived from the source rather than written down: every callable
handed to ``czi_set._per_file`` is a scan worker, and the module that defines
it is what a child imports.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from tests._repo_scan import parse_source

REPO = Path(__file__).resolve().parents[1]

#: What a scan child must not load. Not an exhaustive list of heavy packages —
#: these are the two that were actually being loaded, and the two whose absence
#: the measurement above is of.
FORBIDDEN = ("numpy", "tifffile", "imagecodecs")


def _worker_modules() -> list[str]:
    """Every module a spawned scan worker imports, read out of the source."""
    tree = parse_source(REPO / "pipeline" / "czi_set.py")
    names = {
        node.args[0].id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "_per_file"
        and node.args and isinstance(node.args[0], ast.Name)
    }
    assert names, "no worker functions found — the call shape has changed"
    from pipeline import czi_set
    return sorted({getattr(czi_set, name).__module__ for name in names})


def _imports_in_a_fresh_interpreter(module: str) -> list[str]:
    probe = (f"import sys; import {module}; "
             f"print(' '.join(m for m in {FORBIDDEN!r} if m in sys.modules))")
    done = subprocess.run([sys.executable, "-c", probe], cwd=REPO,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]
    return done.stdout.split()


def test_the_worker_roster_is_the_one_the_scan_pool_is_given():
    """The derivation itself, so that a roster that quietly became empty — or
    stopped naming the module a child really imports — is a failure rather
    than a silently green guard."""
    assert _worker_modules() == ["pipeline.czi_set"]


@pytest.mark.parametrize("module", _worker_modules())
def test_a_scan_worker_module_loads_no_pixel_libraries(module):
    loaded = _imports_in_a_fresh_interpreter(module)

    assert loaded == [], (
        f"importing {module} in a fresh interpreter loads {', '.join(loaded)}. "
        f"A spawned scan worker pays for that once per process, and eight of "
        f"them exhausted a Windows machine's paging file inside numpy's "
        f"_umath_linalg DLL load.")


def test_the_parser_itself_loads_none_of_them_either():
    """``czi_read`` is the parser the worker runs, and the module that used to
    import numpy at the top for the half of itself that decodes pixels."""
    assert _imports_in_a_fresh_interpreter("pipeline.czi_read") == []


def test_the_timepoint_naming_is_reachable_without_them():
    """The one name ``czi_set`` needed from ``explode_common``, and the reason
    the split exists. If this module ever grows a numpy import, every worker
    pays for it again through ``czi_set``."""
    assert _imports_in_a_fresh_interpreter("pipeline.timepoints") == []


def test_the_pool_module_is_light_too():
    """``scan_pool`` is imported by the parent before any child exists, but it
    is also on every child's path through ``czi_set``."""
    assert _imports_in_a_fresh_interpreter("pipeline.scan_pool") == []


# ── and the parts that do need numpy still work ─────────────────────────────

def test_the_exploder_still_gets_numpy_when_it_decodes(tmp_path):
    """The lazy import is inside the decode path, so a real conversion loads
    numpy as it always did. Asserted by converting a file, because "the import
    is somewhere in the module" is satisfied by an import that never runs.

    **Three Z-planes**, which is not decoration. numpy is reached from two
    places — ``_decode_tile`` for every tile, and ``read_projection`` for the
    maximum-intensity collapse — and the second is guarded by
    ``if len(planes) == 1: return planes[0]``. With a single plane that branch
    takes the early return, so deleting the import beside ``np.stack`` left
    this test green (CLAUDE.md §7.8 rule 2: build the fixture so the right and
    the wrong answers differ). A stack makes both paths run.
    """
    from datasets.czi_import.apply import convert
    from datasets.czi_import.czi_import_compute import ConversionParams
    from tests._czi_fixture import plate_metadata_xml, plate_tiles, write_czi

    src = write_czi(tmp_path / "Exp.czi",
                    plate_tiles(wells=1, fovs=1, timepoints=1, channels=1, z=3),
                    xml=plate_metadata_xml(scene_wells={0: "A01"},
                                           channels=["DAPI"]))
    dest = tmp_path / "out"

    convert(str(src), str(dest), ConversionParams(experiment="E"),
            log=lambda _m: None, is_cancelled=lambda: False,
            progress=lambda _d, _t: None, well_progress=None)

    assert (dest / "in" / "A01.zip").is_file()


def test_explode_common_still_re_exports_the_timepoint_names():
    """Moving them must not break the importers that already had them: both
    exploders and the ND2 side take these off ``explode_common``."""
    from pipeline import explode_common, timepoints

    for name in ("TimeScan", "ElapsedTokens", "elapsed_hour_tokens",
                 "hours_token", "regularise_hours"):
        assert getattr(explode_common, name) is getattr(timepoints, name), name
