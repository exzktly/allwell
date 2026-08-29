"""The one spelling of "decode this subprocess's output as UTF-8".

Every tool this app drives — ssh, rsync, ssh-keygen, the remote Linux
pipeline, the bootstrap provisioner — writes UTF-8. ``text=True`` on its own
does not say that: with no ``encoding`` the pipe is decoded with
``locale.getpreferredencoding(False)``, which is UTF-8 on macOS and Linux but
the ANSI codepage (cp1252 for a Western install) on Windows. So a stream that
reads correctly on the developer's Mac either mojibakes or raises
``UnicodeDecodeError`` out of the reader on a user's Windows box, and the
bytes it fails on are the ones the app depends on: the ``✓`` / ``→`` / ``…``
in the remote's progress markers, the em-dash in ``Well X — done.``, and any
non-ASCII byte a filename or a remote path happens to carry.

``errors="replace"`` is the second half. Strict decoding turns one such byte
into an exception raised from inside the reader's iterator, which skips the
``proc.wait()`` that follows it — the run is then reported as never having
started while the process keeps working on the user's plate. A ``?`` in a log
line is the better failure.

Splat it into ``subprocess.run`` / ``subprocess.Popen`` wherever text-mode
output is read::

    subprocess.run(cmd, capture_output=True, timeout=5, **TEXT_UTF8)

``tests/test_subprocess_text_decoding.py`` fails on any text-mode call in
``services/`` or the Analyze modules that decodes with the locale instead.
"""

from __future__ import annotations

from typing import Any

#: ``text=True`` plus the explicit codec it should always have carried.
TEXT_UTF8: dict[str, Any] = {
    "text": True,
    "encoding": "utf-8",
    "errors": "replace",
}
