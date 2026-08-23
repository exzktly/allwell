"""Guards for the dataset tools' write paths and queue handoff.

Every rule here comes from a defect where a tool reported success while
producing less than it was asked for: a merge that discarded both plates' work,
a repair that could truncate the file it exists to preserve, a conversion that
overwrote a plate's images while its measurements survived beside them, and a
queued job that stalled the queue for the session.
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import types
import zipfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal  # noqa: E402

from datasets.job_queue.controller import QueueBridge  # noqa: E402
from services.job_queue import JobSpec, JobState  # noqa: E402
from tests._repo_scan import python_sources, rel  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


# ── 1. a merge keeps both plates' sample definitions ────────────────────────

def _v2_block(pairs, notes="", groups=()):
    from well_viewer.domain.selections_model import make_selection, to_block
    used_names, used_ids, used_colors = set(), set(), set()
    sels = [
        make_selection(name=n, wells=w, replicates=None, hidden=False, color=None,
                       source="user", used_names=used_names, used_ids=used_ids,
                       used_colors=used_colors, fallback_color_idx=i)
        for i, (n, w) in enumerate(pairs)
    ]
    return to_block(sels, well_labels={w: n for n, ws in pairs for w in ws},
                    notes=notes, current_id=sels[0]["id"] if sels else None,
                    plot_groups=list(groups))


def test_a_merge_of_two_current_blocks_keeps_every_selection():
    """``merge_sample_definitions`` read only the legacy ``rep_sets`` /
    ``groups`` keys, and its result *replaces* dataset 1's whole block — so a
    merge of two plates saved by today's app wrote back an empty legacy shape
    and reported success."""
    from datasets.merge_plates.merge_plates_compute import merge_sample_definitions

    b1 = _v2_block([("Control", ["A01", "A02"]), ("Drug", ["A03"])], "one",
                   [{"name": "QC set", "wells": ["A01"]}])
    b2 = _v2_block([("Control", ["A01"]), ("Rescue", ["A02"])], "two",
                   [{"name": "Row A", "wells": ["A01"]}])

    out = merge_sample_definitions(
        {"sample_definitions": b1}, {"sample_definitions": b2},
        well_map={"A01": "B01", "A02": "B02"}, ds2_label="plate2")

    assert "selections" in out, (
        "the merge wrote back a legacy-shaped block; every selection, plot "
        f"group, note and current selection from both plates is gone: {out}")
    names = [s["name"] for s in out["selections"]]
    assert len(names) == 4, f"selections were discarded: {names}"
    assert names[:2] == ["Control", "Drug"], "dataset 1's order was not kept"
    assert "Control (plate2)" in names, "the colliding name was not disambiguated"
    assert {g["name"] for g in out["plot_groups"]} == {"QC set", "Row A"}
    assert "one" in out["notes"] and "two" in out["notes"]
    assert out["schema_version"] == 2


def test_a_merge_remaps_dataset_two_s_wells_and_mints_fresh_ids():
    """Dataset 2's wells move on the merged plate, and a selection id is only
    unique within the block it came from."""
    from datasets.merge_plates.merge_plates_compute import merge_sample_definitions

    b1 = _v2_block([("Control", ["A01"])])
    b2 = _v2_block([("Treated", ["A01", "A02"])])
    out = merge_sample_definitions(
        {"sample_definitions": b1}, {"sample_definitions": b2},
        well_map={"A01": "B01", "A02": "B02"}, ds2_label="p2")

    assert "selections" in out, f"the merge discarded both plates' work: {out}"
    by_name = {s["name"]: s for s in out["selections"]}
    assert by_name["Control"]["wells"] == ["A01"]
    assert by_name["Treated"]["wells"] == ["B01", "B02"], "ds2's wells were not remapped"
    ids = [s["id"] for s in out["selections"]]
    assert len(set(ids)) == len(ids), f"duplicate selection ids: {ids}"
    assert out["current_id"] in set(ids)


def test_a_merge_of_two_legacy_blocks_still_works():
    """Legacy plates exist on disk; ``from_block`` migrates them on the way in."""
    from datasets.merge_plates.merge_plates_compute import merge_sample_definitions

    v1 = {"well_labels": {"A01": "Control"},
          "rep_sets": [{"name": "Rep 1", "wells": ["A01"]}],
          "groups": [{"name": "Control", "hidden": False,
                      "members": ["Rep 1"], "solo_wells": []}]}
    out = merge_sample_definitions({"sample_definitions": v1},
                                   {"sample_definitions": v1},
                                   well_map={"A01": "B01"}, ds2_label="p2")
    assert "selections" in out, (
        f"a legacy input produced a legacy output instead of migrating: {out}")
    assert out["schema_version"] == 2
    assert len(out["selections"]) == 2
    assert out["well_labels"] == {"A01": "Control", "B01": "Control"}


def test_a_merge_of_nothing_is_still_nothing():
    """The control: two plates with no definitions produce no block, rather
    than an empty one that would overwrite something later."""
    from datasets.merge_plates.merge_plates_compute import merge_sample_definitions
    assert merge_sample_definitions({}, {}, well_map={}, ds2_label="p2") is None


# ── 2. a live file is replaced atomically, never truncated ──────────────────

#: Sites exempted from the atomic-write rule, each with the reason.
#:
#: Empty, and that is the point: the rule is stated structurally — a scope that
#: renames what it wrote is doing by hand what ``write_json_atomic`` does — so
#: the three implementations of the rule (``util/json_write``,
#: ``well_viewer/persistence/_io``, ``services/run_journal``) clear it on their
#: own merits rather than by being named here. An earlier draft exempted them
#: by name while the rule was too broad to recognise them, which is an
#: allowlist standing in for a guard that does not work
#: (CLAUDE.md §7.5 #10).
#:
#: ``test_the_atomic_write_allowlist_is_live`` fails on any entry that stops
#: describing a real violation, so the first one added here cannot outlive the
#: code it was added for.
ATOMIC_WRITE_EXEMPT: dict[str, str] = {}


def _truncating_json_writes(path: pathlib.Path) -> list[str]:
    """Every way this repo has spelled "replace a JSON file by truncating it".

    A guard that matched only ``p.write_text(json.dumps(x))`` walked past four
    other live spellings — a payload bound to a name first, a bare ``dumps``
    from ``from json import dumps``, ``write_bytes`` on an encoded string, and
    ``open(p, "w")`` + ``json.dump``. Each of those is the same defect, and each
    is what someone writes when the obvious form has a guard on it.

    **What is not a violation**, and why the analysis is per-function rather
    than per-file: writing a whole document into a *staging* file and renaming
    it onto the destination is the rule, not a breach of it. So a function that
    performs a rename (``os.replace`` / ``Path.replace`` / ``shutil.move``) is
    doing exactly what ``write_json_atomic`` does by hand, and a handle from
    ``os.fdopen`` over a ``mkstemp`` result, or from a context manager whose
    name carries ``atomic``, is a staging handle. An earlier draft of this
    guard checked neither and reported all seven of the codebase's correct
    atomic writers as defects — and a guard whose failures are mostly false is
    one that gets deleted.

    What it cannot see: a handle opened in one function and written in another.
    No caller in this repo does that, and a guard that claims more than it
    decides is worse than one that says what it misses.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for scope in _scopes(tree):
        hits.extend(_truncating_writes_in(scope))
    return hits


_FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)


def _scopes(tree: ast.AST):
    """Every outermost function, plus module level with its functions removed.

    Outermost, not every function: ``persistence/_io.atomic_write_json`` writes
    the staging file inside a nested ``_write_tmp`` and renames in the parent,
    because the rename is wrapped in a retry. Scoping each nested ``def``
    separately splits that pair in half and reports the repo's most careful
    atomic writer as a truncating one.
    """
    nested = {id(inner) for fn in ast.walk(tree) if isinstance(fn, _FUNC)
              for inner in ast.walk(fn) if isinstance(inner, _FUNC) and inner is not fn}
    yield from (n for n in ast.walk(tree)
                if isinstance(n, _FUNC) and id(n) not in nested)
    module_level = [n for n in getattr(tree, "body", [])
                    if not isinstance(n, (*_FUNC, ast.ClassDef))]
    yield ast.Module(body=module_level, type_ignores=[])


def _renames_something(scope: ast.AST) -> bool:
    for node in ast.walk(scope):
        if isinstance(node, ast.Call):
            attr = getattr(node.func, "attr", "")
            if attr in ("replace", "move") and len(node.args) >= 1:
                return True
    return False


def _truncating_writes_in(scope: ast.AST) -> list[str]:
    if _renames_something(scope):
        return []          # staged and renamed: this is the rule, not a breach

    serialised: set[str] = set()
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and _is_dumps_call(node.value):
            serialised.add(node.targets[0].id)

    def _serialises(expr: ast.expr) -> bool:
        if _is_dumps_call(expr):
            return True
        if isinstance(expr, ast.Name):
            return expr.id in serialised
        if isinstance(expr, ast.Call):
            # ``json.dumps(x).encode()`` — the payload is the *receiver* of the
            # call, not one of its arguments, so the func expression has to be
            # followed too. Missing this was how ``write_bytes`` slipped past.
            return (any(_serialises(a) for a in expr.args)
                    or _is_dumps_call(expr.func)
                    or _serialises(expr.func))
        if isinstance(expr, ast.Attribute):
            return _serialises(expr.value)
        return False

    plain_handles = _plainly_opened_handles(scope)
    hits: list[str] = []
    for node in ast.walk(scope):
        if not isinstance(node, ast.Call):
            continue
        func_attr = getattr(node.func, "attr", "")
        # (a) p.write_text(...) / p.write_bytes(...) carrying a serialised payload
        if func_attr in ("write_text", "write_bytes") and node.args \
                and _serialises(node.args[0]):
            hits.append(f"{node.lineno}: {ast.unparse(node)}")
        # (b) json.dump(obj, fh) where *fh* came from a plain open on the
        #     destination itself.
        if func_attr == "dump" and isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id in ("json", "_json") and len(node.args) >= 2:
            handle = node.args[1]
            if isinstance(handle, ast.Name) and handle.id in plain_handles:
                hits.append(f"{node.lineno}: {ast.unparse(node)}")
    return hits


