"""Audit X15 — bootstrap logs must not grow without bound.

A fresh ``bootstrap-<timestamp>.log`` is written on every invocation and they
were never reaped. ``_prune_bootstrap_logs`` keeps the newest N and deletes the
rest, best-effort.
"""
from __future__ import annotations

from services.remote_bootstrap import _prune_bootstrap_logs


def _seed(log_dir, n):
    # Timestamp-sorted names: bootstrap-00000 .. so lexical sort == chronological.
    for i in range(n):
        (log_dir / f"bootstrap-{i:05d}.log").write_text("x")


def test_prunes_oldest_keeping_newest(tmp_path):
    _seed(tmp_path, 30)
    removed = _prune_bootstrap_logs(tmp_path, keep=20)
    survivors = sorted(p.name for p in tmp_path.glob("bootstrap-*.log"))
    assert removed == 10
    assert len(survivors) == 20
    # The newest 20 survive; the oldest 10 (00000..00009) are gone.
    assert survivors[0] == "bootstrap-00010.log"
    assert survivors[-1] == "bootstrap-00029.log"


def test_noop_when_under_cap(tmp_path):
    _seed(tmp_path, 5)
    assert _prune_bootstrap_logs(tmp_path, keep=20) == 0
    assert len(list(tmp_path.glob("bootstrap-*.log"))) == 5


def test_only_touches_bootstrap_logs(tmp_path):
    _seed(tmp_path, 25)
    (tmp_path / "keep-me.txt").write_text("data")
    (tmp_path / "remotes.toml").write_text("cfg")
    _prune_bootstrap_logs(tmp_path, keep=20)
    assert (tmp_path / "keep-me.txt").exists()
    assert (tmp_path / "remotes.toml").exists()
