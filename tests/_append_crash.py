"""Leave an archive the way a killed in-place append leaves it — by killing one.

A test that finishes an append in-process and then truncates the file is not a
crash: the journal says the append completed, so an undo correctly refuses a
file that has since changed. The real cases are a process that dies part-way
through a member (no central directory on disk any more) and one that dies
after the append but before the commit. Both are produced here by a child
process that calls ``os._exit`` at that point, so nothing in it rolls back.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_SCRIPT = """
import os, sys
sys.path.insert(0, {repo!r})
from pathlib import Path
from util.zip_append import AppendRun
archive, mode = Path(sys.argv[1]), sys.argv[2]
run = AppendRun(archive.parent, preserve=[archive.parent / "acquisition.json"])
run.__enter__()
def write(zf):
    zf.writestr("E_GFP_A01_f0_48h.tif", b"half" * 100)
    if mode == "mid":
        with zf.open("E_GFP_A01_f1_48h.tif", "w") as handle:
            handle.write(b"x" * 500)
            zf.fp.flush()
            os._exit(9)
run.append(archive, write)
if mode == "after-then":
    Path(sys.argv[3]).write_text(sys.argv[4])
os._exit(9)
"""


def crash_append(archive: Path, mode: str = "mid", *extra: str) -> None:
    """Kill a child process mid-append (``"mid"``) or after it (``"after"``)."""
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT.format(repo=str(REPO)), str(archive), mode, *extra],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 9, result.stderr
