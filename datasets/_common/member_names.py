"""Schema-safe, field-agnostic zip-member/filename rewriting.

Generalised from ``datasets.concatenate.concatenate_compute.rewrite_member_name``,
which was already structurally field-agnostic (the gate is: recognised image
extension, exactly as many separator-delimited fields as the schema declares
after stripping any role suffix, and ``discovered_channel``/``role``/``ext``
carried through so a channel-scoped suffix like ``_tophat_gfp`` is never
renamed to the wrong token) but hardcoded to rewriting the ``timepoint`` field.
Concatenate needs to shift a timepoint; Merge plates needs to remap a well —
same gate, different field and different validity predicate, so the gate is
the shared code and the predicate is supplied by the caller.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from well_viewer.data.image_identity import (
    ParsedName,
    PipelineSchema,
    ROLE_CATALOG,
    build_image_filename,
    parse_image_filename,
)

# Every extension any catalogued image role writes or accepts — the
# structural gate for "does this zip member look like a schema-conformant
# image filename" (as opposed to some unrelated file a user dropped in).
IMAGE_EXTS = frozenset(
    ext.lower() for spec in ROLE_CATALOG.values() for ext in spec.all_exts
)


def discover_parsed_member(
    name: str, schema: PipelineSchema, channels: tuple[str, ...],
) -> tuple[ParsedName, str]:
    """Parse *name* against *schema*, trying each known channel token as the
    role-suffix discriminator until one resolves a role. Returns
    ``(parsed, discovered_channel)`` — *discovered_channel* is the exact
    token that matched a channel-scoped role suffix (``""`` for an unscoped
    role or a bare/raw image), which is NOT the same as ``parsed.channel``
    (that property prefers the schema's own ``channel`` field and would
    rename a ``_tophat_<chan>``/``_smfish_<chan>`` suffix to the wrong token
    on rebuild).
    """
    best = parse_image_filename(name, schema=schema)
    if best.role is not None:
        return best, ""
    for tok in channels:
        candidate = parse_image_filename(name, schema=schema, fluor_token=tok)
        if candidate.role is not None:
            return candidate, tok
    return best, ""


def rewrite_member_field(
    name: str,
    *,
    schema: PipelineSchema,
    channels: tuple[str, ...],
    field: str,
    new_value: Callable[[str], str | None],
) -> tuple[str, bool]:
    """Return ``(new_name, was_rewritten)`` with *name*'s *field* rewritten
    through *new_value*.

    Rewrites only when *name* is structurally conformant to *schema* —
    recognised image extension, exactly as many separator-delimited fields as
    the schema declares (after stripping any role suffix) — and when
    ``new_value(old_token)`` returns a replacement rather than ``None``. A
    name that fails any of those checks is returned unchanged
    (``was_rewritten=False``) rather than risk a positional rewrite landing
    on the wrong field — a non-schema member of a user-supplied zip, or a
    role suffix this dataset's channel list doesn't cover, passes through
    untouched instead of being silently corrupted.

    *new_value* is the caller's validity predicate: ``parse_hours(...)``
    finite for a timepoint shift, ``canonical_well_label(...)`` equal to an
    expected source well for a well remap. Returning ``None`` refuses the
    rewrite for that member.
    """
    ext = Path(name).suffix.lower()
    if ext not in IMAGE_EXTS:
        return name, False

    parsed, discovered_channel = discover_parsed_member(name, schema, channels)
    parts = parsed.stem.split(schema.sep)
    if len(parts) != len(schema.fields):
        return name, False

    old_token = parsed.fields.get(field, "")
    new_token = new_value(old_token)
    if new_token is None:
        return name, False

    new_fields = dict(parsed.fields)
    new_fields[field] = new_token
    new_name = build_image_filename(
        fields=new_fields, schema=schema, role=parsed.role,
        channel=discovered_channel, ext=parsed.ext,
    )
    return new_name, True
