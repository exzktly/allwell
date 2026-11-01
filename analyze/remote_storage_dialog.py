"""The Storage browser in Manage Remotes: what a host's staging directory holds.

Lists every folder under ``datasets/`` and ``runs/`` on one host — the project
it belongs to, its size, when it was first and last used, whether a run is
using it now, and when it expires — and lets the user delete folders, mark
them to keep, give them an expiry, and set the host-wide "remove after N days
untouched" rule. Everything that touches the host is
``services.remote_storage``; this module only lays it out.

Every remote call runs on a worker thread and answers through
:func:`analyze.remote_dialog.poll_queue_on`, whose timer is parented to the
dialog, so closing it mid-listing drops the answer instead of touching a
deleted widget. One call at a time: the action buttons are disabled while one
is in flight.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QSpinBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from analyze.remote_dialog import poll_queue_on
from services import remote_storage as rs
from services.remote_cache_gc import IN_USE_WINDOW_S
from services.remote_config import STORAGE_EXPIRY_DAYS_RANGE, RemoteHost, write_remote

_DAY = 86400
COLUMNS = ("Project", "Kind", "Folder", "Size", "Created", "Last used", "Expires", "Local output")
_COL_SIZE, _COL_CREATED, _COL_USED, _COL_EXPIRES = 3, 4, 5, 6
_SORT_ROLE = Qt.UserRole + 1


def human_bytes(n: int | None) -> str:
    """``1.4 GB`` — decimal units, one decimal place; ``?`` when not measured."""
    if n is None:
        return "?"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} TB"


def _ago(seconds: int) -> str:
    if seconds < 3600:
        return "just now" if seconds < 120 else f"{seconds // 60} min ago"
    if seconds < 2 * _DAY:
        return f"{seconds // 3600} h ago"
    return f"{seconds // _DAY} d ago"


def _date(epoch: int | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch)) if epoch else "—"


def expiry_text(item: rs.StorageItem, now: int, host_days: int) -> tuple[str, float]:
    """``(text, sort key)`` for the Expires column."""
    if item.active:
        return "In use", -1.0
    if item.expiry == rs.KEEP:
        return "Never (kept)", float("inf")
    due = rs.effective_expiry(item, host_days)
    if due is None:
        return "Never", float("inf")
    if due <= now:
        held = item.kind == "dataset" and now - item.last_used < IN_USE_WINDOW_S
        return ("Expired (held: used recently)" if held else "Expired"), float(due)
    days = max(1, (due - now + _DAY - 1) // _DAY)
    return f"in {days} d{'' if item.expiry else ' (host rule)'}", float(due)


def row_texts(item: rs.StorageItem, now: int, host_days: int) -> list[str]:
    """The cells of one folder's row, in :data:`COLUMNS` order."""
    return [
        item.project or "—",
        "Run" if item.kind == "run" else "Dataset",
        item.name,
        human_bytes(item.size),
        _date(item.created),
        _ago(max(0, now - item.last_used)) if item.last_used else "—",
        expiry_text(item, now, host_days)[0],
        item.output_dir or "—",
    ]


def _tooltip(item: rs.StorageItem, staging_dir: str) -> str:
    lines = [f"On the server: {item.path(staging_dir)}"]
    if not item.label:
        lines.append("No label: made before All-Well labelled its remote folders.")
    for key, title in (("input_dir", "Input"), ("output_dir", "Output"), ("client", "Run from")):
        if item.label.get(key):
            lines.append(f"{title}: {item.label[key]}")
    return "\n".join(lines)


class _Row(QTreeWidgetItem):
    """A row that sorts by each column's own key rather than by its text."""

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        mine, theirs = self.data(col, _SORT_ROLE), other.data(col, _SORT_ROLE)
        if mine is not None and theirs is not None:
            return mine < theirs
        return self.text(col).lower() < other.text(col).lower()


