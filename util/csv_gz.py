"""Per-well result CSVs, in either spelling: ``name.csv`` or ``name.csv.gz``.

The pipeline gzips each well's CSV once the well is finished (a plate's CSVs get
very large), and a dataset folder may therefore hold gzipped CSVs, plain ones,
or a mix — a run started before compression existed and finished after it, an
incremental run on top of an old dataset, a hand-edited file. Every reader has
to treat the two spellings as one file, and this module is the one place that
says how.

**One logical file, two possible spellings.** ``measurements_A1.csv`` and
``measurements_A1.csv.gz`` name the same logical CSV. When both exist the plain
one wins: the gzip is only ever produced from a plain file, so the two coexist
only when a merge wrote a newer plain file beside an older archive, or when the
plain file's removal after compressing failed — in the first case the plain
file is the newer, in the second the two are identical.

Qt-free and stdlib-only: the pipeline, the remote worker (this file is in the
remote upload manifest) and the GUI all import it.
"""

from __future__ import annotations

import gzip
import os
import shutil
import zlib
from pathlib import Path
from typing import IO, Callable

CSV_SUFFIX = ".csv"
GZ_SUFFIX = ".gz"
CSV_GZ_SUFFIX = CSV_SUFFIX + GZ_SUFFIX


#: What reading a damaged ``.csv.gz`` can raise besides ``OSError``: ``EOFError``
#: for a truncated stream, ``zlib.error`` for a corrupt deflate stream (which is
#: neither). Readers that tolerate an unreadable CSV list these with their own
#: ``UnicodeDecodeError`` / ``csv.Error``.
GZ_READ_ERRORS: tuple[type[BaseException], ...] = (EOFError, zlib.error)


def is_csv_name(name: str) -> bool:
    """``True`` for ``x.csv`` and ``x.csv.gz`` (case-insensitive)."""
    low = str(name).lower()
    return low.endswith(CSV_SUFFIX) or low.endswith(CSV_GZ_SUFFIX)


def is_gz_csv_name(name: str) -> bool:
    return str(name).lower().endswith(CSV_GZ_SUFFIX)


def plain_name(name: str) -> str:
    """The ``.csv`` spelling of a CSV name; any other name is returned as is."""
    return str(name)[: -len(GZ_SUFFIX)] if is_gz_csv_name(name) else str(name)


def csv_stem(name: str) -> str:
    """The name with ``.csv`` / ``.csv.gz`` removed (``Path.stem`` leaves ``.csv``
    on a gzipped name)."""
    base = plain_name(Path(str(name)).name)
    return base[: -len(CSV_SUFFIX)] if base.lower().endswith(CSV_SUFFIX) else base


def gz_path(path: Path | str) -> Path:
    """The ``.csv.gz`` spelling of a plain ``.csv`` path."""
    p = Path(path)
    return p if is_gz_csv_name(p.name) else p.with_name(p.name + GZ_SUFFIX)


def plain_path(path: Path | str) -> Path:
    """The ``.csv`` spelling of a ``.csv.gz`` path."""
    p = Path(path)
    return p.with_name(plain_name(p.name))


def existing_csv(path: Path | str) -> Path | None:
    """The file that holds the logical CSV *path* names, or ``None``.

    *path* may be spelled either way. Plain wins over gzip when both exist (see
    the module docstring).
    """
    plain = plain_path(path)
    if plain.is_file():
        return plain
    gz = gz_path(path)
    if gz.is_file():
        return gz
    return None


def open_csv_text(path: Path | str, mode: str = "r", **kwargs) -> IO[str]:
    """Open a ``.csv`` or ``.csv.gz`` for text I/O, ``newline=""`` by default.

    Same defaults as the plain ``open(path, newline="")`` every CSV reader here
    used, so a gzipped file decodes exactly as its plain twin would.
    """
    kwargs.setdefault("newline", "")
    if is_gz_csv_name(Path(path).name):
        return gzip.open(path, mode if "t" in mode else mode + "t", **kwargs)  # type: ignore[return-value]
    return open(path, mode, **kwargs)


def glob_csvs(directory: Path | str, pattern: str = "*") -> list[Path]:
    """Every logical CSV in *directory* whose name stem matches *pattern*.

    *pattern* is a glob for the part before ``.csv`` (``"*_A1"``, ``"measurements_*"``).
    Each logical file appears once, as the path that holds it (plain preferred).
    The result is sorted by the plain spelling of the name so callers that used
    to ``sorted(glob("*.csv"))`` see the same order.
    """
    d = Path(directory)
    try:
        plain = list(d.glob(pattern + CSV_SUFFIX))
        gz = list(d.glob(pattern + CSV_GZ_SUFFIX))
    except OSError:
        return []
    by_logical: dict[str, Path] = {}
    for p in gz:
        by_logical[plain_name(p.name)] = p
    for p in plain:
        by_logical[p.name] = p          # plain overrides gzip
    return [by_logical[k] for k in sorted(by_logical)]


def csv_is_whole(path: Path | str) -> bool:
    """Whether *path* reads to its end: a ``.csv.gz`` decompresses without a truncated or corrupt stream, a plain
    CSV simply exists. A download cut off part-way (rsync's ``--partial``) leaves a gzip stream with no end, which a
    size check calls present."""
    p = Path(path)
    if not is_gz_csv_name(p.name):
        return p.is_file()
    try:
        with gzip.open(p, "rb") as fh:
            while fh.read(1 << 22):
                pass
        return True
    except (OSError, *GZ_READ_ERRORS):
        return False


def gzip_csv(path: Path | str) -> Path:
    """Compress the plain CSV *path* to ``<path>.gz`` and remove the original.

    Crash-safe in the order that matters: the archive is written to a hidden
    sibling, flushed to disk and renamed into place *before* the original is
    removed, so there is always at least one complete copy. A hidden name ending
    in neither ``.csv`` nor ``.csv.gz`` keeps the partial from being mistaken
    for a result by any glob here. Raises ``OSError`` on failure with the plain
    file untouched; callers log it and carry on, because an uncompressed CSV is
    a larger file, not a wrong one.
    """
    src = Path(path)
    if is_gz_csv_name(src.name):        # already compressed: nothing to do, and
        return src                      # the unlink below would delete it
    dest = gz_path(src)
    tmp = dest.with_name(f".{dest.name}.part{os.getpid()}")
    try:
        with open(src, "rb") as fin, open(tmp, "wb") as raw:
            with gzip.GzipFile(filename=src.name, mode="wb", fileobj=raw,
                               compresslevel=6) as fout:
                shutil.copyfileobj(fin, fout, 1 << 20)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(tmp, dest)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    src.unlink()
    return dest


def gzip_csvs(directory: Path | str, pattern: str = "*",
              log_fn: Callable[[str], None] | None = None) -> int:
    """Gzip every plain CSV in *directory* matching *pattern* (see
    :func:`glob_csvs`); return how many were compressed. A failure is logged and
    skipped, never raised."""
    done = 0
    for p in glob_csvs(directory, pattern):
        if is_gz_csv_name(p.name):
            continue
        try:
            gzip_csv(p)
            done += 1
        except OSError as exc:
            if log_fn:
                log_fn(f"could not compress {p.name}: {exc}")
    return done
