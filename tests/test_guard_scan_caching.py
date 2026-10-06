"""Scan reuse must not hide edits, new modules, or nested violations."""

import ast
import os

from tests import _repo_scan
from tests import test_frozen_ctx_guardrail as ctx_guard
from tests import test_no_patch_misses_its_target as patch_guard
from tests import test_no_silent_except as silent_guard
from tests import test_no_unarchived_delete as deletion_guard


def _rewrite_preserving_stat(path, text):
    previous = path.stat()
    assert len(text.encode()) == previous.st_size
    path.write_text(text)
    os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))


def test_pairing_cache_observes_edits_and_file_membership(tmp_path, monkeypatch):
    path = tmp_path / "first.py"
    source = "def unsafe():\n    atomic_zip()\n    rmtree()\n"
    path.write_text(source)
    paths = [path]
    monkeypatch.setattr(deletion_guard, "python_sources", lambda: list(paths))
    monkeypatch.setattr(deletion_guard, "rel", lambda p: p.name)
    assert deletion_guard._pairings() == [("first.py", 1, "unsafe")]
    snapshot = deletion_guard._source_snapshot()
    before = deletion_guard._pairings_for_sources.cache_info().hits
    assert deletion_guard._pairings() == [("first.py", 1, "unsafe")]
    assert deletion_guard._pairings_for_sources.cache_info().hits == before + 1
    _rewrite_preserving_stat(path, source.replace("rmtree", "keep__"))
    assert deletion_guard._source_snapshot() != snapshot
    assert deletion_guard._pairings() == []
    added = tmp_path / "second.py"
    added.write_text(source)
    paths.append(added)
    assert deletion_guard._pairings() == [("second.py", 1, "unsafe")]
    paths.remove(added)
    assert deletion_guard._pairings() == []


def test_silent_handler_cache_observes_edits_and_inner_function(tmp_path, monkeypatch):
    path = tmp_path / "handlers.py"
    source = "def outer():\n    def inner():\n        try: work()\n        except Exception: pass\n"
    path.write_text(source)
    monkeypatch.setattr(_repo_scan, "python_sources", lambda: [path])
    monkeypatch.setattr(_repo_scan, "rel", lambda p: p.name)
    assert silent_guard._silent_sites() == [("handlers.py", "inner", 4)]
    before = silent_guard._silent_sites_in_tree.cache_info().hits
    assert silent_guard._silent_sites() == [("handlers.py", "inner", 4)]
    assert silent_guard._silent_sites_in_tree.cache_info().hits == before + 1
    _rewrite_preserving_stat(path, source.replace("pass", "None"))
    assert silent_guard._silent_sites() == []


def test_context_scan_ignores_mocks_but_sees_nested_real_app_calls():
    tree = ast.parse("def mocked(app):\n    use_ctx(app)\n"
                     "def real(viewer_app):\n    def callback(): use_ctx(self)\n")
    assert list(ctx_guard._bad_ctx_calls(tree, {"use_ctx"}, only_real_app=True)) == [
        (4, "use_ctx", "self")]


def test_call_time_import_scan_distinguishes_module_class_and_function(tmp_path, monkeypatch):
    owner = tmp_path / "owner.py"
    owner.write_text("def value(): pass\n")
    caller = tmp_path / "caller.py"
    caller.write_text("from owner import module_only\n"
                      "class Top:\n    from owner import class_only\n"
                      "def run():\n    from owner import called\n"
                      "    class Inner:\n        from owner import nested\n"
                      "import owner as alias\nresult = alias.value\n")
    monkeypatch.setattr(patch_guard, "python_sources", lambda: [owner, caller])
    monkeypatch.setattr(patch_guard, "REPO_ROOT", tmp_path)
    for fn in (patch_guard._module_paths, patch_guard._read_at_call_time):
        fn.cache_clear()
    try:
        assert patch_guard._read_at_call_time() == frozenset({
            ("owner", "called"), ("owner", "nested"), ("owner", "value")})
    finally:
        for fn in (patch_guard._module_paths, patch_guard._read_at_call_time):
            fn.cache_clear()
