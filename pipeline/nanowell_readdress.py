"""Address an anchored nanowell well from the measurements a run already saved, without running it again.

A timepoint whose central square was not found is still measured in full: every nanowell of its array has a row in
``nanowells/<well>.csv`` with its position, state, count and fluorescence, and every cell it counted a row in the
per-cell CSV, all with blank addresses (``nanowell_run.measure_timepoint``, tracker F8). What the anchor a person
sets afterwards adds is the origin, and with it the addresses. So this step reads the output folder, and nothing
else of the plate: it refits the lattice to the timepoint's own rows, resolves the origin with the run's own
``resolve_origin``, gives every row and every cell its address, decides the well's rim with the run's own evidence
rule (``nanowell_run.RimFrame``), recomputes the timepoints' consistency, and rewrites the site CSV, the per-cell CSV
and the grid record. No image is read and no cell is segmented (tracker H1–H4).

**It refuses rather than approximates.** The rows on disk are exactly the rows a run with the anchor would write
only when (a) the anchor names the site the rows were laid out from — an unaddressed timepoint's array is laid out
from the site nearest its outline's centre (``nanowell_run._unaddressed_positions``) — and (b) the timepoint's
evidence leaves the well's rim as it was. Each check that fails is a refusal naming why, for that timepoint, and
the timepoint stays unaddressed; running the well again in Analyze remains the way to address it.

After the anchors, the end-of-run pass runs as a run would run it (``nanowell_task.plan_reanchor``): an unaddressed
timepoint the rest of the plate can now place is addressed the same way, from its rows.

A re-addressed timepoint's grid block says so (``readdressed``). Its ``manual_anchor_um`` and ``anchors_digest`` are
the ones a run would stamp, so a later incremental run keeps it rather than measuring it again (tracker H7).

Qt-free: Review runs it on a worker, on a dataset the pipeline wrote.
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from pipeline import nanowell_run as nr

log = logging.getLogger(__name__)

#: A row whose ``imaged_fraction`` reaches this saw its nanowell's whole disk (``nanowell_task._kept_evidence``).
WHOLE = 0.999
MANUAL, PLATE_RIGID = "manual", "plate_rigid"


class Refused(Exception):
    """This timepoint cannot be addressed from its saved rows; the message says why."""


# ── one well's files ─────────────────────────────────────────────────────────
@dataclass
class _Table:
    path: Path
    fields: list
    rows: list

    @classmethod
    def read(cls, path: Path) -> "_Table":
        with _open_text(path, "r") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
            return cls(path, list(reader.fieldnames or []), rows)

    def write(self) -> None:
        part = self.path.with_name(self.path.name + ".part")
        with _open_text(part, "w") as fh:
            writer = csv.DictWriter(fh, fieldnames=self.fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(self.rows)
        os.replace(part, self.path)


def _open_text(path: Path, mode: str):
    gz = ".gz" in path.suffixes
    return gzip.open(path, mode + "t", newline="", encoding="utf-8") if gz else open(path, mode, newline="",
                                                                                         encoding="utf-8")


@dataclass
class WellFiles:
    """The grid record, the site rows and the per-cell rows of one well, read, changed in memory and written back."""
    well: str
    grid_path: Path
    grid: dict
    sites: _Table
    cells: _Table | None
    changed: bool = False

    @classmethod
    def load(cls, output_dir: Path, well: str, csv_prefix: str) -> "WellFiles":
        from pipeline.nanowell_task import NANOWELL_DIR
        from util.csv_gz import existing_csv

        grid_path = Path(output_dir) / NANOWELL_DIR / f"{well}_grid.json"
        try:
            grid = json.loads(grid_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise Refused(f"the grid record {grid_path.name} cannot be read ({exc})") from None
        sites_path = Path(output_dir) / NANOWELL_DIR / f"{well}.csv"
        if not sites_path.is_file():
            raise Refused(f"there is no {sites_path.name} to address")
        cells_path = existing_csv(Path(output_dir) / f"{csv_prefix}_{well}.csv")
        return cls(well, grid_path, grid, _Table.read(sites_path),
                   None if cells_path is None else _Table.read(cells_path))

    def order(self) -> list[str]:
        from pipeline.nanowell_task import _tp_rank

        return sorted(self.grid.get("timepoints", {}), key=_tp_rank)

    def save(self) -> None:
        """Rows first and the grid record last: the record's status is what says a timepoint is addressed, so a write
        cut short leaves it unaddressed, and addressing it again starts from the same rows."""
        from pipeline.nanowell_task import _json_default

        self.sites.write()
        if self.cells is not None:
            self.cells.write()
        part = self.grid_path.with_name(self.grid_path.name + ".part")
        part.write_text(json.dumps(self.grid, indent=2, default=_json_default), encoding="utf-8")
        os.replace(part, self.grid_path)


# ── reading the rows ─────────────────────────────────────────────────────────
def _float(value) -> float | None:
    text = str(value if value is not None else "").strip()
    return float(text) if text else None


def _position(row) -> tuple[float, float]:
    return float(row["well_x_um"]), float(row["well_y_um"])


def _address(row) -> tuple[int, int] | None:
    q, r = _float(row.get("nw_q")), _float(row.get("nw_r"))
    return None if q is None or r is None else (int(q), int(r))


def _true(value) -> bool:
    return str(value).strip().lower() == "true"


def _evidence(rows) -> tuple[set, set]:
    """``(detected, seen whole)`` addresses of an addressed timepoint's rows, as ``nanowell_task._kept_evidence``
    reads them; a row flagged a roster mismatch may be a step off and says nothing."""
    detected, whole = set(), set()
    for row in rows:
        address = _address(row)
        if address is None or row.get("consistency") == "roster_mismatch":
            continue
        if _true(row.get("detected")):
            detected.add(address)
        fraction = _float(row.get("imaged_fraction"))
        if fraction is not None and fraction >= WHOLE:
            whole.add(address)
    return detected, whole


# ── the lattice the rows sit on ──────────────────────────────────────────────
def _fit(block: dict, basis, phase) -> "object":
    from pipeline.nanowell_lattice import LatticeFit

    return LatticeFit(True, "", tuple(map(float, basis[:, 0])), tuple(map(float, basis[:, 1])),
                      float(block["pitch_um"]), block.get("angle_deg"), block.get("lattice_kind"),
                      tuple(map(float, phase)), mirrored=bool(block.get("mirrored")))


def _stored_basis(block: dict) -> np.ndarray | None:
    stored = block.get("basis_um")
    return None if not stored else np.column_stack([np.asarray(v, dtype=float) for v in stored])


def lattice_of_unaddressed(block: dict, points: np.ndarray):
    """The lattice an unaddressed timepoint's rows sit on, common frame: the run's own basis when the grid record
    kept it, else one fitted to the rows (the same nearest-east convention, so the same axes); its phase from the
    rows."""
    from pipeline.nanowell_lattice import fit_lattice

    basis = _stored_basis(block)
    if basis is None:
        if block.get("mirrored"):
            raise Refused("the lattice is mirrored and its grid record does not say on which axis")
        refit = fit_lattice(points, expected_pitch=float(block["pitch_um"]), kind=block.get("lattice_kind") or "hex")
        if not refit.ok:
            raise Refused(f"no lattice could be fitted to its saved nanowells ({refit.reason})")
        if abs(refit.angle_deg - float(block.get("angle_deg", refit.angle_deg))) > 1.0:
            raise Refused(f"the lattice fitted to its saved nanowells lies at {refit.angle_deg:.2f} degrees, not the "
                          f"run's {float(block['angle_deg']):.2f}")
        basis = np.column_stack((refit.a1, refit.a2))
    steps = np.rint(np.linalg.solve(basis, (points - points[0]).T).T)
    phase = np.median(points - steps @ basis.T, axis=0)
    return _fit(block, basis, phase)


def lattice_of_addressed(block: dict, rows):
    """The lattice of an addressed timepoint: its kept basis, or the least-squares one through its rows' addresses."""
    pairs = [(a, _position(r)) for r in rows if (a := _address(r)) is not None]
    if len(pairs) < 3:
        raise Refused(f"its timepoint {block.get('timepoint', '')} has too few addressed rows to place its lattice")
    design = np.column_stack((np.array([a for a, _ in pairs], dtype=float), np.ones(len(pairs))))
    solved = np.linalg.lstsq(design, np.array([p for _, p in pairs]), rcond=None)[0]
    basis = _stored_basis(block)
    return _fit(block, solved[:2].T if basis is None else basis, solved[2])


