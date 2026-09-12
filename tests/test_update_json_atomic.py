"""``persistence._io.update_json_atomic`` — the read-modify-write guard itself.

The behavioural half (a run finishing between a viewer read and a viewer write)
lives in ``test_pipeline_info_concurrent_update.py``, which drives the real
writers. These pin the helper's own contract: what it refuses, what it retries,
and that it gives up rather than looping forever.
"""

from __future__ import annotations

import json
import logging

from review.persistence._io import update_json_atomic


def test_a_plain_update_writes(tmp_path):
    path = tmp_path / "doc.json"
    path.write_text(json.dumps({"a": 1}))

    def _mutate(doc):
        doc["b"] = 2
        return doc

    assert update_json_atomic(path, _mutate) is True
    assert json.loads(path.read_text()) == {"a": 1, "b": 2}


def test_a_mutate_returning_none_writes_nothing(tmp_path):
    path = tmp_path / "doc.json"
    path.write_text(json.dumps({"a": 1}))

    assert update_json_atomic(path, lambda doc: None) is False
    assert json.loads(path.read_text()) == {"a": 1}


def test_a_missing_file_is_refused_unless_creation_is_asked_for(tmp_path):
    path = tmp_path / "absent.json"

    assert update_json_atomic(path, lambda doc: {"a": 1}) is False
    assert not path.exists()

    assert update_json_atomic(path, lambda doc: {"a": 1}, create_missing=True)
    assert json.loads(path.read_text()) == {"a": 1}


def test_a_payload_that_is_not_an_object_is_refused(tmp_path):
    path = tmp_path / "list.json"
    path.write_text(json.dumps([1, 2, 3]))

    assert update_json_atomic(path, lambda doc: {"a": 1}) is False
    assert json.loads(path.read_text()) == [1, 2, 3]


def test_unparseable_json_is_refused_rather_than_overwritten(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json")

    assert update_json_atomic(path, lambda doc: {"a": 1}) is False
    assert path.read_text() == "{not json"


def test_a_write_that_lands_between_the_read_and_the_write_is_re_applied(tmp_path):
    """The mutation is re-run against the *new* contents, not written over them."""
    path = tmp_path / "doc.json"
    path.write_text(json.dumps({"owner": "first"}))
    calls = {"n": 0}

    def _mutate(doc):
        calls["n"] += 1
        if calls["n"] == 1:
            # Somebody else rewrites the file while we are preparing our block.
            path.write_text(json.dumps({"owner": "second", "theirs": True}))
        doc["mine"] = True
        return doc

    assert update_json_atomic(path, _mutate) is True
    assert calls["n"] == 2, "the mutation must be re-applied, not overwritten"
    assert json.loads(path.read_text()) == {
        "owner": "second", "theirs": True, "mine": True,
    }


def test_a_file_rewritten_every_time_gives_up_and_says_so(tmp_path, caplog):
    path = tmp_path / "doc.json"
    path.write_text(json.dumps({"n": 0}))
    seen = {"n": 0}

    def _mutate(doc):
        seen["n"] += 1
        path.write_text(json.dumps({"n": seen["n"]}))  # always changes
        doc["mine"] = True
        return doc

    with caplog.at_level(logging.WARNING, logger="review"):
        assert update_json_atomic(path, _mutate, attempts=3) is False

    assert seen["n"] == 3, "it must stop after the attempt budget"
    assert "mine" not in json.loads(path.read_text())
    assert any("Gave up updating" in r.getMessage() for r in caplog.records), (
        "giving up has to be visible; a silently dropped update is the failure "
        "mode this helper exists to remove"
    )
