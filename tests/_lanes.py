"""The test lanes beside the fast lane: which files are in each, and when each runs.

Kept free of every import beyond the standard library so that
``scripts/lane_needed.py`` — which CI runs before it has installed the test
stack — and ``tests/conftest.py``, which marks the tests, read one copy.

Four rosters, each a lane of its own and each marked by the collection hook:

``NANOWELL_E2E_FILES`` (``nanowell_e2e``, ``scripts/test.sh --nanowell``)
    Rendered tiles through the real detection; seconds to a minute per test.
``NANOWELL_FEATURE_FILES`` (``nanowell_feature``, ``--nanowell-features``)
    Everything else about nanowells: the form, the page, the playground's
    array search, Review's nanowell data. About a third of what the fast lane
    used to spend.
``REMOTE_FILES`` (``remote_lane``, ``--remote``)
    ssh, rsync, the remote runner, batches, Slurm, the bootstrap, the Remotes
    dialogs.
``UI_LANE_FILES`` (``ui_lane``)
    GUI layout and appearance. Part of the *local* fast lane; CI leaves them
    out with ``--no-ui`` and never runs them.

**When the gated lanes run.** The first three run only when a change can reach
them. ``scripts/lane_needed.py <gate>`` answers that for a gate in ``GATES``:
a changed path matching the gate's patterns, or a first-party module that one
of the gate's roster files imports *directly* (read from the files on each
run), means run it; a diff that cannot be computed also means run it.
``scripts/preflight.sh`` and CI both ask. The trigger cannot be the import
closure — one ``import analyze.tab`` reaches most of the application — so a
module two imports away can change a gated test's result without triggering
its lane; CI therefore runs every gated lane on each push to ``main``, where
such a miss is caught on the merge rather than never.

**The UI roster.** Tests whose subject is how the GUI *looks and lays out*:
widget geometry, sizing, wrapping and fit, spacing, colours and painting,
stylesheets and theme tokens, phone layout, dashboards and their legends, and
reusable widgets from ``widgets/`` with no app knowledge. A test that drives a
control and asserts what reached the run spec, the argv, a state slice or a file
is a behaviour test even when it builds a Qt form: it must run in CI and does
not belong there. Each UI entry gives the reason it is a layout test.

No file is in two rosters, and no guard (a test that reads the whole source
tree) is in any: guards run on every push.
"""
from __future__ import annotations