def _plainly_opened_handles(scope: ast.AST) -> set[str]:
    """Names bound by ``with open(x, "w")`` / ``x.open("w")`` in this scope."""
    plain: set[str] = set()
    for node in ast.walk(scope):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            call = item.context_expr
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = getattr(func, "id", None) or getattr(func, "attr", "")
            if "atomic" in str(name).lower() or name == "fdopen":
                continue
            if name != "open":
                continue
            var = item.optional_vars
            if isinstance(var, ast.Name):
                plain.add(var.id)
    return plain


def _is_dumps_call(expr: ast.expr) -> bool:
    """``json.dumps(...)``, ``_json.dumps(...)`` or a bare ``dumps(...)``."""
    if not isinstance(expr, ast.Call):
        return False
    func = expr.func
    if isinstance(func, ast.Attribute):
        return func.attr == "dumps"
    return isinstance(func, ast.Name) and func.id == "dumps"


def test_no_live_json_file_is_replaced_by_truncating_it():
    """``path.write_text(json.dumps(...))`` truncates the destination the
    instant it opens. For ``pipeline_info.json`` that is the user's gating,
    sample definitions, ratios and notes gone at once if anything interrupts
    the write — the blocks the tools that rewrite it exist to preserve.

    Scanned over ``tests/_repo_scan.python_sources``, which is the repo's one
    definition of "every application source file". A hand-written surface is
    how the last three guards each ended up blind exactly where another could
    see: an earlier draft of this one listed five package roots and no root
    modules, so it could not see ``process_microscopy.py`` — the *primary*
    writer of ``pipeline_info.json``.
    """
    offenders: list[str] = []
    for path in python_sources():
        name = rel(path)
        if name in ATOMIC_WRITE_EXEMPT:
            continue
        offenders.extend(f"{name}:{hit}" for hit in _truncating_json_writes(path))
    assert not offenders, (
        f"{len(offenders)} site(s) replace a JSON file with a truncating write "
        "instead of util.json_write.write_json_atomic:\n  " + "\n  ".join(offenders))


