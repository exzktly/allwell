"""Which panels a batch figure draws, and what its axes are fixed to.

A batch mode's figure is not one plot. The line mode stacks four panels —
mean, fraction above the cutoff, N(above), and a CDF — and the bar mode three,
each of them a *different quantity*. Until now a run took all of them at
whatever range matplotlib autoscaled to, so preparing one figure for a paper
meant exporting it, opening it in something else, and fixing the axes by hand
for every group.

This module is the shared half of the fix: each panel a mode draws is declared
(:class:`Panel`), the user's answers about it are read once per run
(:class:`AxesSpec`), and the figure is built with **only the panels that were
kept** (:func:`panel_axes`).

**Why the limits are not simply set on the axes here.** Every batch figure goes
through ``core.plot_style.apply_export_style_prefs`` on its way to disk, and
that pass re-reads the application's export-style preferences and re-applies
them — so a limit stamped on the axes at render time is one autoscale away from
being replaced by the app's, silently. The two mechanisms that pass already
honours are used instead: **limits ride in the preferences** it reads
(:meth:`AxesSpec.prefs_overlay`), and **the scale is pinned on the axes** with
the ``_aw_force_xlog`` / ``_aw_force_ylog`` marker that pass checks before it
consults the shared ``x_log`` (:func:`pin_scales`). Nothing here re-implements
what that pass does; it hands it different numbers.

**A blank field means "leave it as it is", not "zero".** Every field is
optional and an unfilled one falls through to whatever the app-wide Export
Style sidebar holds, which is the behaviour a batch figure already had. Writing
an unfilled field as ``0`` would turn "I did not set a bottom" into "the
bottom is zero", which on a log axis is not even a limit.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Panel:
    """One panel a mode's figure draws, as the user is offered it.

    *key* identifies it in this module and in the panel's own render code;
    *label* is what the checkbox says. A mode whose figure is a single axes
    declares exactly one, so the controls read the same on every mode rather
    than appearing only on the stacked ones.
    """

    key: str
    label: str


@dataclass(frozen=True)
class PanelAxis:
    """What the user asked for on one panel.

    ``included`` False means the panel is not drawn at all — the figure comes
    out with one fewer row rather than with an empty frame where it was.
    """

    included: bool = True
    y_min: float | None = None
    y_max: float | None = None
    #: ``None`` means "leave the scale alone" — which is not the same as
    #: ``False``. A panel that pins linear overrides an axis that chose log
    #: from its own data (the Response tab's dose axis does exactly that), and
    #: a user who never touched the control has not asked for that.
    y_log: bool | None = None


@dataclass(frozen=True)
class AxesSpec:
    """One run's answer about its axes, read once before the first job.

    Read once for the reason every other control on these panels is
    (``ScatterAxes``): ``_run_batch_jobs`` pumps ``processEvents()`` between
    jobs, so a field edited mid-export would apply to some figures and not
    others, and the folder would hold one manifest describing two different
    sets of axes.
    """

    x_min: float | None = None
    x_max: float | None = None
    #: ``None`` means "leave the scale alone" — see :attr:`PanelAxis.y_log`.
    x_log: bool | None = None
    #: Ordered, one entry per declared panel, in the order the mode declares
    #: them. A tuple of pairs rather than a dict because the order *is* the
    #: figure's row order and a mapping would leave it to the caller to
    #: remember that.
    panels: tuple[tuple[str, PanelAxis], ...] = ()

    def for_panel(self, key: str) -> PanelAxis:
        """This panel's answer, or the default for a key nobody declared."""
        for panel_key, axis in self.panels:
            if panel_key == key:
                return axis
        return PanelAxis()

    def drawn_keys(self) -> tuple[str, ...]:
        """The panels this run keeps, in figure order."""
        return tuple(key for key, axis in self.panels if axis.included)

    def draws_nothing(self) -> bool:
        """True when every panel was unticked.

        A figure with no panels is not a smaller figure, it is a blank page —
        so the panels refuse the run rather than writing one per group.
        """
        return bool(self.panels) and not self.drawn_keys()

    # ── what the style pass is handed ────────────────────────────────────
    def prefs_overlay(self, base: dict, drawn_keys: tuple[str, ...],
                      *, x_keys: tuple[str, ...] | None = None) -> dict:
        """*base* with this run's limits merged in, as a **copy**.

        Never in place. ``_ensure_export_style_prefs`` hands back the
        application's own live preferences dictionary — the one the Export
        Style sidebar reads and writes — so merging into it would leave a batch
        run's numbers sitting in the sidebar afterwards, applied to every plot
        the user drew next. The nested ``axis_buckets`` is deep-copied for the
        same reason: a shallow copy shares it.

        **A single-panel mode writes flat preferences; a stacked one writes
        buckets.** ``apply_export_style_prefs`` keys a bucket by the axes'
        position in ``fig.axes``, which is right for a stack of named panels
        and wrong for a figure whose one logical plot is drawn as several axes
        — the faceted scatter draws one axes per group, and a bucket on the
        first would leave the rest autoscaled while the user had asked for one
        range. A flat preference reaches every axes on the figure, which is
        what "this plot's y range" means there.

        *x_keys* names the panels the **shared X row governs**, because not
        every panel of a stack is on the same x. The line mode's CDF plots
        intensity where its three siblings plot time, so an X row the user
        filled in hours was clipping the CDF to 0–48 intensity units — three
        correct panels and a fourth that looks empty, with nothing saying why.
        ``None`` means every drawn panel, which is right for a single-panel
        mode and for a stack whose panels really do share one axis.
        """
        prefs = dict(base or {})
        prefs["axis_buckets"] = copy.deepcopy(base.get("axis_buckets") or {}) \
            if base else {}
        if not drawn_keys:
            return prefs
        takes_x = drawn_keys if x_keys is None else tuple(x_keys)
        if len(drawn_keys) == 1:
            key = drawn_keys[0]
            axis = self.for_panel(key)
            # "All", so the flat limits below are not scoped away from the
            # figure's later axes — the whole point of the flat form here.
            prefs["axis_target"] = "All"
            if key in takes_x:
                _put(prefs, "x_lim_min", self.x_min)
                _put(prefs, "x_lim_max", self.x_max)
            _put(prefs, "y_lim_min", axis.y_min)
            _put(prefs, "y_lim_max", axis.y_max)
            return prefs
        buckets = prefs["axis_buckets"]
        for index, key in enumerate(drawn_keys, start=1):
            axis = self.for_panel(key)
            bucket = dict(buckets.get(str(index)) or {})
            if key in takes_x:
                _put(bucket, "x_lim_min", self.x_min)
                _put(bucket, "x_lim_max", self.x_max)
            _put(bucket, "y_lim_min", axis.y_min)
            _put(bucket, "y_lim_max", axis.y_max)
            buckets[str(index)] = bucket
        return prefs