#: Test files run by the local fast lane and left out of CI (``scripts/test.sh --no-ui``).
UI_LANE_FILES: dict[str, str] = {
    # ── reusable widgets (widgets/), no app knowledge ──────────────────────
    "test_a_plate_keeps_its_size.py": "plate height-for-width beside a growing sibling",
    "test_button_row.py": "a button row wraps instead of widening its host",
    "test_chip_group.py": "ChipGroup press/selection behaviour of a bare widget",
    "test_color_picker_popover.py": "the colour picker's SV square and hue strip",
    "test_color_swatch_row.py": "swatch layout, gaps and the Custom tile",
    "test_context_banner_fits.py": "a banner shows its whole sentence at any width",
    "test_control_band_threshold_placement.py": "Threshold sits last in the band at row height",
    "test_control_row_spacing.py": "control-row and checkbox geometry as numbers",
    "test_disabled_controls_look_disabled.py": "a disabled control renders differently",
    "test_drawer_edges.py": "drawer geometry on the right and bottom edges",
    "test_eventfilter_qstandarditem.py": "the drawer's event filter survives a stray item",
    "test_flow_group.py": "a label and its control stay one FlowLayout item",
    "test_flow_layout.py": "FlowLayout wrapping and vertical centring",
    "test_flow_layout_visibility.py": "FlowLayout re-lays out on show/hide",
    "test_plate_canvas_role.py": "the canvas plate role's size caps",
    "test_plate_order_editor.py": "the acquisition-ordering plate widget",
    "test_plate_progress.py": "PlateProgress headline, legend and divider layout",
    "test_plate_progress_dial.py": "a well in progress paints as a dial",
    "test_plate_selection_models.py": "the plate widget's selection models and rings",
    "test_plate_selection_rim.py": "a selected well looks selected",
    "test_plate_tooltip_guard.py": "the plate tooltip handler on an inactive window",
    "test_plot_card_header_state.py": "PlotCard header controls keep their state",
    "test_plot_card_save_button.py": "PlotCard's hover save buttons",
    "test_rail_nav_height.py": "a rail nav takes its rows' height, not the region's",
    "test_rail_nav_label_follows_selection.py": "only the selected rail entry is bright",
    "test_responsive_row.py": "a row of panels stacks before it clips",
    "test_segmented_control_tones.py": "the segmented control paints its declared tones",
    "test_selection_row_chips.py": "a saved selection's chips or placeholder",
    "test_split_view_layout.py": "Split View pane sizes and splitter handle",
    "test_stage_path_view.py": "the stage-path view's geometry borrowed from a plate",
    "test_stepper_affixes.py": "a stepper with a suffix validates its text",
    "test_stepper_focus_keeps_height.py": "focusing a stepper keeps its touch height",
    "test_widgets_support.py": "set_variant updates the style property",
    "test_wrap_checkbox.py": "a wrapping checkbox reports its height",
    "test_section_header_layout.py": "a section title keeps one line while it fits",
    # ── theme tokens and the stylesheet (ui/theme.py) ──────────────────────
    "test_theme_accent_pairing.py": "accent2 moves with accent in every theme",
    "test_theme_icon_assets.py": "every icon URL in the stylesheet exists",
    "test_theme_registry.py": "the theme registry's start and fallback themes",
    "test_theme_section_header.py": "section-header titles are styled as headers",
    "test_nanowell_theme.py": "the nanowell occupancy colour ramp",
    "test_home_plate_colors.py": "Home's plate paints one shared colour per state",
    "test_home_plate_render.py": "what Home's plate paints, sampled from pixels",
    "test_cell_viewer_outline.py": "the cell viewer paints the red outline",
    # ── page layout: widths, fit, alignment, placement ─────────────────────
    "test_analyze_banners_are_readable.py": "Analyze's banners have a rule and room",
    "test_analyze_narrow_sidebar_fit.py": "the Analyze form fits the rail width",
    "test_left_panel_width_consistency.py": "the three left panels share one width",
    "test_mode_bar_collapse.py": "the mode bar collapses with the rail",
    "test_page_alignment.py": "every page starts its first row at one height",
    "test_review_actions_placement.py": "the dataset action cluster's place on the band",
    "test_playground_dock_fits.py": "the Playground dock never squeezes its text",
    "test_spin_box_shows_its_value.py": "a spin box has room for its number",
    "test_properties_panel_frame.py": "the Properties panel's frame and label column",
    "test_dataset_equation_row.py": "the 'dataset 1 + dataset 2 = destination' row",
    "test_sidebar_visibility.py": "the rail collapse toggle drives the right sidebar",
    "test_datasets_terminology.py": "Datasets headings and dashboard titles",
    # ── progress dashboards (paint + legend) ───────────────────────────────
    "test_dashboard_legends.py": "every dashboard legend matches what it paints",
    "test_datasets_dashboard_outcomes.py": "every Datasets dashboard paints the same end states",
    "test_merge_plates_dashboard.py": "Merge Plates dashboard colours and markers",
    "test_import_loose_dashboard.py": "Import Loose dashboard stages and markers",
    "test_subset_dashboard.py": "Subset dashboard stage display",
    "test_job_queue_view.py": "the job queue page's captions, scroll area and rows",
    # ── phone layout ───────────────────────────────────────────────────────
    "test_phone_layout.py": "phone layout's rules, type tokens and rescale pass",
    "test_phone_layout_app.py": "phone layout through the real window",
    "test_phone_fit.py": "a desk-shaped form narrowed to a phone",
    "test_phone_datasets_shell.py": "phone shell fits the Datasets pages",
    "test_phone_form_swipe.py": "form pages scroll by a drag on a phone",
    "test_phone_plot_swipe.py": "a swipe on a plot scrolls its page",
    "test_phone_panel_sheet.py": "the Properties panel as a full-width sheet",
    "test_home_phone_layout.py": "Home's Overview as one column on a phone",
    "test_analyze_phone_view.py": "Analyze's form-as-page phone layout",
}


