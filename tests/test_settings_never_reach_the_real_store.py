"""Nothing a test file builds once — before its first test — reads the
developer's real ``~/.config/AllWell/AllWellApp.conf``.

The per-test redirect in ``conftest._settings_go_to_a_scratch_file`` is
function-scoped, and pytest builds module- and session-scoped fixtures before
any function-scoped one. A full-app window built per file therefore read the
real store, and a ``phone_text_size=L`` left there by running the app broke
``test_phone_layout_app.py``. ``conftest._session_settings_go_to_a_scratch_file``
closes that window; this checks it from inside a module-scoped fixture, where
the gap was.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def store_seen_at_module_setup():
    from core import app_settings
    return Path(app_settings.app_settings().fileName()).resolve()


def test_a_module_scoped_fixture_sees_a_scratch_store(store_seen_at_module_setup):
    from PySide6.QtCore import QSettings
    real = Path(QSettings("AllWell", "AllWellApp").fileName()).resolve()
    assert store_seen_at_module_setup != real, (
        f"a module-scoped fixture read the real settings store {real}")
    assert store_seen_at_module_setup.name == "AllWellApp.ini"
