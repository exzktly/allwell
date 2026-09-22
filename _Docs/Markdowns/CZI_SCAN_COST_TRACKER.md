# The CZI converter's scan cost — remediation tracker

A user reported that pointing the Convert CZI page at a 250 GB source folder
hung the window, that reading the headers alone had previously taken *hours*,
and that the time bought nothing — the conversion had still not started. The
folder is **661 files of about 450 MB each, on a network share**.

This file exists because the work is four interlocking changes across the
reader, the set layer, the page and the run, which is more than one session can
hold honestly in its head (CLAUDE.md §6.1). It is live state, not a write-up,
and it is deleted by the pull request that closes its last row.

## How to read a row

`state` is one of `open`, `done` or `declined`. A row reaches `done` only when
the defect can no longer recur — which means a test that was **watched failing
without the fix** (§7.7 #2), named in the Evidence column. "Implemented" is not
`done`. A row that turns out not to be a defect is `declined` with the reason
written both here and at the site.

The counts in the summary are derived from the rows, never typed
independently: if the summary says two are done, two rows say `done`.

## Summary

| Area | Total | open | done | declined |
|---|---|---|---|---|
| Wasted passes | 2 | 0 | 2 | 0 |
| Parse cost | 2 | 0 | 2 | 0 |
| Responsiveness | 1 | 0 | 1 | 0 |
| **Total** | **5** | **0** | **5** | **0** |

## What was measured, before any of it was changed

Measured on synthetic ZEN-shaped files with 4×6-pixel images, so that every
number below is the cost of **parsing** and none of it is the cost of moving
pixels.

Header-read (peek) cost against the size of the file's metadata document:

| Scenes | Tile elements per scene | Metadata XML | Peek per file | × 96 files |
|---|---|---|---|---|
| 96 | 0 | 0.01 MB | 0.9 ms | 0.1 s |
| 96 | 50 | 0.27 MB | 18.6 ms | 1.8 s |
| 96 | 500 | 2.73 MB | 200.6 ms | 19.3 s |
| 96 | 2000 | 11.31 MB | 1124.3 ms | 107.9 s |

Header-read cost is **linear in the metadata document's size, about 100 ms per
megabyte**, and is independent of how large the images are. In a ZEN
split-scene export every file carries the whole plate's metadata, so that one
document is read and parsed once per file, per pass.

Window responsiveness during a scan, sampled as the worst gap between firings
of a 10 ms timer while the real Convert CZI page scanned a 96-file plate
totalling 158 MB: **worst gap 395 ms, 12 of 73 ticks over 100 ms**.

## Rows

| # | Row | State | Place | Evidence |
|---|---|---|---|---|
| S1 | **The run re-reads the whole plate**, twice over: `PackedConverterWorker.run` calls `spec.read_source` to validate, and `explode_czi` then calls `read_czi_set` of its own accord. The set the page spent hours building is discarded both times. Answered by keying the layout on the file rather than by threading the object through four layers — a `CziLayout` is aggregate data, never the subblocks, so it costs kilobytes to keep and a megabyte of subblock directory plus a round trip per file to rebuild. | `done` | `pipeline/czi_read.cached_layout`, `::remember_layout` | Three reads of one file now open it once, counted as **file opens**, which is what a share charges for. Three mutations on HEAD: the layout never remembered (3 opens), `read_times` dropped from the key (the cheap layout served to the caller that asked for the times, reporting a plate as having no timing), and the cache handing back the object it holds. That last one **passed at first** — the store deep-copies too, so scribbling on the first read's layout could never reach the cache; the test now scribbles on the *second*, which is the one served from it. |
| S2 | **Convert was gated on the full read** — in fact on the peek too, since the gate was set to False when a scan began. Neither is a permission: the run reads and checks the plate itself and writes nothing until it has. | `done` | `datasets/czi_import/view.py::_ready_before_the_scan` | `tests/test_czi_import_page.py`, three new cases plus a rewritten one. Four mutations on HEAD, each caught by the intended test: the gate back to False during a scan, the unread note dropped while the button stays on, a source holding no `.czi` accepted, and the form's own problems no longer consulted. The rewritten case pins the **pair** — available *and* labelled unchecked — since either alone is satisfied by a wrong page. Writing the tests also found a live gap: `refresh_readout` returned early while a scan was in flight and never re-evaluated the gate, so a grid typed into mid-scan kept the verdict the gate had when the scan started. |
| S3a | **One question of the metadata document was one complete traversal of it.** `_find_all` is a `root.iter()` per lookup; a header read asked four — the scenes, the scene list, and `Shape` and `ArrayName` within each scene. | `done` | `pipeline/czi_read._DocIndex`, `::_scene_wells` | `tests/test_czi_metadata_is_read_once.py` counts element visits through `_localname`, which every traversal calls once per element. Three mutations on HEAD: the index walking per lookup (4.0 walks of a 77,097-element document, the figure this row claimed), the two subtree walks restored (3.0), and the name-preference order reversed. The last was caught by nothing else — all 40 tests in `test_czi_read.py` passed against it. Measured: a peek of an 11.31 MB document went from 1124.3 ms to 884.9 ms. |
| S3b | **The same document is read off disk and parsed once per pass, per file.** `peek_scene_wells` and `read_czi_layout` each open the file and each run `ET.fromstring` over the same bytes, and nothing is cached, so re-picking the same folder pays for all of it again. Measured on an 11.31 MB document: `ET.fromstring` is **80.6%** of a header read, against 9.5% to build the index and 10.0% to ask it the three questions — so this is the larger half of the parse cost by four to one. | `done` | `pipeline/czi_read.parse_plate_metadata`, `::cached_plate_metadata`, `::remember_plate_metadata` | Four passes over one file went from 4 parses and 3.30 s to 1 parse and 1.32 s. `tests/test_czi_metadata_is_read_once.py`; three mutations on HEAD, each caught by the intended test — the key dropping `st_mtime_ns` (a file rewritten to the same length served its old wells, which is the stale-reuse failure the key exists to prevent), the cache never answering (4 parses), and `from_filename` baked into the cached answer. |
| S4 | **The scan pool is sized for network latency but fed GIL-bound work.** `scan_workers` caps at eight with a rationale entirely about covering round trips, and `czi_read` is a pure-Python parser, so those threads contend for the interpreter lock with the thread drawing the window. Measured on 96 local files, where the cost is all parsing: one worker 3.48 s with the event loop never starved; eight workers 4.22 s, starved 65.6% of the run, single pauses of 266 ms — slower *and* unusable. | `done` | `pipeline/czi_read._PARSE_LOCK` | The pool now reads wide and parses narrow. Same scan: starved **19.2%** rather than 65.6%, worst pause 227 ms rather than 266 ms, and the peek's latency gain intact (809 ms on one thread against 107 ms on eight, against the 797/104 the pool was sized by). `tests/test_czi_scan_leaves_the_window_alive.py` asserts both halves. Three mutations on HEAD: no lock (8 parsed at once), the lock taken around the reading too (the files opened one after another — the half a careless fix breaks), and the per-plane acquisition parse left unlocked. That last **passed at first**: with one timepoint there is a single such parse and whether it overlaps is a race, so the fixture now carries eight and it fails 3 times out of 3. **Not a cure.** ~227 ms pauses remain, and the measurements put the residue in contention between the pool's threads rather than in the parsing; removing it means a process pool, which has its own costs and was not done. |