def test_the_atomic_write_allowlist_is_live():
    """An allowlist entry that no longer names a real write is an exemption
    nobody is watching. Each one must still contain the thing it exempts."""
    stale = [name for name in ATOMIC_WRITE_EXEMPT
             if not _truncating_json_writes(REPO_ROOT / name)]
    assert not stale, (
        "these files are exempted from the atomic-write rule but no longer "
        f"contain a write it would flag — drop the entry: {stale}")


def test_the_atomic_writer_leaves_the_destination_alone_when_the_payload_is_bad(tmp_path):
    """An unserialisable payload raises before the replace, so the file on disk
    is still the last good one — the case a truncating write destroys."""
    from util.json_write import write_json_atomic

    target = tmp_path / "out" / "pipeline_info.json"
    write_json_atomic(target, {"cell_gating": {"gfp": 10.0}})
    with pytest.raises((TypeError, ValueError)):
        write_json_atomic(target, {"bad": object()})
    assert json.loads(target.read_text()) == {"cell_gating": {"gfp": 10.0}}
    assert [p.name for p in target.parent.iterdir()] == ["pipeline_info.json"], (
        "a staging file was left behind")


def test_a_repair_preserves_the_user_blocks_it_rewrites_around(tmp_path):
    """Validate's repair rewrites ``pipeline_info.json`` to fix the
    availability lists. Every other block in that file is somebody's analysis.

    This drives the real ``apply_repairs``. An earlier version called
    ``write_json_atomic`` twice and asserted that JSON round-trips, which is a
    property of ``json``, not of the repair.

    It is a regression guard rather than a proof of the atomic-write rule, and
    it passes on the branch this one is based on — as it should, since the
    preservation contract held there too. What it guards is *this* change:
    rewriting the repair's write is exactly the kind of edit that drops a block
    nobody was looking at. The atomicity half is measured by
    ``test_no_live_json_file_is_replaced_by_truncating_it``, and the fixture
    here is built with a plain write so the test does not depend on the module
    under test to set itself up.
    """
    from datasets.validate import apply as validate_apply

    root = tmp_path / "plate"
    (root / "in").mkdir(parents=True)
    (root / "out").mkdir(parents=True)
    info_path = root / "out" / "pipeline_info.json"
    gating = {"fluor_gates": {"gfp": 12.0}}
    ratios = [{"name": "g/r", "numerator_channel": "gfp"}]
    info_path.write_text(json.dumps({
        "schema": "well:fov:timepoint",
        "schema_fields": ["well", "fov", "timepoint"],
        "cell_gating": gating,
        "sample_definitions": _v2_block([("Control", ["A01"])], notes="mine"),
        "ratios": ratios,
        "available_timepoints": ["0h"],
        "available_fovs": ["1"],
    }, indent=2))
    (root / "in" / "A01.zip").write_bytes(b"")

    report = types.SimpleNamespace(
        root=root,
        stale_available_timepoints=["0h"], missing_available_timepoints=[],
        stale_available_fovs=[], missing_available_fovs=[],
    )
    validate_apply.apply_repairs(root, report)

    back = json.loads(info_path.read_text())
    assert back["cell_gating"] == gating, "the repair destroyed the gating"
    assert back["ratios"] == ratios, "the repair destroyed the ratio metrics"
    assert back["sample_definitions"]["notes"] == "mine"
    assert "available_timepoints" in back


# ── 3. an archive that reads as complete really is ──────────────────────────

