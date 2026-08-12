"""Frame scopes — "which wells / FOVs / timepoints does this act on?"

The Segmentation tab has two region gestures that both need an answer to that
question. A drag-delete normally acts on the one frame on screen and can be
armed wider; a crop normally acts on every frame in the dataset and can be
narrowed. Same three fields, same normalisation, same readout grammar — only the
un-armed default and the wording differ, and those are the two things a
:class:`ScopeSpec` carries.

Everything here is Qt-free: it reads typed state and the control row's combos
through ``ctx``, so the description and the option lists are unit-testable
without building a widget. The popover itself is
:mod:`well_viewer.features.preview.frame_scope_view`.

The ``None`` / empty-set distinction is load-bearing on both sides. ``None`` is
the un-armed field, whose meaning is the spec's (``"this frame"`` for the
delete, ``"all frames"`` for the crop). An *empty set* is the user having
unchecked every entry, which names no frames at all. Collapsing the two would
turn a stray click into either a plate-wide delete or a silently disabled crop.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable


@dataclass(frozen=True)
class ScopeSpec:
    """Everything that distinguishes one frame-scope control from another.

    ``state_attr`` + ``fields`` locate the three ``set | None`` attributes on
    typed state (``ctx.state.<state_attr>.<field>``). ``title`` and the wording
    fields drive the readout; ``seed_current_frame`` decides what an un-armed
    dropdown shows ticked when the popover opens — the displayed frame's value
    for a control that means "this frame", every option for one that means "all
    frames". ``on_change`` is called after any write, for a scope whose change
    has to re-derive something (the crop re-projects ``Crop_Pass``).
    """

    state_attr: str
    fields: tuple[str, str, str]
    title: str
    unarmed_text: str
    unarmed_field_words: tuple[str, str, str]
    button_key: str
    seed_current_frame: bool
    hint: str
    tooltip: str
    reset_label: str
    reset_tooltip: str
    reset_status: str
    plate_status_ok: str
    plate_status_empty: str
    on_change: Callable | None = None


def scope_state(ctx, spec: ScopeSpec):
    """The typed state object holding *spec*'s three fields."""
    return getattr(ctx.state, spec.state_attr)


def read_scope(ctx, spec: ScopeSpec) -> tuple:
    """The three armed field values, in spec order (``set`` or ``None`` each)."""
    state = scope_state(ctx, spec)
    return tuple(getattr(state, f) for f in spec.fields)


def is_armed(ctx, spec: ScopeSpec) -> bool:
    """Whether any field names something other than the spec's default."""
    return any(v is not None for v in read_scope(ctx, spec))


def apply_scope_change(ctx, spec: ScopeSpec) -> None:
    """Run the spec's re-derivation, if it has one.

    Separate from the state write because the two have wildly different costs.
    Writing a field is an attribute set; the crop's re-derivation re-projects
    ``Crop_Pass`` over every cached well, redraws the active plot and re-renders
    the canvas. A control that emits one signal per ticked checkbox has to be
    able to do the first eleven times and the second once.
    """
    if spec.on_change is not None:
        spec.on_change(ctx)


def write_scope_field(ctx, spec: ScopeSpec, index: int, values,
                      *, apply: bool = True) -> None:
    """Arm one field, leaving the other two alone.

    Committing all three together would be the trap: ticking two wells would
    also pin the FOV and timepoint to whatever happened to be on screen, so
    navigating to the next FOV would silently put the user outside their own
    scope. A field the user never touched stays ``None`` and keeps meaning
    whatever the spec says un-armed means.

    ``apply=False`` writes the state without re-deriving anything; the caller
    then owes an :func:`apply_scope_change`, which is how the popover coalesces
    a burst of ticks into one recompute.
    """
    setattr(scope_state(ctx, spec), spec.fields[index],
            None if values is None else {str(v) for v in values})
    if apply:
        apply_scope_change(ctx, spec)


def reset_scope(ctx, spec: ScopeSpec) -> None:
    """Return every field to un-armed, then re-derive."""
    state = scope_state(ctx, spec)
    for f in spec.fields:
        setattr(state, f, None)
    apply_scope_change(ctx, spec)