# ── one timepoint ────────────────────────────────────────────────────────────
@dataclass
class Origin:
    method: str                      # MANUAL or PLATE_RIGID
    position: tuple | None = None    # a hand-set anchor, common (well) frame
    prediction: object = None        # an OriginPrediction, common frame
    digest: str = ""                 # the plate's anchors as they were (``nanowell_task._stamp_anchors``)


@dataclass
class Plan:
    """What addressing one timepoint changes, decided in full before anything is written."""
    timepoint: str
    origin: tuple
    agreement: str
    addresses: dict                  # site row index (in the table) -> (q, r)
    cells: dict                      # cell row index -> (q, r)
    roster: frozenset
    consistency: dict = field(default_factory=dict)      # timepoint -> Consistency


def _resolve(fit, origin: Origin, detected) -> tuple[tuple, str]:
    from pipeline.nanowell_lattice import resolve_origin

    if origin.method == MANUAL:
        resolved = resolve_origin(fit, manual=origin.position, detected=detected, fiducial_margin=nr.FIDUCIAL_MARGIN)
    else:
        resolved = resolve_origin(fit, plate_rigid=origin.prediction, fiducial_margin=nr.FIDUCIAL_MARGIN)
    if resolved.status != "anchored":
        raise Refused(resolved.reason)
    return resolved.origin_xy, resolved.agreement