def test_a_member_that_fails_its_crc_is_never_published(tmp_path, monkeypatch):
    """The member-list check answers "is the index complete?". Bytes corrupted
    inside a deflate stream leave the index untouched, so the archive opens,
    lists every member, and raises ``Bad CRC-32`` only when something reads
    one — a well whose images are gibberish, published as a clean copy."""
    from util import zip_write
    from util.zip_write import ZipWriteError, atomic_zip

    real = zipfile.ZipFile

    class _CorruptOnRead(real):
        def testzip(self):
            return "A01_f1_w1.tif"

    monkeypatch.setattr(zip_write.zipfile, "ZipFile", _CorruptOnRead)
    target = tmp_path / "A01.zip"
    with pytest.raises(ZipWriteError, match="CRC"):
        with atomic_zip(target) as zf:
            zf.writestr("A01_f1_w1.tif", b"x" * 4000)
    assert not target.exists(), "a corrupt archive was published"
    assert list(tmp_path.iterdir()) == [], "a staging file was left behind"


def test_a_container_copy_does_not_pay_for_verification_it_does_not_need(tmp_path):
    """``verify`` follows ``durable`` for the same reason: an archive holding
    the only copy of freshly computed data is worth the read-back, a copy whose
    source is still on disk beside it is not."""
    from util.zip_write import atomic_zip

    seen: list[bool] = []
    real = zipfile.ZipFile

    class _Counting(real):
        def testzip(self):
            seen.append(True)
            return None

    import util.zip_write as zw
    zw.zipfile.ZipFile = _Counting
    try:
        with atomic_zip(tmp_path / "durable.zip") as zf:
            zf.writestr("m.tif", b"x" * 100)
        assert seen, "a durable write skipped verification"
        seen.clear()
        with atomic_zip(tmp_path / "copy.zip", durable=False) as zf:
            zf.writestr("m.tif", b"x" * 100)
        assert not seen, "a container copy paid for a full CRC read-back"
    finally:
        zw.zipfile.ZipFile = real


def test_the_whole_file_tier_stages_like_every_other_tier(tmp_path, monkeypatch):
    """The tier-1 fast path copied straight onto the destination, five lines
    above a comment stating that nothing here is written straight to the
    destination. An interrupted copy left a truncated ``<WELL>.zip``."""
    import shutil

    from datasets._common import containers

    src = tmp_path / "src.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("A01_f1_w1.tif", b"z" * 2000)
    keep = lambda _container, name: name  # noqa: E731

    real_copy = shutil.copy2

    def _interrupted(a, b, **kw):
        real_copy(a, b, **kw)
        raise KeyboardInterrupt("killed mid-copy")

    dest = tmp_path / "out" / "A01.zip"
    monkeypatch.setattr(containers.shutil, "copy2", _interrupted)
    with pytest.raises(KeyboardInterrupt):
        containers.copy_container_members(dest, [src], keep)
    assert not dest.exists(), "an interrupted whole-file copy left a partial well"
    assert not list(dest.parent.iterdir()), "a staging file was left behind"

    monkeypatch.setattr(containers.shutil, "copy2", real_copy)
    written, dropped = containers.copy_container_members(dest, [src], keep)
    assert zipfile.ZipFile(dest).namelist() == ["A01_f1_w1.tif"]
    assert written == frozenset({"A01_f1_w1.tif"}) and not dropped


# ── 4. a conversion checks its destination wherever it is started from ──────

