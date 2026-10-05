"""The remote runner's facade is its ``__all__``, and nothing else is reached
through it.

``services/remote_runner.py`` grew by extraction: each time a cluster moved out
(transport, conda, run ids, scratch, wait snippets, suspend, then fetch,
staging, process, upload and jobs), the runner kept re-exporting the moved
names so its tests did not have to change. That made a smaller file without
making a smaller API, because every test still reached the old module and the
module graph behind it was invisible to callers. Workstream R of
the remediation plan (deleted when its last row closed; ``git log --
_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`` recovers it) drew the boundary
(row R0), inventoried 85 other names reached through the runner, and moved
every consumer to the module that owns what it reaches (rows R1–R5). The
inventory is now empty, so this guard states the end state directly:

* ``SUPPORTED_FACADE`` is the stable application/CLI surface and equals the
  module's ``__all__``;
* no file reaches any other name through ``services.remote_runner``;
* the runner re-exports nothing outside its facade.

"Reaches" means any of four access forms, not only ``from … import``: module
attribute reads and writes (``rr.X``), named setattr/patch calls
(``monkeypatch.setattr(rr, "X", …)``), and string patch targets
(``"services.remote_runner.X"``). An import-only scan once reported eleven
re-exports as unused while ``tests/test_remote_transit_volume.py`` still
cleared ``rr._transit_refused`` through the module. ``tests/_facade_scan.py``
holds the scan, and ``tests/test_guards_bite.py`` shows it seeing each form.

That includes the standard library: ``"services.remote_runner.subprocess.run"``
patches the shared ``subprocess`` module exactly as ``"subprocess.run"`` does,
so it is written the second way.

Do not re-export a name here to make a test pass; import it from its owner. A
genuinely new public facade name updates ``__all__`` and ``SUPPORTED_FACADE``
together, with a reviewed reason.
"""

from __future__ import annotations

import functools
import importlib
from pathlib import Path

from tests._facade_scan import consumers_of, reexports

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER = REPO_ROOT / "services" / "remote_runner.py"
MODULE = "services.remote_runner"

#: Stable even where the spelling is private: ``_REQUIRED_REMOTE_FILES`` is the
#: packaging contract the bootstrap and the PyInstaller spec read by this path.
#: ``LocalInputMode`` and ``ZippedInput`` are the staging request types the CLI
#: and ``PipelineRunner`` hand to ``stage_local_zips``.
SUPPORTED_FACADE = frozenset({
    "LocalInputMode",
    "OutputFilter",
    "RemoteRunner",
    "TRANSIENT_PREFIX",
    "WELL_XFER_PREFIX",
    "ZippedInput",
    "_REQUIRED_REMOTE_FILES",
    "input_well_containers",
    "repo_root",
    "stage_local_zips",
})


@functools.cache
def _consumers() -> dict[str, set[tuple[str, str]]]:
    return consumers_of(MODULE, exclude=[RUNNER])


def test_remote_runner_supported_facade_is_explicit():
    runner = importlib.import_module(MODULE)
    names = getattr(runner, "__all__", None)
    assert names is not None, f"{MODULE} must declare __all__"
    assert set(names) == set(SUPPORTED_FACADE)


def test_nothing_outside_the_facade_is_reached_through_the_runner():
    users = _consumers()
    unexpected = set(users) - SUPPORTED_FACADE
    assert not unexpected, (
        "These reaches into services.remote_runner widen its effective API. "
        "Import from (or patch) the module that owns the name instead:\n  "
        + "\n  ".join(f"{n}: {sorted(users[n])}" for n in sorted(unexpected))
    )


def test_the_runner_reexports_only_its_facade():
    """A re-export outside ``__all__`` is either dead weight or the start of a
    new compatibility surface; both are refused."""
    extra = reexports(RUNNER) - SUPPORTED_FACADE
    assert not extra, (
        "services.remote_runner imports these names without using them, and "
        "they are not part of its facade; delete the imports:\n  "
        + "\n  ".join(sorted(extra))
    )