#: The nanowell end-to-end files, which run in their own lane
#: (``scripts/test.sh --nanowell``, a CI job of its own) rather than the fast
#: lane. Each renders synthetic tiles and runs the real lattice and site
#: detection over them, so a test body is seconds to a minute of NumPy and
#: SciPy: together about 1,075 s of the fast lane's 1,960 s when they joined it,
#: which took the lane from about 2.5 min to 9.5 min on four workers and put
#: CI within three minutes of its 15-minute cap. ``test_nanowell_run.py`` alone
#: was 475 s, and with one file per worker it set the whole lane's wall time.
#: Qt-free; the nanowell form and page tests stay in the fast lane.
#: ``tests/test_the_fast_lane_stays_fast.py`` holds every entry to a real file
#: and the CI workflow to running this lane.
NANOWELL_E2E_FILES = {
    "test_nanowell_run.py": "the (well, timepoint) task body on rendered tiles; 475 s",
    "test_nanowell_infer.py": "array inference on rendered images; 210 s",
    "test_nanowell_cli.py": "process_microscopy.main in nanowell mode, end to end; 111 s",
    "test_nanowell_detect.py": "lattice and site detection on rendered tiles; 52 s",
    "test_nanowell_resume.py": "a killed well resumes; 42 s",
    "test_nanowell_reanchor.py": "the end-of-run re-anchoring pass; 39 s",
    "test_nanowell_review_fixes.py": "review findings pinned on rendered runs; 37 s",
    "test_nanowell_merge_endtoend.py": "two datasets concatenated then analysed; 36 s",
    "test_nanowell_preview.py": "test detection on one well and timepoint; 33 s",
    "test_nanowell_sites.py": "site state on rendered tiles; 22 s",
    "test_nanowell_detection_chain.py": "image in, addresses out; 18 s",
    "test_nanowell_registration_reuse.py": "a second run reuses the first's tile registration; 29 s",
    "test_a_nanowell_well_is_read_in_place.py": "a nanowell well reads its tiles from the archive, never unpacked; 30 s",
    "test_nanowell_tile_brightness.py": "tiles of different gain and offset, and a misread surround, measure alike; 105 s",
    "test_nanowell_real_plate.py": "tracker section F: the real plate's geometry on rendered tiles; 20 s",
    "test_nanowell_manual_anchor_run.py": "an anchor set by hand addresses a well end to end; 40 s",
    "test_nanowell_segmenter_input_sheet.py": "a run writes the sample of what its segmenter was given; 8 s",
}


