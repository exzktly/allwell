"""Pass or fail a CI test step from its JUnit report rather than pytest's exit code.

PySide6 (offscreen QPA) can raise SIGSEGV in its Qt teardown at interpreter
exit, after every test has passed, which exits the process with code 139. Both
CI test jobs therefore run ``scripts/test.sh ... || true`` and gate here: a test
failure or error, or a mid-run crash that leaves no report, fails the job; only
the post-success teardown segfault is tolerated. pytest writes the report at
session end, before the atexit teardown that segfaults, so a clean run always
produces it.

    python scripts/junit_gate.py .pytest-report.xml
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main(argv: list[str]) -> int:
    report = Path(argv[1] if len(argv) > 1 else ".pytest-report.xml")
    if not report.exists():
        print("No pytest report written — pytest crashed before finishing.")
        return 1
    root = ET.parse(report).getroot()
    suites = root.findall("testsuite") or ([root] if root.tag == "testsuite" else [])
    tests = sum(int(s.get("tests", 0)) for s in suites)
    failures = sum(int(s.get("failures", 0)) for s in suites)
    errors = sum(int(s.get("errors", 0)) for s in suites)
    print(f"pytest report: tests={tests} failures={failures} errors={errors}")
    return 1 if (tests == 0 or failures or errors) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