def _well_sort_key(label: str):
    """Plate order, with a defined answer for labels that are not positions.

    ``parse_rc`` returns ``(None, None)`` for anything it cannot read as a well
    position, and a dataset folder can hold both — ``A01.csv`` beside a
    ``summary_all.csv``. Sorting on ``parse_rc`` alone then raises ``TypeError``
    comparing ``None`` with ``str`` and takes the whole popover down with it, so
    unparseable labels sort last, alphabetically, instead.
    """
    from well_viewer.domain.viewer_state import parse_rc
    row, col = parse_rc(label)
    if row is None or col is None:
        return (1, "", "", str(label))
    return (0, str(row), str(col), str(label))


def scope_options(ctx) -> tuple[list[str], list[str], list[str]]:
    """The pickable wells / FOVs / timepoints for the loaded dataset.

    Read fresh every time the popover opens rather than cached at build time —
    the tab is built lazily and can outlive several datasets, so a list captured
    once would offer the previous plate's wells.
    """
    from well_viewer.data.image_identity import normalize_fov, normalize_tp
    wells = sorted(ctx.state.dataset.well_paths.keys(), key=_well_sort_key)
    fovs = [normalize_fov(f) for f in (ctx.state.dataset.all_fovs_cache or [])]
    tps = [normalize_tp(t) for t in (ctx.state.dataset.all_timepoints_cache or [])]
    return wells, fovs, tps


def current_frame(ctx) -> tuple[str, str, str]:
    """The well / FOV / timepoint the Segmentation control row is showing.

    Normalised the same way the option lists and the gestures' own scope
    resolution are, so a seeded tick actually lands on an offered option — a
    combo reading ``T01`` and an option list holding ``1`` are the same
    timepoint, and comparing them raw would leave the dropdown looking empty.
    """
    from well_viewer.data.image_identity import normalize_fov, normalize_tp
    # The well stays as the dataset spells it — see ``callbacks.override_key``.
    well = str(ctx.state.preview.selected_well or "").strip()
    fov_menu = (ctx.runtime.review_image.fov_menu
                or ctx.widgets.get_or_none("_preview_fov_cb"))
    fov = normalize_fov(fov_menu.currentText()) if fov_menu is not None else ""
    tp_cb = ctx.runtime.review_image.tp_cb
    tp = normalize_tp(tp_cb.currentText()) if tp_cb is not None else ""
    return well, fov, tp


def describe_scope(ctx, spec: ScopeSpec) -> str:
    """One-line readout of the armed scope, used as the button's label.

    The scope is legible without opening anything, deliberately: a scope that
    stays armed across gestures has to be visible on the control row, or a later
    delete reaches further — or a later crop reaches less far — than the user
    remembers asking for.
    """
    armed = read_scope(ctx, spec)
    if all(v is None for v in armed):
        return f"{spec.title}: {spec.unarmed_text}"

    def _part(values, noun: str, total: int, unarmed_word: str) -> str:
        if values is None:
            return unarmed_word
        n = len(values)
        if n == 0:
            return f"no {noun}s"
        if total and n >= total:
            return f"all {noun}s"
        return f"{n} {noun}s" if n != 1 else f"1 {noun}"

    totals = scope_options(ctx)
    nouns = ("well", "FOV", "TP")
    return f"{spec.title}: " + " · ".join(
        _part(values, noun, len(options), word)
        for values, noun, options, word
        in zip(armed, nouns, totals, spec.unarmed_field_words)
    )


def refresh_scope_button(ctx, spec: ScopeSpec) -> None:
    """Re-label the scope button. Safe to call when the tab was never built.

    Every path that changes a scope goes through here — including the ones
    outside the popover (a dataset swap, a Reset that clears the crop) — because
    a button still advertising a scope the state no longer holds is the one way
    this control can lie.
    """
    btn = ctx.widgets.get_or_none(spec.button_key)
    if btn is None:
        return
    btn.setText(describe_scope(ctx, spec) + " ▾")
