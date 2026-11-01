"""The Analyze log pane's text, written to the run's output folder as the run goes.

The pane is the only place a run's account of itself ends up — the pipeline's own lines, the remote host's, the
transfers', and the app's notes about all three — and it keeps the last 10,000 lines on screen and nothing once the
window closes. A run whose results look wrong a day later (a nanowell plate with no confluence anywhere, say) then
has nothing to read but the files it wrote. So every line the pane is given is also appended to
``<output>/logs/analyze-<YYYYmmdd-HHMMSS>.log``, one file per run, verbatim and in order.

The output folder is not known when the run starts: the runner resolves it on its own thread
(``services.pipeline_runner.PipelineRunner.last_output_dir``, reset to ``None`` when a run starts). Until
:meth:`RunLogFile.runner_started` the folder the runner names is the *previous* run's, so it is not looked at; lines
given before the folder is known are held and written first once it is. A run that never reaches an output folder — refused, or failed
while resolving its input — writes nothing. Measured on a synthetic nanowell well, the pane says about 2 KB per well
per timepoint: 2 MB for 96 wells at 10 timepoints.

Writing is best-effort and never costs the pane a line: a folder that cannot be written to is reported once, through
the value :meth:`RunLogFile.write` returns, and the run carries on. Qt-free, so it is tested without a window.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Callable, Iterable
from pathlib import Path

from core.safe import swallow

LOG_DIR = "logs"
PREFIX = "analyze-"
#: Lines held before the output folder is known. A run that resolves its input prints a few dozen; the bound only
#: stops a run that never resolves from growing without limit.
MAX_HELD = 5000


class RunLogFile:
    """One run's log file. :meth:`begin` when a run starts, :meth:`write` with every batch the pane is given,
    :meth:`end` when it finishes."""

    def __init__(self, clock: Callable[[], _dt.datetime] = _dt.datetime.now) -> None:
        self._clock = clock
        self._active = False
        self._held: list[str] = []
        self._dropped = 0
        self._fh = None
        self._failed = False
        self.path: Path | None = None
        self._started: _dt.datetime | None = None
        self._follows_runner = False

    def begin(self) -> None:
        """A run is starting: forget the last one's file and hold lines until the output folder is known."""
        self.end()
        self._active, self._held, self._dropped, self._failed = True, [], 0, False
        self.path, self._started = None, self._clock()
        self._follows_runner = False

    def runner_started(self) -> None:
        """The runner has taken this run (``PipelineRunner.start`` returned): the output folder it names from now on,
        ``None`` until resolved, is this run's. Before this it is the last run's, and the notes logged before the
        runner starts would have gone into that run's folder."""
        self._follows_runner = True

    def write(self, lines: Iterable[tuple[str, str]], output_dir: Path | None) -> str | None:
        """Append *lines* (the pane's ``(text, tag)`` pairs). *output_dir* is the folder the runner names, ``None``
        while it is not yet known; it is taken as this run's only after :meth:`runner_started`. Returns a sentence to show once when the file cannot be written, else ``None``."""
        if not self._active or self._failed:
            return None
        texts = [text if text.endswith("\n") else text + "\n" for text, _tag in lines]
        if not self._follows_runner:
            output_dir = None
        if self._fh is None:
            if output_dir is None:
                room = MAX_HELD - len(self._held)
                self._held.extend(texts[:max(room, 0)])
                self._dropped += max(len(texts) - max(room, 0), 0)
                return None
            problem = self._open(Path(output_dir))
            if problem is not None:
                return problem
            texts = self._held + texts
            if self._dropped:
                texts.insert(len(self._held), f"[run log] {self._dropped} line(s) before the output folder was "
                                              "known were not kept.\n")
            self._held = []
        try:
            self._fh.write("".join(texts))
            self._fh.flush()
        except OSError as exc:
            return self._fail(f"writing {self.path}", exc)
        return None

    def end(self) -> None:
        """The run has finished: close the file. Lines after this are not written."""
        self._active = False
        self._held = []
        if self._fh is not None:
            # The content was flushed batch by batch, so a failed close loses nothing written.
            with swallow("close the run's log file"):
                self._fh.close()
            self._fh = None

    def _open(self, output_dir: Path) -> str | None:
        stamp = (self._started or self._clock()).strftime("%Y%m%d-%H%M%S")
        folder = output_dir / LOG_DIR
        path = folder / f"{PREFIX}{stamp}.log"
        n = 1
        while path.exists():            # two runs started in one second, into one folder
            n += 1
            path = folder / f"{PREFIX}{stamp}-{n}.log"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            self._fh = open(path, "a", encoding="utf-8", errors="replace")
        except OSError as exc:
            return self._fail(f"creating {path}", exc)
        self.path = path
        return None

    def _fail(self, what: str, exc: OSError) -> str:
        self._failed = True
        if self._fh is not None:
            with swallow("close the run's log file after a failed write"):
                self._fh.close()
            self._fh = None
        return f"[warn] This run's log is not being saved to the output folder ({what} failed: {exc}).\n"
