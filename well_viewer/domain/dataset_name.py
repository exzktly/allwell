"""What to call the dataset that was loaded from a given directory.

One rule, in one place, because the answer is not simply the folder's name and
every caller that assumed it was got it wrong in the same way.

The standard Analyze-pipeline layout is ``<dataset_root>/in`` for the raw images
and ``<dataset_root>/out`` for everything the pipeline wrote, and Review loads
the ``out`` half — so ``data_dir.name`` is the literal string ``out`` for every
such dataset, which tells a reader nothing and tells two datasets apart not at
all. The name worth showing is the root's, one level up.

Qt-free and side-effect-free apart from the one directory test that decides
which layout this is.
"""

from __future__ import annotations

from pathlib import Path

#: The pipeline's output folder, which Review loads as its ``data_dir``.
_OUT_DIR = "out"

#: Its sibling holding the raw images. Both must be present for a folder called
#: "out" to be the output half of a split layout rather than a dataset that
#: happens to be named that.
_IN_DIR = "in"


def dataset_display_name(data_dir) -> str:
    """The name to show for the dataset loaded from *data_dir*.

    For the in/out layout that is the dataset root's name; for a flat dataset it
    is the loaded folder's own name. The layout is detected structurally — a
    sibling ``in`` directory beside a folder called ``out`` — the same way
    ``core.load_controller.load_path`` decides it when opening the folder, so a
    genuinely flat dataset whose own top-level folder happens to be called
    "out" keeps its own name rather than borrowing its parent's.

    Returns ``""`` for no directory at all, and falls back to the full path for
    the pathological case of a directory with no name (a filesystem root).
    """
    if data_dir is None:
        return ""
    path = Path(data_dir)
    if path.name == _OUT_DIR and (path.parent / _IN_DIR).is_dir():
        return path.parent.name or str(path)
    return path.name or str(path)
