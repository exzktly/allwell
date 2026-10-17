# Review fix tracker — Playground nanowell search and the cell pool

Live state for the review findings this branch owns (reviews of PRs #992 and #997). One row per finding.
Counts are read from the rows, never typed. This file is deleted by the commit that closes its last row.

| ID | Severity | Defect | State | Test |
|---|---|---|---|---|
| H4 | High | `nanowell_compute._field_rows`: on a hexagonal lattice turned 35–55° the lattice fit folds onto another row, the square's edges read ±30° against it, and the Playground suggests that ±30 as the Square edge angle — a value that makes `check_orientation` pass and the run address on the wrong row. | done — the field stays blank with a "folded: a run will refuse this rotation" note when a hexagonal reading is beyond the run's 15° tolerance; red first on unfixed code ("the Playground offers '-30'", 4 of 4 rotations) | `tests/test_playground_nanowell_compute.py::test_a_folded_hexagonal_lattice_is_reported_and_never_given_an_angle_that_hides_the_fold`, `::test_an_unfolded_hexagonal_lattice_keeps_the_default_the_run_accepts` |
| M14 | Med | `nanowell_compute.suggest_from_image`: a tile that never shows the array's rim gets an Array geometry (and an array centre and ring count) from a circle fitted to the visible patch, which flips with the tile's position. | open | — |
| L21 | Low | `nanowell_compute.archive_contents` needs exactly the schema's token count; the run's parser accepts extra trailing tokens (`exp_Phase_A01_F0_T0_z0.tif`). Also: the controller's dataset-schema branch has no test. | open | — |
| M8 | Med | `cell_pool.CellPool.add` maps only `OutOfMemoryException` / `IOException` to `CellPoolFullError`; a file-backed pool on a full disk raises `FatalException` / `TransactionException`, uncaught, and every later query on the invalidated database raises. Group and well are listed before the insert. | open | — |
| L9 | Low | `cell_pool.CellPool`: the file-backed table has no disk cap, so a large selection can take the volume to 0 bytes free. | open | — |
| L10 | Low | `cell_pool.CellPool`: `allwell-cellpool-*` spill directories left by a crash are never swept. | open | — |
