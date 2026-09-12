"""Audit E15 — a corrupt mask/image in the image table must leave a log
breadcrumb, not render silently as "no overlay" (which reads as a clean QC view).
"""
from __future__ import annotations

import logging

import review.data.image_discovery as idisc
from review.data import frame_load as fl


def test_load_mask_array_logs_on_decode_failure(monkeypatch, caplog):
    def _boom(*a, **k):
        raise RuntimeError("corrupt mask")

    monkeypatch.setattr(idisc, "open_imgref_as_array", _boom)
    well, tp, fov = "A01", "0", "1"
    cache = {(well, "__mask__", "raw"): {(str(fov), str(tp)): object()}}

    logger_name = "review.data.frame_load"
    with caplog.at_level(logging.DEBUG, logger=logger_name):
        assert fl.load_mask_array(None, cache, well, tp, fov) is None
    assert any("mask" in r.getMessage().lower() for r in caplog.records), \
        "a failed mask decode must leave a breadcrumb"
