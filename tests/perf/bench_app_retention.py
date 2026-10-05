#!/usr/bin/env python3
"""Measure what a closed ``ReviewApp`` leaves behind, construction after construction.

Run directly; this is a script a human reads, not a test, and it asserts
nothing. Nothing it prints is evidence that a change works — behaviour is pinned
by tests — but it is the measurement the retained-ownership work
(`_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`, workstream Q) is judged
against, so it reports enough about its environment for two runs to be
compared and says so when a metric is missing rather than printing zero.

    python tests/perf/bench_app_retention.py                       # 5 iterations, mini dataset
    python tests/perf/bench_app_retention.py --iterations 3 --fixture empty
    python tests/perf/bench_app_retention.py --json /tmp/app-retention.json
    python tests/perf/bench_app_retention.py --roots               # Q1 diagnostics

Each iteration, in one process with one ``QApplication``:

1. construct ``ReviewApp`` (and, with the default ``mini`` fixture, load the
   same mini dataset the full-app tests load);
2. record the live QObject/QWidget counts, the Python wrapper count and RSS;
3. close it, deliver deferred deletes, drop every strong reference this script
   holds, and collect garbage;
4. record the same numbers again, plus whether the ``ReviewApp`` itself is
   still alive (a weak reference) and the surviving Python objects of Qt types,
   grouped by type.

``--roots`` adds Q1's attribution: for the surviving ``ReviewApp`` (if any) and
a bounded sample of surviving widgets, the referrer chain types that keep them
alive — types and attribute names only, never object representations.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import sys
import tempfile
import time
import weakref
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _rss_bytes() -> tuple[int | None, str]:
    """Current resident set size, and where the number came from.

    ``/proc/self/statm`` gives the current figure on Linux. ``resource``'s
    ``ru_maxrss`` is a *peak*, not a current value, so it is reported under a
    different source name and must not be compared with a ``/proc`` reading.
    """
    statm = Path("/proc/self/statm")
    if statm.exists():
        pages = int(statm.read_text().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE"), "proc-statm-current"
    try:
        import resource
    except ImportError:
        return None, "unavailable"
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB, macOS bytes.
    scale = 1 if sys.platform == "darwin" else 1024
    return peak * scale, "resource-ru_maxrss-peak"


def _qt_python_objects() -> Counter:
    """Live Python objects whose type comes from PySide6, by type name."""
    counts: Counter = Counter()
    for obj in gc.get_objects():
        mod = getattr(type(obj), "__module__", "")
        if isinstance(mod, str) and mod.startswith("PySide6"):
            counts[type(obj).__name__] += 1
    return counts


def _app_typed_survivors() -> Counter:
    """Live Python objects whose class is defined in this repository's code."""
    roots = ("review.", "widgets.", "ui.", "core.", "analyze.", "datasets.")
    counts: Counter = Counter()
    for obj in gc.get_objects():
        mod = getattr(type(obj), "__module__", "")
        if isinstance(mod, str) and mod.startswith(roots) and not isinstance(obj, type):
            counts[f"{mod}.{type(obj).__name__}"] += 1
    return counts


def _qt_counts(qapp) -> dict:
    from PySide6.QtWidgets import QApplication

    widgets = QApplication.allWidgets()
    top = QApplication.topLevelWidgets()
    return {"qwidgets": len(widgets), "top_level_widgets": len(top)}


def _flush_deletes(qapp) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        qapp.processEvents()


def _referrer_chain(obj, depth: int = 4, width: int = 6) -> list[str]:
    """Up to *depth* levels of referrer types for *obj*, never their values.

    Frames, this script's own containers and the gc's own lists are skipped so
    the chain describes the application's references, not the measurement.
    """
    skip = {id(sys._getframe())}
    lines: list[str] = []
    frontier = [obj]
    for level in range(depth):
        nxt = []
        for target in frontier:
            for ref in gc.get_referrers(target):
                if id(ref) in skip or isinstance(ref, type(sys._getframe())):
                    continue
                kind = type(ref).__name__
                where = ""
                if isinstance(ref, dict):
                    owner = next((o for o in gc.get_referrers(ref)
                                  if getattr(o, "__dict__", None) is ref), None)
                    if owner is not None:
                        key = next((k for k, v in ref.items() if v is target), "?")
                        where = f" {type(owner).__module__}.{type(owner).__name__}.{key}"
                elif kind in ("cell", "function", "method"):
                    where = f" {getattr(ref, '__qualname__', '')}"
                lines.append(f"{'  ' * level}{kind}{where}")
                nxt.append(ref)
                if len(nxt) >= width:
                    break
        frontier = nxt
        if not frontier:
            break
    return lines


