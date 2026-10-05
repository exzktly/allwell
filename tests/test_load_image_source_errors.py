"""Audit E2 — ``_load_image_source`` still returns None on unreadable input
(the sampling contract), but no longer swallows the failure silently: it logs
a breadcrumb naming the source instead of ``except Exception: return None``.
"""
from __future__ import annotations

import logging

from pipeline import empty_well


def test_missing_path_returns_none(tmp_path):
    assert empty_well._load_image_source(tmp_path / "does_not_exist.tif") is None


def test_bad_zip_member_returns_none(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_text("this is not a zip archive")
    assert empty_well._load_image_source((bad, "frame.tif")) is None


def test_bad_input_logs_breadcrumb(tmp_path, caplog):
    with caplog.at_level(logging.DEBUG, logger="process_microscopy"):
        assert empty_well._load_image_source(tmp_path / "missing.tif") is None
    assert any("missing.tif" in r.getMessage() for r in caplog.records), \
        "a failed load must leave a log breadcrumb, not vanish silently"