class RemoteStorageDialog(QDialog):
    """Browse and clean up one host's ``datasets/`` and ``runs/`` folders.

    Emits :attr:`host_changed` with the saved host when the host-wide expiry
    is changed, so Manage Remotes can re-read ``remotes.toml``.
    """

    host_changed = Signal(object)

    def __init__(self, parent: QWidget | None, host: RemoteHost, *, cache=None) -> None:
        super().__init__(parent)
        if cache is None:
            from services.remote_upload import DatasetCache
            cache = DatasetCache(host)
        self._host = host
        self._cache = cache
        self._listing: rs.Listing | None = None
        self._busy = False
        self.setWindowTitle(f"Remote Storage — {host.name}")
        self.setModal(True)
        self.resize(1000, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(8)

        top = QHBoxLayout()
        self._summary = QLabel(f"{host.ssh_alias}:{host.remote_staging_dir}", self)
        self._summary.setObjectName("Muted")
        self._summary.setWordWrap(True)
        top.addWidget(self._summary, 1)
        self._refresh_btn = QPushButton("Refresh", self)
        self._refresh_btn.clicked.connect(lambda: self.refresh())
        top.addWidget(self._refresh_btn)
        layout.addLayout(top)

        self._tree = QTreeWidget(self)
        self._tree.setColumnCount(len(COLUMNS))
        self._tree.setHeaderLabels(list(COLUMNS))
        self._tree.setRootIsDecorated(False)
        self._tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._tree.setSortingEnabled(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.header().setSectionResizeMode(QHeaderView.Interactive)
        self._tree.header().setStretchLastSection(True)
        self._tree.itemSelectionChanged.connect(self._refresh_buttons)
        layout.addWidget(self._tree, 1)

        actions = QHBoxLayout()
        self._delete_btn = QPushButton("Delete…", self)
        self._delete_btn.setProperty("variant", "danger")
        self._delete_btn.clicked.connect(self._on_delete)
        actions.addWidget(self._delete_btn)
        actions.addSpacing(12)
        self._keep_btn = QPushButton("Keep forever", self)
        self._keep_btn.setToolTip("Never remove the selected folders automatically.")
        self._keep_btn.clicked.connect(lambda: self._apply_expiry(rs.expiry_value(keep=True), "kept"))
        actions.addWidget(self._keep_btn)
        self._expire_btn = QPushButton("Expire in", self)
        self._expire_btn.setToolTip("Remove the selected folders automatically after this many days.")
        self._expire_btn.clicked.connect(self._on_expire)
        actions.addWidget(self._expire_btn)
        self._expire_days = QSpinBox(self)
        self._expire_days.setRange(1, STORAGE_EXPIRY_DAYS_RANGE[1])
        self._expire_days.setValue(14)
        self._expire_days.setSuffix(" days")
        actions.addWidget(self._expire_days)
        self._clear_btn = QPushButton("Use host rule", self)
        self._clear_btn.setToolTip("Drop the selected folders' own setting; the host-wide rule applies again.")
        self._clear_btn.clicked.connect(lambda: self._apply_expiry(rs.expiry_value(), "reset to the host rule"))
        actions.addWidget(self._clear_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        rule = QHBoxLayout()
        rule.addWidget(QLabel("Remove folders left untouched for", self))
        self._host_days = QSpinBox(self)
        self._host_days.setRange(*STORAGE_EXPIRY_DAYS_RANGE)
        self._host_days.setSpecialValueText("never")
        self._host_days.setSuffix(" days")
        self._host_days.setValue(host.storage_expiry_days)
        self._host_days.valueChanged.connect(self._refresh_buttons)
        rule.addWidget(self._host_days)
        self._save_rule_btn = QPushButton("Save", self)
        self._save_rule_btn.setToolTip(
            "Applied at the start of every run on this host. Folders in use, kept folders, and "
            f"datasets a run used in the last {IN_USE_WINDOW_S // _DAY} days are never removed.")
        self._save_rule_btn.clicked.connect(self._on_save_rule)
        rule.addWidget(self._save_rule_btn)
        self._sweep_btn = QPushButton("Remove expired now", self)
        self._sweep_btn.clicked.connect(self._on_sweep)
        rule.addWidget(self._sweep_btn)
        rule.addStretch(1)
        self._close_btn = QPushButton("Close", self)
        self._close_btn.clicked.connect(self.accept)
        rule.addWidget(self._close_btn)
        layout.addLayout(rule)

        self._status = QLabel("", self)
        self._status.setObjectName("Muted")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._refresh_buttons()
        self.refresh()

    # ── async plumbing ──────────────────────────────────────────────────

    def _run_async(self, work, on_done, busy_text: str) -> bool:
        """Run *work()* on a worker thread; call ``on_done(result, error)`` on the GUI thread."""
        if self._busy:
            return False
        self._busy = True
        self._status.setText(busy_text)
        self._refresh_buttons()
        result_q: queue.Queue = queue.Queue()

        def _worker() -> None:
            try:
                result_q.put((work(), ""))
            except Exception as exc:
                result_q.put((None, f"{type(exc).__name__}: {exc}"))

        def _finish(result, error: str) -> None:
            self._busy = False
            on_done(result, error)
            self._refresh_buttons()

        threading.Thread(target=_worker, daemon=True).start()
        poll_queue_on(self, result_q, _finish)
        return True

    # ── listing ─────────────────────────────────────────────────────────

    def refresh(self, message: str = "") -> None:
        """Re-list the host; *message* (the outcome of the step that asked) is shown once it answers."""
        self._run_async(lambda: rs.list_storage(self._cache, detail=True),
                        lambda result, error: self._on_listed(result, error, message),
                        f"{message + ' ' if message else ''}Reading the server's datasets and runs folders "
                        "(sizes take a while on a big cache)…")

    def _on_listed(self, result, error: str, message: str = "") -> None:
        listing, why = result if result else (None, error)
        if listing is None:
            self._status.setText(f"{message + ' ' if message else ''}Could not list "
                                 f"{self._host.remote_staging_dir}: {why or error}. Nothing shown is current.")
            return
        self._listing = listing
        self._populate()
        self._status.setText(message)

    def _populate(self) -> None:
        listing = self._listing
        if listing is None:
            return
        days = self._host_days.value()
        self._tree.setSortingEnabled(False)
        self._tree.clear()
        for item in listing.items:
            row = _Row(self._tree, row_texts(item, listing.now, days))
            row.setData(0, Qt.UserRole, item)
            row.setData(_COL_SIZE, _SORT_ROLE, item.size if item.size is not None else -1)
            row.setData(_COL_CREATED, _SORT_ROLE, item.created or 0)
            row.setData(_COL_USED, _SORT_ROLE, item.last_used)
            row.setData(_COL_EXPIRES, _SORT_ROLE, expiry_text(item, listing.now, days)[1])
            tip = _tooltip(item, self._host.remote_staging_dir)
            for col in range(len(COLUMNS)):
                row.setToolTip(col, tip)
        self._tree.setSortingEnabled(True)
        self._tree.sortByColumn(_COL_USED, Qt.AscendingOrder)
        for col in range(len(COLUMNS) - 1):
            self._tree.resizeColumnToContents(col)
        self._summary.setText(self.summary_text())

    def summary_text(self) -> str:
        listing = self._listing
        if listing is None:
            return ""
        runs = sum(1 for i in listing.items if i.kind == "run")
        used = sum(i.size or 0 for i in listing.items)
        free = f" · {human_bytes(listing.free_bytes)} free" if listing.free_bytes is not None else ""
        return (f"{self._host.ssh_alias}:{self._host.remote_staging_dir} — {runs} run folder(s), "
                f"{len(listing.items) - runs} dataset(s), {human_bytes(used)} in use{free}")

    # ── selection ───────────────────────────────────────────────────────

    def selected_items(self) -> list[rs.StorageItem]:
        return [row.data(0, Qt.UserRole) for row in self._tree.selectedItems()]

    def _refresh_buttons(self) -> None:
        has = bool(self._tree.selectedItems()) and not self._busy
        for btn in (self._delete_btn, self._keep_btn, self._expire_btn, self._clear_btn):
            btn.setEnabled(has)
        self._refresh_btn.setEnabled(not self._busy)
        self._sweep_btn.setEnabled(not self._busy and self._listing is not None)
        self._save_rule_btn.setEnabled(not self._busy and self._host_days.value() != self._host.storage_expiry_days)

    # ── delete ──────────────────────────────────────────────────────────

    def _confirm(self, title: str, text: str) -> bool:
        """Ask before an irreversible step. A seam: tests replace it."""
        return QMessageBox.question(self, title, text, QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) == QMessageBox.Yes

    def delete_prompt(self, items: list[rs.StorageItem]) -> str:
        """The confirmation text for deleting *items*: what goes, and what that costs."""
        total = sum(i.size or 0 for i in items)
        names = "\n".join(f"  • {rs.describe(i)}" for i in items[:12])
        more = f"\n  … and {len(items) - 12} more" if len(items) > 12 else ""
        warn = []
        now = self._listing.now if self._listing else int(time.time())
        if any(i.kind == "dataset" and now - i.last_used < IN_USE_WINDOW_S for i in items):
            warn.append("A dataset here was used in the last "
                        f"{IN_USE_WINDOW_S // _DAY} days; the next run of that plate uploads it again.")
        if any(i.kind == "run" for i in items):
            warn.append("Results already downloaded are not affected, but anything a run left unfetched "
                        "is lost, and a later resume of that plate starts over.")
        return (f"Permanently delete {len(items)} folder(s), {human_bytes(total)}, from "
                f"{self._host.ssh_alias}?\n\n{names}{more}\n\n" + "\n".join(warn)
                + "\n\nFolders found in use at the moment of deletion are left alone. This cannot be undone.")

    def _on_delete(self) -> None:
        chosen = self.selected_items()
        active = [i for i in chosen if i.active]
        items = [i for i in chosen if not i.active]
        if not items:
            self._status.setText("Every selected folder is in use by a run; nothing to delete.")
            return
        if not self._confirm("Delete remote folders", self.delete_prompt(items)):
            return
        skipped = len(active)
        self._run_async(lambda: rs.delete_items(self._cache, items),
                        lambda result, error: self._on_outcomes(result, error, "deleted", skipped),
                        f"Deleting {len(items)} folder(s)…")

    def _on_outcomes(self, result, error: str, verb: str, skipped: int = 0) -> None:
        outcomes, why = result if result else ({}, error)
        done = sum(1 for s in outcomes.values() if s in ("gone", "ok"))
        busy = sum(1 for s in outcomes.values() if s == "busy") + skipped
        failed = [f"{k} {n}" for (k, n), s in outcomes.items() if s not in ("gone", "ok", "busy")]
        parts = [f"{done} folder(s) {verb}."]
        if busy:
            parts.append(f"{busy} in use and left alone.")
        if failed:
            parts.append(f"Failed: {', '.join(failed[:6])}{' …' if len(failed) > 6 else ''}.")
        if why:
            parts.append(f"({why})")
        self.refresh(" ".join(parts))

    # ── expiry ──────────────────────────────────────────────────────────

    def _on_expire(self) -> None:
        now = self._listing.now if self._listing else None
        days = self._expire_days.value()
        self._apply_expiry(rs.expiry_value(days=days, now=now), f"set to expire in {days} days")

    def _apply_expiry(self, value: str, verb: str) -> None:
        items = self.selected_items()
        if items:
            self._run_async(lambda: rs.set_expiry(self._cache, items, value),
                            lambda result, error: self._on_outcomes(result, error, verb),
                            f"Updating {len(items)} folder(s)…")

    def _on_save_rule(self) -> None:
        new_host = replace(self._host, storage_expiry_days=self._host_days.value())
        try:
            write_remote(new_host)
        except OSError as exc:
            self._status.setText(f"Could not write remotes.toml: {exc}")
            return
        self._host = new_host
        self._cache.host = new_host
        self.host_changed.emit(new_host)
        self._populate()
        rule = (f"folders untouched for {new_host.storage_expiry_days} days are removed at the start of each run"
                if new_host.storage_expiry_days else "nothing is removed automatically")
        self._status.setText(f"Saved: {rule}.")
        self._refresh_buttons()

    def _on_sweep(self) -> None:
        listing = self._listing
        if listing is None:
            return
        days = self._host_days.value()
        due = rs.plan_expired(listing.items, listing.now, days)
        if not due:
            self._status.setText("Nothing has expired.")
            return
        if not self._confirm("Remove expired folders", self.delete_prompt(due)):
            return
        lines: list[str] = []

        def work():
            return rs.remove_expired(self._cache, listing, log=lines.append, host_days=days)

        def done(_result, error: str) -> None:
            self.refresh(" ".join(line.strip().removeprefix("[remote] storage: ") for line in lines)
                         or error or "Nothing removed.")

        self._run_async(work, done, f"Removing {len(due)} expired folder(s)…")