def _build_fixture(kind: str) -> Path | None:
    if kind == "empty":
        return None
    from tests.fixtures.mini_dataset import build_mini_dataset

    base = Path(tempfile.mkdtemp(prefix="allwell-retention-"))
    return build_mini_dataset(base / "mini_dataset")


def run(iterations: int, fixture: str, roots: bool) -> dict:
    from PySide6.QtWidgets import QApplication

    qapp = QApplication.instance() or QApplication([])
    dataset = _build_fixture(fixture)
    from review.runtime_app import ReviewApp

    gc.collect()
    _flush_deletes(qapp)
    rss0, rss_source = _rss_bytes()
    start = {"rss": rss0, **_qt_counts(qapp),
             "pyside_objects": sum(_qt_python_objects().values())}
    records = []
    for i in range(iterations):
        t0 = time.perf_counter()
        app = ReviewApp()
        if dataset is not None:
            app._load_path(dataset)
        qapp.processEvents()
        built_s = time.perf_counter() - t0
        rss_open, _ = _rss_bytes()
        open_counts = _qt_counts(qapp)
        open_children = len(app.findChildren(__import__(
            "PySide6.QtCore", fromlist=["QObject"]).QObject))

        alive = weakref.ref(app)
        app.close()
        app.deleteLater()
        del app
        _flush_deletes(qapp)
        gc.collect()
        _flush_deletes(qapp)
        rss_closed, _ = _rss_bytes()
        survivors = _qt_python_objects()
        app_types = _app_typed_survivors()
        record = {
            "iteration": i + 1,
            "build_seconds": round(built_s, 3),
            "open": {"rss": rss_open, "app_qobject_children": open_children,
                     **open_counts},
            "closed": {
                "rss": rss_closed,
                "review_app_alive": alive() is not None,
                **_qt_counts(qapp),
                "pyside_objects": sum(survivors.values()),
                "app_typed_objects": sum(app_types.values()),
            },
            "top_pyside_survivors": survivors.most_common(12),
            "top_app_survivors": app_types.most_common(12),
        }
        if roots and alive() is not None:
            record["review_app_referrers"] = _referrer_chain(alive())
        records.append(record)
        print(f"[{i + 1}/{iterations}] built {built_s:.2f}s  "
              f"open rss {(rss_open or 0) / 2**20:.0f} MiB, "
              f"{open_children} QObject children  ->  closed rss "
              f"{(rss_closed or 0) / 2**20:.0f} MiB, ReviewApp alive="
              f"{record['closed']['review_app_alive']}, "
              f"widgets={record['closed']['qwidgets']}, "
              f"pyside objs={record['closed']['pyside_objects']}, "
              f"app objs={record['closed']['app_typed_objects']}",
              flush=True)

    import PySide6
    from PySide6.QtCore import qVersion
    return {
        "environment": {
            "os": platform.platform(),
            "python": platform.python_version(),
            "pyside": PySide6.__version__,
            "qt": qVersion(),
            "qpa": os.environ.get("QT_QPA_PLATFORM"),
            "rss_source": rss_source,
        },
        "fixture": fixture,
        "iterations": iterations,
        "start": start,
        "records": records,
        "per_iteration_growth": _growth(records),
    }


def _growth(records: list[dict]) -> dict:
    """Mean per-iteration growth of each closed-state metric after the first.

    The first iteration pays one-off costs (imports, font registration, caches),
    so growth is measured from it rather than from the start.
    """
    if len(records) < 2:
        return {}
    first, last = records[0]["closed"], records[-1]["closed"]
    n = len(records) - 1
    out = {}
    for key in ("rss", "qwidgets", "top_level_widgets", "pyside_objects",
                "app_typed_objects"):
        a, b = first.get(key), last.get(key)
        out[key] = None if a is None or b is None else round((b - a) / n, 1)
    out["review_apps_alive_at_end"] = sum(
        1 for r in records if r["closed"]["review_app_alive"])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iterations", type=int, default=5)
    ap.add_argument("--fixture", choices=("mini", "empty"), default="mini")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--roots", action="store_true",
                    help="attribute what keeps a closed ReviewApp alive (slower)")
    args = ap.parse_args(argv)
    result = run(max(1, args.iterations), args.fixture, args.roots)
    growth = result["per_iteration_growth"]
    print("\nper-iteration growth after the first:", json.dumps(growth))
    print("environment:", json.dumps(result["environment"]))
    if args.json:
        args.json.write_text(json.dumps(result, indent=2))
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
