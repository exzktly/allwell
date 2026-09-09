# Directory-scan branch — review remediation

Adversarial review of the `claude/windows-pipeline-dir-slowness-n4s8z3` branch
(PR #908), run against `3ae64ff`. One row per finding. A row reaches `done` only
when a test pins it and that test has been watched failing without the fix.

Counts are read off the rows below, never typed from memory.

| # | Severity | Finding | State | Where |
|---|---|---|---|---|
| 1 | High | The folder scan applies a schema `schema_infer` itself marks non-confident, so picking a single-channel plate writes `experiment:well:ignore:fov` into the picker and `_schema_errors()` then blocks Run behind a dialog. The branch's own test uses a two-channel plate, the one shape where right and wrong agree. | done | `analyze/input.py` `_autofill_from_input_scan` |
| 2 | High | `folders_after_zipper` still stats every entry (`p.is_dir()` per entry), measured at one stat per loose image and growing linearly, on every FLAT plate at the start of every remote run — three lines from a site that was converted. ARCHITECTURE says all three were swept and that the cost stops growing with the plate; both are false as measured. | done | `services/remote_runner.py` (two sites) |
| 3 | High | `tif_files_in` now raises where `Path.glob` swallowed `PermissionError`, and `count_expected_wells`' documented best-effort fallback is unguarded, so selecting an unreadable folder directly and pressing Run raises out of the resolver with nothing logged — while the readout shows the friendly `UNREADABLE` text. | done | `services/input_resolution_service.py` `count_expected_wells` |
| 4 | Medium-high | "A well archive is a file" was applied to the planner and not to `_well_zips_in`, `input_well_containers`, `pipeline/input_layout.py` or `process_microscopy.py`, so a directory named `A01.zip` is now judged differently by the planner and by the runner that acts on it. | done | `services/remote_scratch.py`, `services/remote_runner.py`, `pipeline/input_layout.py`, `process_microscopy.py` |
| 5 | Low-medium | `_fold_well_folders_into_existing_zips` lost the explicit name-ordering that the same commit restored at its two sibling sites; Windows-only and narrow, but an absence rather than a written exemption. | done, no test | `services/input_resolution_service.py` |
| 6 | Low-medium | `files_and_subdirs`' bare `except OSError: continue` also suppresses the errnos `Path.is_file()` genuinely re-raises (EACCES, EIO), turning them into silently missing images — the docstring claims the drop matches `pathlib`. | done | `util/dir_scan.py` |
| 7 | Low | The `apt-get update` change can turn a fetch failure into "Unable to locate package", which still fails the job but no longer names the cause. | done | `.github/workflows/*.yml` |
| 8 | Low | Claim that Paths are sorted rather than names (because `PurePath.__lt__` casefolds on Windows) is untested — the fixture is all-lowercase, where the two orders are identical. | done | `tests/test_dir_scan.py` |

## Notes on two rows

**Row 5 is fixed but carries no test, and that is not an oversight.** Name order
and path order are identical on POSIX for the same strings, so no assertion on
this platform can tell `files.sort()` from `files.sort(key=lambda p: p.name)`.
The reason is written at the site instead, and
`test_dir_scan.py::test_path_order_is_not_name_order_on_windows` pins the
`pathlib` premise the whole choice rests on — and says in its own docstring which
half it cannot cover.

**Row 4 was converted at two sites of four.** `remote_scratch._well_zips_in` and
`pipeline.input_layout.find_well_zips` judge *which archives are wells*, and they
had to agree with the Analyze planner. `process_microscopy.py:3689` and
`pipeline/capacity.py:112` also glob `*.zip`, but neither is asking that question
— one collects member stems from every archive present, the other sizes the
largest member — and both hand the path straight to `zipfile.ZipFile`, which
raises on a directory and is handled beside the call. Declined as not siblings,
with the reason recorded rather than the sites silently skipped.

`find_well_folders` was converted as well, and was a genuine miss: it asked
`p.is_dir()` per entry inside the *pipeline*, on exactly the FLAT layout it is
called for.