def _put(target: dict, key: str, value: float | None) -> None:
    """Write *value* under *key*, leaving the key alone when it is unset.

    An unset field must not overwrite an app-wide preference with a blank: the
    user typed nothing here, which is a question they did not answer rather
    than an answer of "no limit".
    """
    if value is not None:
        target[key] = value


def pin_scales(pairs, spec: AxesSpec, *,
               x_keys: tuple[str, ...] | None = None) -> None:
    """Pin each drawn panel's log/linear choice against the later style pass.

    ``apply_export_style_prefs`` re-reads the shared ``x_log`` / ``y_log`` on
    every pass and would otherwise stamp them over a panel's own choice — the
    same clash the Response tab's dose axis already resolves this way, and with
    the same marker, so there is one mechanism rather than two.

    A panel whose choice is ``None`` is left unpinned, so an axis that derived
    its own scale from the data keeps it. Pinning ``False`` there would take
    the Response tab's logarithmic dose axis linear for a user who never
    touched the control.

    *pairs* is ``(panel key, axes)``, **not** a mapping: a mode whose one
    logical panel is drawn as several axes — the faceted scatter draws one per
    group — repeats its key, and a dict would keep only the last of them and
    leave the rest unpinned while the user had asked for one scale.
    """
    for key, ax in pairs:
        if ax is None:
            continue
        axis = spec.for_panel(key)
        takes_x = x_keys is None or key in x_keys
        if takes_x and spec.x_log is not None:
            ax._aw_force_xlog = bool(spec.x_log)
        if axis.y_log is not None:
            ax._aw_force_ylog = bool(axis.y_log)


# ── building the figure with only the kept panels ────────────────────────

@dataclass
class PanelAxesResult:
    """The axes a render should draw on, and which of them reached the figure.

    ``axes`` holds an entry for **every** declared panel, drawn or not, because
    the renderers this feeds take a full bundle and paint each member — the
    line renderer's ``LineAxes`` is four axes and it calls methods on all four.
    An excluded panel gets a real axes on a throwaway figure instead of
    ``None``: the renderer draws onto it, nothing on the exported figure moves,
    and no shared renderer had to learn a new shape to make it happen.
    """

    axes: dict = field(default_factory=dict)
    drawn_keys: tuple[str, ...] = ()
    #: The scratch figure the excluded panels were drawn onto, kept only so a
    #: caller can drop it. Never registered with pyplot, so there is nothing to
    #: close — it is garbage once the render returns.
    scratch: object = None


def panel_axes(fig, panels: tuple[Panel, ...], spec: AxesSpec, *,
               share_x: tuple[str, ...] = ()) -> PanelAxesResult:
    """Lay out *fig* with one row per kept panel; scratch the rest.

    *share_x* names the panels that share an x axis when they are drawn
    together — the line and bar stacks share a time axis. The base is the first
    of them that survived the user's selection, so unticking the top panel does
    not leave the rest sharing against an axis that is not on the figure.
    """
    from matplotlib.figure import Figure

    drawn = spec.drawn_keys() or tuple(p.key for p in panels)
    axes: dict = {}
    scratch = None
    base = None
    row = 0
    for panel in panels:
        if panel.key not in drawn:
            if scratch is None:
                scratch = Figure()
            axes[panel.key] = scratch.add_subplot(1, 1, 1)
            continue
        row += 1
        kwargs = {}
        if panel.key in share_x and base is not None:
            kwargs["sharex"] = base
        ax = fig.add_subplot(len(drawn), 1, row, **kwargs)
        if panel.key in share_x and base is None:
            base = ax
        axes[panel.key] = ax
    return PanelAxesResult(axes=axes, drawn_keys=drawn, scratch=scratch)


def figure_height(base_height: float, panels: int, drawn: int) -> float:
    """A figure's height scaled to how many panels it kept.

    Proportional rather than fixed: a four-panel figure's height is four
    panels' worth, and keeping one of them at that height gives a strip of ink
    in a page of white. Floored at a single panel's share so a one-panel figure
    is not squeezed to nothing by a rounding error.
    """
    if panels <= 0:
        return base_height
    per = base_height / float(panels)
    return max(per, per * max(int(drawn), 1))
