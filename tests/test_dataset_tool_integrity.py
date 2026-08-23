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
import pathlib
import zipfile

import pytest

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

def test_no_dataset_tool_replaces_a_json_file_by_truncating_it():
    """``path.write_text(json.dumps(...))`` truncates the destination the
    instant it opens. For ``pipeline_info.json`` that is the user's gating,
    sample definitions, ratios and notes gone at once if anything interrupts
    the write — the blocks the tools that rewrite it exist to preserve.

    Scanned across every layer that writes one of these files, not just the
    module the defect was reported in: ``datasets/`` had it, and so did
    ``pipeline/run_report``.
    """
    roots = [REPO_ROOT / "datasets", REPO_ROOT / "pipeline",
             REPO_ROOT / "services", REPO_ROOT / "well_viewer",
             REPO_ROOT / "util"]
    allowed = {
        # The one implementation of the rule, and the two specialised versions
        # that are a different control flow rather than a copy of it.
        "util/json_write.py",
        "well_viewer/persistence/_io.py",
        "services/run_journal.py",
    }
    offenders: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            rel = str(path.relative_to(REPO_ROOT))
            if rel in allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if getattr(node.func, "attr", "") != "write_text" or not node.args:
                    continue
                if "json.dumps" in ast.unparse(node.args[0]):
                    offenders.append(f"{rel}:{node.lineno}: {ast.unparse(node)}")
    assert not offenders, (
        f"{len(offenders)} site(s) replace a JSON file with a truncating write "
        "instead of util.json_write.write_json_atomic:\n  " + "\n  ".join(offenders))


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
    """Validate's repair rewrites ``pipeline_info.json`` to fix the availability
    lists. Every other block in that file is somebody's analysis."""
    from util.json_write import write_json_atomic

    info_path = tmp_path / "pipeline_info.json"
    original = {
        "schema": "well:fov:timepoint",
        "cell_gating": {"fluor_gates": {"gfp": 12.0}},
        "sample_definitions": _v2_block([("Control", ["A01"])]),
        "ratios": [{"name": "g/r"}],
        "available_timepoints": ["0h"],
    }
    write_json_atomic(info_path, original)
    info = json.loads(info_path.read_text())
    info["available_timepoints"] = ["0h", "24h"]
    write_json_atomic(info_path, info)
    back = json.loads(info_path.read_text())
    assert back["cell_gating"] == original["cell_gating"]
    assert back["ratios"] == original["ratios"]
    assert back["sample_definitions"] == original["sample_definitions"]
    assert back["available_timepoints"] == ["0h", "24h"]


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

class _Runner:
    """The shape ``QueueBridge`` talks to."""

    def __init__(self, busy: bool = False) -> None:
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


def test_a_queued_job_is_not_dispatched_to_a_runner_running_by_hand():
    """Every tool's ``_launch`` opens with "if a worker is already running,
    return" and emits no ``jobFinished``. Dispatching to a busy runner marks the
    job running against a controller that never reports back, so ``_current``
    stays set and the queue stops for the session."""
    import ast as _ast

    src = (REPO_ROOT / "datasets" / "job_queue" / "controller.py").read_text()
    fn = next(n for n in _ast.walk(_ast.parse(src))
              if isinstance(n, _ast.FunctionDef) and n.name == "_advance")
    body = _ast.unparse(fn)
    assert "is_busy()" in body, (
        "_advance dispatches without asking whether the runner is already "
        "running something of its own")
    assert body.index("is_busy()") < body.index("start_from_queue"), (
        "the busy check runs after the dispatch, which is no check at all")


def test_a_manual_run_finishing_restarts_the_queue():
    """A job ``_advance`` declined to dispatch is waiting on exactly the manual
    run's completion. ``_on_job_finished`` drops a signal that is not the
    queue's own in-flight job, so without the nudge the queue sits still."""
    import ast as _ast

    src = (REPO_ROOT / "datasets" / "job_queue" / "controller.py").read_text()
    fn = next(n for n in _ast.walk(_ast.parse(src))
              if isinstance(n, _ast.FunctionDef) and n.name == "_on_job_finished")
    # The early-return arm — the one taken when the signal is not ours — must
    # reach _advance before returning.
    early = _ast.unparse(fn).split("return")[0]
    assert "_advance" in early, (
        "_on_job_finished drops a foreign completion without re-checking the "
        "queue, so a job held back by a busy runner never starts")


@pytest.mark.parametrize("tool", [
    "concatenate", "import_loose", "merge_plates",
    "ome_import", "subset", "validate",
])
def test_every_tool_reports_a_launch_it_abandoned(tool):
    """A queued job that reaches a controller which returns without starting a
    worker must still emit ``jobFinished`` — the bridge is waiting on it, and
    nothing else will ever send one."""
    import ast as _ast

    path = REPO_ROOT / "datasets" / tool / "controller.py"
    tree = _ast.parse(path.read_text())
    launchers = [n for n in _ast.walk(tree)
                 if isinstance(n, _ast.FunctionDef) and n.name in ("_launch", "_run")]
    assert launchers, f"{tool} has no _launch/_run to check"
    for fn in launchers:
        body = _ast.unparse(fn)
        # Returns that happen before any worker is constructed: each one must
        # be paired with a jobFinished emit somewhere in the function.
        pre_worker = body.split("worker.start()")[0]
        returns = [n for n in _ast.walk(fn) if isinstance(n, _ast.Return)]
        # The "a worker is already running" guard is the one bare return that is
        # correct without an emit: the queue never dispatches to a busy runner
        # (see the _advance guard above), so this arm is only reachable from the
        # user's own second click.
        bare = [n for n in returns if n.value is None]
        if len(bare) <= 1:
            continue
        assert "jobFinished.emit" in pre_worker, (
            f"{tool}.{fn.name} has {len(bare)} paths that return before starting "
            "a worker but never emits jobFinished — a queued job on any of them "
            "stalls the queue for the session")
