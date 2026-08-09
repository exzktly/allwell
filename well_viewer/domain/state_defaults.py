"""Restore a typed state dataclass to its declared defaults, in place.

The reset commands (``persistence/reset_scopes.py``) need to return a state
slice to the value a freshly-opened dataset would have. Assigning a brand-new
instance over ``ctx.state.<slice>`` is not an option: widgets, controllers and
the redraw coordinator all hold references to the existing object, so a
replacement would leave half the app pointing at the old one.

Restoring field-by-field from a fresh instance keeps the identity and — the
reason this lives in one helper rather than an explicit field list at each call
site — automatically covers fields added later. A hand-written reset drifts the
moment somebody adds a preference to the slice, and the drift is invisible: the
new field simply stops being reset, while the persisted copy of it is still
rewritten.

Qt-free: stdlib only.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass


def restore_defaults(obj, *, keep: frozenset[str] = frozenset()) -> list[str]:
    """Reset every field of dataclass *obj* to its declared default, in place.

    Fields named in *keep* are left as they are — for values that are derived
    from the loaded dataset rather than chosen by the user, where the declared
    default ("", None) would be a worse state than the current one.

    Returns the names of the fields that were actually changed, so a caller can
    log or test what a reset touched. Raises ``TypeError`` when *obj* is not a
    dataclass instance; a silent no-op there would make a reset that resets
    nothing look like a reset that worked.

    "Default" means the value a no-argument instance of the same class carries,
    so a field whose default comes from ``__post_init__`` resets to whatever
    that computes rather than to a bare sentinel.
    """
    if not is_dataclass(obj) or isinstance(obj, type):
        raise TypeError(f"restore_defaults() needs a dataclass instance, got {obj!r}")
    fresh = type(obj)()
    changed: list[str] = []
    for f in fields(obj):
        if f.name in keep:
            continue
        default = getattr(fresh, f.name)
        if getattr(obj, f.name) != default:
            changed.append(f.name)
        setattr(obj, f.name, default)
    return changed