def _laid_out_from(block: dict, fit) -> tuple | None:
    """The site an unaddressed timepoint's rows were laid out from: the one nearest its outline's centre."""
    from pipeline.nanowell_lattice import _snap_origin

    boundary = block.get("array_boundary")
    return None if not boundary else _snap_origin(fit, boundary["centre_um"])[0]


def _index_rows(points: np.ndarray, fit, origin) -> list[tuple[int, int]]:
    from pipeline.nanowell_lattice import index_points

    indexed = index_points(points, fit, origin)
    bad = [e for e in indexed if e.q is None]
    if bad:
        raise Refused(f"{len(bad)} of its {len(indexed)} saved nanowells do not sit on one lattice site each "
                      f"({bad[0].reason.lower()})")
    return [(e.q, e.r) for e in indexed]


def _rim(files: WellFiles, tp: str, fit, origin, mine: dict, radius_um: float) -> frozenset:
    """The well's roster with *tp* anchored, by the run's own rule; refuses when it differs from the kept one."""
    rows_of = _rows_by_timepoint(files)
    anchored = [t for t in files.order() if t != tp and files.grid["timepoints"][t].get("status") == nr.OK]
    kept = files.grid.get("array_radius", {}).get("roster")
    if anchored and (kept is None or files.grid["array_radius"].get("roster_rule") != nr.ROSTER_RULE_EVIDENCE):
        raise Refused("the well's other timepoints were analysed under the older disk rule for its rim, which its "
                      "saved rows cannot reproduce")
    kept = None if kept is None else frozenset(tuple(map(int, a)) for a in kept)
    own_rim = nr.RimFrame.of(fit, origin, radius_um)
    addresses = set(mine.values())
    found = {mine[i] for i in mine if _true(files.sites.rows[i].get("detected"))}
    whole = {mine[i] for i in mine if (_float(files.sites.rows[i].get("imaged_fraction")) or 0.0) >= WHOLE}
    # The rows are the sites its own evidence supported (laid out from this origin, _laid_out_from), so a band site
    # with no row was left out for having been seen whole and empty.
    whole |= own_rim.band - addresses
    evidence = {t: _evidence(rows_of[t]) for t in anchored}
    first = min(anchored + [tp], key=files.order().index)
    rim = own_rim if first == tp else nr.RimFrame.of(
        lattice_of_addressed({**files.grid["timepoints"][first], "timepoint": first}, rows_of[first]), (0.0, 0.0),
        radius_um)
    off = nr._misanchored({**{t: evidence[t][0] for t in anchored}, tp: found}) if anchored else {}
    if off:
        names = ", ".join(f"{t} by {step}" for t, step in sorted(off.items()))
        raise Refused(f"its nanowells and the other timepoints' sit a lattice step apart ({names}): the anchor is "
                      "probably on a neighbour of the central square")
    detected = found.union(*(evidence[t][0] for t in anchored))
    seen = whole.union(*(evidence[t][1] for t in anchored), (rim.band - kept) if kept is not None else ())
    roster = rim.supported(detected, seen)
    if kept is not None and roster != kept:
        raise Refused(f"its nanowells change the well's rim ({len(roster - kept)} added, {len(kept - roster)} "
                      "removed), so the well's other timepoints would need measuring again")
    if roster != addresses:
        raise Refused(f"its saved rows hold {len(addresses - roster)} nanowell(s) the rim leaves out and lack "
                      f"{len(roster - addresses)} it includes")
    return roster


