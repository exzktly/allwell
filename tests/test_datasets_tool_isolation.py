"""No Datasets tool imports another Datasets tool (ARCHITECTURE §6b).

§6b states the rule and its purpose: helpers shared by more than one tool live
in ``datasets/_common/`` "rather than being imported from one feature package
into another, so each feature stays independently deletable". Two tools had
reached across anyway — ``subset_compute`` and ``merge_plates_compute`` both
imported from ``concatenate_compute`` — so deleting the Concatenate package
would have broken two features that have nothing to do with it.

The roster comes from ``datasets_tab._FEATURES`` rather than being written out
here, the same way ``test_datasets_log_panel`` does it: a hardcoded list makes a
sweep say "all six tools" while meaning "the six I knew about", and a seventh
tool is covered from the moment it is registered.

``_common`` is the sanctioned direction. So is a tool importing from itself.
Everything else is the coupling this test exists to prevent.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASETS = REPO_ROOT / "datasets"


def _tool_packages() -> list[str]:
    """Every registered tool's package name, plus the Job Queue that hosts them.

    ``_FEATURES`` carries a ``kind`` per queueable tool; Job Queue is a page
    with no ``kind`` of its own, so it is named here to keep it inside the rule
    rather than left as the one package allowed to reach anywhere.
    """
    from datasets_tab import _FEATURES

    kinds = {f.kind for f in _FEATURES if f.kind}
    return sorted(kinds | {"job_queue"})


def test_no_tool_imports_another_tool():
    tools = _tool_packages()
    offenders = []
    for tool in tools:
        pkg = DATASETS / tool
        if not pkg.is_dir():
            continue
        for path in sorted(pkg.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    mods = [node.module or ""]
                elif isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                else:
                    continue
                for mod in mods:
                    parts = mod.split(".")
                    if len(parts) < 2 or parts[0] != "datasets":
                        continue
                    other = parts[1]
                    if other in ("_common", tool):
                        continue
                    if other in tools:
                        offenders.append(
                            f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno} "
                            f"— {tool} imports {mod}"
                        )
    assert not offenders, (
        "A Datasets tool imports another Datasets tool, so deleting the second "
        "would break the first (ARCHITECTURE §6b). Move the shared symbol into "
        "`datasets/_common/`:\n  " + "\n  ".join(sorted(offenders))
    )


def test_every_registered_tool_has_a_package():
    """Guards the sweep itself: a tool whose package this test cannot find is a
    tool it silently is not checking."""
    missing = [t for t in _tool_packages() if not (DATASETS / t).is_dir()]
    assert not missing, f"registered tool(s) with no datasets/<name>/ package: {missing}"