def test_a_conversion_refuses_a_destination_that_already_holds_a_plate(tmp_path):
    """``destination_problem`` was consulted only by the page, and a queued job
    reaches ``convert`` through ``start_from_queue`` without the page's field
    hint ever being read. The conversion overwrites each ``in/<WELL>.zip`` it
    produces while ``out/`` survives beside it — measurements describing inputs
    that are no longer there."""
    from datasets.ome_import.apply import convert
    from datasets.ome_import.ome_import_compute import ConversionParams, OmeImportError

    dest = tmp_path / "dest"
    (dest / "in").mkdir(parents=True)
    (dest / "in" / "A01.zip").write_bytes(b"an earlier plate")
    src = tmp_path / "stack.ome.tif"
    src.write_bytes(b"not really an ome-tiff")

    with pytest.raises(Exception) as exc:
        convert(src, dest, ConversionParams(grid_rows=2, grid_cols=2))
    # The harm, stated first: without the check the conversion gets as far as
    # reading the source, and everything after that overwrites archives in a
    # folder whose ``out/`` measurements describe the ones being replaced.
    assert isinstance(exc.value, OmeImportError) and "Already holds" in str(exc.value), (
        "the conversion did not refuse a destination that already holds a "
        f"plate; it failed later, with {type(exc.value).__name__}: {exc.value}")
    assert (dest / "in" / "A01.zip").read_bytes() == b"an earlier plate"


def test_a_conversion_accepts_an_empty_destination(tmp_path):
    """The control: the check must not refuse the ordinary case. It gets past
    the destination check and fails on the source, which is a different error."""
    from datasets.ome_import.apply import convert
    from datasets.ome_import.ome_import_compute import ConversionParams, OmeImportError

    src = tmp_path / "stack.ome.tif"
    src.write_bytes(b"not really an ome-tiff")
    with pytest.raises(Exception) as exc:
        convert(src, tmp_path / "fresh", ConversionParams(grid_rows=2, grid_cols=2))
    assert not (isinstance(exc.value, OmeImportError)
                and "Already holds" in str(exc.value)), (
        "an empty destination was refused")


# ── 5. the queue never strands a job on a busy runner ───────────────────────