def _rows_by_timepoint(files: WellFiles) -> dict:
    out: dict = {}
    for row in files.sites.rows:
        out.setdefault(row["timepoint"], []).append(row)
    return out


def _cells(files: WellFiles, tp: str, mine: dict, pitch: float) -> dict:
    """Each of *tp*'s cells to the nanowell it was counted in: the nearest row within half a pitch. Refuses unless
    every row ends with exactly the cells its ``n_cells`` says."""
    if files.cells is None:
        return {}
    index = [i for i, row in enumerate(files.cells.rows) if row.get("timepoint") == tp]
    sites = sorted(mine)
    if not index:
        counted = sum(int(_float(files.sites.rows[i].get("n_cells")) or 0) for i in sites)
        if counted:
            raise Refused(f"its nanowells count {counted} cell(s) and the per-cell table holds none of them")
        return {}
    centres = np.array([_position(files.sites.rows[i]) for i in sites])
    out, tally = {}, dict.fromkeys(sites, 0)
    for k in index:
        point = np.array(_position(files.cells.rows[k]))
        distance = np.hypot(*(centres - point).T)
        j = int(np.argmin(distance))
        if distance[j] > pitch / 2:
            raise Refused(f"a cell at ({point[0]:.0f}, {point[1]:.0f}) um lies {distance[j]:.0f} um from every "
                          "nanowell, so which one counted it cannot be told")
        out[k] = mine[sites[j]]
        tally[sites[j]] += 1
    wrong = [i for i in sites if tally[i] != int(_float(files.sites.rows[i].get("n_cells")) or 0)]
    if wrong:
        raise Refused(f"the cells do not match the nanowells' counts at {len(wrong)} nanowell(s), so they cannot be "
                      "given addresses")
    return out