#: Nanowell feature tests: the form, the page, the playground's search, Review's
#: nanowell data — everything nanowell that is not the rendered end-to-end runs
#: above. ``scripts/test.sh --nanowell-features``. The description is each file's
#: own docstring; membership is by subject, so no further reason is owed.
NANOWELL_FEATURE_FILES: dict[str, str] = {
    "test_an_unaddressed_nanowell_timepoint_offers_its_anchor.py": "A nanowell timepoint the run left unaddressed is named as such, and opens straight onto …",
    "test_an_unaddressed_nanowell_well_is_not_staged_and_never_processed.py": "A nanowell well whose timepoints ran and could not be addressed is partial on Home, not …",
    "test_batch_export_nanowell_mode.py": "Batch Export's Nanowells mode (tracker I4): per export group, the chosen view per metric …",
    "test_nanowell_addressing.py": "Exact address conventions independent of image detection",
    "test_nanowell_anchors_follow_the_dataset_tools.py": "The nanowell anchors set by hand go where the tools put the images …",
    "test_nanowell_array_controls.py": "The Array view's band stays put, its overlays toggle with one button, its labels carry the …",
    "test_nanowell_array_refits_and_position_colours.py": "Two reported Nanowells defects",
    "test_nanowell_batch_release.py": "A batched nanowell run frees each batch's wells on the remote once the batch has ended",
    "test_nanowell_cell_dots.py": "The Array view's Cells layer: a dot at the centre of each cell the run counted (nanowell …",
    "test_nanowell_cell_rows_are_linear.py": "A nanowell well's per-cell rows cost the same per cell at a million cells as at ten",
    "test_nanowell_final_pass.py": "A batched nanowell run's re-anchoring is one parallel pass after the last batch",
    "test_nanowell_cellpose_model.py": "The Cellpose model the nanowell count segments with (nanowell tracker L2)",
    "test_nanowell_completeness.py": "Completeness of a nanowell well is counted in timepoints, the unit of work there (tracker P8)",
    "test_nanowell_contents.py": "Section C: what is inside each nanowell (tracker §3.5, §3.6)",
    "test_nanowell_count_settings_sit_with_segmentation.py": "The nanowell count's own segmentation settings sit in the Segmentation section, shown in …",
    "test_nanowell_crop_cells.py": "The counted cells drawn on every Nanowells crop, as the Array view's Cells layer draws them …",
    "test_nanowell_crop_view.py": "The selected nanowell's crops: the strip under the site detail, and **Pop out**, which …",
    "test_nanowell_dataset_carry.py": "Subset and Merge Plates carry a dataset's nanowell tables (tracker P10b, P10c), driven …",
    "test_nanowell_dataset_tools.py": "Subset, Merge Plates and Concatenate refuse nanowell results by name; Validate checks the …",
    "test_nanowell_display_range.py": "The Nanowells page's **Display range**: a *min* / *max* / **Auto** brightness window for …",
    "test_nanowell_edge_points_vectorised.py": "``nanowell_detect._edge_points`` is vectorised over its rays without changing a bit",
    "test_nanowell_extremes_layout.py": "The Extremes montage laid out to fill its figure, the timepoints it shows chosen, and each …",
    "test_nanowell_fiducial_reason.py": "An unaddressed timepoint says why the central square was refused (tracker F1)",
    "test_nanowell_figures_read.py": "The Nanowells page's figures read at the size they are drawn: type scaled to the panel, …",
    "test_nanowell_fixed_cohort.py": "The fixed-cohort option (tracker I8): every line over time follows one set of nanowells",
    "test_nanowell_fluorescence_channel.py": "Fluorescence on the Nanowells page: the page's own *Channel* control for Brightness and …",
    "test_nanowell_form.py": "The Nanowells section, driven through the real ``AnalyzeTab`` (tracker A1, A2, A3, A5; …",
    "test_nanowell_form_grid.py": "The Analyze form's custom plate format (tracker A7) and tile grid for a dataset with no …",
    "test_nanowell_form_registration.py": "The Analyze form's *Reuse registration* field: an earlier nanowell run's output folder …",
    "test_nanowell_form_review.py": "Behaviour of the Nanowells section that a reviewer found missing, each driven through the …",
    "test_nanowell_form_settings.py": "The settings the Analyze form exposes reach the analysis, not just the argv (tracker A2)",
    "test_nanowell_full_montage.py": "The Array view at full resolution: a well's registered montage drawn from the raw tiles, …",
    "test_nanowell_geometry_review.py": "Findings of an independent review of the geometry modules, each reproduced before it was fixed",
    "test_nanowell_lattice.py": "Recover geometry against independently known lattice coordinates",
    "test_nanowell_levels.py": "Bringing a nanowell to the template's levels before dividing by it (``nanowell_measure`` …",
    "test_nanowell_manual_anchor.py": "Anchors set by hand for nanowell wells whose central square the run could not find …",
    "test_nanowell_ownership.py": "Ownership checks calculate seam geometry without using the implementation",
    "test_nanowell_page.py": "The Nanowells page driven through its real builder, worker and canvas, in a minimal shell …",
    "test_nanowell_page_array_layers.py": "The Array view's elements are each turned on and off, and the three views it replaced are …",
    "test_nanowell_page_compute.py": "The Nanowells page's pure layer: the beeswarm geometry and the Distribution model (tracker …",
    "test_nanowell_page_extremes.py": "The Nanowells page's Extremes view: one well's nanowells with the largest and the smallest …",
    "test_nanowell_page_starting_cells.py": "The Nanowells page's *Starting cells* presets of its *Subset*: only the nanowells that held …",
    "test_nanowell_page_views.py": "The Array map, Trajectories, Position and Array QC data, and the nanowell crops (tracker …",
    "test_nanowell_page_views_ui.py": "The Nanowells page's Array, Trajectories and Position views, driven through the real builder,",
    "test_nanowell_pattern_record.py": "The record built from a described grid for a dataset without one (tracker P15)",
    "test_nanowell_plate_diameters.py": "Each well's nanowell diameter against its plate's (tracker F11)",
    "test_nanowell_plate_rigid.py": "Plate transforms tested against an independently evaluated screen rotation",
    "test_nanowell_preview_job.py": "Test detection runs in a child process, so a segmenter that kills its process cannot kill the app",
    "test_nanowell_reads_selected_wells.py": "The Nanowells page reads only the wells it draws",
    "test_nanowell_responding_threshold.py": "Fraction responding is counted above a cut the user sets, one per metric, with no default …",
    "test_nanowell_review_data.py": "Review's nanowell data and calculations (tracker Section D), on tables written in the …",
    "test_nanowell_review_load.py": "Review reads what a real nanowell run wrote (tracker D1), and the well loader never reads …",
    "test_nanowell_segment_crops.py": "The nanowell count's segmenter is given a crop around each nanowell, not the whole tile …",
    "test_nanowell_segment_on.py": "What the nanowell count's segmenter is given: the tile as imaged, or the rim-divided tile …",
    "test_nanowell_segmenter_view.py": "What the segmenter is shown, written beside each nanowell's count",
    "test_nanowell_shows_the_rail_plate.py": "Opening the Nanowells page shows the rail plate",
    "test_nanowell_subset.py": "The Nanowells page's *Subset*: any number of conditions — starting cells, a metric at a …",
    "test_nanowell_synth.py": "Ground-truth geometry is checked independently of the fitting code",
    "test_nanowell_timepoint_hours.py": "A nanowell run's per-cell CSV carries ``timepoint_hours``, as the per-field path's does",
    "test_playground_nanowell_button.py": "The Playground's Nanowell array buttons, pressed for real, fill the window with the Analyze …",
    "test_playground_nanowell_compute.py": "The Playground's nanowell array search: from one well to the Analyze form's Nanowells settings",
    "test_remote_fetch_nanowells.py": "A nanowell well's tables come back with its other output (tracker P9)",
    "test_test_detection_outlines_can_be_hidden.py": "Test detection's nanowell outlines can be turned off, to see the montage they cover",
    "test_tile_register.py": "Registration, montage and fused crops, checked against tiles rendered from a known array",
}

