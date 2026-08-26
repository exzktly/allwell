"""Guardrail: the data layer must not import ``features/`` — at any nesting.

``well_viewer/data`` is a foundational layer: features build on it, not the
other way round. ``test_layering.py`` already bans *module-scope*
``well_viewer.features`` imports in ``data`` / ``domain`` / ``persistence``.
This guard is stricter for ``data/``: it bans feature imports **at any level**
(module *or* function-local), because the whole point of the cache-projector
inversion (roadmap B1) is that ``data/data_loading.get_rows`` no longer reaches
up into ``features/qc`` + ``features/crop`` — the projectors are registered from
``core/projector_registry`` instead. A function-local feature import would
silently re-couple the layer, so it must fail CI.
"""

from __future__ import annotations

import ast
from pathlib import Path
from tests._repo_scan import parse_source

DATA_DIR = Path(__file__).resolve().parent.parent / "well_viewer" / "data"
FORBIDDEN_PREFIX = "well_viewer.features"


def _feature_imports(tree: ast.Module):
    """``(lineno, target)`` for every ``import``/``from`` naming a feature,
    at ANY nesting (``ast.walk``, not just module body)."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == FORBIDDEN_PREFIX or mod.startswith(FORBIDDEN_PREFIX + "."):
                out.append((node.lineno, mod))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == FORBIDDEN_PREFIX or alias.name.startswith(
                    FORBIDDEN_PREFIX + "."
                ):
                    out.append((node.lineno, alias.name))
    return out


def test_data_layer_has_no_feature_imports():
    offenders = []
    for path in sorted(DATA_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = parse_source(path)
        for lineno, target in _feature_imports(tree):
            offenders.append(f"{path.relative_to(DATA_DIR.parent.parent)}:{lineno} -> {target}")
    assert not offenders, (
        "well_viewer/data/ must not import well_viewer.features at ANY level "
        "(the cache-projector inversion, roadmap B1). Register the behaviour "
        "from core (see core/projector_registry) instead of importing the "
        "feature here:\n  " + "\n  ".join(offenders)
    )
