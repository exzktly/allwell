"""Shared assertion for the A8 widget-registry migration tests.

Nine per-feature test modules each proved the same three things about their own
tab builder's Qt handles, with the same three assertions written out longhand.
Only the builder and the handle list differ, so the loop lives here and the
modules keep their own ``_HANDLES`` tuple, their own docstring explaining what
that feature moved, and their own file.

Deliberately *not* collapsed into one parametrised module. Each file documents a
distinct migration, is findable by the feature name in its filename, and — since
every one of them uses the ``viewer_app`` fixture and so lands in the slow lane
— nine separate files shard across the nightly runners where one merged file
could not (see ``scripts/test_slow.sh``).
"""

from __future__ import annotations


def assert_handles_registered(app, handles) -> None:
    """Every name in *handles* is in the registry, stamped on *app*, and the
    two resolve to the same object.

    The third assertion is the load-bearing one: ``WidgetRegistry.register``
    both stores the handle and ``setattr``s it onto the app, and the legacy
    ``app._<widget>`` readers throughout the controllers depend on those two
    staying the same object rather than merely both being non-None.
    """
    for name in handles:
        assert app._widget_registry.has(name), f"{name} not registered"
        assert getattr(app, name, None) is not None, f"{name} not on app"
        assert getattr(app, name) is app._widget_registry.get(name), (
            f"{name}: app attribute and registry entry are different objects"
        )
