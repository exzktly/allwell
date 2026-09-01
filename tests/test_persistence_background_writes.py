"""The debounced autosaves do their disk I/O off the GUI thread.

The report was that editing the Home → Variables tab hung the window: renaming a
variable or ticking a value onto the plate froze the app for a second or two,
then recovered. The repaint was never the cost — a full Variables rebuild is
7.5 ms at 96 wells and 24 ms at 384. The cost was the autosave 500 ms later:
``update_json_atomic`` is a read-modify-verify-write, which is seven filesystem
round trips, and on the network share the dataset lives on each of those is a
hundred milliseconds or so. Measured with every filesystem operation slowed to
150 ms, one autosave took 1206 ms in 8 round trips on the GUI thread.

These tests pin the fix at the boundary that was actually wrong — *which thread
performs the file operations* — rather than on any value the save computes,
because every value it computes was already correct.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import types
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from well_viewer.domain.sample_variables import (
    Variable, VariableSet, VariableValue,
)
from well_viewer.persistence import _io, sample_variables
from well_viewer.persistence.registry import PersistenceRegistry


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    return QApplication.instance() or QApplication([])


class _Notify:
    """Stands in for ``ctx.notify``, recording what reached the two surfaces."""

    def __init__(self) -> None:
        self.status: list[str] = []
        self.toasts: list[str] = []
        self.threads: list[int] = []

    def set_status(self, message: str) -> None:
        self.status.append(message)
        self.threads.append(threading.get_ident())

    def toast(self, message: str, *, kind: str = "info", msec: int = 3500) -> None:
        self.toasts.append(message)


def _plate(tmp_path: Path) -> Path:
    """A dataset folder whose sidecar already carries the pipeline's own keys.

    Those keys matter: the whole reason this write is a read-modify-write is
    that ``pipeline_info.json`` has other writers, so a test that starts from an
    empty file cannot tell a merge from an overwrite.
    """
    info = tmp_path / "pipeline_info.json"
    info.write_text(json.dumps({"schema": "wells", "fluor_tokens": ["DAPI"]}))
    return info


def _vset(label: str) -> VariableSet:
    return VariableSet(
        variables=(Variable(id="v1", name=label, kind="categorical", unit="",
                            values=(VariableValue(id="c0", label="ctrl",
                                                  number=None),)),),
        assignments={"v1": {"A1": "c0", "B2": "c0"}},
    )


def _registry(tmp_path: Path, label: str = "Cell line"):
    notify = _Notify()
    ctx = types.SimpleNamespace(
        state=types.SimpleNamespace(
            dataset=types.SimpleNamespace(data_dir=str(tmp_path)),
            selection=types.SimpleNamespace(variables=_vset(label)),
        ),
        notify=notify,
    )
    reg = PersistenceRegistry(ctx)
    reg.register("sample_variables", save=sample_variables.save_to_data_dir)
    return reg, ctx, notify


def _written_name(info: Path) -> str | None:
    doc = json.loads(info.read_text())
    block = doc.get("variables") or {}
    variables = block.get("variables") or []
    return variables[0]["name"] if variables else None


# --------------------------------------------------------------------------


def test_the_debounced_save_does_no_file_io_on_the_gui_thread(tmp_path,
                                                              monkeypatch):
    """The one that reproduces the report.

    Asserted on the thread that performed the write, not on how long the call
    took: a timing threshold passes on a fast local disk whatever thread it ran
    on, which is exactly the machine this suite runs on.
    """
    info = _plate(tmp_path)
    reg, _ctx, _notify = _registry(tmp_path)

    wrote_on: list[int] = []
    real = _io._replace_with_text

    def _record(path, text, *, durable):
        wrote_on.append(threading.get_ident())
        return real(path, text, durable=durable)

    monkeypatch.setattr(_io, "_replace_with_text", _record)

    gui = threading.get_ident()
    stats: list[int] = []
    real_exists = Path.exists

    def _stat(self):
        if watching:
            stats.append(threading.get_ident())
        return real_exists(self)

    watching = True
    monkeypatch.setattr(Path, "exists", _stat)

    reg.schedule_save("sample_variables")
    reg._flush("sample_variables")          # the debounced path, verbatim
    assert wrote_on == [], (
        "the write happened during _flush, so it was still on the GUI thread"
    )
    assert stats == [], (
        f"_flush still stats the share on the GUI thread ({len(stats)} times) — "
        "resolving the sidecar's path has to move with the write"
    )
    watching = False
    reg.flush_all()

    assert wrote_on, "nothing was written at all"
    assert gui not in wrote_on, (
        f"the sidecar was written on the GUI thread ({wrote_on!r})"
    )
    assert _written_name(info) == "Cell line"
    assert json.loads(info.read_text())["fluor_tokens"] == ["DAPI"], (
        "the pipeline's own keys were not preserved through the deferred write"
    )


def test_flush_all_waits_for_the_writes_it_queued(tmp_path, monkeypatch):
    """``flush_all`` is called at quit and on a dataset swap; both need the
    bytes on disk before the next line runs, so a queue left in flight is the
    same lost edit it exists to prevent, one thread across."""
    info = _plate(tmp_path)
    reg, _ctx, _notify = _registry(tmp_path, label="Dose")

    real = _io._replace_with_text

    def _slow(path, text, *, durable):
        # Long enough that a flush_all which forgot to drain returns first.
        time.sleep(0.3)
        return real(path, text, durable=durable)

    monkeypatch.setattr(_io, "_replace_with_text", _slow)

    reg.schedule_save("sample_variables")
    reg.flush_all()

    assert _written_name(info) == "Dose", (
        "flush_all returned before the queued write had landed"
    )


def test_a_failed_background_write_reaches_the_status_line_and_the_log(
        tmp_path, monkeypatch, caplog):
    """A deferred write has no caller left to return False to.

    Without this the only signal that a filled 96-well grid was never written
    would be finding it gone next session — degraded output indistinguishable
    from good output.
    """
    _plate(tmp_path)
    reg, _ctx, notify = _registry(tmp_path)

    def _boom(path, text, *, durable):
        raise OSError("read-only file system")

    monkeypatch.setattr(_io, "_replace_with_text", _boom)

    with caplog.at_level(logging.WARNING, logger="well_viewer"):
        reg.schedule_save("sample_variables")
        reg.flush_all()
        QCoreApplication.processEvents()    # deliver the queued failure signal

    assert notify.status, "the failure never reached the status line"
    assert "sample_variables" in notify.status[-1]
    assert "could not be saved" in notify.status[-1]
    assert notify.toasts, "the failure never raised a toast"
    assert any("sample_variables" in r.getMessage() and r.levelno >= logging.WARNING
               for r in caplog.records), "the failure was not logged"


def test_the_failure_report_is_delivered_on_the_gui_thread(tmp_path,
                                                           monkeypatch):
    """``set_status`` writes a widget, and the failure is raised on the writer.

    What this pins is the delivery thread, not the connection type: a direct
    connection, or a writer that called a plain callback instead of emitting,
    would both paint the status label from the worker. (PySide6's default
    AutoConnection happens to resolve to a queued delivery here, because a
    bound method of a non-QObject receiver is dispatched against the sender's
    thread affinity — so the explicit flag in ``registry`` states the
    requirement rather than repairing it.)
    """
    _plate(tmp_path)
    reg, _ctx, notify = _registry(tmp_path)
    monkeypatch.setattr(
        _io, "_replace_with_text",
        lambda path, text, *, durable: (_ for _ in ()).throw(OSError("nope")))

    gui = threading.get_ident()
    reg.schedule_save("sample_variables")
    reg.flush_all()
    QCoreApplication.processEvents()

    assert notify.threads, "the failure never reached the status line"
    assert notify.threads == [gui] * len(notify.threads), (
        f"the status line was written from a worker thread: {notify.threads!r} "
        f"(GUI is {gui})"
    )


def test_an_undeferred_save_still_writes_inline_and_reports_its_own_failure(
        tmp_path, monkeypatch):
    """Only the debounced path is deferred. ``save(name)`` — the dataset-swap
    force-save, an explicit Save button, the smFISH threshold writer — must
    still write before it returns and still return a real answer."""
    info = _plate(tmp_path)
    reg, _ctx, _notify = _registry(tmp_path, label="RNA")

    wrote_on: list[int] = []
    real = _io._replace_with_text

    def _record(path, text, *, durable):
        wrote_on.append(threading.get_ident())
        return real(path, text, durable=durable)

    monkeypatch.setattr(_io, "_replace_with_text", _record)

    reg.save("sample_variables")

    assert wrote_on == [threading.get_ident()]
    assert _written_name(info) == "RNA"


def test_the_payload_is_snapshotted_when_the_write_is_queued(tmp_path):
    """``_doc.write`` hands ``atomic_write_json`` the *live* in-memory cache.

    Serialising on the calling thread is what makes that safe: a worker
    iterating that dict while the GUI thread edits it would write the later doc
    at best, and raise "dictionary changed size during iteration" at worst.
    """
    path = tmp_path / "persistence.json"
    jobs: list = []
    doc = {"line_order": ["A1", "A2"]}

    with _io.background_writes(jobs.append):
        _io.atomic_write_json(path, doc, deferrable=True)

    doc["line_order"].append("A3")          # the next edit, before the job runs
    doc["crop_region"] = {"x": 1}
    assert jobs, "the write was not queued"
    ok, detail = jobs[0]()
    assert ok, detail

    assert json.loads(path.read_text()) == {"line_order": ["A1", "A2"]}


def test_queued_writes_run_in_the_order_they_were_made(tmp_path):
    """One serialized worker, FIFO: the last edit the user made has to be the
    last one on disk. Two threads, or a pool, would let an earlier edit land
    last and silently win."""
    from well_viewer.persistence._writer import PersistenceWriter

    writer = PersistenceWriter()
    order: list[int] = []

    def _job(n: int):
        def run() -> tuple[bool, str]:
            time.sleep(0.02 if n == 0 else 0)   # the first one is the slow one
            order.append(n)
            return True, ""
        return run

    for n in range(5):
        writer.submit("line_order", _job(n))
    assert writer.drain(5.0)
    assert order == [0, 1, 2, 3, 4]


def test_nothing_is_deferred_outside_a_background_writes_block(tmp_path):
    """The sink is thread-local and entered only by ``_flush``. Everywhere else
    — including on the writer thread itself, which is what stops a job
    re-queueing itself for ever — ``deferrable=True`` writes inline."""
    path = tmp_path / "x.json"
    _io.atomic_write_json(path, {"a": 1}, deferrable=True)
    assert json.loads(path.read_text()) == {"a": 1}

    seen: list = []
    with _io.background_writes(seen.append):
        done = threading.Event()

        def _on_another_thread():
            _io.atomic_write_json(tmp_path / "y.json", {"b": 2},
                                  deferrable=True)
            done.set()

        threading.Thread(target=_on_another_thread).start()
        done.wait(5.0)

    assert seen == [], "a write on another thread was captured by the GUI sink"
    assert json.loads((tmp_path / "y.json").read_text()) == {"b": 2}
