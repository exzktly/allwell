# Job Queue recipes and the folder-watch trigger — remediation tracker

A live tracker (CLAUDE.md §6.1) for the body of work that adds two things to
the Datasets Job Queue:

* **Watch folder**, a new queueable tool. A watch job waits until a folder has
  changed and then had no file added, removed, grown or touched for a fixed
  quiet period, and then succeeds, releasing the jobs chained after it. It
  fires once per chain.
* **Recipes**, saved chain templates. A chain in the queue (for example
  Watch → Convert → Analyze) is saved under a name with its settings; applying
  the recipe to a folder or file queues the whole linked chain again. The
  parameters that belong to one particular acquisition are worked out when
  each step starts rather than frozen into the recipe: which packed file in a
  watched folder a converter reads, and Analyze's filename schema and channels.

Decisions taken with the user before any code (2026-11-26): a recipe is a
saved chain template; the watch fires once per chain (it does not re-arm);
the data landing in a watched folder is packed acquisition files
(CZI / ND2 / OME-TIFF) or loose TIFs; "settled" means a quiet period only,
judged by polling names, sizes and modification times.

The counts below are derived from the rows. Delete this file in the pull
request that closes its last row.

## Rows

| # | Item | State | Where | Evidence |
|---|------|-------|-------|----------|
| 1 | Qt-free settle rule: snapshot a folder, decide waiting / settling / settled | todo | `datasets/watch_folder/watch_compute.py` | |
| 2 | Watch worker + controller implementing the QueueableController protocol | todo | `datasets/watch_folder/worker.py`, `controller.py` | |
| 3 | Watch page (folder, quiet minutes, wait-for-new-files, Start / + Queue, live status) | todo | `datasets/watch_folder/view.py` | |
| 4 | Register the kind in every per-kind table, its own lane, and the Datasets rail | todo | `services/job_kinds.py`, `services/job_lanes.py`, `datasets/tab.py`, `datasets/job_queue/row.py`, `view.py` | |
| 5 | A watch's folder can feed a converter's or Zip loose's source (link target for file-reading kinds, from a watch only) | todo | `services/job_kinds.py`, row menus | |
| 6 | Run-time source resolution: a converter handed a folder picks the one packed file in it, or fails naming what it found | todo | `services/run_resolve.py`, `datasets/core/packed_convert.py` | |
| 7 | Recipe model: save a chain as a template (paths templated on the chain's input), instantiate it on a new input | todo | `services/job_recipes.py` | |
| 8 | Recipe persistence beside the queue, with the malformed-file guard | todo | `services/job_recipes.py` | |
| 9 | Recipes menu on the Job Queue page: save from a row, apply, delete | todo | `datasets/job_queue/view.py`, `row.py`, `controller.py` | |
| 10 | Analyze steps in a recipe read schema and channels at run time | todo | `services/job_recipes.py` | |
| 11 | ARCHITECTURE.md and README_WellViewer.md describe both features | todo | `_Docs/Markdowns/` | |

## Counts

todo 11 · done 0 · declined 0
