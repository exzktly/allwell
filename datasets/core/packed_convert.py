"""What every packed-file converter does the same way, above the pipeline.

Three Datasets tools now turn one packed acquisition file into the per-well
``.zip`` archives the pipeline reads — *Convert OME-TIFF*, *Convert CZI* and
*Convert ND2*. :mod:`pipeline.explode_common` already holds the half of that
they share *below* the Qt line: the member names, the resume check, the worker
count, the timepoint plan, the elapsed-hour rendering. This module holds the
half above it, which had been copied twice and was about to be copied a third
time.

**Measured before it was extracted** (CLAUDE.md §7.5 #9). Hashing each
method's normalised AST across the two existing controllers put eight of
eleven byte-identical — ``_on_run``, ``_on_queue_requested``,
``_on_succeeded``, ``_on_failed``, ``_on_finished``,
``_on_open_in_analyze_requested``, ``start_from_queue`` and ``__init__`` —
with only ``_launch``, ``collect_spec`` and ``show_job`` diverging, and those
three only in which worker class, which kind string and which params dataclass
they name. The two workers differ in one method, and that one only in which
read/plan pair it calls and which exceptions count as a refusal rather than a
crash.

**Why this is not in** :class:`~datasets.core.tool_controller.DatasetToolController`.
That base is for all seven Datasets tools, and its own docstring records the
measurement that keeps these methods out of it: across the seven,
``_on_queue_requested`` has seven distinct bodies and ``_on_finished`` five.
Across the *converters* they are one body each. So this sits between the two,
the same way :class:`~datasets.core.tool_controller.DestinationToolController`
does for the copy-into-a-destination tools — a base for a subset that really
agrees, rather than a base full of flags for a set that does not.

A converter is therefore a :class:`ConverterSpec` plus a page: everything that
differs is data, and a fourth format would be a spec and a view rather than a
fourth copy of a controller.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import MISSING, asdict, dataclass, field, fields
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from core.safe import swallowed as gui_swallowed
from datasets.core.tool_controller import DatasetToolController
from util.aw_safe import swallowed


@dataclass(frozen=True)
class ConverterSpec:
    """Everything that distinguishes one packed-file converter from another.

    Data rather than subclass hooks on purpose: a reader can see the whole of
    what makes the ND2 converter different from the CZI one by reading one
    object, where a set of overridden methods would have to be collected from
    three files first.
    """

    #: The Job Queue's name for this tool — also its key in the tab's
    #: controller map, so it must match the ``kind`` its ``_Feature`` declares.
    kind: str
    #: Named in the DEBUG breadcrumb when a swallowed failure is logged.
    label: str
    #: What the file is called in the one line the worker logs before reading.
    noun: str
    #: The settings dataclass this converter's page produces.
    params_cls: type
    #: ``(src, params) -> source``. Whatever :attr:`plan` takes — a layout for
    #: one file, a set for a converter that reads a folder as one plate.
    read_source: Callable
    #: ``(source, params) -> plan``. Raises one of :attr:`refusals` when the
    #: settings cannot produce a dataset; called before anything is written.
    plan: Callable
    #: ``datasets.<tool>.apply.convert``.
    convert: Callable
    #: Exceptions that are a *refusal* rather than a crash: the destination
    #: already holds a plate, the file and the grid disagree, the source is
    #: unsound. These are shown as themselves, because a traceback logged for
    #: an ordinary expected answer teaches a user to ignore tracebacks.
    refusals: tuple[type, ...]
    #: The exploder's cancellation exception.
    cancelled: type
    #: Extra keyword arguments :attr:`read_source` needs. Empty for all three
    #: converters today; here so a reader does not have to wonder whether the
    #: uniform two-argument call is a coincidence.
    read_kwargs: dict = field(default_factory=dict)


CANCELLED_MESSAGE = (
    "Cancelled — the wells already converted were left in the destination; "
    "re-running with the same settings finishes the rest rather than "
    "repeating them."
)


class PackedConverterWorker(QThread):
    """Plans and executes one conversion, streaming progress to the UI.

    Planning and execution both run inside :meth:`run`, and their progress,
    result and errors are marshalled back through queued signals — the only
    safe way to reach the UI thread from a worker, which has no event loop of
    its own (CLAUDE.md §5). The pixel work stays in the Qt-free
    ``pipeline.*_explode`` and ``datasets.*.apply`` modules.
    """

    logLine = Signal(str)          # one line for the status window
    progress = Signal(int, int)    # (planes_written, total_planes)
    # (well, done, total) — one well's share of the conversion, so its cell on
    # the acquisition plate can fill as a dial rather than the page having only
    # a single run-wide bar. Quantised in each tool's ``apply.convert``.
    wellFraction = Signal(str, int, int)
    succeeded = Signal(str, str)   # (destination root, summary line)
    failed = Signal(str)           # user-facing error message

    def __init__(self, spec: ConverterSpec, src: str, dest: str, params,
                 parent=None) -> None:
        super().__init__(parent)
        self._spec = spec
        self._src = src
        self._dest = dest
        self._params = params

    def run(self) -> None:  # noqa: D401 — QThread entry point
        spec = self._spec
        try:
            self.logLine.emit(f"Reading {spec.noun} metadata…")
            source = spec.read_source(self._src, self._params,
                                      **spec.read_kwargs)
            spec.plan(source, self._params)     # validate before writing
        except spec.refusals as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # pragma: no cover — defensive
            swallowed(f"{spec.label} planning")
            self.failed.emit(f"Unexpected error while planning: {exc}")
            return

        try:
            summary = spec.convert(
                self._src, self._dest, self._params,
                log=self.logLine.emit,
                is_cancelled=self.isInterruptionRequested,
                progress=self.progress.emit,
                well_progress=self.wellFraction.emit,
            )
        except spec.cancelled:
            self.failed.emit(CANCELLED_MESSAGE)
        except spec.refusals as exc:
            # A refusal, not a crash — see ``ConverterSpec.refusals``.
            self.failed.emit(str(exc))
        except Exception as exc:  # pragma: no cover — defensive
            swallowed(f"{spec.label} convert")
            self.failed.emit(f"Conversion failed: {exc}")
        else:
            self.succeeded.emit(str(Path(self._dest)), summary)


class PackedConverterController(DatasetToolController):
    """Wires a converter page to its background worker.

    A subclass supplies a :class:`ConverterSpec` and builds its page; every
    method below is shared. Kept separate from the view so each page stays a
    pure Qt builder (CLAUDE.md §3).
    """

    #: Set by the subclass before ``super().__init__``.
    spec: ConverterSpec

    def __init__(
        self,
        *,
        on_open_in_analyze: Callable[[str, dict], None] | None = None,
        on_add_to_queue: Callable[[str, dict], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._shutdown_label = self.spec.label
        self._on_open_in_analyze = on_open_in_analyze
        self._on_add_to_queue = on_add_to_queue
        self._worker: PackedConverterWorker | None = None
        self._last_result: tuple[bool, str, str] = (False, "", "")

        self.page = self._build_page()
        self.page.runRequested.connect(self._on_run)
        self.page.queueRequested.connect(self._on_queue_requested)
        self.page.openInAnalyzeRequested.connect(self._on_open_in_analyze_requested)

    def _build_page(self):
        """The converter's page. One line in each subclass."""
        raise NotImplementedError

    def _teardown(self) -> None:
        """Drop any background source scan before the widgets go.

        A page that scans its source off the UI thread to draw a plate map
        must stop: a scan landing after the page has gone is a queued call
        into a deleted widget, which ends in a native crash with no Python
        traceback. Read defensively because the lifecycle guard
        (``tests/test_datasets_controller_lifecycle``) builds controllers with
        ``cls.__new__`` on purpose, to exercise stop/shutdown without the cost
        of a real Qt page — a teardown that assumed one turned that guard into
        an ``AttributeError``.
        """
        page = getattr(self, "page", None)
        stop = getattr(page, "stop_scanning", None)
        if stop is not None:
            stop()

    # ── run ──────────────────────────────────────────────────────────────────
    def _on_run(self) -> None:
        self._launch()

    def _launch(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        src, dest = self.page.source_path(), self.page.destination_path()
        if not src or not dest:
            missing = "source file" if not src else "destination folder"
            message = f"No {missing} selected."
            self.page.append_log(f"✗ {message}")
            self.page.set_outcome("failed", message)
            self._last_result = (False, message, "")
            # No worker gets started on this path, so ``_on_finished`` never
            # fires — emit directly, or a queued run would sit marked "running"
            # waiting for a ``jobFinished`` that was never coming, and the whole
            # queue would stop behind it.
            try:
                self.jobFinished.emit(False, message, "")
            except Exception:
                gui_swallowed()
            return
        self.page.clear_log()
        self.page.clear_outcome()
        # A fresh run starts on an empty plate — a second conversion must not
        # begin wearing the first one's dials.
        self.page.clear_well_fractions()
        self.page.set_progress(0, 0)
        self.page.set_running(True)

        worker = PackedConverterWorker(self.spec, src, dest, self.page.params())
        worker.logLine.connect(self.page.append_log)
        worker.progress.connect(self.page.set_progress)
        worker.wellFraction.connect(self.page.set_well_fraction)
        worker.succeeded.connect(self._on_succeeded)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        worker.start()

    # ── job queue ────────────────────────────────────────────────────────────
    def _tuple_fields(self) -> frozenset[str]:
        """Params this converter declares as a tuple.

        A queued job is JSON on disk, and JSON has no tuple: a
        ``tuple[str, ...]`` field comes back a list, and a dataclass restored
        from it holds a list where every comparison and every ``asdict``
        round-trip expects a tuple. The OME converter had this as two hand-written
        conversions in its own controller; doing it from the declared defaults
        instead means the next converter with a tuple field gets it right
        without knowing the problem exists.

        Read from the field's default rather than its annotation because every
        module here carries ``from __future__ import annotations``, so the
        annotation is the *string* ``"tuple[str, ...]"`` and matching on that
        would be a parser.
        """
        names = set()
        for f in fields(self.spec.params_cls):
            default = f.default
            if default is MISSING and f.default_factory is not MISSING:
                default = f.default_factory()
            if isinstance(default, tuple):
                names.add(f.name)
        return frozenset(names)

    def collect_spec(self) -> dict:
        params = asdict(self.page.params())
        for name in self._tuple_fields():
            if name in params:
                params[name] = list(params[name])
        return {
            "source": self.page.source_path(),
            "destination": self.page.destination_path(),
            "params": params,
        }

    def _on_queue_requested(self) -> None:
        if self._on_add_to_queue is not None:
            self._on_add_to_queue(self.spec.kind, self.collect_spec())

    def show_job(self, spec: dict) -> None:
        self.page.set_source_path(spec.get("source", ""))
        self.page.set_destination_path(spec.get("destination", ""))
        params = dict(spec.get("params") or {})
        if params:
            for name in self._tuple_fields():
                if name in params:
                    params[name] = tuple(params[name] or ())
            self.page.set_params(self.spec.params_cls(**params))

    def start_from_queue(self, spec: dict) -> None:
        self.show_job(spec)
        self._launch()

    # ── worker results ───────────────────────────────────────────────────────
    def _on_succeeded(self, dest_root: str, summary: str) -> None:
        self.page.append_log("")
        self.page.append_log(f"✓ {summary}")
        self.page.set_outcome("done", summary)
        self.page.show_open_in_analyze(dest_root)
        self._last_result = (True, summary, dest_root)

    def _on_failed(self, message: str) -> None:
        self.page.append_log("")
        self.page.append_log(f"✗ {message}")
        # On the page as well as in the log: the raw log is hidden by default,
        # so a log line on its own is a failure the user never sees. Same split
        # every other Datasets tool makes on its dashboard headline.
        self.page.set_outcome(
            "cancelled" if message.startswith("Cancelled") else "failed", message)
        self._last_result = (False, message, "")

    def _on_finished(self) -> None:
        self.page.set_running(False)
        self.page.refresh_readout()
        ok, message, dest_root = self._last_result
        try:
            self.jobFinished.emit(ok, message, dest_root)
        except Exception:
            gui_swallowed()

    def _on_open_in_analyze_requested(self, dest_root: str) -> None:
        if self._on_open_in_analyze is not None:
            # Hand the channel-role assignment across so Analyze's channel
            # tokens arrive pre-filled instead of retyped.
            self._on_open_in_analyze(dest_root, self.page.channel_tokens_dict())
