"""The matplotlib bootstrap must not make font discovery a startup cost.

matplotlib otherwise enumerates every system font on first cache build (minutes
on some Windows and macOS hosts). ``startup.mpl_bootstrap.configure()`` must pin
a cache dir without importing the eager ``matplotlib.font_manager`` module.

The regression test checks module-import state because elapsed-time assertions
would be host-dependent and would miss a fast development machine.
"""

from __future__ import annotations

import os

def test_configure_does_not_import_matplotlib_font_manager():
    import sys
    from startup import mpl_bootstrap

    was_loaded = "matplotlib.font_manager" in sys.modules
    mpl_bootstrap.configure()
    assert ("matplotlib.font_manager" in sys.modules) is was_loaded


def test_configure_sets_a_cache_dir():
    from startup import mpl_bootstrap

    mpl_bootstrap.configure()
    assert os.environ.get("MPLCONFIGDIR")
