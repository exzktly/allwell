"""Audit X8 — view_state persists only the export-style prefs that differ from
``DEFAULT_EXPORT_STYLE_PREFS``. Saving the full dict froze the old default into
persistence.json, so a later app version that changed a default was silently
overridden for users who never customized that key.
"""
from __future__ import annotations

import types

from well_viewer.core.plot_style import DEFAULT_EXPORT_STYLE_PREFS
from well_viewer.persistence.view_state import _nondefault_export_prefs


def _app(prefs):
    return types.SimpleNamespace(_export_style_prefs=prefs)


def test_all_defaults_persist_nothing():
    assert _nondefault_export_prefs(_app(dict(DEFAULT_EXPORT_STYLE_PREFS))) == {}


def test_only_changed_key_is_persisted():
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    key = sorted(DEFAULT_EXPORT_STYLE_PREFS)[0]
    prefs[key] = object()  # guaranteed != default
    assert set(_nondefault_export_prefs(_app(prefs))) == {key}


def test_extra_non_default_key_kept():
    prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    prefs["_custom_only"] = "x"
    out = _nondefault_export_prefs(_app(prefs))
    assert out == {"_custom_only": "x"}


def test_missing_attr_is_safe():
    assert _nondefault_export_prefs(types.SimpleNamespace()) == {}
