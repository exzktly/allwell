"""Guardrail: the domain/ and data/ layers must not import up into features/.

Business logic (``well_viewer.domain``) and the data layer
(``well_viewer.data``) are foundational — features build on them, not the other
way round. A module-level ``from well_viewer.features... import ...`` inside
domain/ or data/ is a layering inversion (it couples core logic to a UI
feature). This test scans the AST of those layers and fails on any such import,
locking in the P3 cleanup so it can't silently regress.

Only *module-level* imports are checked: a function-local import is the
sanctioned escape hatch for a genuine, isolated cycle break.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "well_viewer"
GUARDED_LAYERS = ("domain", "data")
FORBIDDEN_PREFIX = "well_viewer.features"


def _module_level_imports(tree: ast.Module):
    """Yield ImportFrom/Import nodes that sit at module top level only."""
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node


def _imports_feature(node) -> bool:
    if isinstance(node, ast.ImportFrom):
        mod = node.module or ""
        return mod == FORBIDDEN_PREFIX or mod.startswith(FORBIDDEN_PREFIX + ".")
    if isinstance(node, ast.Import):
        return any(
            alias.name == FORBIDDEN_PREFIX or alias.name.startswith(FORBIDDEN_PREFIX + ".")
            for alias in node.names
        )
    return False


def test_domain_and_data_do_not_import_features():
    offenders = []
    for layer in GUARDED_LAYERS:
        for path in sorted((ROOT / layer).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in _module_level_imports(tree):
                if _imports_feature(node):
                    target = (
                        node.module if isinstance(node, ast.ImportFrom)
                        else ", ".join(a.name for a in node.names)
                    )
                    offenders.append(f"{path.relative_to(ROOT.parent)}:{node.lineno} -> {target}")
    assert not offenders, (
        "Layering violation: well_viewer/{domain,data} must not import from "
        "well_viewer.features at module scope. Move the shared code down into "
        "domain/ or data/, or use a function-local import for a real cycle "
        "break:\n  " + "\n  ".join(offenders)
    )