def _consistency(files: WellFiles, tp: str, roster: frozenset) -> dict:
    """``timepoint -> Consistency`` over the well's anchored timepoints with *tp* among them, as a run computes it
    (``nanowell_run._consistency``): pitch against the first in time, and each one's roster (the shared one)."""
    from pipeline.nanowell_sites import TimepointLattice, check_consistency

    blocks = files.grid["timepoints"]
    anchored = [t for t in files.order() if t == tp or blocks[t].get("status") == nr.OK]
    lattices = [TimepointLattice(t, float(blocks[t]["pitch_um"]), float(blocks[t]["angle_deg"]), 0.0, roster)
                for t in anchored]
    reference = lattices[0].angle_deg
    lattices = [replace(t, plate_rotation_deg=t.angle_deg - reference) for t in lattices]
    return {c.timepoint: c for c in check_consistency(lattices)}


def plan_timepoint(files: WellFiles, tp: str, origin: Origin) -> Plan:
    """Decide everything addressing *tp* changes, or raise :class:`Refused`. Writes nothing."""
    block = files.grid.get("timepoints", {}).get(tp)
    if block is None:
        raise Refused("the grid record has no such timepoint")
    if block.get("status") != nr.UNADDRESSED:
        raise Refused(f"it is {block.get('status')}, not unaddressed" + (
            f" (origin from {block.get('origin_method')})" if block.get("status") == nr.OK else ""))
    if (files.grid.get("settings") or {}).get("geometry") == nr.GEOMETRY_INFER:
        raise Refused("its geometry is inferred from the plate, whose array is not decided by a radius")
    radius_um = files.grid.get("array_radius", {}).get("radius_um")
    if radius_um is None:
        raise Refused("the well has no shared array radius")
    index = [i for i, row in enumerate(files.sites.rows) if row["timepoint"] == tp]
    if len(index) < 6:
        raise Refused("too few saved nanowells to place a lattice")
    points = np.array([_position(files.sites.rows[i]) for i in index])
    fit = lattice_of_unaddressed(block, points)
    detected = points[[_true(files.sites.rows[i].get("detected")) for i in index]]
    position, agreement = _resolve(fit, origin, detected)
    laid = _laid_out_from(block, fit)
    if laid is None or np.linalg.norm(np.subtract(laid, position)) > fit.pitch / 4:
        raise Refused("its saved nanowells were laid out from the site nearest the array's outline, which is not the "
                      "site its origin names, so its rim cannot be decided from them")
    mine = dict(zip(index, _index_rows(points, fit, position)))
    roster = _rim(files, tp, fit, position, mine, float(radius_um))
    cells = _cells(files, tp, mine, fit.pitch)
    return Plan(tp, tuple(map(float, position)), agreement, mine, cells, roster, _consistency(files, tp, roster))


def apply_plan(files: WellFiles, plan: Plan, origin: Origin) -> None:
    """Write *plan* into the well's tables and grid record (in memory; :meth:`WellFiles.save` writes them)."""
    blocks = files.grid["timepoints"]
    block = blocks[plan.timepoint]
    kind = block.get("lattice_kind")
    for i, (q, r) in plan.addresses.items():
        files.sites.rows[i].update(nw_q=str(q), nw_r=str(r), nw_ring=str(nr.lattice_ring(q, r, kind)))
    for k, (q, r) in plan.cells.items():
        files.cells.rows[k].update(nw_q=str(q), nw_r=str(r), nw_ring=str(nr.lattice_ring(q, r, kind)),
                                   nw_label=f"{q}_{r}")
    stage = block.get("stage_offset_um")
    block.update(status=nr.OK, reason="", origin_method=origin.method, origin_agreement=plan.agreement,
                 origin_stage_um=None if stage is None else [plan.origin[0] + stage[0], plan.origin[1] + stage[1]],
                 anchors_digest=origin.digest,
                 readdressed={"from": "saved measurements", "origin_method": origin.method,
                              "origin_um": list(plan.origin)})
    if origin.method == MANUAL:
        block["manual_anchor_um"] = [float(v) for v in origin.position]
    files.grid.setdefault("array_radius", {}).update(roster=[list(a) for a in sorted(plan.roster)],
                                                      roster_rule=nr.ROSTER_RULE_EVIDENCE)
    for t, agree in plan.consistency.items():
        blocks[t].update(consistency=agree.status, consistency_detail=agree.detail)
    for row in files.sites.rows:
        if row["timepoint"] in plan.consistency:
            row["consistency"] = plan.consistency[row["timepoint"]].status
    files.changed = True


