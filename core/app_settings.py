"""Where this application's per-user settings live, in one place.

``QSettings("AllWell", "AllWellApp")`` was constructed at five call sites across
three modules, which is five places that have to agree on an organisation and an
application name for the window geometry, the remote-host profiles and the
recent-datasets list to end up in the same file.

That duplication was not the expensive part. The expensive part was that
reaching the handle meant reaching for ``_qsettings`` on *some* object, and the
object a feature has is not the one that defines it: ``ctx.window`` is the
``ReviewApp`` review widget, while ``_qsettings`` is a method of the
``AllWellApp`` main window that *contains* it, with no forwarding between them.
A ``getattr(ctx.window, "_qsettings", None)`` therefore reads ``None`` in the
shipped application and every value silently reads as absent — which is what
happened to the recent-datasets list: it never wrote and never read, while its
tests passed by monkeypatching the missing attribute into existence.

So this is a module-level function and not a method on anything. There is no
object to have the wrong one of.
"""

from __future__ import annotations

#: The organisation and application names Qt keys the settings file on. Changing
#: either orphans every setting every user has — the file is found by this pair,
#: not by a path — so they are named here rather than repeated as literals.
ORGANISATION = "AllWell"
APPLICATION = "AllWellApp"


def app_settings():
    """This user's ``QSettings`` for All-Well.

    Cheap to construct: ``QSettings`` is a handle onto a cached backing store,
    not a file read, so callers make one where they need it rather than holding
    one. Imported inside the function so this module stays importable without
    Qt, which is what lets the settings-backed stores under ``core/`` be
    exercised in the fast lane against a plain stub.
    """
    from PySide6.QtCore import QSettings

    return QSettings(ORGANISATION, APPLICATION)
