# Delete layer — remediation tracker

A user's remote home folder was emptied by an `rm -rf` that reached the host cut (Git for Windows' `ssh.exe` caps a
command at ~8,186 characters; PR #1257). An audit of every delete the app performs found more sites that could lose
data that is not the app's. This tracker holds the work of putting every recursive delete behind one layer.

**The layer.** Remote: `services/remote_delete.py` — `remote_delete(...)` checks each target is strictly inside a
declared root in Python, then sends `aw_rm`, a shell function that re-checks on the host with resolved paths
(`pwd -P`), refuses the root itself, `$HOME`, `/`, anything not owned by the user, and removes with
`--one-file-system`. Node-side scripts embed `aw_rm`. Local: `util/safe_delete.safe_rmtree(path, inside=root)`.
A guard test fails on any recursive delete outside the layer that is not a named exemption with its reason.

States: `todo` · `doing` · `done` (a test pins it, watched failing without the fix) · `declined` (reason at the site
and here). Counts are read from the rows below, not typed.

The local rows are `done` when the guard (L3) was watched red naming the site and the site now goes through the layer or
is a named exemption with its reason, unless the row also changed behaviour, which then needs its own test.

## Layer

| # | Item | State | Evidence |
|---|---|---|---|
| L1 | `services/remote_delete.py`: Python check + host-side `aw_rm` | done | `tests/test_remote_delete_layer.py` (bash, dash, sh: inside the root, refused outside it however spelled, a symlink removed as a link, home or `/` roots refused, a refused delete never sent, `find -exec` through `exec_sh`, a missing root a no-op). Mutations, each caught: `aw_rm` without its inside-the-root check (13 failed), with home and `/` accepted as roots (3), removing the target as written instead of through its resolved parent (11), the Python `refusal` accepting everything (17) |
| L2 | `util/safe_delete.py`: `safe_rmtree(path, inside=root)` | done | `test_l2_*` (inside removed, link refused and its target kept, `..` and a parent link refused, home and filesystem-root roots refused). Mutation: `refusal` accepting everything, 4 failed, F7's link case among them |
| L3 | Guard: no recursive delete outside the layer, exemptions named | done | `tests/test_every_recursive_delete_goes_through_the_layer.py`, watched red naming 34 sites before the roster; `tests/test_guards_bite.py::test_recursive_delete_guard_*` (9 shapes caught, 6 lookalikes cleared); mutating the routing check let both unrouted `rm` argv shapes through, emptying the regex let all four shell shapes through |

## Audit findings (the seven)

| # | Site | Risk | State | Evidence |
|---|---|---|---|---|
| F1 | `remote_bootstrap.install_miniforge_if_missing`: `rm -rf "$HOME/miniforge3"` when conda is not detected | High | done | Watched red on the code before this change (81749c3), in a worktree, against a stand-in home in the test's temporary folder: the old install script, with `curl`/`wget` stubbed to fail so nothing downloads, removed a `~/miniforge3` it had not installed. Now removes one only with `MINIFORGE_MARKER`, which the install writes; otherwise stops (exit 3). `test_f1_*` |
| F2 | `remote_bootstrap.force_clean_script`: `rm -rf ~/miniforge3 ~/allwell` | High | done | Watched red on the code before this change (81749c3), in a worktree, against a stand-in home in the test's temporary folder: the old `--force` removed a personal `~/miniforge3` and `~/allwell/my-notes.txt`. Now the marker gate for miniforge, and of `~/allwell` only `code/`, not a link. `test_f2_*`; `tests/test_bootstrap_env_repair.py` (its miniforge now carries the marker), which also caught `--force` looping over the literal `__ENVS__` after this change, fixed |
| F3 | `remote_reuse.clean_earlier_attempts`: `rm -rf "$d"`, `d` from the local run journal, unconfined | High | done | Watched red on the code before this change (81749c3), in a worktree, against a stand-in home in the test's temporary folder: the old `_aw_clean` removed `$HOME/thesis` when handed it. Now `confine_to_runs` keeps only run-directory names under the runs root, and the host removes through `aw_rm "$_aw_runs" "$d"`. `test_f3_*`, `tests/test_remote_storage_liveness.py` |
| F4 | `remote_transport.delete_remote_paths`: paths joined unquoted into `rm -f --` | Medium | done | Watched red on the code before this change (81749c3), in a worktree, against a stand-in home in the test's temporary folder: the old unquoted `rm -f -- ~/my stage/…` removed `~/stage/runs/run-a/out/x_A01.csv`, a file of the user's the second word reached. Now `remote_delete.command` under the staging directory. `test_f4_*` |
| F5 | `remote_csv`: output dir unquoted in the gzip loop | Medium | done | Watched red on the code before this change (81749c3), in a worktree, against a stand-in home in the test's temporary folder: the old unquoted gzip loop over `~/a b/out/*_A01.csv` gzipped and removed `~/b/out/x_A01.csv`. Now the directory is quoted. `test_f5_*` |
| F6 | Zip Loose Folder in place deletes original TIFs without re-reading the archive | Medium | done | Watched red on the code before this change (81749c3), in a worktree, against a stand-in home in the test's temporary folder: with an archive that silently lacked one TIF, the old Zip Loose removed that TIF. Now `archive_falls_short` reads the archive back first; a short one keeps every original and stops. `test_f6_*` |
| F7 | `datasets/core/containers.clear_destination`: `rmtree` of a destination's `in/`/`out/`, no overlap check | Medium | done | Watched red on the code before this change (81749c3), in a worktree, against a stand-in home in the test's temporary folder: the old `clear_destination` removed the source dataset's `in/` when the destination was the source. Now an overlap is refused and folders go through `safe_rmtree(inside=dest.root)`. `test_f7_*` |

