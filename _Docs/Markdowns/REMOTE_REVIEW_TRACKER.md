# Remote pipeline review — findings tracker

**This document is temporary.** It holds the findings of a full review of the
remote (ssh / Slurm) run path, made because the remote path had lost a run of
plates in quick succession and its owner no longer trusted it. CLAUDE.md §6.1
requires a body of work this size to live on disk rather than in conversation.
It is deleted by the pull request that closes its last row, and that pull
request says so in its body.

## How this review was made, and what the rows are worth

Six reviewers each took one slice of the remote path and were told to run the
code rather than read it wherever they could: driver protocol and allocation
lifecycle; staging and upload; fetch, verification and cleanup; resume,
re-attach and the run journal; the run as the app reports it; and the last
thirty days of fix history. Their probes drove the real `Fetcher`,
`FetchedOutputVerifier`, `release_batch`, `_run_batches`, `PipelineRunner`,
`AnalyzeTab` and the generated bash driver, with only the binaries swapped
(`ssh` / `rsync` / `srun` / `squeue` / `scancel` stand-ins that run the remote
side under local bash and fail on cue).

- **Evidence `REPRODUCED`** means a probe drove the real code to the failure.
  The probes lived in a session scratchpad and are **not** preserved; their
  fixtures were the reviewer's own (CLAUDE.md §7.5 #8). So every row is still a
  hypothesis about the tree as it stood at review time (commit `96b603d` on
  `claude/batch-handoff-ack`): **reproduce it again before fixing it**, and
  turn that reproduction into the regression test.
- **Evidence `INFERRED`** means read from code only.
- Rows that more than one reviewer reported independently are merged; the
  sources column names each.

### States

