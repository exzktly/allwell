"""Classify how far ``version.py`` moved between the base branch and HEAD.

Split out of ``scripts/preflight.sh`` so it can be tested. CLAUDE.md §6 says
every pull request bumps the version exactly once, and the check enforced only
half of that: it failed a branch that moved more than one increment and let a
branch that moved none pass with "unchanged". Two versions on ``main`` each
shipped two pull requests' contents that way — one PR's bump collided with the
PR merged before it and nobody re-bumped, and another carried no bump at all.

Verdicts, one on stdout:

    ONE         exactly one increment (patch, minor with patch reset, or major
                with both reset) — the only passing verdict
    SAME        no bump at all
    MANY        more than one increment, or a move backwards or sideways
    UNREADABLE  one side is not a three-part dotted integer version

A stacked branch (one based on another unmerged branch rather than on
``main``) measured against ``origin/main`` reports ``MANY`` when the branch
below carries its own bump; that is a property of the comparison, not of the
branch, and the bump must not be "corrected" to satisfy it (CLAUDE.md §6).
"""

from __future__ import annotations

import sys


def _parse(version: str) -> tuple[int, int, int] | None:
    try:
        parts = tuple(int(x) for x in version.strip().split("."))
    except ValueError:
        return None
    if len(parts) != 3:
        return None
    return parts  # type: ignore[return-value]


def classify(base: str, head: str) -> str:
    """The verdict for a move from *base* to *head* (see the module docstring)."""
    b, h = _parse(base), _parse(head)
    if b is None or h is None:
        return "UNREADABLE"
    if h == b:
        return "SAME"
    one = (h == (b[0], b[1], b[2] + 1)
           or h == (b[0], b[1] + 1, 0)
           or h == (b[0] + 1, 0, 0))
    return "ONE" if one else "MANY"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: version_delta.py <base-version> <head-version>", file=sys.stderr)
        return 2
    print(classify(argv[1], argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