## Remote recursive deletes to move onto the layer

| # | Site | State | Evidence |
|---|---|---|---|
| R1 | `DatasetCache._ssh_run` `rm` argv (run dir `in/`, final pass, batch staging fallback, runner output and cleanup) | done | Watched red on the code before this change (81749c3), in a worktree, against a stand-in home in the test's temporary folder: the old `DatasetCache._ssh_run` sent `rm -rf ~/.allwell-staging/runs/run-a/escape/thesis` (a link inside staging to the home folder) and removed `~/thesis`. Now every `rm` goes through `aw_rm`. `tests/test_remote_delete_layer.py::test_every_rm_the_cache_sends_goes_through_aw_rm` |
| R2 | `remote_batch_staging.prune_argv` (`find … -exec rm -rf`) | done | `find … -exec` through `exec_sh(remote_dir)`; the guard (L3) catches the `-exec rm` spelling (bite case `find-exec-rm`) and exempts no such site |
| R3 | `remote_code_sync._prune_old_versions` (`find … -exec rm -rf`) | done | As R2, `exec_sh(root)` |
| R4 | `remote_commands` batch driver `rm -rf in && mv b<i> in` (node side) | declined | `rm -rf --one-file-system -- in` of a fixed name in the run directory the driver is in; `aw_rm` would take the launch to 8,268 characters, past the 8,000 cap that keeps it from being cut. Reason at the site and in the guard's roster (`_batch_driver_snippet`) |
| R5 | `remote_reservation` stale sweep and release | done | Watched red on the code before this change (81749c3), in a worktree, against a stand-in home in the test's temporary folder: the old stale sweep, on a stale reservation that was a link to the home folder, emptied the home folder (`rm -rf "$_aw_d"` with the glob's trailing `/` follows the link). The test that first pinned this never let the link look stale, so it passed on the old code too; it now ages the target and fails there. Now `aw_rm`, which removes the link itself. `test_r5_*` |
| R6 | `remote_storage` deletion script (own checks: inside, ours, mine, mount, one filesystem, `pwd -P`) | declined | Its own host-side checks are at least those of `aw_rm`, and each folder is named by the person or by the run that made it. Named exemption (`delete_script`) |

## Local recursive deletes (`shutil.rmtree`) — audit, then the layer or a named exemption

| # | File | State | Evidence |
|---|---|---|---|
| D1 | `datasets/core/containers.py` (F7) | done | `clear_destination` through `safe_rmtree` (F7); `publish_container` is a named exemption: its own `__aw_tmp_` staging, and a destination replaced only by a whole staged copy |
| D2 | `pipeline/io.py` | done | `remove_directory` refuses any name not `_tmp_*` and any link. `test_d2_*`, watched red on the old code (it removed a folder named `A01`) |
| D3 | `services/remote_fetch_place.py` | done | Named exemptions: the per-well CSV transit directory `_new_transit_dir` (mkdtemp) made |
| D4 | `services/remote_fetch.py` | done | Named exemption: the `allwell-merge-*` extraction directory the same function made |
| D5 | `services/remote_staging.py` | done | Named exemption: the staging directory `_open_staging_if_needed` made, holding rewritten copies only |
| D6 | `services/remote_scratch.py` | done | Named exemption: only `allwell-*` entries older than the cutoff directly in the app's scratch and transit folders |
| D7 | `services/acquisition_transport.py` | done | Named exemption: the `allwell-acquisition-*` transit directory the same function made |
| D8 | `pipeline/stardist_cache.py` | done | Found by this audit: the purge removed every `.h5`/`.zip` under `~/.keras/models` and any folder holding a `weights_best.h5` — someone's own Keras models, or the whole folder. Now only `models/StarDist2D|3D`, folders through `safe_rmtree`. `tests/test_model_recovery.py::test_purge_leaves_every_other_keras_model`, watched red on the old code (it removed `models` itself); the tests there now run with a temporary `HOME`. The download lock is a named exemption |
| D9 | `util/zip_write.py` | done | Named exemption: `replace_folder_with_archive` removes the folder only when every file in it was archived; `tests/test_no_unarchived_delete.py` holds its callers |
| D10 | `core/local_storage.py` | done | Named exemption: the storage dialog's own listing, re-listed at Clear, links unlinked and never followed |
| D11 | `review/runtime_app.py` | done | `_cleanup_tmp` removed with `DatasetState.tmp_dir`: nothing ever assigned it, so the `rmtree` could only ever have removed whatever someone later wired into it |
| D12 | `review/features/batch_export/service.py`, `_destinations.py` | done | Named exemptions: the `.batch_export_tmp_*` / `.batch_dest_tmp_*` directories made with mkdtemp |
| D13 | `review/data/cell_pool.py` | done | The stale-spill sweep through `safe_rmtree(path, inside=root)`; a pool's own spill (mkdtemp) is a named exemption. `tests/test_cell_pool.py` |
| D14 | `startup/mpl_bootstrap.py` | done | Named exemption: the `allwell-mplfonts-*` directory made with mkdtemp |
| D15 | `scripts/derive_profile.py` | done | Named exemption: a developer tool run on a repository copy, never by the app |
| D16 | `services/remote_fetch_nanowells.py` (not in the original list; the guard found it) | done | The nanowell reference swap through `safe_rmtree(inside=<dataset folder>)`. `tests/test_remote_fetch_nanowells.py` |
