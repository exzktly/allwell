"""Where a dataset's input images are, given the folder the user opened.

A run lays a dataset out as ``<root>/in`` (the images it read) beside ``<root>/out`` (what it wrote). A user may
open either ``<root>`` or ``<root>/out``; both are the same dataset, and the input images are ``<root>/in`` in both
cases. The loader used to recognise only the first: opening ``out/`` directly left ``in_dir`` unset, so every
viewer that reads input frames — the Image Table's phase row, the raw source, a nanowell run's tiles, which its
``out.zip`` does not hold at all — reported its image missing with ``in/`` sitting right beside it, and named
``in/`` as the remedy. The in-place-append recovery in the same loader already accepted both, so the two answered
one question differently.

:func:`resolve` is that one answer. Qt-free, and it imports nothing above the data layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DatasetLayout:
    #: The folder holding the run's outputs — what the well loader reads.
    out_dir: Path
    #: The folder holding the run's input images, or ``None`` when neither layout has one.
    in_dir: Path | None
    #: Whether the user opened ``<root>`` of an ``in/`` + ``out/`` pair (``out_dir`` is then ``<root>/out``).
    opened_root: bool


def resolve(path: Path) -> DatasetLayout:
    """The layout of the dataset opened at *path*.

    * ``<path>/in`` and ``<path>/out`` both exist: the root of a pair.
    * *path* is named ``out`` (any case) and ``<path>/../in`` exists: the output half of a pair.
    * otherwise: a flat output folder with no input images beside it.
    """
    path = Path(path)
    if (path / "in").is_dir() and (path / "out").is_dir():
        return DatasetLayout(out_dir=path / "out", in_dir=path / "in", opened_root=True)
    if path.name.lower() == "out" and (path.parent / "in").is_dir():
        return DatasetLayout(out_dir=path, in_dir=path.parent / "in", opened_root=False)
    return DatasetLayout(out_dir=path, in_dir=None, opened_root=False)