def confirm_timepoint(files: WellFiles, tp: str, origin: Origin) -> None:
    """An anchor set on a timepoint the run already addressed: when it names the same site, record it as a run would
    (the origin from the anchor, agreeing with what found it before), so a later incremental run keeps the
    timepoint; when it names another site, refuse — moving every address needs the well measured again."""
    block = files.grid["timepoints"][tp]
    rows = _rows_by_timepoint(files).get(tp, [])
    fit = lattice_of_addressed({**block, "timepoint": tp}, rows)
    detected = np.array([_position(r) for r in rows if _true(r.get("detected"))]).reshape(-1, 2)
    position, _ = _resolve(fit, origin, detected)
    if np.linalg.norm(np.subtract(position, fit.phase_origin)) > fit.pitch / 4:
        raise Refused(f"it is addressed from {block.get('origin_method')} at another nanowell than the anchor's, and "
                      "moving every address needs the well run again")
    before = block.get("origin_method")
    block.update(origin_method=MANUAL, manual_anchor_um=[float(v) for v in origin.position],
                 origin_agreement="agree" if before in ("fiducial", "array_centre") else "single_source",
                 anchors_digest=origin.digest)
    files.changed = True


def readdress_timepoint(files: WellFiles, tp: str, origin: Origin) -> Plan:
    plan = plan_timepoint(files, tp, origin)
    apply_plan(files, plan, origin)
    return plan


# ── a dataset ────────────────────────────────────────────────────────────────
@dataclass
class Summary:
    addressed: dict = field(default_factory=dict)     # "well/timepoint" -> origin method
    refused: dict = field(default_factory=dict)       # "well/timepoint" -> why

    def as_dict(self) -> dict:
        return {"addressed": dict(sorted(self.addressed.items())), "refused": dict(sorted(self.refused.items()))}