class _FakeQueueRunner(QObject):
    """The shape ``QueueBridge`` talks to — a real QObject, so its
    ``jobFinished`` is a real signal the bridge can connect to."""

    jobFinished = Signal(bool, str, str)

    def __init__(self, busy: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.busy = busy
        self.started: list[dict] = []

    def is_busy(self) -> bool:
        return self.busy

    def show_job(self, spec: dict) -> None:
        pass

    def start_from_queue(self, spec: dict) -> None:
        self.started.append(spec)

    def stop(self) -> None:
        pass


def _queued_job(job_id: str, kind: str = "widget") -> "JobSpec":
    return JobSpec(id=job_id, kind=kind, label=job_id, spec={"n": job_id})


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_a_queued_job_is_not_dispatched_to_a_runner_running_by_hand(qapp, tmp_path):
    """Every tool's ``_launch`` opens with "if a worker is already running,
    return" and emits no ``jobFinished``. Dispatching to a busy runner marks
    the job running against a controller that never reports back, so
    ``_current`` stays set and the queue stops for the rest of the session.

    Driven through a real ``QueueBridge``. The first version of this test
    asserted that the substring ``is_busy()`` appeared before
    ``start_from_queue`` in the source of ``_advance`` — which a body reading
    ``if runner.is_busy(): pass`` satisfies, and which never constructs a
    bridge at all.
    """
    bridge = QueueBridge(state_dir=tmp_path)
    runner = _FakeQueueRunner()
    bridge.register("widget", runner)
    runner.busy = True                      # the user pressed Run by hand
    bridge.queue.add(_queued_job("a"))
    bridge.start()

    assert runner.started == [], "a queued job was handed to a busy controller"
    assert bridge.queue.get("a").state == JobState.PENDING, (
        "the job was marked running against a controller that will never "
        "report back — the queue is now stopped for the session")


def test_the_manual_run_finishing_releases_the_held_back_job(qapp, tmp_path):
    """The other half, and neither works alone. A job ``_advance`` declined to
    dispatch is waiting on exactly the manual run's completion, and
    ``_on_job_finished`` drops a signal that is not the queue's own — so
    without the nudge the queue sits still until something else happens to
    call ``_advance``."""
    bridge = QueueBridge(state_dir=tmp_path)
    runner = _FakeQueueRunner()
    bridge.register("widget", runner)
    runner.busy = True
    bridge.queue.add(_queued_job("a"))
    bridge.start()
    assert runner.started == []

    # The by-hand run finishes: not the queue's job, but the runner is free.
    runner.busy = False
    runner.jobFinished.emit(True, "done by hand", "")
    qapp.processEvents()                    # the nudge is a queued singleShot

    assert len(runner.started) == 1, (
        "the held-back job never started; the queue is stopped even though "
        "its runner has been free since the manual run finished")
    assert bridge.queue.get("a").state == JobState.RUNNING


def test_a_free_runner_still_takes_the_job_immediately(qapp, tmp_path):
    """The control: the busy check must not hold back a job on an idle
    runner, which is every ordinary queue run."""
    bridge = QueueBridge(state_dir=tmp_path)
    runner = _FakeQueueRunner()
    bridge.register("widget", runner)
    bridge.queue.add(_queued_job("a"))
    bridge.start()
    assert len(runner.started) == 1
    assert bridge.queue.get("a").state == JobState.RUNNING


@pytest.mark.parametrize("tool", [
    "concatenate", "import_loose", "merge_plates",
    "ome_import", "subset", "validate",
])
def test_every_tool_reports_a_launch_it_abandoned(tool):
    """A queued job that reaches a controller which returns without starting a
    worker must still emit ``jobFinished`` — the bridge is waiting on it, and
    nothing else will ever send one.

    Checked per *return statement*, not once per function. The first version
    asked only whether the string ``jobFinished.emit`` appeared anywhere before
    ``worker.start()``, which three of the six tools skipped entirely (they had
    one bare return, so the guard `continue`d) and which the other three
    satisfied with a single emit however many silent returns sat beside it.

    The one return that needs no emit is the "a worker is already running"
    guard every launcher opens with. That arm is unreachable from the queue —
    ``QueueBridge._advance`` refuses to dispatch to a busy runner, which
    ``test_a_queued_job_is_not_dispatched_to_a_runner_running_by_hand`` pins —
    so it is only reachable from the user's own second click, where the page
    is right there in front of them.
    """
    path = REPO_ROOT / "datasets" / tool / "controller.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    launchers = [n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name in ("_launch", "_run")]
    assert launchers, f"{tool} has no _launch/_run to check"

    unreported: list[str] = []
    for fn in launchers:
        starts = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                  and ast.unparse(n).endswith(".start()")]
        start_line = min((n.lineno for n in starts), default=10**9)
        busy_guard = _busy_guard_line(fn)
        for block in _statement_blocks(fn):
            emitted = False
            for stmt in block:
                if _emits_job_finished(stmt):
                    emitted = True
                if not isinstance(stmt, ast.Return):
                    continue
                if stmt.lineno >= start_line:
                    continue                # after the worker started
                if busy_guard is not None and stmt.lineno == busy_guard:
                    continue                # the already-running guard
                if not emitted:
                    unreported.append(f"{fn.name}, line {stmt.lineno}")
    assert not unreported, (
        f"{tool} returns without starting a worker and without emitting "
        f"jobFinished at: {', '.join(unreported)} — a queued job on any of "
        "those paths stalls the queue for the session")


def _statement_blocks(node: ast.AST):
    """Every statement list in the tree, so a return is judged against the
    branch it is actually in.

    A line-proximity window is not good enough and was measured failing: a new
    silent ``return`` three lines below a *different* branch's emit passed the
    first hardened draft of this guard. What matters is whether the emit is a
    preceding sibling of the return, not whether it is nearby.
    """
    for child in ast.walk(node):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(child, field, None)
            if isinstance(block, list) and block:
                yield block


def _emits_job_finished(stmt: ast.stmt) -> bool:
    return any("jobFinished.emit" in ast.unparse(n)
               for n in ast.walk(stmt) if isinstance(n, ast.Call))


def _busy_guard_line(fn: ast.FunctionDef) -> int | None:
    """The line of the ``if <worker> is running: return`` arm, if present."""
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        test = ast.unparse(node.test)
        if "isRunning()" in test and len(node.body) == 1 \
                and isinstance(node.body[0], ast.Return):
            return node.body[0].lineno
    return None
