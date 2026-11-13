"""Calibrate the central square's acceptance thresholds against a plate's own answers (tracker F6).

A nanowell timepoint is addressed from its central square only when the most square outline found near the array's
centre leads the runner-up by more than ``nanowell_run.FIDUCIAL_MARGIN`` *and* scores at least
``nanowell_run.FIDUCIAL_RATIO`` times it (``nanowell_lattice.fiducial_accepted``). Both numbers were set on rendered
squares and never calibrated on a real plate. When they are too strict for a plate, the run finds the square and
refuses it: the timepoint is left unaddressed, the QC view asks for an anchor by hand, and the ring it draws — the
run's own guess — is the place the person then clicks.

Every grid record keeps what the square search found at each timepoint, unrounded so that this can be done:
``fiducial.best`` and ``fiducial.runner_up`` (the two highest squareness scores) and ``fiducial.position_um`` (where
the best one is, in the well frame the anchors file uses). Two things on disk say whether that guess was right:

* **an anchor set by hand** (``in/nanowell_anchors.json``) at the same well and timepoint. The guess is *right* when it
  lies within half a pitch of the click — the same nanowell, since the run snaps a click to the nearest site — and
  *wrong* otherwise. The square search does not look at the anchor, so a guess from a run that was handed the anchor
  is still an independent measurement.
* **the identity check** (``pipeline.nanowell_identity``), for a timepoint the run addressed from the square itself
  (``origin_method == "fiducial"``) and nobody anchored: *confirmed* means its addresses name the same nanowells as
  at the reference, so the square was right; *contradicted* because the square the run took lies more than
  ``SQUARE_AGREEMENT_PITCHES`` from where the reference's square correlates means it was wrong. A timepoint
  contradicted for another reason, or not checked, says nothing about its square and is left unlabelled.

From those examples it reports what the current thresholds accept, and searches a grid of (margin, ratio) pairs for
the one that accepts the most right guesses while accepting no wrong one — the most conservative such pair when
several tie. It then counts how many of the timepoints refused today, labelled or not, that pair would accept. It
changes nothing: the thresholds stay where they are until someone sets them from these numbers.

``python -m pipeline.nanowell_square_calibration <output folder> [--input_dir <in folder>] [--csv]`` from source;
the packaged app's executable with :data:`FROZEN_SENTINEL` first (``all_well_launcher``). ``--csv`` prints one row
per timepoint with a square guess instead of the summary, measurements only, so it can be shared.

Qt-free.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

#: The launcher's dispatch argument for this command in the packaged app, where ``python -m`` cannot run.
FROZEN_SENTINEL = "--run-square-calibration"

NANOWELL_DIR = "nanowells"
#: A guess within this many pitches of the hand-set anchor names the same nanowell: the run snaps a click to the
#: nearest lattice site, and the next site is a whole pitch away.
SAME_NANOWELL_PITCHES = 0.5

RIGHT, WRONG = "right", "wrong"
#: The (margin, ratio) pairs searched: margins 0.00 to 0.50 by 0.01, ratios 1.00 to 5.00 by 0.05.
MARGINS = tuple(round(0.01 * i, 2) for i in range(51))
RATIOS = tuple(round(1.0 + 0.05 * i, 2) for i in range(81))
#: Fewer wrong guesses than this, and "accepts none of them" is a statement about a small sample: the summary says so.
FEW_WRONG = 5

CSV_COLUMNS = ("well", "timepoint", "status", "origin_method", "best", "runner_up", "lead", "ratio",
               "accepted_now", "label", "evidence", "distance_pitches")


@dataclass(frozen=True)
class Example:
    """One timepoint whose square search produced a guess."""

    well: str
    timepoint: str
    status: str
    origin_method: str
    best: float
    runner_up: float
    label: str | None = None              # RIGHT, WRONG, or None when nothing on disk says
    evidence: str = ""                    # "anchor" or "identity"
    distance_pitches: float | None = None  # guess to the hand-set anchor, for an anchored timepoint

    @property
    def lead(self) -> float:
        return self.best - self.runner_up

    @property
    def ratio(self) -> float:
        return self.best / self.runner_up if self.runner_up > 0 else math.inf


def accepts(example: Example, margin: float, ratio: float) -> bool:
    """``nanowell_lattice.fiducial_accepted``'s rule on *example*'s two scores."""
    return example.best - example.runner_up > margin and example.best >= ratio * example.runner_up


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pair(value) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    x, y = _number(value[0]), _number(value[1])
    return None if x is None or y is None else (x, y)


def read_grids(out_dir) -> dict[str, dict]:
    """``well -> {timepoint -> grid block}`` from every readable ``nanowells/<well>_grid.json``."""
    from util.well_token import canonical_well_label

    out = {}
    for path in sorted((Path(out_dir) / NANOWELL_DIR).glob("*_grid.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("timepoints"), dict):
            continue
        well = canonical_well_label(str(payload.get("well") or path.name[:-len("_grid.json")]))
        if well is not None:
            out[well] = {str(tp): b for tp, b in payload["timepoints"].items() if isinstance(b, dict)}
    return out


def _identity_entries(out_dir, well: str) -> dict:
    """The well's identity-check entries by timepoint, from a current sidecar; empty without one."""
    from pipeline.nanowell_identity import read_sidecar

    payload = read_sidecar(out_dir, well)
    entries = (payload or {}).get("timepoints")
    return entries if isinstance(entries, dict) else {}


