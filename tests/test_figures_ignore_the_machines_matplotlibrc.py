"""A matplotlibrc on the machine does not change how big All-Well's figure text is.

The figures are laid out for matplotlib's default 100 dpi; a site rc file saying ``figure.dpi: 200`` doubled
every figure's text against its card on every plot page. ``startup.mpl_rc.pin`` puts the value back. Run in a
fresh interpreter, because matplotlib reads its rc files once, at import, and this process has already done so.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

PROBE = (
    "import matplotlib; before = matplotlib.rcParams['figure.dpi']\n"
    "from startup import mpl_rc; mpl_rc.pin()\n"
    "from matplotlib.figure import Figure\n"
    "print(before, Figure().dpi)\n"
)


def _run(tmp_path: Path) -> tuple[float, float]:
    rc = tmp_path / "matplotlibrc"
    rc.write_text("figure.dpi: 200\n", encoding="utf-8")
    env = {**os.environ, "MATPLOTLIBRC": str(rc), "PYTHONPATH": str(REPO)}
    out = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, env=env, cwd=REPO,
                         timeout=120, check=True).stdout.split()
    return float(out[0]), float(out[1])


def test_a_figure_is_drawn_at_100_dpi_whatever_the_rc_file_says(tmp_path):
    before, after = _run(tmp_path)
    assert before == 200.0, "the probe's rc file was not read, so this measured nothing"
    assert after == 100.0


def test_main_and_the_render_harness_both_pin_it():
    """Both start-up paths call ``mpl_rc.pin`` before the window is built — the harness is what measures every
    page, and a harness that skipped it would measure a machine rather than the app."""
    main_src = (REPO / "allwell.py").read_text(encoding="utf-8")
    harness = (REPO / "tests" / "_page_render_case.py").read_text(encoding="utf-8")
    assert "mpl_rc.pin()" in main_src.split("def main(", 1)[1]
    start = harness.split("def start_window(", 1)[1].split("\ndef ", 1)[0]
    assert start.index("mpl_rc.pin()") < start.index("AllWellApp(")
