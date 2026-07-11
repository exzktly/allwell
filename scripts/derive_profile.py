#!/usr/bin/env python3
"""Derive a physically-stripped tree for a build profile.

The runtime profile filter (``well_viewer.core.profile``) lets one codebase
*run* as any subset with no deletion — that is the day-to-day mechanism. This
script is the other half: for a **shippable** thin artifact where you want the
excluded code actually gone, it diffs the full-app catalog (``profiles/full.toml``)
against a target profile and deletes what the target excludes.

    # See what the thin profile would remove (safe, read-only):
    python scripts/derive_profile.py --profile thin

    # Actually delete (run on a branch, review the diff, then commit):
    python scripts/derive_profile.py --profile thin --apply

What it removes: each catalog feature the target profile does not admit — its
source ``paths`` and its ``tests`` globs. What it does NOT do: re-tighten the
ratchet guards (line/complexity/deferred-import budgets) or drop the excluded
feature's descriptor line from ``features/registry.py`` — those are surgical and
are reported as follow-ups so a human keeps the guardrails honest. Run the test
suite after ``--apply``; the profile-boot and feature-removal guards catch any
missed cross-reference.

Idempotent: paths already absent (e.g. on an already-thin tree) are skipped.
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

try:  # tomllib is stdlib on 3.11+; fall back to tomli on 3.10 (project pin).
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - depends on interpreter version
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class Deletion:
    feature: str
    paths: list  # existing paths (files or dirs)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_catalog(path: Path) -> list:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return list(data.get("feature") or [])


def plan_deletions(catalog: list, profile, repo_root: Path) -> list:
    """Return the :class:`Deletion` list a *profile* implies against *catalog*.

    A catalog feature is deleted when ``profile.includes`` rejects a descriptor
    shim built from its ``name`` / ``parent`` — the exact predicate the runtime
    filter uses, so the generated tree and the runtime subset always agree.
    Only paths/globs that resolve to something on disk are included.
    """
    out: list = []
    for entry in catalog:
        name = str(entry.get("name") or "")
        parent = str(entry.get("parent") or "")
        shim = SimpleNamespace(name=name, parent=parent)
        if profile.includes(shim):
            continue
        # ``keep`` globs are tests that *look* like this feature's but exercise
        # shared kept code (e.g. test_smfish_aggregation hits data_loading, not the
        # smFISH tab). Subtract them so an over-broad ``tests`` glob never deletes
        # a kept-code test.
        keep: set = set()
        for pattern in entry.get("keep") or []:
            keep.update(glob.glob(str(repo_root / pattern)))
        found: list = []
        for rel in entry.get("paths") or []:
            p = repo_root / rel
            if p.exists():
                found.append(p)
        for pattern in entry.get("tests") or []:
            for hit in glob.glob(str(repo_root / pattern)):
                if hit not in keep:
                    found.append(Path(hit))
        if found:
            out.append(Deletion(feature=name, paths=found))
    return out


def _rm(p: Path) -> None:
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", required=True,
                    help="target profile name (profiles/<name>.toml) or a path")
    ap.add_argument("--catalog", default=None,
                    help="full-app catalog (default: profiles/full.toml)")
    ap.add_argument("--apply", action="store_true",
                    help="perform deletions (default: dry-run)")
    args = ap.parse_args(argv)

    repo_root = _repo_root()
    # Import the profile loader from the tree being operated on.
    sys.path.insert(0, str(repo_root))
    from well_viewer.core.profile import load_profile

    catalog_path = Path(args.catalog) if args.catalog else repo_root / "profiles" / "full.toml"
    catalog = load_catalog(catalog_path)
    profile = load_profile(args.profile)

    plan = plan_deletions(catalog, profile, repo_root)
    if not plan:
        print(f"Nothing to remove for profile {profile.name!r} "
              "(already stripped, or profile admits every catalog feature).")
        return 0

    verb = "Deleting" if args.apply else "Would delete"
    for d in plan:
        print(f"[{d.feature}]")
        for p in d.paths:
            rel = os.path.relpath(p, repo_root)
            print(f"  {verb}: {rel}")
            if args.apply:
                _rm(p)
    print()
    print(f"{len(plan)} feature(s), "
          f"{sum(len(d.paths) for d in plan)} path(s).")
    if args.apply:
        print("Follow-ups (not automated — keep the guardrails honest):")
        print("  * remove each excluded feature's descriptor from "
              "well_viewer/features/registry.py")
        print("  * re-tighten ratchets: test_monolith_budgets, "
              "test_complexity_ratchet, test_deferred_imports")
        print("  * run: pytest tests/test_feature_profile.py "
              "tests/test_feature_removal.py")
    else:
        print("Dry run — re-run with --apply to delete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
