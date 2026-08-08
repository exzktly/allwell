"""Decide what a guard's run on the base branch actually proved.

Split out of ``scripts/prove_guard.sh`` so it can be tested: this is the piece
that decides whether a proof counts, and it has already been wrong once. Its
first version matched pytest's summary with ``^\\d+ passed``, which never fires —
pytest pads that line with ``=``, so it does not begin with the count — and the
script reported "could not tell what failed" for a guard that had cleanly
passed.

The distinction the whole tool exists for: a red run is only evidence if the
guard *asserted*. An import error, a collection error, or an exception raised
while formatting the guard's own failure message all look identical in a
terminal and all measured nothing.

Verdicts, one per line on stdout, offenders following:

    PASSED              the rule already held; the test is a hypothesis
    ERRORED <type>      the guard fell over before measuring anything
    ASSERTED <n>        the guard asserted, naming n distinct sites
    UNCLEAR             a failure this cannot classify
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: `E   SomeError: ...` — the exception type pytest prints in a failure block.
_EXC = re.compile(r"^E\s+([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)):", re.M)
#: `E   path/to/file.py:123 — …` — the offender lines guards emit in their
#: assert message. The convention every guard in `tests/` follows.
_OFFENDER = re.compile(r"^E\s+(\S+\.py:\d+)", re.M)
_BARE_ASSERT = re.compile(r"^E\s+assert", re.M)


def classify(text: str) -> tuple[str, list[str]]:
    """Return ``(verdict, offenders)`` for one pytest run's output."""
    # pytest's summary line is padded with "=", so the count is not at the
    # start of the line. Match it anywhere, and only call it a pass when
    # nothing failed or errored.
    passed = re.search(r"\b\d+ passed\b", text)
    failed = re.search(r"\b\d+ (?:failed|errors?)\b", text)
    if passed and not failed:
        return "PASSED", []

    exc_types = _EXC.findall(text)
    collection = bool(
        re.search(r"ERROR (?:collecting|tests/)", text)
        or "errors during collection" in text
    )
    non_assert = [t for t in exc_types if t != "AssertionError"]

    # A collection error, or any exception that is not the guard's own
    # assertion, means the run never reached the rule.
    if collection or (non_assert and "AssertionError" not in exc_types):
        return "ERRORED", [non_assert[0] if non_assert else "collection error"]
    if "AssertionError" not in exc_types and not _BARE_ASSERT.search(text):
        return "UNCLEAR", []

    return "ASSERTED", sorted(set(_OFFENDER.findall(text)))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: prove_guard_verdict.py <pytest-output-file>", file=sys.stderr)
        return 2
    verdict, extra = classify(Path(argv[1]).read_text(errors="replace"))
    if verdict == "ERRORED":
        print(f"ERRORED {extra[0]}")
    elif verdict == "ASSERTED":
        print(f"ASSERTED {len(extra)}")
        print("\n".join(extra[:10]))
    else:
        print(verdict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
