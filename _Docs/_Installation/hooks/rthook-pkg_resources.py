# rthook-pkg_resources.py
# Runtime hook — runs before any user code when the bundled app starts.
# Ensures pkg_resources (bundled as a data directory) is importable.

import sys
import os
from pathlib import Path

# In a PyInstaller bundle, sys._MEIPASS is the extracted data directory.
# pkg_resources was bundled there as a data tree by hook-pkg_resources.py.
if hasattr(sys, "_MEIPASS"):
    _meipass = Path(sys._MEIPASS)
    _pkg_res = _meipass / "pkg_resources"
    if _pkg_res.is_dir() and str(_meipass) not in sys.path:
        sys.path.insert(0, str(_meipass))