def csv_prefix_of(output_dir: Path) -> str:
    try:
        info = json.loads((Path(output_dir) / "pipeline_info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "measurements"
    return str((info.get("execution_options") or {}).get("csv_prefix") or "measurements")


def readdress_dataset(output_dir, input_dir, wells=None) -> Summary:
    """Address every anchored, unaddressed timepoint of the dataset from its saved rows, then whatever the rest of
    the plate can now place. *wells* limits the anchors applied (the end-of-run pass still reads the whole plate).
    Raises ``nanowell_task.NanowellInputError`` for an anchors file that cannot be used."""
    from pipeline.nanowell_task import _well_manual_anchors

    output_dir, summary = Path(output_dir), Summary()
    prefix = csv_prefix_of(output_dir)
    for well in sorted(_anchored_wells(input_dir, wells)):
        anchors, digest = _well_manual_anchors(Path(input_dir), well)
        try:
            files = WellFiles.load(output_dir, well, prefix)
        except Refused as exc:
            summary.refused.update({f"{well}/{tp}": str(exc) for tp in anchors})
            continue
        for tp, position in sorted(anchors.items()):
            block = files.grid.get("timepoints", {}).get(tp, {})
            if block.get("status") == nr.OK and block.get("manual_anchor_um") == [float(v) for v in position]:
                continue                                           # already addressed from this anchor
            _attempt(files, tp, Origin(MANUAL, tuple(position), digest=digest), summary)
        if files.changed:
            files.save()
    _plate_pass(output_dir, input_dir, prefix, summary)
    _record(output_dir, summary)
    return summary


def _anchored_wells(input_dir, wells) -> set:
    from util.nanowell_anchors import anchors_path, read_anchors

    found = {well for well, _tp in read_anchors(anchors_path(input_dir))}
    return found if wells is None else found & {str(w) for w in wells}


def _attempt(files: WellFiles, tp: str, origin: Origin, summary: Summary) -> None:
    key = f"{files.well}/{tp}"
    confirming = origin.method == MANUAL and files.grid.get("timepoints", {}).get(tp, {}).get("status") == nr.OK
    try:
        (confirm_timepoint if confirming else readdress_timepoint)(files, tp, origin)
    except Refused as exc:
        summary.refused[key] = str(exc)
        log.warning("Nanowell %s not addressed from its saved rows: %s.", key, exc)
        return
    summary.addressed[key] = origin.method
    summary.refused.pop(key, None)
    log.info("Nanowell %s addressed from its saved rows (origin from %s).", key, origin.method)


def _plate_pass(output_dir: Path, input_dir, prefix: str, summary: Summary) -> None:
    """The run's end-of-run pass on the saved records: each unaddressed (well, timepoint) the plate's other wells
    now place is addressed from its rows, through the same acceptance rule."""
    from pipeline.nanowell_task import _read_grids, _well_manual_anchors, plan_reanchor, plates_of

    grids = _read_grids(output_dir)
    by_settings: dict = {}
    for well, grid in grids.items():
        by_settings.setdefault(json.dumps(grid.get("settings"), sort_keys=True), {})[well] = grid
    for group in by_settings.values():
        for members in plates_of(group).values():
            predictions, reasons = plan_reanchor(members)
            for well, by_tp in sorted(predictions.items()):
                _, digest = _well_manual_anchors(Path(input_dir), well)
                try:
                    files = WellFiles.load(output_dir, well, prefix)
                except Refused as exc:
                    summary.refused.update({f"{well}/{tp}": str(exc) for tp in by_tp})
                    continue
                for tp, prediction in sorted(by_tp.items()):
                    shift = np.asarray(files.grid["timepoints"][tp].get("montage_origin_um") or (0.0, 0.0))
                    common = replace(prediction, position=tuple(np.asarray(prediction.position) + shift))
                    _attempt(files, tp, Origin(PLATE_RIGID, prediction=common, digest=digest), summary)
                if files.changed:
                    files.save()


def _record(output_dir: Path, summary: Summary) -> None:
    """``nanowell.readdress`` in ``pipeline_info.json``, and the run's ``nanowell.reanchor`` summary brought up to
    date: what the plate addressed, and every timepoint still unaddressed with why."""
    from pipeline.io import _safe_atomic_text_write
    from pipeline.nanowell_task import _read_grids
    from pipeline.run_report import merge_into_pipeline_info

    info_path = output_dir / "pipeline_info.json"
    try:
        existing = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
    except (OSError, ValueError) as exc:
        log.warning("Could not read %s to record the re-address (%s).", info_path, exc)
        return
    block = dict(existing.get("nanowell", {}))
    reanchor = dict(block.get("reanchor") or {})
    carried = {key for key, method in summary.addressed.items() if method == PLATE_RIGID}
    reanchor["anchored"] = sorted(set(reanchor.get("anchored", ())) | carried)
    remaining = {}
    for well, grid in _read_grids(output_dir).items():
        for tp, b in grid.get("timepoints", {}).items():
            if b.get("status") == nr.UNADDRESSED:
                key = f"{well}/{tp}"
                remaining[key] = summary.refused.get(key) or (reanchor.get("remaining") or {}).get(key) or b.get(
                    "reason", "")
    reanchor["remaining"] = remaining
    block["reanchor"] = reanchor
    block["readdress"] = summary.as_dict()
    try:
        merge_into_pipeline_info(info_path, {"nanowell": block}, atomic_writer=_safe_atomic_text_write)
    except (OSError, ValueError) as exc:
        log.warning("Could not record the re-address in %s: %s", info_path, exc)
