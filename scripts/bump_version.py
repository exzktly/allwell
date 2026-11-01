"""Bump ``version.py`` by one increment. Run by ``.github/workflows/version-bump.yml`` after each push to ``main``.

Pull requests do not touch ``version.py``. Every pull request used to bump it, so any two pull requests open at the
same time edited the same line: the second to merge either conflicted or landed with no increment of its own, and
each fix meant re-running the whole of CI for a one-line change. Bumping after the merge, on ``main`` itself, gives
every merge exactly one increment with nothing for two branches to disagree about.

Which increment is taken from the merged pull request's labels: ``major`` or ``minor`` select those, and anything
else (no label, or a push that came from no pull request) is a patch.

    python scripts/bump_version.py --labels "minor,docs"     # prints the new version, rewrites version.py
    python scripts/bump_version.py --labels "" --dry-run     # prints it without writing
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_LINE = re.compile(r'^__version__ = "(\d+)\.(\d+)\.(\d+)"$', re.MULTILINE)


def part_for_labels(labels: list[str]) -> str:
    """The increment the merged pull request's labels ask for: ``major`` beats ``minor`` beats the default patch."""
    names = {label.strip().lower() for label in labels}
    if "major" in names:
        return "major"
    if "minor" in names:
        return "minor"
    return "patch"


def bumped(version: tuple[int, int, int], part: str) -> tuple[int, int, int]:
    """*version* moved by one *part* increment, with the lower parts reset."""
    major, minor, patch = version
    if part == "major":
        return (major + 1, 0, 0)
    if part == "minor":
        return (major, minor + 1, 0)
    if part == "patch":
        return (major, minor, patch + 1)
    raise ValueError(f"unknown increment {part!r}")


def rewrite(text: str, part: str) -> tuple[str, str]:
    """``(new_text, new_version)``: *text* with its single ``__version__`` line bumped by *part*.

    Raises ``ValueError`` unless exactly one well-formed ``__version__ = "X.Y.Z"`` line is present, so a malformed
    file fails the workflow loudly rather than being committed half-edited.
    """
    matches = list(_LINE.finditer(text))
    if len(matches) != 1:
        raise ValueError(f'expected one `__version__ = "X.Y.Z"` line, found {len(matches)}')
    match = matches[0]
    new = ".".join(str(n) for n in bumped(tuple(int(g) for g in match.groups()), part))
    return text[:match.start()] + f'__version__ = "{new}"' + text[match.end():], new


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--labels", default="", help="comma-separated labels of the merged pull request")
    parser.add_argument("--file", type=Path, default=_REPO / "version.py")
    parser.add_argument("--dry-run", action="store_true", help="print the new version without writing it")
    args = parser.parse_args(argv)

    part = part_for_labels([name for name in args.labels.split(",") if name.strip()])
    text, new = rewrite(args.file.read_text(encoding="utf-8"), part)
    if not args.dry_run:
        args.file.write_text(text, encoding="utf-8")
    print(new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
