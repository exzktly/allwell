# Work package W2 — 8 items

## Item 18 — severity Medium

**Reported location (line numbers are stale):** `datasets/subset/apply.py:242`

**Original finding:** The "did anything land?" check is skipped exactly when the filtered archive came out empty — "nothing to compare against" conflated with "nothing landed".

**Verified state at HEAD:** datasets/subset/apply.py:243 still reads `if fname and kept_in_names and fname not in kept_in_names:` — the `and kept_in_names` truthiness test skips the 'did anything land?' check exactly when the filtered in/<well>.zip came out empty, which is the reported conflation. The only change cde2f8a7 made to this file was swapping two `write_text(json.dumps(...))` calls for `write_json_atomic`.

## Item 67 — severity High

**Reported location (line numbers are stale):** `datasets/merge_plates/apply.py:83`

**Original finding:** `_remap._rename` discards `rewrite_member_field`'s `was_rewritten` flag, so a member failing the structural gate keeps dataset 2's original well token inside the destination well's archive, silently. The CSV row is equally unrewritten, so the `dest_in_names` check still passes.

**Verified state at HEAD:** datasets/merge_plates/apply.py:84-88 _remap._rename still returns _remap_well_field(...)[0] and drops the was_rewritten flag; rewrite_member_field (datasets/_common/member_names.py:119-131) returns the name unchanged for a non-conformant member, so it lands in the destination archive under dataset 2's well token. Only name collisions are refused (_refuse_dropped, line 104-125); _merge_measurements:230-234 leaves the CSV row equally unrewritten, so the dest_in_names check at line 238 still passes.

## Item 68 — severity Medium

**Reported location (line numbers are stale):** `datasets/import_loose/apply.py:179`

**Original finding:** In-place mode zips and unlinks the TIFs of **every** well-named subfolder under the source, including pre-existing ones; the preview is non-recursive and loose-TIF-only. Bytes survive in the archives, so this is unannounced scope rather than loss — but preview ≠ apply on the pane's one destructive path.

**Verified state at HEAD:** datasets/import_loose/apply.py:217-235 _zip_well_folders still iterates every directory entry of the folder whose name canonicalises to a well and zips/unlinks its TIFs, with no distinction between folders run_zipper just created and pre-existing ones; import_loose_compute.scan_folder (line 183-215) is still non-recursive and loose-TIF-only, and convert_in_place (line 264-312) adds no warning about the extra scope.

## Item 69 — severity Medium

**Reported location (line numbers are stale):** `datasets/subset/subset_compute.py:215` vs `:182`

**Original finding:** `row_matches` reads the `timepoint_hours` **column** and `member_matches` reads the **filename** token. A legacy CSV with a blank `timepoint_hours` loses every row while all images copy, and `_verify` names the wrong cause.

**Verified state at HEAD:** datasets/subset/subset_compute.py:215-225 row_matches still reads float(row['timepoint_hours']) and returns False on a blank/unparsable value, while member_matches (line 182-212) parses the filename's timepoint token — so a legacy CSV with a blank column drops every row while the images copy. subset/apply.py:130-137 _verify still blames the filter ('check that the filter actually matches data in the source dataset').

## Item 70 — severity Medium

**Reported location (line numbers are stale):** `datasets/_common/dataset_paths.py:187`

**Original finding:** Two containers canonicalising to the same well (`A1.zip` + `A01.zip`) collapse by plain dict overwrite in glob order. The `out_csvs` sibling documents its identical overwrite and names the caller that must not trust it; the input side has no warning and every copy tool trusts it.

**Verified state at HEAD:** datasets/_common/dataset_paths.py:187-205 in_containers still assigns containers[well] = zip_path in glob order with no collision detection or warning, so A1.zip and A01.zip collapse silently; the sibling out_csvs (line 208-222) documents its identical overwrite while the input side, which every copy tool trusts, still does not.

## Item 73 — severity Low

**Reported location (line numbers are stale):** `util/zip_write.py:154`

**Original finding:** `replace_folder_with_archive`'s `resolve()` identity conflates symlink and hardlink aliases in both directions: an archived symlink is reported as `kept`, and a second name for an archived file is destroyed by the folder removal though the archive holds no member under that name.

**Verified state at HEAD:** util/zip_write.py:153-168 replace_folder_with_archive still builds archived_resolved from Path(p).resolve() and compares p.resolve() for each walked file; there is no st_ino/samefile handling and no mention of symlinks or hardlinks anywhere in the module. An in-folder symlink to an archived file still resolves equal (so it is not 'kept' and dies with the rmtree), and an archived path that is itself a symlink out of the folder is dropped from archived_resolved and reported as kept.

## Item 74 — severity Medium

**Reported location (line numbers are stale):** `WellPlateZipper.py:180`

**Original finding:** `copy2`/`move` silently overwrite same-basename files under `--recursive`; the progress line counts sources, not survivors.

**Verified state at HEAD:** WellPlateZipper.py:186-195 folder_well still does place(f, os.path.join(well_dir, os.path.basename(f))) for shutil.copy2/shutil.move with no existence check, then prints f'{well}: {len(files)} files' — the source count, not the survivor count. Under --recursive two same-basename files from different subfolders still collapse silently.

## Item 76 — severity Low

**Reported location (line numbers are stale):** `datasets/subset/apply.py:120`, `concatenate/apply.py:436`, `merge_plates/apply.py:243`

**Original finding:** The `seg_fp` restamp is set only inside `with swallow(...)`; on a failed stat the entry silently keeps the **source** dataset's fingerprint and the carried-over override is later discarded as stale.

**Verified state at HEAD:** All three sites still assign inside the suppression: datasets/subset/apply.py:121-122, datasets/merge_plates/apply.py:271-272 and datasets/concatenate/apply.py:438-439 each do `with swallow(f"stat {zip_path}"): entry['seg_fp'] = f"{zip_path.stat().st_mtime_ns}"`, so a failed stat leaves the source dataset's fingerprint on the carried-over override, which validate/apply.py:83 then treats as stale.

