"""The list of datasets this user opened recently, and the rules that shape it.

**Why the list is not in the dataset folder.** A list *about* many datasets
cannot live inside one of them, and a dataset copied to a colleague must not
carry the author's history of other plates. So it lives in ``QSettings``,
beside the window geometry the shell already keeps there.

**Qt-free, deliberately.** Every function here takes the settings object as an
argument rather than constructing one, so the whole module is exercised in the
fast lane against a plain dict-backed stub. The alternative — reaching for
``QSettings("AllWell", "AllWellApp")`` inside these functions — would make every
test either skip or write to the developer's real configuration file, and a test
that mutates the machine it runs on is not a test anyone will run twice.

**The display name is stored, not re-derived.** ``dataset_display_name`` decides
between a flat dataset's own folder name and an in/out layout's root by asking
whether an ``in`` sibling exists, which is a second filesystem call per entry on
top of the reachability check the view already makes. Recording it once, at the
moment the dataset actually opened, halves that and is the more truthful answer
besides: it says what the folder was called *then*.

Note what this does **not** buy, because the first version of this paragraph
claimed it did: the paint still stats once per row, so an unmounted network
mount can still block it. Halving the stats is not removing them, and if that
blocking ever becomes real the fix is to move the check off the paint, not to
store more.

**Nothing here checks whether a path still exists.** Reachability is asked at
the moment the rows are drawn, by ``home.controller._recent_entry_rows``, and
the entry is shown either way — a folder that is not there is drawn disabled
with the reason, never hidden.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

_logger = logging.getLogger("well_viewer")

#: The ``QSettings`` key. Namespaced so it cannot collide with the geometry and
#: remote-profile keys already under the same organisation/application pair.
SETTINGS_KEY = "recent_datasets/v1"

#: How many entries are kept. Ten is what a card of tiles can show without
#: scrolling and rather more than the number of plates anyone holds in mind.
MAX_RECENTS = 10


@dataclass(frozen=True)
class RecentDataset:
    """One remembered dataset.

    *path* is the directory Review was pointed at — the ``out`` half for an
    in/out layout, since that is what reopening it must pass back. *name* is
    what to show. *opened_at* is a Unix timestamp, kept so the list can be
    ordered and a tooltip can say when, and it is the *last* open rather than
    the first.
    """

    path: str
    name: str
    opened_at: float


def decode(raw) -> list[RecentDataset]:
    """Parse what came out of ``QSettings``. Anything unreadable is an empty list.

    A settings file is user-writable, survives across versions, and is the one
    input here that arrives without a schema. Every failure mode — absent, not a
    string, not JSON, JSON that is not a list, an entry missing a field, a
    timestamp that is not a number — means the same thing to the caller: there
    is no history to offer. Raising instead would take the whole window down at
    startup over a file the user can simply be given a fresh one of.

    Entries that are individually malformed are dropped and the rest kept, so
    one bad row does not erase nine good ones.
    """
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError) as exc:
        _logger.debug("recent-datasets setting is not readable JSON (%s); "
                      "starting from an empty list", exc)
        return []
    if not isinstance(parsed, list):
        return []
    out: list[RecentDataset] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        name = item.get("name")
        opened_at = item.get("opened_at")
        if not isinstance(path, str) or not path:
            continue
        if not isinstance(name, str):
            name = ""
        if not isinstance(opened_at, (int, float)) or isinstance(opened_at, bool):
            opened_at = 0.0
        out.append(RecentDataset(path=path, name=name, opened_at=float(opened_at)))
    return out


def encode(entries: list[RecentDataset]) -> str:
    """The JSON string to store. The inverse of :func:`decode`."""
    return json.dumps([
        {"path": e.path, "name": e.name, "opened_at": e.opened_at}
        for e in entries
    ])


def _resolved(path) -> str:
    """*path* as the key two entries are considered the same by.

    ``Path.resolve()`` and not ``os.path.abspath``: the two spellings that
    reach here for one dataset are a relative path typed on the command line
    and an absolute one chosen in the file dialog, and on macOS the dialog's
    answer goes through ``/private/var`` where the shell's goes through
    ``/var``. Only ``resolve`` collapses the symlink.

    ``strict=False`` — an entry whose drive is not mounted still has to compare
    equal to itself, or reopening it once it *is* mounted would leave two rows
    naming one folder.
    """
    try:
        return str(Path(path).expanduser().resolve(strict=False))
    except (OSError, ValueError, RuntimeError):
        return str(path)


def remember(entries: list[RecentDataset], path, name: str,
             opened_at: float) -> list[RecentDataset]:
    """*entries* with *path* moved to the front. Pure; returns a new list.

    Deduplicated by resolved path rather than by the string given, so opening
    the same folder by two spellings does not fill the list with one dataset.
    The **new** entry's name and timestamp win, because a folder that has been
    renamed should be offered under the name it has now.

    **The resolved path is what gets stored**, not the spelling that was typed.
    ``--data_dir ../plate`` is a real way to open a dataset, and a relative path
    means a different folder from a different working directory — which a
    launched ``.app`` bundle has. Stored raw, that row is re-resolved on the
    next launch and either draws disabled under a reason that is not true, or,
    if some other ``../plate`` exists, draws **enabled** under the old dataset's
    name and opens a different plate. Resolving once, here, is the same value
    the dedupe already computed.

    Capped at :data:`MAX_RECENTS` from the front, so the entry just opened can
    never be the one dropped.
    """
    key = _resolved(path)
    kept = [e for e in entries if _resolved(e.path) != key]
    fresh = RecentDataset(path=key, name=name, opened_at=float(opened_at))
    return [fresh, *kept][:MAX_RECENTS]


def load(settings) -> list[RecentDataset]:
    """Read the stored list. *settings* is anything with ``value(key, default)``."""
    return decode(settings.value(SETTINGS_KEY, ""))


def store(settings, entries: list[RecentDataset]) -> None:
    """Write *entries*. *settings* is anything with ``setValue(key, value)``."""
    settings.setValue(SETTINGS_KEY, encode(entries))


def record_open(settings, path, name: str, opened_at: float) -> list[RecentDataset]:
    """Read, :func:`remember`, write. Returns the new list.

    The whole of what a successful load has to do, in one call, so the load
    controller carries no knowledge of the storage shape.
    """
    entries = remember(load(settings), path, name, opened_at)
    store(settings, entries)
    return entries