def _identity_label(block: dict, entry) -> str | None:
    """What the identity check says about a square the run accepted itself: RIGHT, WRONG, or ``None``."""
    from pipeline.nanowell_identity import CONFIRMED, CONTRADICTED, SQUARE_AGREEMENT_PITCHES

    if block.get("status") != "ok" or block.get("origin_method") != "fiducial" or not isinstance(entry, dict):
        return None
    if entry.get("verdict") == CONFIRMED:
        return RIGHT
    agreement = _number(entry.get("square_agreement_pitches"))
    if entry.get("verdict") == CONTRADICTED and agreement is not None and agreement > SQUARE_AGREEMENT_PITCHES:
        return WRONG
    return None


def collect(out_dir, anchors: dict) -> tuple[list[Example], dict]:
    """Every timepoint whose square search produced a guess, labelled where the disk says; and counts of the
    timepoints that produced none (``no_guess``: no outline was square enough, or none could be scored), of anchors
    that could not be compared (``anchor_without_guess``), and of grid records read (``wells``)."""
    examples: list[Example] = []
    counts = {"wells": 0, "timepoints": 0, "no_guess": 0, "anchor_without_guess": 0}
    for well, blocks in read_grids(out_dir).items():
        counts["wells"] += 1
        identity = None
        for tp, block in blocks.items():
            counts["timepoints"] += 1
            fid = block.get("fiducial") if isinstance(block.get("fiducial"), dict) else {}
            best, runner_up, guess = _number(fid.get("best")), _number(fid.get("runner_up")), _pair(fid.get("position_um"))
            anchor = anchors.get((well, tp))
            if best is None or runner_up is None or guess is None:
                counts["no_guess"] += 1
                counts["anchor_without_guess"] += anchor is not None
                continue
            label, evidence, distance = None, "", None
            pitch = _number(block.get("pitch_um"))
            if anchor is not None and pitch:
                distance = math.dist(guess, anchor.position()) / pitch
                label, evidence = (RIGHT if distance < SAME_NANOWELL_PITCHES else WRONG), "anchor"
            elif anchor is not None:
                counts["anchor_without_guess"] += 1
            else:
                if identity is None:
                    identity = _identity_entries(out_dir, well)
                label = _identity_label(block, identity.get(tp))
                evidence = "identity" if label else ""
            examples.append(Example(well, tp, str(block.get("status") or ""), str(block.get("origin_method") or ""),
                                    best, runner_up, label, evidence, distance))
    return examples, counts


@dataclass(frozen=True)
class Tally:
    margin: float
    ratio: float
    right: int                  # right guesses accepted
    wrong: int                  # wrong guesses accepted
    refused_now: int            # timepoints the current thresholds refuse whose guess this pair accepts


def tally(examples, margin: float, ratio: float, current: tuple[float, float]) -> Tally:
    right = sum(1 for e in examples if e.label == RIGHT and accepts(e, margin, ratio))
    wrong = sum(1 for e in examples if e.label == WRONG and accepts(e, margin, ratio))
    gained = sum(1 for e in examples if accepts(e, margin, ratio) and not accepts(e, *current))
    return Tally(margin, ratio, right, wrong, gained)


