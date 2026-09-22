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
| Wasted passes | 2 | 2 | 0 | 0 |
| Parse cost | 1 | 1 | 0 | 0 |
| Responsiveness | 1 | 1 | 0 | 0 |
| **Total** | **4** | **4** | **0** | **0** |

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
| S1 | **The run re-reads the whole plate.** `explode_czi` calls `read_czi_set(czi_path, files=files)` itself, so the set the page spent hours building is discarded and every file is opened and parsed a third time before the first archive is written. | `open` | `pipeline/czi_explode.py:305` | |
| S2 | **Convert is gated on the full read.** The peek already names the wells; the full read is a soundness check that walks every file's subblock directory. Until it lands the button stays off, so the user waits through a pass whose result the run does not keep (S1). | `open` | `datasets/czi_import/view.py::_begin_scan`, `::_on_peeked` | |
| S3 | **Each file's metadata is read and parsed twice per pass, and traversed twice per parse.** `peek_scene_wells` and `read_czi_layout` each open the file and each run `ET.fromstring` over the same document. Within one parse, `_find_all` is a full `root.iter()` per lookup, and `_scene_wells` and `_declared_scene_indices` each call it at root level. Nothing is cached, so re-picking the same folder pays it all again. | `open` | `pipeline/czi_read.py:344 _find_all`, `:380 _scene_wells`, `:409 _declared_scene_indices`, `:523 peek_scene_wells` | |
| S4 | **The scan pool is sized for network latency but fed GIL-bound work.** `scan_workers` caps at eight threads with a written rationale about covering round trips, and `pipeline/czi_read.py` is a pure-Python parser, so eight of those threads hold the GIL and starve the UI thread. This is the out-of-domain mistake of §7.1: a pool sized for waiting, given computing. | `open` | `pipeline/czi_set.py:212 scan_workers`, `:215 _per_file` | |
