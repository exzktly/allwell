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

import logging
import os
from pathlib import Path

from core.safe import swallow
from util.state_dir import SETTINGS_NAME, STATE_DIR_ENV, app_file

_LOG = logging.getLogger(__name__)

#: The organisation and application names Qt keys the settings file on. Changing
#: either orphans every setting every user has — the file is found by this pair,
#: not by a path — so they are named here rather than repeated as literals.
ORGANISATION = "AllWell"
APPLICATION = "AllWellApp"


#: Whether this process has already looked for settings in the native store.
_adopted = False


def settings_path() -> Path:
    """``settings.ini`` in the app's folder (``util.state_dir``)."""
    return app_file(SETTINGS_NAME)


def app_settings():
    """This user's ``QSettings`` for All-Well: an INI file in the app's folder.

    ``settings.ini`` beside everything else All-Well keeps on the machine
    (``util.state_dir``), on every platform, rather than the platform's native
    store — a plist under ``~/Library/Preferences`` on macOS, the registry on
    Windows, ``~/.config/AllWell`` on Linux — which put the settings in a
    different place on each platform and in none of them beside the rest.
    Settings an earlier version left in the native store are copied over once
    (:func:`_adopt_native_settings`).

    Cheap to construct: ``QSettings`` is a handle onto a cached backing store,
    not a file read, so callers make one where they need it rather than holding
    one. Imported inside the function so this module stays importable without
    Qt, which is what lets the settings-backed stores under ``core/`` be
    exercised in the fast lane against a plain stub.
    """
    from PySide6.QtCore import QSettings

    path = settings_path()
    _adopt_native_settings(path)
    return QSettings(str(path), QSettings.IniFormat)


def _native_settings():
    """The native store earlier versions used — its own function so a test can
    stand an INI file in for the registry or a plist."""
    from PySide6.QtCore import QSettings

    native = QSettings(ORGANISATION, APPLICATION)
    # macOS answers allKeys() with the system-wide defaults too (AppleLanguages
    # and the like) unless fallbacks are off; those are not the app's to copy.
    native.setFallbacksEnabled(False)
    return native


def _adopt_native_settings(path: Path) -> None:
    """Copy the native store's settings into *path*, once, when *path* is new.

    Only when *path* does not exist yet, at most once per process, and never
    while ``ALLWELL_STATE_DIR`` is set (it means "not the real folders", and the
    native store is the real one). The native store is cleared only after the
    INI file has been written without error, so a failure loses nothing: the
    next launch finds no ``settings.ini`` and tries again.
    """
    global _adopted
    if _adopted:
        return
    _adopted = True
    if os.environ.get(STATE_DIR_ENV, "").strip() or path.exists():
        return
    from PySide6.QtCore import QSettings

    with swallow("reading the settings an earlier version kept in the native store",
                 level=logging.WARNING, logger=_LOG):
        native = _native_settings()
        keys = native.allKeys()
        if not keys:
            return
        ini = QSettings(str(path), QSettings.IniFormat)
        for key in keys:
            ini.setValue(key, native.value(key))
        ini.sync()
        if ini.status() != QSettings.NoError:
            _LOG.warning("Could not write %s (status %s); the settings stay in "
                         "the native store for now.", path, ini.status())
            return
        native.clear()
        native.sync()
        _LOG.info("Moved %d settings from %s to %s.", len(keys), native.fileName(), path)