def recommend(examples, current: tuple[float, float]) -> Tally | None:
    """The searched pair accepting the most right guesses and no wrong one; among ties the strictest (largest margin,
    then largest ratio). ``None`` with no right guess to calibrate on."""
    if not any(e.label == RIGHT for e in examples):
        return None
    labelled = [e for e in examples if e.label is not None]
    best_key, best_pair = None, None
    for margin in MARGINS:
        for ratio in RATIOS:
            right = wrong = 0
            for e in labelled:
                if accepts(e, margin, ratio):
                    if e.label == RIGHT:
                        right += 1
                    else:
                        wrong += 1
            if wrong:
                continue
            key = (right, margin, ratio)
            if best_key is None or key > best_key:
                best_key, best_pair = key, (margin, ratio)
    return None if best_pair is None else tally(examples, *best_pair, current)


def _current() -> tuple[float, float]:
    from pipeline.nanowell_run import FIDUCIAL_MARGIN, FIDUCIAL_RATIO

    return float(FIDUCIAL_MARGIN), float(FIDUCIAL_RATIO)


def _spread(values) -> str:
    values = sorted(v for v in values if math.isfinite(v))
    if not values:
        return "none"
    middle = values[len(values) // 2]
    return f"{values[0]:.3f} / {middle:.3f} / {values[-1]:.3f}"


def summary_lines(examples, counts: dict, current: tuple[float, float]) -> list[str]:
    margin, ratio = current
    right = [e for e in examples if e.label == RIGHT]
    wrong = [e for e in examples if e.label == WRONG]
    refused = [e for e in examples if not accepts(e, margin, ratio)]
    lines = [
        f"{counts['wells']} well(s), {counts['timepoints']} timepoint(s) read.",
        f"A square guess at {len(examples)} timepoint(s); none at {counts['no_guess']} (no outline square enough, or "
        "none scored): no threshold on the lead changes those.",
        f"Current thresholds: lead > {margin:g} and best >= {ratio:g} x runner-up. They refuse {len(refused)} of the "
        f"{len(examples)} guesses.",
        "",
        f"Guesses checked against your anchors: {sum(e.evidence == 'anchor' for e in examples)} "
        f"({sum(e.evidence == 'anchor' and e.label == RIGHT for e in examples)} on the nanowell you clicked, "
        f"{sum(e.evidence == 'anchor' and e.label == WRONG for e in examples)} elsewhere). "
        f"{counts['anchor_without_guess']} anchor(s) had no guess to compare.",
        f"Checked by the identity check (squares the run accepted itself): "
        f"{sum(e.evidence == 'identity' and e.label == RIGHT for e in examples)} confirmed, "
        f"{sum(e.evidence == 'identity' and e.label == WRONG for e in examples)} the wrong square.",
        "",
        "                 lead (min / median / max)      ratio (min / median / max)",
        f"  right ({len(right):>4})  {_spread(e.lead for e in right):<30} {_spread(e.ratio for e in right)}",
        f"  wrong ({len(wrong):>4})  {_spread(e.lead for e in wrong):<30} {_spread(e.ratio for e in wrong)}",
        "",
    ]
    now = tally(examples, margin, ratio, current)
    lines.append(f"Now: {now.right} of {len(right)} right guesses accepted, {now.wrong} of {len(wrong)} wrong ones.")
    pick = recommend(examples, current)
    if pick is not None and now.wrong == 0 and now.right == pick.right:
        lines.append("The current thresholds already accept every right guess any searched pair does, and no wrong "
                     "one: nothing to change.")
        return lines
    if pick is None:
        lines.append("No recommendation: no guess is known to be right (set anchors, or run the identity check), or "
                     "every pair searched also accepts a wrong one.")
        return lines
    lines.append(f"Suggested: lead > {pick.margin:g} and best >= {pick.ratio:g} x runner-up — accepts {pick.right} "
                 f"of {len(right)} right guesses and none of the {len(wrong)} wrong ones, and would accept "
                 f"{pick.refused_now} of the {len(refused)} guesses refused today.")
    if len(wrong) < FEW_WRONG:
        lines.append(f"Only {len(wrong)} wrong guess(es) to test against: accepting none of so few says little about "
                     "the rest of the plate. Check a sample of the newly accepted timepoints before relying on it.")
    return lines


def csv_rows(examples, current: tuple[float, float]) -> list[dict]:
    return [{"well": e.well, "timepoint": e.timepoint, "status": e.status, "origin_method": e.origin_method,
             "best": round(e.best, 4), "runner_up": round(e.runner_up, 4), "lead": round(e.lead, 4),
             "ratio": round(e.ratio, 3) if math.isfinite(e.ratio) else "inf",
             "accepted_now": accepts(e, *current), "label": e.label or "", "evidence": e.evidence,
             "distance_pitches": "" if e.distance_pitches is None else round(e.distance_pitches, 3)}
            for e in examples]


def _listing(folder: Path) -> tuple[list[str] | None, str]:
    """``(names, "")`` of *folder*'s entries, or ``(None, why)`` when it cannot be listed."""
    import os

    try:
        return sorted(os.listdir(folder)), ""
    except PermissionError:
        return None, (f"{folder} cannot be read: permission denied. On a Mac this is usually the Terminal not being "
                      "allowed into that location (an external drive, Desktop, Documents or a cloud folder): allow it "
                      "under System Settings → Privacy & Security → Files and Folders (or Full Disk Access) and try "
                      "again")
    except OSError as exc:
        return None, f"{folder} cannot be read: {exc}"


def find_output_dir(given) -> tuple[Path | None, str]:
    """The analysed output folder *given* names — the folder itself, the dataset folder whose ``out/`` it is, or its
    ``nanowells/`` folder — or ``(None, why)``, saying what the path resolved to and what it holds, so a path that
    is wrong, unreadable or simply another folder each reads differently."""
    typed = str(given)
    # Windows' command line reads a backslash before the closing quote ("…\out\") as an escaped quote, so the
    # argument arrives as …\out" with the quote in it; no folder name ends in one, so it is taken off.
    path = Path(typed[:-1] if typed.endswith('"') else typed).expanduser()
    shown = f"{typed!r}" + (f" (resolved to {path.resolve()})" if str(path.resolve()) != typed else "")
    if not path.exists():
        return None, (f"{shown} does not exist. Check the spelling, and quote a path that holds spaces; a ~ inside "
                      "quotes is not expanded by the shell, so write the path out in full or leave the ~ outside them")
    if not path.is_dir():
        return None, f"{shown} is a file, not a folder: give the run's output folder (the one holding {NANOWELL_DIR}/)"
    candidates = [path, path / "out"] + ([path.parent] if path.name == NANOWELL_DIR else [])
    for candidate in candidates:
        names, why = _listing(candidate) if candidate.is_dir() else ([], "")
        if names is None:
            return None, why
        if NANOWELL_DIR in names and (candidate / NANOWELL_DIR).is_dir():
            return candidate, ""
    names, _why = _listing(path)
    held = ", ".join(names[:12]) + (f", … ({len(names)} in all)" if len(names) > 12 else "") if names else "nothing"
    return None, (f"{shown} holds no {NANOWELL_DIR}/ folder, and neither does an out/ inside it, so it is not an "
                  f"analysed nanowell dataset. It holds: {held}")


def main(argv=None) -> int:
    """Print the calibration of *output folder*; exit 2 when it is not an analysed nanowell dataset or its anchors
    file cannot be read, 0 otherwise."""
    import argparse

    from util.nanowell_anchors import AnchorsFileError, anchors_path, read_anchors

    parser = argparse.ArgumentParser(description="Compare the central-square search's scores with the anchors set by "
                                                 "hand and the identity check, and suggest acceptance thresholds.")
    parser.add_argument("output_dir")
    parser.add_argument("--input_dir", help="the dataset's in/ folder (default: the in/ beside the output folder)")
    parser.add_argument("--csv", action="store_true", help="print one row per timepoint with a square guess instead")
    args = parser.parse_args(argv)
    out_dir, why = find_output_dir(args.output_dir)
    if out_dir is None:
        print(why)
        return 2
    input_dir = Path(args.input_dir) if args.input_dir else out_dir.parent / "in"
    try:
        anchors = read_anchors(anchors_path(input_dir))
    except AnchorsFileError as exc:
        print(f"The anchors file in {input_dir} cannot be used: {exc}")
        return 2
    if not anchors:
        print(f"No anchors set by hand in {input_dir}; only the identity check labels guesses.")
    examples, counts = collect(out_dir, anchors)
    current = _current()
    if args.csv:
        writer = csv.DictWriter(sys.stdout, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(csv_rows(examples, current))
    else:
        for line in summary_lines(examples, counts, current):
            print(line)
    return 0


if __name__ == "__main__":                                        # pragma: no cover - the command line
    raise SystemExit(main())
