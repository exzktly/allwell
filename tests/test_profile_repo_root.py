"""`core.profile` must resolve the repository root, and it does so by depth.

`_repo_root()` counts parent directories from its own `__file__`, so the count
is a statement about where the module sits. Move the module and the count is
wrong, and **nothing raises**: `profiles_dir()` names a directory that does not
exist, no profile resolves, and `active_profile()` falls back to the full
application. A thin (stripped) build would then ship every feature, which is the
precise failure the function's own comment is written to prevent — and a build
that ships too much looks, from the outside, exactly like a build that worked.

This is not hypothetical. The count was `parents[2]` while the module lived at
`review/core/profile.py`; lifting it to `core/profile.py` left it pointing
one directory *above* the checkout until this test was written.

Held on the profiles the repository actually ships rather than on the number
itself: asserting `parents[1]` would only restate the implementation, and would
go on passing if the whole scheme were replaced with something equally wrong.
"""
from __future__ import annotations

from tests._repo_scan import REPO_ROOT


def test_the_repo_root_is_the_repository_root():
    from core.profile import _repo_root

    assert _repo_root() == REPO_ROOT, (
        f"core.profile._repo_root() answered {_repo_root()}, but the repository "
        f"root is {REPO_ROOT}. The function counts parents from its own "
        "__file__, so this means the module moved and the count did not follow. "
        "Nothing else reports it: profiles_dir() simply names a directory that "
        "is not there, and every build falls back to the full application.")


def test_the_profiles_directory_is_the_one_that_ships():
    """The directory `ALLWELL_PROFILE` is resolved against must exist and hold the profiles."""
    from core.profile import profiles_dir

    d = profiles_dir()
    assert d == REPO_ROOT / "profiles", f"profiles_dir() answered {d}"
    assert d.is_dir(), f"{d} is not a directory — no profile can resolve"
    shipped = sorted(p.name for p in d.glob("*.toml"))
    assert shipped, (
        f"{d} holds no .toml files, so every ALLWELL_PROFILE build would fall "
        "back to the full application without saying so")


def test_a_shipped_profile_actually_resolves():
    """The end the user reaches: a named profile parses into a restricted feature set.

    The two tests above would both pass against a `_repo_root` that found the
    right directory by luck while `active_profile` was broken some other way.
    This one drives the function the application calls.
    """
    import os

    from core.profile import active_profile, profiles_dir

    # Deliberately not the first profile on disk: that is `full.toml`, the
    # catalogue for the whole application, whose resolved `features` is `None`
    # *correctly*. Testing with it would make the assertion below true of a
    # working resolver and of one that read nothing at all — the two answers
    # have to differ for the test to measure anything.
    restricting = [p.stem for p in sorted(profiles_dir().glob("*.toml"))
                   if p.stem != "full"]
    assert restricting, (
        "no profile other than `full` on disk, so nothing here can tell a "
        "resolved profile from the fallback")
    name = restricting[0]
    prev = os.environ.get("ALLWELL_PROFILE")
    try:
        os.environ["ALLWELL_PROFILE"] = name
        prof = active_profile()
    finally:
        if prev is None:
            os.environ.pop("ALLWELL_PROFILE", None)
        else:
            os.environ["ALLWELL_PROFILE"] = prev

    assert prof.name == name, (
        f"ALLWELL_PROFILE={name} resolved to profile {prof.name!r}. A profile "
        "that silently resolves to something else is the full-application "
        "fallback wearing the requested name.")
    assert prof.features is not None, (
        f"profile {name!r} admitted every feature (features is None), which is "
        "the full application. Either the .toml was not read or it was read "
        "from the wrong place.")
