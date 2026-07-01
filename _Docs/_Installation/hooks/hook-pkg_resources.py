# hook-pkg_resources.py
# pkg_resources lives inside conda's setuptools but isn't pip-visible.
# Find it directly from the file system and bundle it as a data tree.

import sys, os
from pathlib import Path

# Locate pkg_resources in the active Python environment
_pkg_res = None
for p in sys.path:
    candidate = Path(p) / "pkg_resources"
    if candidate.is_dir() and (candidate / "__init__.py").exists():
        _pkg_res = candidate
        break

if _pkg_res:
    # Bundle the entire pkg_resources directory as data
    datas = [(str(_pkg_res), "pkg_resources")]
    hiddenimports = ["pkg_resources"]
else:
    datas = []
    hiddenimports = []
