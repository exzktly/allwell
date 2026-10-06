"""The real app records an analysis edit in the dataset's provenance log.

One test through the real shell (CLAUDE.md §7.8 rule 3): a structural sample-
definitions edit, queued by the app's own debounced save and landed by
``flush_all`` — the path quitting and switching datasets take, which runs the
write on the background writer thread. Everything else about the log is
covered in the fast lane (``tests/test_review_provenance.py``).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from util import provenance_log as P  # noqa: E402


def test_an_edit_flushed_by_the_app_is_in_the_history(loaded_app, mini_dataset):
    app = loaded_app
    before = P.read_events(mini_dataset)
    already = len(before.events)

    sid = app._sel_add(name="ProvenanceProbe", wells=[], source="user")
    assert sid is not None
    assert "sample_definitions" in app._persistence._pending
    app._persistence.flush_all()

    new = P.read_events(mini_dataset).events[already:]
    changed = [e["detail"]["domain"] for e in new if e["event"] == "analysis_changed"]
    assert "sample_definitions" in changed, new
    event = next(e for e in new if e["detail"].get("domain") == "sample_definitions")
    assert event["source"] == "review"
    assert event["detail"]["file"] == "pipeline_info.json"
    assert event["detail"]["before"] != event["detail"]["after"]
