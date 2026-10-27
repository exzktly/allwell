"""Read a well archive's images in place, one field at a time.

A zip-mode well used to be unpacked before its first field ran: every image the
well needed was copied out of ``<well>.zip`` into the scratch directory, and only
then did processing start. On a cluster, where scratch is usually the networked
home directory and eight workers unpack a well each at the same moment, that
copy alone held the start of every batch for about ten minutes, although each
field then took two seconds to process.

A zip does not have to be unpacked to be read. Every member is stored, and
compressed, on its own, and the central directory at the end of the archive
records where each one starts, so one member can be read without touching any
other. :class:`ZipMemberReader` does that: it hands back a member's bytes when a
field asks for them, and the field decodes them from memory. Nothing is written
to scratch for the inputs, the first field starts as soon as the worker has
the archive's directory, and a field resumed from a checkpoint never reads its
images at all.

The groups keep their paths (``<scratch>/_tmp_extract_<well>/<member>``) as
*names*: the processing code takes ``.name`` / ``.stem`` from them to name its
outputs and parse the filename schema, and only
:func:`pipeline.io.load_2d_gray_with_ceiling` ever opened one. The reader is
looked up by that basename, which is what the grouper keyed the archive on.

**Threads.** A well's fields can run on several threads
(``--fov_threads``). One ``ZipFile`` shared between them is safe but serialises
their reads behind a lock, so each thread opens its own handle, once, and keeps
it for the well. :meth:`ZipMemberReader.close` closes them all.

Stdlib only, so it stays importable wherever the pipeline is.
"""

from __future__ import annotations

import io
import threading
import zipfile
from pathlib import Path


class ZipMemberReader:
    """Member bytes of one well archive, read on demand by basename.

    The archive's members are keyed by their basename with any folder inside
    the zip stripped, and the first occurrence of a duplicate wins — the same
    rule :func:`process_microscopy.find_image_groups_in_zip` applies when it
    builds the groups, so a group never names a member this reader resolves
    differently.
    """

    def __init__(self, zip_path: Path) -> None:
        self.zip_path = Path(zip_path)
        self._local = threading.local()
        self._handles: list[zipfile.ZipFile] = []
        self._lock = threading.Lock()
        self._closed = False

    def _handle(self) -> tuple[zipfile.ZipFile, dict[str, zipfile.ZipInfo]]:
        """This thread's open archive and its basename index, opened once."""
        zf = getattr(self._local, "zf", None)
        if zf is None:
            with self._lock:
                if self._closed:
                    raise ValueError(f"reader for {self.zip_path.name} is closed")
                zf = zipfile.ZipFile(self.zip_path, "r")
                self._handles.append(zf)
            index: dict[str, zipfile.ZipInfo] = {}
            for info in zf.infolist():
                base = Path(info.filename).name
                if base and not info.is_dir() and base not in index:
                    index[base] = info
            self._local.zf = zf
            self._local.index = index
        return zf, self._local.index

    def read(self, name: str) -> bytes:
        """The bytes of the member whose basename is *name*.

        Raises ``KeyError`` naming the archive when no member has that name. A
        member that fails its CRC raises ``zipfile.BadZipFile``, as unpacking it
        would have.
        """
        zf, index = self._handle()
        info = index.get(Path(name).name)
        if info is None:
            raise KeyError(f"{name} is not in {self.zip_path.name}")
        return zf.read(info)

    def open(self, path: Path | str) -> io.BytesIO:
        """The member named by *path*'s basename, as an in-memory file."""
        return io.BytesIO(self.read(Path(path).name))

    def close(self) -> None:
        """Close every handle any thread opened. Idempotent."""
        with self._lock:
            self._closed = True
            handles, self._handles = self._handles, []
        for zf in handles:
            zf.close()

    def __enter__(self) -> ZipMemberReader:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