#: Remote / HPC tests: ssh, rsync, the remote runner and its batches, Slurm,
#: the bootstrap and the Remotes dialogs. ``scripts/test.sh --remote``.
REMOTE_FILES: dict[str, str] = {
    "test_a_batched_restart_reuses_the_remote.py": "A restarted batched run looks at the remote before it uploads or runs anything",
    "test_a_batched_resume_does_not_download_first.py": "A batched resume starts the run at once; wells already here are painted, whatever the …",
    "test_a_batched_subset_run_finds_its_wells.py": "A remote run split into batches can be limited to named wells",
    "test_a_lost_batch_handoff_is_noticed.py": "A batch handed to the batch driver either reaches it, or the run says it did not",
    "test_a_remote_run_end_to_end.py": "A remote run, driven end to end against a host that really has a filesystem",
    "test_a_remote_suspend_winds_the_run_down.py": "A remote Suspend asks the pipeline to wind down; it does not kill the session",
    "test_a_resume_keeps_checkpoints_when_the_blank_moves.py": "A resumed batched run keeps its wells' part-way checkpoints though the blank's path changed",
    "test_a_resumed_remote_run_downloads_before_uploading.py": "Resuming a remote run downloads what the server finished; it never re-uploads it",
    "test_a_retry_keeps_the_staged_batches.py": "A batched run that dies before its node boots does not re-send its uploads",
    "test_a_run_reserves_its_node.py": "A scheduled run asks Slurm for its node to itself (--exclusive) unless the Remotes dialog says not to",
    "test_a_second_incremental_remote_run_does_its_work.py": "A resumed remote run must run the groups it uploaded, however often it resumes",
    "test_a_slow_merge_does_not_hold_the_next_download.py": "One well's merge does not hold the next well's download — or anyone's remote copy but its own",
    "test_a_starved_slurm_job_says_so.py": "A Slurm job given one core of a big node says so in the run log",
    "test_a_stop_mid_download_leaves_nothing_broken.py": "A Stop part-way through a download leaves a truncated zip; the next attempt replaces it, …",
    "test_a_subset_run_resumes_only_its_wells.py": "A run limited to named wells resumes only those wells, so an unrelated unfinished run does …",
    "test_a_vanished_app_suspends_the_batch_in_flight.py": "An app that disappears while a batch is running hands the node back",
    "test_acquisition_remote_transport.py": "Metadata reaches scoped uploads and changes cache identity without pixel edits",
    "test_analyze_background_remote_ui.py": "Analyze-tab UI grouping regressions (runs headless / offscreen Qt)",
    "test_analyze_run_target_is_the_users_choice.py": "Audit U2 — \"Run on:\" keeps the target the user picked",
    "test_batch_driver.py": "One Slurm allocation for every batch, instead of one per batch",
    "test_batched_empty_well_correction.py": "Empty-well correction has to reach every batch, from one upload",
    "test_batched_remote_returncode.py": "Audit E1 — a crashed batched remote run must NOT report success",
    "test_batched_remote_teardown.py": "Audit X10 / E20 — batched-remote teardown safety",
    "test_batched_run_progress.py": "Run progress over a batched remote run must count wells, not markers",
    "test_bootstrap_cellpose_env.py": "The bootstrap creates, probes and installs the ``allwell-cellpose`` env beside ``allwell``",
    "test_bootstrap_env_repair.py": "Bootstrap can rebuild an environment whose creation failed part-way",
    "test_bootstrap_frozen_dispatch.py": "Guardrail: the remote-bootstrap subprocess must use the frozen dispatch",
    "test_bootstrap_lib_key.py": "Tests for services/remote_bootstrap (v5.2.2 additions)",
    "test_bootstrap_log_retention.py": "Audit X15 — bootstrap logs must not grow without bound",
    "test_bootstrap_probes_every_requirement.py": "A re-bootstrap installs whatever the remote env is missing, not only TensorFlow",
    "test_default_remote_index.py": "Pure test for the Analyze 'Run on:' default-selection helper",
    "test_fetch_builds_a_runnable_launcher.py": "Fetch turns a JupyterHub profile into a launcher srun can run",
    "test_fetch_materialize_atomic.py": "Audit X2 / X9 / E14 — remote-fetch robustness",
    "test_fetch_reports_what_it_is_doing.py": "The plate and the log must agree about where a well's download is",
    "test_force_reprocesses_a_remote_run.py": "Force reprocess all wells reprocesses all wells on a remote run",
    "test_jupyterhub_profiles.py": "Tests for JupyterHub compute-profile discovery in services.remote_config",
    "test_merge_into_gz_csv.py": "A downloaded partial merges straight into the well's ``.csv.gz`` — fast, never plain on disk",
    "test_one_known_hosts_file_on_windows.py": "On Windows every ssh the app starts reads one known-hosts file, so a changed host key is …",
    "test_only_wells_remote.py": "``--only_wells`` on a remote run: only the subset is sent, and the host is",
    "test_reattach_makes_its_own_run_dir.py": "A re-attached run still needs the directory its *own* control files live in",
    "test_remote_allocation_overlap.py": "Ask the scheduler for a node while the plate is still uploading",
    "test_remote_cellpose_env.py": "A Cellpose run on a remote host uses the ``allwell-cellpose`` env when the host has it, …",
    "test_remote_conda_env.py": "The remote run resolves — and checks — the conda environment it will use",
    "test_remote_config.py": "Tests for services/remote_config.py",
    "test_remote_config_writer.py": "Tests for services.remote_config.write_remote / delete_remote",
    "test_remote_dialog.py": "Smoke tests for analyze.remote_dialog (v5.1.0)",
    "test_remote_dialog_defects.py": "Audit U4, U5, U9, U10, U11, U14, U15, U16 — the Manage Remotes dialogs",
    "test_remote_fetch_freshness.py": "A remote run has to prove it replaced the output it was told to replace",
    "test_remote_fetch_verification.py": "What a remote run's fetched output must prove before the remote copy goes",
    "test_remote_incremental.py": "Client-side incremental split for remote runs",
    "test_remote_input_scan_cost.py": "Starting a remote run must not stat every image in a FLAT plate",
    "test_remote_process.py": "The single-launch remote process keeps its promises to the Fetcher",
    "test_remote_runner_facade.py": "The remote runner's facade is its ``__all__``, and nothing else is reached",
    "test_remote_scratch_dir.py": "The pipeline's scratch volume on the remote is a per-host setting",
    "test_remote_spawn_phases.py": "What ``RemoteRunner.spawn`` leaves behind for the rest of the run",
    "test_remote_staging.py": "Local input staging cleans up after itself, on success and on failure",
    "test_remote_transit_volume.py": "A remote run must not write to the home volume",
    "test_remote_write_retry.py": "The remote client's own writes survive a transient storage fault",
    "test_rsync_retry.py": "Tests for the v5.1.1 rsync retry helper",
    "test_run_journal_reattach.py": "Re-attaching a remote run to the output directory a previous one recorded",
    "test_scheduler_probe.py": "The Remotes dialog's scheduler test runs through the real launch path",
    "test_srun_boot_retry.py": "The ``srun`` wrapper prefers awake nodes and retries a node that fails to boot",
    "test_ssh_key_setup.py": "Tests for services.ssh_key_setup (v5.2.1)",
    "test_staging_rewrite_reasons.py": "Why an upload rewrites a well archive, and when it should not bother",
    "test_stop_never_waits_on_a_remote_upload.py": "Stop, pressed while a remote run is still uploading, answers at once",
    "test_stream_rsync_retry.py": "Audit A11 — the shared streaming-rsync retry loop",
    "test_the_launch_command_survives_the_command_line.py": "The launch command reaches the remote intact from a Windows client",
    "test_the_remote_link_fails_fast.py": "A bad link to the remote is noticed, retried or reported — never waited on",
    "test_the_slurm_job_indicator.py": "The Analyze pane's Slurm-job indicator: lit only while a job is open",
    "test_transient_progress_lines.py": "Transfer progress is live state, so it does not go in the log",
    "test_upload_rail_counts_wells.py": "The Upload rail counts wells that are on the remote, not rsync's chatter",
}