- **`todo`** — not started.
- **`wip`** — started on a branch, not finished; the row says where it stopped.
- **`done`** — fixed **and** pinned by a test that was watched failing without
  the fix (CLAUDE.md §7.7 #2). Name the test in the row.
- **`declined`** — examined and deliberately not done, with the reason here and
  at the site. Never delete a row.

Counts are not kept in this header; derive them from the rows
(`grep -c '| todo |'`), per CLAUDE.md §6.1 #3.

### Severity

- **CRITICAL** — wrong, stale or missing data reported as a successful result,
  or input / output data destroyed.
- **HIGH** — a run hangs, dies silently or with a message naming the wrong
  cause, or wastes hours of node time.
- **MEDIUM** — degraded behaviour with a workaround.
- **LOW** — cosmetic or narrow.

---

## The one pattern behind most of it

Almost every CRITICAL row is the same shape: **a remote command that failed is
read as "there is nothing there"**, and the code then acts on that answer —
deletes the remote copy, passes verification over zero wells, stamps a new
settings marker onto old data. The second shape is **verification that checks a
file exists locally, not that this run replaced it.** Fixing A1 (fail closed on
a failed listing) removes the trigger for most of section A; the rows stay
separate because each consumer must also handle "unknown" correctly once the
listing can say it.

---

## A. A failed remote command read as "nothing there"

| ID | Sev | Finding | Where | Evidence | Source | State |
|---|---|---|---|---|---|---|
| A1 | CRITICAL | `Fetcher._list_remote_glob` never reads the ssh exit code, so a failed listing returns `[]` and `_rsync_glob` returns `(0, 0)` — identical to "the remote has no such files". Root trigger of A2–A5. | `services/remote_fetch.py` `_list_remote_glob`, `_rsync_glob` | REPRODUCED (ssh exiting 255 → `('gpu', [], None)` → `(0, 0)`) | fetch, history, verified by the coordinating session | todo |
| A2 | CRITICAL | A well whose CSV never arrived (listing failed, or the CSV rsync failed) is still marked fetched: the settings marker is pulled and the remote input, zip and CSV are deleted. On a re-run under new settings the *old* local CSV passes verify and is stamped with the new marker, so `finished_here` never recomputes it. Verify checks the CSV exists, not that it was replaced. | `services/remote_fetch.py` `_place` (fetched / marker / delete gated on the zip only), `_verify_local_manifest` | REPRODUCED (verify `passed=True` over the old row; `finished_here(new) -> ['A01']`) | fetch | todo |
| A3 | CRITICAL | A failed listing during verification means zero wells are checked; verify passes and cleanup runs `rm -rf` on the remote run directory and the dataset cache. Reached on a FULL CACHE HIT re-run (no done-markers fire) or any well the host skipped. Verify builds its well list from done-markers plus that listing, never from the uploaded wells. | `services/remote_fetch.py` `_list_completed_wells_in`, `_verify_local_manifest` | REPRODUCED (both `rm -rf` issued; local held only the fetch report) | fetch | todo |
| A4 | CRITICAL | Nanowell batch release: a failed `nanowells/` table pull leaves the stale local grid, which reads as "not re-anchored"; a failed listing makes `_rsync_glob` report `(0, 0)` and `0 < 0` is false. Either way the remote `<well>_out.zip`, cell CSV and input are deleted while the local copy predates the re-anchoring, and the log says "pulled again after the end-of-run re-anchoring re-ran it" (which paints the well downloaded). `pull_nanowell_results` also ignores a failed `nanowells/` pull. | `services/remote_fetch_nanowells.py` `release_batch`, `pull_nanowell_results` | REPRODUCED (`released=['B02']`, remote emptied, verify passed with no 24 h rows) | fetch, history | todo |
| A5 | CRITICAL | End-of-run retry cannot replace an announced well whose in-stream zip pull failed (it re-pulls only incomplete zips, or stale ones under Force), yet pulls the new settings marker unconditionally — so the next run skips the well and its verify can then pass and delete the new remote output. | `services/remote_fetch.py` `_retry_missing_wells` | REPRODUCED (only rsync was the marker; local CSV still old) | fetch | todo |
| A6 | CRITICAL | Incremental merge failure (one EIO on the output volume) logs that the new rows were not added and "are still on the remote", then the well is marked fetched and its remote copy deleted; verify passes. | `pipeline/io.py` merge, `services/remote_fetch_place.py` | REPRODUCED (remote empty, `passed=True`, CSV missing FOV 2 whose image is in the zip) | fetch | todo |
| A7 | HIGH | A batch's stale `.aw_batch_N_ready` flag survives if `_start_driver`'s clearing `rm -rf` fails (result discarded); the driver then swaps in a half-uploaded batch and reports `rc=0`, and wells that never announced are absent from verification. Same discarded-result shape at `remote_runner.py` (stale upload sentinel, stale suspend request, incremental run's old outputs — each with a comment naming the consequence) and `remote_final_pass.py` (`mkdir` / `find -exec mv` / `rm` / `mv`). | `services/remote_process.py` `_start_driver`; `services/remote_runner.py`; `services/remote_final_pass.py` | REPRODUCED for the ready flag (1 of 4 wells processed, `rc=0`); others INFERRED | driver, history | todo |
| A8 | MEDIUM | Failure to pull this run's `pipeline_info.json` does not fail verification; cleanup then deletes the only correct copy, and the local sidecar describes a different run. | `services/remote_fetch.py` verify | REPRODUCED in part | fetch | todo |
| A9 | MEDIUM | The heartbeat `touch` result is discarded, so a failing heartbeat is invisible until the remote gives the node back (120 s between batches) or suspends the batch (600 s), and the remote's message then blames the app. | `services/remote_process.py` `_UploadHeartbeat.beat` | REPRODUCED (7–12 failed touches, nothing logged) | driver, orchestration | todo |
| A10 | LOW | `list_remote_csvs` returns `[]` rather than "unknown" on ssh failure; errs safe today because verify still fails. | `services/remote_csv.py` | INFERRED | fetch | todo |

## B. Partial or stale output reported complete

| ID | Sev | Finding | Where | Evidence | Source | State |
|---|---|---|---|---|---|---|
| B1 | CRITICAL | The completion seal is app-side only. On Resume, a well left part way (Stop's SIGTERM wind-down, or a mid-batch watchdog suspend) is re-sent into an output dir still holding its unsealed `out.zip` + CSV + settings marker; the host's own skip (`output_exists_for_well and well_settings_allow_reuse`) skips it, verify pulls the partial copy, and the run reports success. Field-mode wells stay wrong on every later resume until Force. | `process_microscopy.py` host skip; `pipeline/well_skip.py`; contradicts `services/remote_batch_resume.py` docstring | REPRODUCED (app uploads the part-way well; host logs `SKIP well … output already exists`); verify step INFERRED | resume | todo |
| B2 | HIGH | "Is this well finished?" is answered six ways; three of them (`_pipeline_launch_is_pointless`, `remote_recover`, the pre-flight and re-attach in `remote_runner`) count any `*_out.zip` and ignore the seal. A run that failed with PARTIAL wells becomes "Remote pipeline completed successfully" on a plain re-run (FULL CACHE HIT replaces the pipeline with `true`), with Auto-delete then cleaning the remote. | `services/remote_run_id.py` `_pipeline_launch_is_pointless`; `services/remote_recover.py`; `services/remote_runner.py`; `services/remote_fetch.py` `_list_completed_wells_in` | INFERRED (predicate returns True for that state by direct call) | fetch, resume, history | todo |
| B3 | HIGH | The Analyze run plate paints partial wells complete: `ProgressTracker` has no pattern for `PARTIAL`, `NOT ATTEMPTED`, storage failure, "no records produced", or a nanowell "N without rows" with N > 0. Wells in flight at a crash stay amber "processing". The pipeline's own exit line admits it ("partial wells look complete on the plate map"). | `services/pipeline_runner.py` `ProgressTracker`; `widgets/run_dashboard.py` | REPRODUCED (A01 PARTIAL 9/12 failed → green; A02 4 of 4 timepoints without rows → green; A03 no CSV → blue, dial full) | orchestration; user report | todo |
| B4 | MEDIUM | The client-side completeness verdict ("INCOMPLETE: N well(s) cover fewer FOV-groups…") reaches the log as INFO (the adapter drops the level), and verification's pass/fail never consults `run_check`. | `services/remote_log_adapter.py`; `services/remote_fetch.py` verify | REPRODUCED for the classification; verdict path INFERRED | orchestration | todo |
| B5 | LOW | The end-of-run retry prints the "zip in …" / "CSV file(s) in …" lines that paint a well downloaded even when it pulled nothing. | `services/remote_fetch.py` `_retry_missing_wells` | REPRODUCED | fetch | todo |
| B6 | LOW | `_materialize_incremental` replaces an unreadable local `<well>_out.zip` with the partial new-FOVs-only zip; earlier FOVs' images vanish and verify passes because the zip "changed". | `services/remote_fetch.py` | INFERRED | fetch | todo |

## C. Silent stalls and unbounded waits

| ID | Sev | Finding | Where | Evidence | Source | State |
|---|---|---|---|---|---|---|
| C1 | HIGH | Nothing in the app notices a silent remote stream. There is no last-line timestamp anywhere; the line iterator blocks on `queue.get()` with no timeout. After an hour of silence every surface still reads "Processing N/M", "ETA 0s", a stale "Transferring… NN%". | `services/remote_process.py` `_QueueLineIterator`; `analyze/progress.py`; `analyze/tab.py` | REPRODUCED (identical surfaces before and after a simulated hour) | orchestration; incident 1 | todo |
| C2 | HIGH | The driver's per-batch budget `timeout N` has no `-k`. The pipeline catches SIGTERM as a cooperative suspend and continues to a FOV-group boundary a hung well never reaches; if the `conda run` wrapper dies on SIGTERM instead, `timeout` returns 124 while the real pipeline is orphaned and keeps writing `out/` during the next batch. Either way the timeout is then reported as a suspend (D1). | `services/remote_commands.py` `_batch_driver_snippet` | REPRODUCED (3 s budget still running at 15 s; orphan with ppid 1) | driver, orchestration | todo |
| C3 | HIGH | `ServerAliveCountMax = 2 × queue_wait_timeout_min`: at the default 30 the app takes 30 min to notice a dead connection, and "wait indefinitely" means never. It does not bound the queue wait at all (sshd answers keepalives while the job is queued), and nothing else reads that setting. With `ControlMaster=auto` the heartbeat, hand-off, `scancel` and driver stream all share that one connection. | `services/remote_config.py`; tooltip in `analyze/remote_dialog.py` | REPRODUCED against a real local sshd (a silent command survives `Interval=1 CountMax=2`; detection time is Interval × CountMax) | driver, orchestration, staging | todo |
| C4 | HIGH | rsync exit 255 (ssh connect failure: MaxStartups throttling, `kex_exchange_identification`, connection reset) is never retried, and the fetch opens a fresh ssh per rsync (~10 per well, no ControlMaster). A throttled login node is the likely trigger for section A. | `services/remote_transport.py` `_RSYNC_RETRY_CODES` | REPRODUCED (rc 255, no retry line) | fetch | todo |
| C5 | HIGH | `_rsync_optional` uses a 60 s wall-clock `timeout` with no `stop_event`; a re-anchored zip that takes longer is killed four times (~254 s per well) and verification fails on every run. Stop cannot interrupt it. | `services/remote_fetch.py` `_rsync_optional` | REPRODUCED (8 MB at 100 KB/s → `ok=False` after 254 s) | fetch | todo |
| C6 | HIGH | The driver's 2 h upload wait applies even while the heartbeat is fresh, so a healthy upload of a large batch (6 nanowell wells ≈ 48 GB needs > 53 Mbit/s) ends the run with "batch N never arrived". | `services/remote_commands.py`; `services/remote_wait_snippets.py` `UPLOAD_WAIT_TIMEOUT_S` | REPRODUCED (scaled: 6 s deadline, fresh heartbeat, exit 75) | driver | todo |
| C7 | MEDIUM | `_pull_listed` passes no `--timeout` and the rsync transport sets no `ServerAliveInterval`; a half-open connection mid-pull holds `flush_and_join` until TCP keepalive gives up (~2 h) — the docstring claims each fetch is bounded. | `services/remote_fetch.py` `_pull_listed`, `_rsync_ssh_transport` | INFERRED (argv REPRODUCED) | fetch | todo |
| C8 | MEDIUM | `DatasetCache._ssh_script` has no timeout; its callers include every pre-launch resume listing (`find_leftovers`, `adopt_finished`, `stage_from_leftovers`, `report_resumable_checkpoints`) and the verify listing. Over a dead link each waits up to C3's 30 min, and Stop cannot interrupt it (no tracked process yet). | `services/remote_upload.py` `_ssh_script` | REPRODUCED (blocked the child's full run where `_ssh_run` returned 124) | driver, fetch, resume, staging, history | todo |
| C9 | MEDIUM | Nothing keeps the Mac awake during a remote run. A sleeping client stops the heartbeat: the node is handed back within 2 min between batches or the batch suspended after 10 min, then C3 adds up to 30 min before the app notices, then D2 reports "stopped". | (no `caffeinate` / power assertion anywhere) | INFERRED | driver | todo |
| C10 | LOW | The heartbeat staleness check compares the login node's file mtime with the compute node's clock; > 120 s skew means exit 75 at every batch boundary. | `services/remote_wait_snippets.py` | INFERRED | driver | todo |
| C11 | LOW | When the GUI event queue fills, per-well "— done." lines are dropped silently; the one-time warning can itself be dropped and its claim that output is still captured in the help-drawer log is false. | `services/pipeline_runner.py` | REPRODUCED | orchestration | todo |

## D. Failures reported with the wrong cause

| ID | Sev | Finding | Where | Evidence | Source | State |
|---|---|---|---|---|---|---|
| D1 | HIGH | Any SIGTERM the pipeline receives is reported as the user's Suspend: walltime, preemption, an admin's `scancel`, the driver's own `timeout` (C2), the heartbeat watchdog (A9, C9). The headline is amber "Cancelled — suspended; resume to finish", the raw log is not opened, the journal records `suspended`, and the real cause (`CANCELLED … DUE TO TIME LIMIT`) is latched but never shown. | `services/pipeline_runner.py` `_note_suspend_evidence`, `_settle_remote_suspend`; `analyze/log_pane.py` | REPRODUCED (rc 143 → "Cancelled — suspended") | orchestration, driver | todo |
| D2 | HIGH | A lost driver is reported as the user's Stop: the batch reader sets `self._stop`, which *is* the runner's `_stop_requested`. Covers walltime, node failure, the driver's exit 75, a failed batch upload and the hand-off failure. Shows amber "Cancelled — stopped", records `stopped`, marks the Job Queue job cancelled and **pauses the whole queue**, and cancels the Fetcher so finished-but-undownloaded wells stay on the remote. | `services/remote_process.py` `_read_driver`; `services/pipeline_runner.py`; `datasets/job_queue/controller.py` | REPRODUCED (three scenarios: `was_stopped=True`, journal `stopped`, fetcher cancelled) | driver | todo |
| D3 | HIGH | An ssh drop or unreachable host is reported as "Remote pipeline exited with code 255"; ssh's own "server not responding" line is classed INFO and not quoted; the log then says "job closed — the remote process has exited" and the Slurm icon goes off, neither verified — a single-shot job keeps running and billing. Stop with the network down says "stopped" with one WARN. | `services/pipeline_runner.py` (`JOB_CLOSED_LINE`, `exit_message`, `classify_log_line`); `services/remote_runner.py` | REPRODUCED | orchestration | todo |
| D4 | MEDIUM | A failed batched run's headline has no reason ("…exited with code 1."): the batch loop's own lines bypass the runner's tail. The failed-batch tally says the batch's wells are missing even when one partial well failed it. | `services/remote_process.py`; `services/pipeline_runner.py` | REPRODUCED for `exit_message` | orchestration | todo |
| D5 | MEDIUM | "Transferring… NN%" is fed by both upload and download progress and cleared only by a compute event; after a batch upload it keeps the last value (incident 1 showed 79 % for an hour). | `services/remote_process.py`; `analyze/progress.py` | REPRODUCED | orchestration | todo |
| D6 | LOW | Wells skipped as already complete locally show blue "on server, awaiting fetch" forever; "SKIP well … empty-well reference only" matches no pattern. Stop / Suspend during a queue wait: Suspend leaves the job queued, Stop of a never-started job says "job already finished". A local read error mid-run reads "Failed to start pipeline". | `services/pipeline_runner.py` | REPRODUCED (first); others INFERRED | orchestration | todo |

## E. Allocations and concurrent runs

| ID | Sev | Finding | Where | Evidence | Source | State |
|---|---|---|---|---|---|---|
| E1 | HIGH | A refused second session cancels the first session's live job: `runner._run` is set before `_start_driver` refuses with "owned", and the runner's `finally` then calls `close_remote_job`, which reads the shared `run.pid.slurm` and `scancel`s it. Exactly what a user does after incident 1 (start the same plate again from a second window). | `services/remote_process.py`; `services/pipeline_runner.py` | REPRODUCED for the remote half (real shell, fake `squeue`/`scancel`); call order INFERRED | driver | todo |
| E2 | HIGH | A resume never checks the earlier attempt's still-live job (the sweep and the "owned" guard look only at the new run's pidfile). Inside the old driver's last minutes the resume asks for a second allocation, can `mv` the live batch's input away, and lets two pipelines write one output. | `services/remote_process.py`; `services/remote_jobs.py` | REPRODUCED for the target pidfile; consequences INFERRED | resume | todo |
| E3 | MEDIUM | A node failing mid-run is classed as a boot failure: the srun wrapper re-runs the driver from batch 1 (the replay reports finished batches as rc 90, deletes the in-flight batch's input, and on a final pass the held archives), or a single-shot run from the top with `--force`. | `services/remote_srun_retry.py` `_BOOT_FAILURE_RE` | REPRODUCED (both reviewers) | driver, orchestration | todo |
| E4 | MEDIUM | The orphan sweep calls a heartbeat ≤ 120 s old "owned", so Resume right after a Stop (or a retry right after a failed run) is refused with "another session is already running this exact dataset", naming a session that does not exist. | `services/remote_jobs.py` | REPRODUCED | driver, resume | todo |
| E5 | LOW | Dataset-cache GC evicts with no in-use check (opt-in). An srun launcher containing `-o`/`--error`/`--pty` is not refused, so driver output goes to a file and the app hears nothing. | `services/remote_upload.py`; launcher validation | INFERRED | history, driver | todo |

## F. Resume and reuse keys

| ID | Sev | Finding | Where | Evidence | Source | State |
|---|---|---|---|---|---|---|
| F1 | HIGH | A nanowell well with any timepoint reached but not addressed is never "finished" and never sealed (`groups_covered` counts only `ok` timepoints), contradicting the module's own "reached is enough". Every remote re-run or resume recomputes it in full and adds it to `--force_wells`, triggering F2. The existing test passes only because its fixture omits the run_report record the pipeline writes. | `pipeline/nanowell_task.py` `write_well_report` call; `services/remote_finished_wells.py` | REPRODUCED (adding the real record flips `sent=None` to `sent=['B02'], forced=['B02']`). Re-measured on `37b5ee5`: same result, `forced: ['B02']`. Fixed: a nanowell record now carries `groups_reached` (timepoints given any verdict, `nanowell_task.record_nanowell_completeness`) and `run_report.well_is_whole` asks that of it, so an `unaddressed` or `no_lattice` timepoint no longer bars the seal or `finished_here`; `groups_covered` stays the `ok`-only count Validate and the INCOMPLETE summary read. A legacy record without the field keeps the old rule (its `failed` list still bars it, as `test_a_nanowell_well_its_record_says_failed_is_sent_again` pins). Test: tests/test_a_remote_rerun_skips_finished_wells.py::test_a_nanowell_timepoint_the_run_reached_without_an_address_is_not_sent_alone_again (fixture now writes the pipeline's own record; red with the rule reverted, `sent=['B02']`), tests/test_a_part_way_well_is_not_sealed.py::test_a_nanowell_well_is_sealed_when_the_run_reached_every_timepoint | resume | done |
| F2 | HIGH | `--force_wells` is part of the run digest (only `--force` is stripped; `--only_wells` and `--no_auto_threshold` aren't either), and Resume adds `--force_wells` itself — so a resume cannot find the attempt it resumes: uploaded batch archives are re-sent, wells finished on the remote recomputed, `keep/` never adopted, the old attempt orphaned. A single forced well also disables re-attach for the whole plate. | `services/remote_run_id.py`; `services/pipeline_runner.py`; `services/remote_process.py`; `services/remote_batch_resume.py` | REPRODUCED (`--force_wells A05` changes the digest; `stored_remote_addresses` → `[]`) | resume | todo |
| F3 | CRITICAL (latent) | Held nanowell wells adopted from an earlier attempt are re-anchored by this run's final pass, but their rewritten cell CSV and `out.zip` are never pulled (only `nanowells/` is); the local plate then pairs a re-anchored grid with pre-re-anchoring cell data, verify passes. Hidden today by F1; becomes the normal path once F1 is fixed — fix together. | `services/remote_final_pass.py` `adopt_earlier_holds`; `services/remote_fetch_nanowells.py` | REPRODUCED | resume | todo |
| F4 | HIGH (latent) | Held wells drop out of the end-of-run re-anchoring: `_start_driver` removes the run's `keep/` before adoption, adoption looks only in the re-attached output's run dir, a suspended run's Auto-delete removes `keep/`, and a failed or stopped final pass leaves the archives in `b<n+1>/` where nothing looks. The next run says "no well was held for it" and the timepoints stay unaddressed while the output looks complete. | `services/remote_process.py` `_start_driver`; `services/remote_final_pass.py`; `services/pipeline_runner.py` suspend cleanup | REPRODUCED (two resume cases); suspend / failed-pass cases INFERRED | resume, driver | todo |
| F5 | MEDIUM | The run journal dedupes attempts by output dir, so an intermediate attempt's run dir is never listed: its staged archives are re-uploaded, its `keep/` lost, its blank alias dropped (checkpoints pruned). | `services/run_journal.py`; `services/remote_batch_resume.py` `_roots` | REPRODUCED | resume | todo |
| F6 | MEDIUM | Earlier attempts' run directories are never deleted, even with Auto-delete; with F2 every forced resume orphans a whole attempt (nanowell ≈ 8 GB per well) and can fill the remote quota. | `services/remote_runner.py` | INFERRED | resume | todo |
| F7 | MEDIUM | No build identity in any reuse key, and `pipeline_argv_digest` is not pinned per build the way the settings digest is; an argv change between builds silently moves or aliases every digest. Recorded build in the seal would at least make it visible. | `util/settings_digest.py`; `services/remote_run_id.py` | INFERRED | resume, history | todo |
| F8 | MEDIUM | `tests/test_reuse_is_keyed_completely.py` registers none of the five remote reuse decisions (`finished_here`, `find_leftovers`/`adopt_finished`, `recover_finished_outputs`, `_pipeline_launch_is_pointless`, `_nothing_left_to_upload`), and its "every decision is registered" check compares a hand-written roster with itself. | `tests/test_reuse_is_keyed_completely.py` | measured | history | todo |

## G. The batch hand-off fix on `claude/batch-handoff-ack`

Incident 1 (a batched nanowell run silent for an hour after "batch 4: upload
complete …; handing it to the driver") led to `services/remote_handoff.py`:
the ready-flag `touch` is retried and verified, and a driver that says it is
waiting for a batch and does not start it within five minutes is chased. Its
review found defects in the fix itself.

| ID | Sev | Finding | Where | Evidence | Source | State |
|---|---|---|---|---|---|---|
| G1 | HIGH | The ready-flag `touch` that hands each batch to the driver ran with `check=False` and its result was never read; a lost hand-off left the app waiting forever and the driver polling for two hours. | `services/remote_process.py` `_run_batches`; `services/remote_final_pass.py` | REPRODUCED by `tests/test_a_lost_batch_handoff_is_noticed.py` (four tests red with the fix removed) | incident 1 | wip — fix and tests on the branch; G2–G6 must close before it ships |
| G2 | MEDIUM | A `touch` that timed out (124) may have landed; `raise_flag` retries for ~8 min, gives up and ends the run — `_release_driver` then kills a batch the driver had already started. 124 and 255 must read as "unknown", and `watch` must be consulted before giving up. | `services/remote_handoff.py` `raise_flag`, `hand_off` | REPRODUCED ("batch 1 starting" arrived, run killed anyway) | driver | todo |
| G3 | MEDIUM | `chase_handoff` treats a failed `test -f` (ssh 255 / 124) as "flag missing", re-raises, then ends the run with the wrong diagnosis. | `services/remote_handoff.py` `chase_handoff` | REPRODUCED | driver | todo |
| G4 | MEDIUM | The fix's ERROR is then reported as the user's Stop (D2), and its tests miss it because `_FakeProc.terminate` does not end the stream as a real ssh does. | `tests/test_batch_driver.py` `_FakeProc` | REPRODUCED | driver | todo |
| G5 | MEDIUM | The fix's message "The uploaded wells stay on the remote — Resume picks them up" is false whenever F2 applies (most nanowell resumes). | `services/remote_handoff.py` | REPRODUCED via F2 | resume | todo |
| G6 | LOW | A user Stop during the retry pause still logs "the connection to the host is failing" and the run gets rc 1 instead of 130. Retries over a dead ControlMaster (C3) all go through the same dead connection. | `services/remote_handoff.py` | INFERRED | driver | todo |

## H. Environment

| ID | Sev | Finding | Where | Evidence | Source | State |
|---|---|---|---|---|---|---|
| H1 | MEDIUM | Remote env readiness is judged with `importlib.util.find_spec`, which misses an installed-but-broken module (the imagecodecs-after-numpy-upgrade case that has already happened); the run then dies late. Import the modules instead. | `services/remote_conda.py` | INFERRED from the history | history | todo |

## S. Staging and upload

S1–S3 destroy the user's **local raw images** — the only copy — and are the
most urgent rows in this file. They apply to FOLDER and FLAT input (loose TIFs,
or one folder per well); ZIP and MIXED input stage copies and are not affected.

| ID | Sev | Finding | Where | Evidence | Source | State |
|---|---|---|---|---|---|---|
| S1 | CRITICAL | Channels the user did not select are deleted from their only copy at staging time, before any upload and whether or not the run succeeds. FOLDER/FLAT staging zips each well folder and deletes its TIFs as soon as the zip is published, then `_filter_channels_in_staging` rewrites that zip **in place** keeping only the nuclear + selected channels. The comment above the call says the zips are "scratch artifacts" and the loose sources "still intact" — no longer true since the folders are consumed as they are zipped. The Analyze tab always runs with `selective_channels=True` (`analyze/run_spec.py`) and `move_source_tifs=True` (`analyze/form.py`). The ZIP/MIXED planner (`_plan_staging` + `_materialize_staging` into a transit dir) has the protections this legacy filter lacks. | `services/remote_staging.py` `stage_local_zips`, `_filter_channels_in_staging` | REPRODUCED through `resolve_input_output` + `stage_local_zips` (DAPI/GFP/BF plate, GFP selected: 6 BF images before, 0 anywhere on disk after, nothing uploaded yet) — re-run and confirmed by the coordinating session | staging | todo |
| S2 | CRITICAL | A well folder holding no `.tif`/`.tiff` (PNG images, metadata only, a `masks/` subfolder) is never uploaded, never named in the log, and then `rmtree`'d by the success hook: it skips the zip step with `continue` before joining `preserved`, so it stays in `folders_to_clean` — the comment above says such a folder "is left whole". PNGs in a mixed folder are silently not uploaded, though the local pipeline packs them. AppleDouble `._*.tif` files are packed and counted as TIFs. | `services/remote_staging.py` `_zip_well_folder`, the zip-up loop, `_on_success` | REPRODUCED (staged only A01; after the hook, A02's folder gone) | staging | todo |
| S3 | CRITICAL | FLAT layout: the success hook unlinks every top-level `*.tif` snapshotted before the zipper ran, archived or not — a `flatfield_reference_DAPI.tif` or `plate_overview.tif` beside the plate images is deleted after a successful remote run. The same shape is in `services/input_resolution_service.py` (`source_tifs = tif_files_in(raw)`), not probed. | `services/remote_staging.py` `_on_success` | REPRODUCED (both never-archived files gone) | staging | todo |
| S4 | HIGH | A stalled upload hangs forever and Stop cannot end it: the upload rsync has no `--timeout`, its ssh transport no `ServerAlive*`, and `_run_rsync_streaming` checks the stop event only when rsync prints a line. The fetch side already uses `--timeout=120` (half-swept). In overlap mode the heartbeat keeps the Slurm node allocated meanwhile. | `services/remote_upload.py`; `services/remote_transport.py` | REPRODUCED against a real local sshd (remote frozen after 61 MB; still blocked 120 s after Stop) | staging | todo |
| S5 | HIGH | A connection dropped mid-upload exits rsync 255 ("Broken pipe", "unexplained error (code 255)"), which is not in `_RSYNC_RETRY_CODES`: a single-shot run fails after one attempt, a batched run aborts the remaining batches hours into the plate. Same code set as C4 on the fetch side; retry 255 on transport failures, not on "Permission denied" / "Host key verification failed". | `services/remote_transport.py` | REPRODUCED (sshd session killed with SIGKILL and SIGTERM: rc 255, 1 attempt) | staging, fetch | todo |
| S6 | HIGH | Every retry re-uploads the whole plate and every failed attempt leaves a full copy on the remote: staged rewrites get a fresh mtime each run, so `fingerprint_archives` → `acquisition_cache_key` → `run_id` all change and `datasets/<key>/` starts empty. Applies to any channel subset saving ≥ 10 % of the bytes and every incremental run; defeats the batched reuse `remote_batch_staging.py` documents. GC is off by default. | `services/remote_scratch.py` `fingerprint_archives`; staging rewrite | REPRODUCED (same plate and settings, two different keys) | staging | todo |
| S7 | MEDIUM | A half-installed `allwell` env passes the pre-launch guard (it checks only `conda-meta/history`; the bootstrap's own `.pip_failed`/`.pip_running` markers are never read for it), so the run dies with `ModuleNotFoundError` after the queue wait and the full upload. The Cellpose env already gets markers plus an import probe. Related to H1. | `services/remote_conda.py` `_conda_env_guard` | REPRODUCED ("GUARD PASSED" with `.pip_failed` present and nothing installed) | staging | todo |
| S8 | MEDIUM | `RemoteHost.ssh_argv` has no `BatchMode=yes` and no `ConnectTimeout`, though `ssh_key_setup.py` says every call has them — a terminal-launched app can block on a hidden password prompt. (Its keepalive is C3; a dead ControlMaster making every control command return 124 for up to 30 min while a fresh connection works was REPRODUCED here.) | `services/remote_config.py` `ssh_argv` | INFERRED (dead-master half REPRODUCED) | staging | todo |
| S9 | MEDIUM | A failed `_ssh_run(check=True)` surfaces as "Pipeline setup failed: Command '[…]' returned non-zero exit status 255." with ssh's own stderr dropped (auth failure, changed host key, quota, "ControlPath too long"). On macOS the ControlPath template `~/.ssh/allwell-cm-%r@%h:%p` can exceed the 104-byte socket limit for a long user or host name; `%C` avoids it. When the upload-verification listing's ssh fails, the run says "rsync reported success but some files did not reach the remote", blaming the wrong thing. | `services/remote_upload.py`; `services/remote_config.py` `_controlpath_template` | REPRODUCED (the dropped text was "ControlPath too long") | staging | todo |
| S10 | LOW | A failed or stopped transfer paints its well fully uploaded (`_UploadWellProgress.finish()` runs unconditionally). `update_bandwidth` rewrites `remotes.toml` non-atomically after every upload. The code sync goes into one shared `remote_project_dir` with no per-run snapshot, so two app versions or a sync between batches can run mixed code. `_materialize_staging` reads each member fully into RAM. The retry backoff `time.sleep` ignores Stop. | `services/remote_upload.py`; `services/remote_config.py`; `services/remote_staging.py` | REPRODUCED (first); others INFERRED | staging | todo |

## P. Guards and harness — what stops the next one

The history review found that ~38 % of the remote fixes in the last thirty
days repaired a defect an earlier fix in the same window introduced, and that
the remote tests model the four things that failed in the field — transient
ssh failures, the real scheduler, state carried across builds and runs, and
the app↔driver handshake as a pair — as always-succeeding stubs. These rows
are the mechanical answers (CLAUDE.md §7.6 #5: a guard or a test that presses
the button, never another pair of eyes).

| ID | Sev | Finding | Where | Evidence | Source | State |
|---|---|---|---|---|---|---|
| P1 | HIGH | No harness runs the app and the remote side as a pair. Build a localhost end-to-end harness: an `ssh` shim that runs the remote command under bash with a temporary HOME, real rsync, stand-in `srun`/`sinfo`/`squeue`/`scancel` that exec, the real packed launch command and driver, the real pipeline on synthetic nanowell tiles across three batches plus the final pass — then inject faults (the Nth ssh returns 255, one call hangs) and assert every run ends either correct or visibly failed. Would have caught incident 1, A4, and three of the last month's fix-of-fix chains. | `tests/` (new) | measured gap | history | todo |
| P2 | MEDIUM | AST guard: no `_ssh_run` / `_ssh_script` / `subprocess.run` of ssh with `check=False` whose result is discarded, unless allowlisted at the site with a reason. Add the evasions to `tests/test_guards_bite.py`. | `tests/` (new) | — | history | todo |
| P3 | MEDIUM | AST guard: every `subprocess.run` / `Popen.wait` under `services/` passes a timeout, allowlisting the bootstrap installs by name with a reason. | `tests/` (new) | — | history | todo |
| P4 | MEDIUM | One definition of "this well is finished", seal-aware, used by every consumer in B2; a guard that no module outside it lists `*_out.zip` to decide completion. | `services/` | — | history, fetch, resume | todo |

---

## Incident 1 — what would tell the hypotheses apart

Reported: batched nanowell run, 18 wells (3 batches) done, batch 4 uploaded,
"handing it to the driver" was the last line for an hour; the log also held
"warning unable to open monitor interface", which is not an All-Well string.

- **H1 — the hand-off `touch` failed or timed out and was ignored (G1).** On the
  cluster: `.aw_batch_4_ready` absent from the run dir, `b4/` still holding the
  six archives, `in/` holding batch 3's; `sacct -j $(cat <run>/run.pid.slurm)
  -o JobID,State,ExitCode,Start,End` shows exit 75 about two hours after the
  hand-off.
- **H2 — the Mac slept or the ssh master's connection died just after the
  hand-off (C3, C9).** `.aw_heartbeat` mtime frozen near the hand-off; job end
  about two minutes later with exit 75 ("the app stopped responding");
  `pmset -g log | egrep 'Sleep|Wake'` on the Mac; the host's
  `queue_wait_timeout_min` in remotes.toml.
- **H3 — batch 4 started and then hung (C2).** `ready` present, `b4/` gone,
  `in/` holding batch 4's archives, no `.aw_batch_4_done`, the job still
  running. The app log would hold "batch-driver: batch 4 starting", which the
  user reports it does not.
- **H4 — the batch ran but its output never reached the app.**
  `.aw_batch_4_done` written later; compare the tail of
  `<run>/run.pid.srun-err` (a tee of the driver's output while the wrapper
  lives) with the app's log.

Where the "monitor interface" warning sits matters: before the hand-off line it
is noise; after it, the stream was alive past the hand-off, which weakens H2.
