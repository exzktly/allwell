"""A nanowell run's results are keyed on the Reuse registration folder a person named, here and on a remote host.

``--nanowell_registration_from`` takes every timepoint's located result from the named folder as it is: the tile
positions, the lattice, the central square and every nanowell's address (``pipeline.nanowell_located``). It is
checked only to fit the plate's tiles, never against the images or the settings, so the numbers a run produces
depend on which folder was named. Both reuse keys left it out — the settings key (``util.settings_digest``) and the
remote run's address (``services.remote_run_id``) — so re-running into the same output folder with the field blanked
or pointed at another folder, Force not ticked, skipped every well as finished and reported the first folder's
addresses as this run's; a remote run landed in the first run's directory and fetched its old outputs back.

The folder is keyed by its identity (``registration_identity``, a hash of its path), which a remote host is sent as
``--nanowell_registration_identity`` because it reads the folder from a copy in its own run directory — a path new on
every batched attempt, which cannot be the key. A folder the app carried to a host on its own
(``--nanowell_registration_carried``) is reused there only where it is exact, and is not keyed.

Wrong implementations these reject: leaving the folder out of either key (the defect); keying the host on its copy's
path (its key then differs from this computer's for the same run, and changes on every attempt); keying a carried
folder (every remote run's key would differ from the same plate run locally); a key that changes with a trailing
separator typed on the folder.
"""

from __future__ import annotations

import types
from pathlib import Path

from pipeline.cli_args import build_parser
from util.settings_digest import registration_identity, settings_key_for

_BASE = ["--input_dir", "/plate/in", "--output_dir", "/plate/out", "--nanowell_format", "100um"]


def _key(*extra: str) -> str:
    return settings_key_for(vars(build_parser().parse_args([*_BASE, *extra])))


def test_the_settings_key_names_the_folder_the_located_results_came_from():
    none, x, y = _key(), _key("--nanowell_registration_from", "/data/X"), _key("--nanowell_registration_from", "/data/Y")
    assert len({none, x, y}) == 3
    assert _key("--nanowell_registration_from", "/data/X/") == x, "a trailing separator is the same folder"


def test_a_well_made_from_one_folder_is_not_reused_by_a_run_naming_another_or_none(tmp_path):
    from pipeline.settings_record import record_well_settings, well_settings_allow_reuse
    from util.settings_digest import primary_settings_key

    x = _key("--nanowell_registration_from", "/data/X")
    record_well_settings(tmp_path, "A01", primary_settings_key(x))
    assert well_settings_allow_reuse(tmp_path, "A01", x)
    assert not well_settings_allow_reuse(tmp_path, "A01", _key())
    assert not well_settings_allow_reuse(tmp_path, "A01", _key("--nanowell_registration_from", "/data/Y"))


def test_a_carried_folder_is_not_keyed():
    assert _key("--nanowell_registration_from", "/host/runs/r1/reg", "--nanowell_registration_carried") == _key()


def test_the_host_computes_this_computer_s_key_for_the_same_run():
    """Through the real argv rewrite: the host is given its own copy's path and the named folder's identity, and its
    key is the one this computer computes — and stays the same when the copy moves to another attempt's directory."""
    from services.remote_config import RemoteHost
    from services.remote_runner import LocalInputMode, OutputFilter, RemoteRunner

    local = ["/py", "/pm.py", *_BASE, "--nanowell_registration_from", "/data/X"]
    runner = RemoteRunner(RemoteHost(name="t", ssh_alias="t"))
    keys = []
    for attempt in ("run-a", "run-b"):
        runner._run = types.SimpleNamespace(registration_dir=f"~/.st/runs/{attempt}/reg")
        remote = runner._build_remote_argv(local, "/r/in", "/r/out", OutputFilter.FULL, LocalInputMode.ZIP)
        assert "/data/X" not in remote, "the host was handed a path on this computer"
        assert remote[remote.index("--nanowell_registration_identity") + 1] == registration_identity("/data/X")
        start = next(i for i, a in enumerate(remote) if a.startswith("--"))
        keys.append(settings_key_for(vars(build_parser().parse_known_args(remote[start:])[0])))
    assert keys[0] == keys[1] == _key("--nanowell_registration_from", "/data/X")


def test_the_remote_run_s_address_names_the_folder():
    from services.remote_run_id import pipeline_argv_digest

    local = ["/py", "/pm.py", *_BASE]
    none = pipeline_argv_digest(local)
    x = pipeline_argv_digest([*local, "--nanowell_registration_from", "/data/X"])
    y = pipeline_argv_digest([*local, "--nanowell_registration_from", "/data/Y"])
    assert len({none, x, y}) == 3


def test_the_identity_is_a_hash_of_the_path_not_the_path():
    assert registration_identity(Path("/data/X")) == registration_identity("/data/X")
    assert "/data/X" not in registration_identity("/data/X")