#: The harness files every gate watches: a change to how lanes are split or run
#: can break any of them.
_HARNESS = (
    r"^tests/_lanes\.py$",
    r"^tests/conftest\.py$",
    r"^scripts/test\.sh$",
    r"^scripts/lane_needed\.py$",
    r"^pyproject\.toml$",
    r"^tests/requirements-dev\.txt$",
    r"^\.github/workflows/ci\.yml$",
)

#: Gate name -> (the rosters it runs, the path patterns that trigger it).
#: ``scripts/lane_needed.py`` adds each roster file's own direct imports to the
#: patterns, so only paths no roster file imports by name need listing here.
GATES: dict[str, tuple[tuple[dict[str, str], ...], tuple[str, ...]]] = {
    "nanowell": (
        (NANOWELL_E2E_FILES, NANOWELL_FEATURE_FILES),
        (r"nanowell", r"^pipeline/", r"^process_microscopy\.py$",
         r"tile_register", *_HARNESS),
    ),
    "remote": (
        (REMOTE_FILES,),
        (r"^services/remote", r"ssh", r"slurm", r"srun", r"rsync", r"scheduler",
         r"bootstrap", r"transport", r"^scripts/allwell_remote", r"^analyze/remote_dialog",
         r"^services/run_journal", r"^services/pipeline_runner", *_HARNESS),
    ),
}
