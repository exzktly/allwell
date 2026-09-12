"""The recent-datasets list as three surfaces draw it.

``core/recent_datasets`` is the storage and its rules — pure, Qt-free, settings
injected. This is the layer above it: reading the list through the shell's own
settings handle, deciding what each row should say and whether it can be
opened, and performing the open and the forget.

**Why this is in ``core`` and not on the Home feature.** Three surfaces show
this list and two of them are the shell's own: the rail head's dataset menu
(``app_shell_view._dataset_button``), Home's *Recent datasets* card, and the
no-dataset placeholder Home puts the same rows on. ``core/load_controller``
already writes the list. With the display rules living inside
``features/home``, the shell could only reach them through a feature service
seam or an import it is not allowed to make — so the rules moved to the layer
that already owns both ends, and Home reads them from here like anyone else.

**Nothing is cached.** Every call re-reads ``QSettings`` and re-stats the
folders. The list is at most :data:`~well_viewer.core.recent_datasets.MAX_RECENTS`
long, the menu builds its rows when it is about to be shown, and a cache would
have to be invalidated by a load, by a forget and by the other surface's forget
— three writers for ten rows.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import QTimer

from core.safe import swallow

_logger = logging.getLogger("well_viewer")


def recent_datasets(ctx) -> list:
    """The remembered datasets, newest first, as ``RecentDataset`` records.

    Reads ``QSettings`` through the shell's own handle rather than constructing
    a second one, so this cannot drift from where the window geometry and the
    remote profiles are kept. An unreadable setting is an empty list — see
    ``core.recent_datasets.decode`` — so this never raises into a page build.

    **Nothing here touches the filesystem.** Whether a remembered folder still
    exists is asked at the moment the rows are built, by :func:`entry_rows`,
    because the commonest reason one has vanished is an external drive that is
    not mounted and a stat against an absent network mount can block for
    seconds. A page build that walked five of those would hang the launch it is
    meant to speed up.
    """
    from core.app_settings import app_settings
    from well_viewer.core.recent_datasets import load

    # A module function, not ``ctx.window._qsettings``: ``ctx.window`` is the
    # review widget and that method belongs to the main window containing it,
    # so the guarded ``getattr`` spelling read ``None`` in the shipped app and
    # this list was empty on every launch while its tests passed.
    try:
        return load(app_settings())
    except Exception:
        _logger.warning("could not read the recent-datasets list; there will "
                        "be no history to offer this session.", exc_info=True)
        return []


def is_the_open_dataset(entry_path, data_dir) -> bool:
    """Whether *entry_path* is the dataset ``data_dir`` was loaded from.

    Not a string comparison: what the list stores is the folder Review was
    *pointed at*, while ``data_dir`` is the folder it ended up reading — and for
    the standard in/out layout those differ by one level, because ``load_path``
    descends into ``out``. Comparing them directly would leave the open dataset
    on its own page's "open something else" list, which is the one row on it
    that can do nothing.
    """
    if not data_dir:
        return False
    here = Path(data_dir)
    entry = Path(entry_path)
    return entry == here or (here.name == "out" and entry == here.parent)


def opened_ago(opened_at: float) -> str:
    """``"\\nLast opened 3 days ago"``, or ``""`` when there is no timestamp.

    What tells two rows apart when someone has three plates from one experiment
    open in rotation and the folder names differ by a digit. Coarse on purpose:
    the useful distinction is today / this week / a while back, and a precise
    clock time invites the reader to work out whether it was before or after
    something else, which this cannot answer.
    """
    if not opened_at:
        return ""
    seconds = max(0.0, time.time() - float(opened_at))
    minutes = seconds / 60
    if minutes < 1:
        return "\nLast opened just now"
    if minutes < 60:
        count, unit = int(minutes), "minute"
    elif minutes < 60 * 24:
        count, unit = int(minutes // 60), "hour"
    else:
        count, unit = int(minutes // (60 * 24)), "day"
    return f"\nLast opened {count} {unit}{'' if count == 1 else 's'} ago"


#: What a row that cannot be opened says, and why it is still drawn.
UNREACHABLE_NOTE = (
    "is not reachable right now — the folder has moved or its drive is not "
    "mounted. Removing it from the list is the × on the row.")


def entry_rows(ctx, limit: int, *, exclude=None, on_removed=None) -> list:
    """``(label, sublabel, on_click, enabled, tooltip, on_remove)`` per row.

    One function for every surface that draws this list, so the card, the
    placeholder and the rail head's menu cannot disagree about which folders are
    reachable or what a row is called.

    **A folder that is not there is shown, disabled, with the reason.** Hiding
    it would tell the user their history had been lost, when what actually
    happened is that a drive is unplugged — and the row coming back by itself
    when the drive returns is the behaviour that makes the list trustworthy.

    *exclude* drops the dataset already open, for the card that sits inside it.

    *on_removed* is what to run **after** a row has been forgotten — repainting
    whatever drew it. A caller that passes none gets ``on_remove=None`` on every
    row and therefore no remove affordance, which is what the rail head's menu
    wants: a ``QMenu`` item is a line of text with nothing to hover a second
    control over. Note that a row can be removable while it is *not* openable:
    an unreachable folder is exactly the row a user most wants off the list, so
    the remove callback is set from *on_removed* alone and never gated on
    ``enabled``.
    """
    rows = []
    entries = [e for e in recent_datasets(ctx)
               if not is_the_open_dataset(e.path, exclude)]
    for entry in entries[:limit]:
        path = Path(entry.path)
        exists = False
        with swallow(f"check whether {path} is still reachable"):
            exists = path.is_dir()
        parent = str(path.parent) if path.parent != path else ""
        when = opened_ago(entry.opened_at)
        rows.append((
            entry.name or path.name or entry.path,
            parent,
            (lambda _p=path: open_recent(ctx, _p)) if exists else None,
            exists,
            f"{path}{when}" if exists else f"{path} {UNREACHABLE_NOTE}",
            (lambda _p=path: forget_recent(ctx, _p, then=on_removed))
            if callable(on_removed) else None,
        ))
    return rows


def open_recent(ctx, path) -> None:
    """Open a remembered dataset, by the route a browsed one takes.

    ``_load_path`` and the same deferral the folder picker uses, so a recent
    open and a browsed open cannot diverge — the deferral is what lets the
    window repaint before a load that may take seconds, and a direct call here
    would make the two paths feel different for no reason anyone could name.
    """
    load = getattr(ctx.window, "_load_path", None)
    if not callable(load):
        return
    QTimer.singleShot(50, lambda: load(Path(path)))


def forget_recent(ctx, path, *, then=None) -> None:
    """Drop *path* from the history, then run *then*.

    The write and the repaint are one call because they are one action to the
    user: a row whose × leaves it on screen until something else redraws the
    page is indistinguishable from a button that did nothing, which is the
    failure this codebase keeps finding in controls that *exist* without
    *working*.

    A settings write that fails is logged and the repaint still runs, so the
    row comes back rather than vanishing from a list it is still stored in.
    """
    from core.app_settings import app_settings
    from well_viewer.core.recent_datasets import record_forget

    try:
        record_forget(app_settings(), path)
    except Exception:
        _logger.warning("could not remove %s from the recent-datasets list.",
                        path, exc_info=True)
    if callable(then):
        then()
