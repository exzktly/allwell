"""The Heat Map colours by the Fixed vmin/vmax the user typed.

Three ways a Fixed colour scale used to be thrown away, all of them silent —
the grid redrew on its automatic range and looked exactly like a grid that had
been given no range at all (CLAUDE.md §7, degraded output must never be
indistinguishable from good output):

* **A limit of zero.** ``_resolve_color_scale`` read the two limits as
  ``float(vmin or nan)``, and ``0.0 or nan`` is ``nan``. Zero is the most
  ordinary lower bound anyone types into a colour scale, and typing it
  discarded the vmax beside it as well.
* **One limit typed, the other left blank.** The pair was accepted only if
  *both* ends were finite, so capping a saturating plate by filling in vmax
  alone did nothing whatever.
* **A pair that cannot describe a range** (vmax at or below vmin). Refusing it
  is right; refusing it without a word is what made it look like the Fixed
  mode itself was broken.

The assertions are on ``ax.images[0].get_clim()`` — the limits matplotlib is
actually drawing with — rather than on ``_resolve_color_scale``'s return value,
because a renderer that computed the right pair and then handed ``imshow``
something else would pass the second and fail the user (§7.4 #1). The unit
tests below it exist to name *which* end went wrong when the boundary test
goes red.

The last section is about a different half of the same report: a number typed
into the Display popover never reached the state at all, because closing a
``QMenu`` popover emits no ``editingFinished``.
"""

from __future__ import annotations

import types

import pytest

from review.features.heatmap import controller as HM
from review.features.heatmap.models import HeatmapLayout

_TP = 0.0
_VAL = "gfp_mean_intensity"

# The four wells of the fixture and the value each one holds. They span
# 5 → 500, so the automatic range is (5.0, 500.0) and every fixed range asserted
# below is a *different* pair — a fixture where the two coincided could not tell
# an honoured Fixed scale from a discarded one (§7.8 rule 2).
_WELL_VALUES = {"A01": 5.0, "A02": 120.0, "B01": 300.0, "B02": 500.0}
_AUTO_RANGE = (5.0, 500.0)


def _block_layout() -> HeatmapLayout:
    """A 2×2 grid holding the four wells of ``_WELL_VALUES``."""
    layout = HeatmapLayout(name="Block", rows=2, cols=2)
    layout.assign(0, 0, ["A01"])
    layout.assign(0, 1, ["A02"])
    layout.assign(1, 0, ["B01"])
    layout.assign(1, 1, ["B02"])
    return layout


@pytest.fixture
def drawn(monkeypatch):
    """Draw the heat map and hand back ``(ax, statuses)``.

    Everything outside the renderer's own module is stubbed to a constant, as
    in ``test_heatmap_reads_the_sample_map``; the cell values are the one thing
    that is not, because the automatic colour range is derived from them and
    this file is about telling that range apart from the typed one.

    ``statuses`` collects every ``ctx.notify.set_status`` line, so a test can
    assert that a refused range was *said* and not only refused.
    """
    from matplotlib.figure import Figure

    def _draw(*, scale_mode="Auto", vmin=None, vmax=None, log_scale=False,
              before_draw=None):
        fig = Figure()
        ax = fig.add_subplot(1, 1, 1)
        canvas = types.SimpleNamespace(draw_idle=lambda: None)
        statuses: list[str] = []
        layout = _block_layout()
        widgets = {
            "_heatmap_fig": fig, "_heatmap_ax": ax, "_heatmap_canvas": canvas,
            "_heatmap_cax": None,
            "_heatmap_metric_cb": types.SimpleNamespace(
                currentText=lambda: HM.METRIC_MEAN),
            "_heatmap_tp_cb": types.SimpleNamespace(currentIndex=lambda: 0),
            # The two Fixed-scale fields, as the tab's own handler reads them:
            # by ``text()``, off ``ctx.widgets``.
            "_heatmap_vmin_edit": _Edit(),
            "_heatmap_vmax_edit": _Edit(),
            "_heatmap_scale_cb": _Combo(),
        }
        ctx = types.SimpleNamespace(
            window=None,
            state=types.SimpleNamespace(
                dataset=types.SimpleNamespace(
                    well_paths=dict.fromkeys(_WELL_VALUES, "p")),
                channel=types.SimpleNamespace(
                    active_val_col=_VAL, active_channel="GFP",
                    active_metric="mean_intensity", ratio_index={}),
                plot=types.SimpleNamespace(use_fov_replicates=False, use_sem=True),
                selection=types.SimpleNamespace(selected_wells=set(), variables=None),
                cell_gating=types.SimpleNamespace(
                    use_scatter_thresh=False, use_scatter_gates=False,
                    scatter_gates=[], thresh_choices=None, selected_gate=None),
                heatmap=types.SimpleNamespace(
                    tp_values=[_TP], scale_mode=scale_mode, vmin=vmin, vmax=vmax,
                    repset_avg=False, log_scale=log_scale, show_error=False,
                    label_from_sample_map=False),
            ),
            runtime=types.SimpleNamespace(heatmap=types.SimpleNamespace(
                layouts=[layout], global_range_cache=None, label_drag=None,
                colorbar=None, global_vmin=None, global_vmax=None)),
            widgets=types.SimpleNamespace(get_or_none=widgets.get),
            notify=types.SimpleNamespace(set_status=statuses.append),
        )
        ctx.state.heatmap.active_layout_name = layout.name

        def _value(_ctx, wells, *_a, **_k):
            vals = [_WELL_VALUES[w] for w in (wells or []) if w in _WELL_VALUES]
            return sum(vals) / len(vals) if vals else float("nan")

        monkeypatch.setattr(HM, "current_focus_tp", lambda _c: None)
        monkeypatch.setattr(HM, "on_cutoff_for_column", lambda *_a, **_k: 0.0)
        monkeypatch.setattr(HM, "get_all_fluor_gates", lambda _c: {})
        monkeypatch.setattr(HM, "active_channel_label", lambda _c: "GFP")
        monkeypatch.setattr(HM, "apply_export_style_to_current",
                            lambda *_a, **_k: None)
        monkeypatch.setattr(HM, "_cell_value", _value)
        monkeypatch.setattr(
            HM, "_cell_stat",
            lambda ctx_, wells, *_a, **_k: (_value(ctx_, wells), float("nan")))
        if before_draw is not None:
            before_draw(ctx)
        HM.redraw_heatmap(ctx)
        return ax, statuses

    return _draw


class _Edit:
    """The part of ``QLineEdit`` the Fixed-scale handler touches."""

    def __init__(self, text: str = "") -> None:
        self._text = text

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:
        self._text = str(text)

    def blockSignals(self, _blocked: bool) -> bool:
        return False


class _Combo:
    """The part of ``QComboBox`` the Fixed-scale handler touches."""

    def __init__(self, text: str = "Auto") -> None:
        self._text = text

    def currentText(self) -> str:
        return self._text

    def setCurrentText(self, text: str) -> None:
        self._text = str(text)

    def blockSignals(self, _blocked: bool) -> bool:
        return False


def _clim(ax) -> tuple[float, float]:
    """The colour limits the drawn image carries."""
    assert ax.images, "the renderer drew no image"
    return tuple(float(v) for v in ax.images[0].get_clim())


# ── what the grid is actually drawn with ────────────────────────────────────

def test_the_automatic_range_is_the_span_of_the_values(drawn):
    """The baseline every Fixed assertion below has to differ from."""
    ax, _ = drawn(scale_mode="Auto")
    assert _clim(ax) == _AUTO_RANGE


def test_a_fixed_range_starting_at_zero_is_the_one_drawn(drawn):
    """Zero is a limit, not a blank field.

    ``float(vmin or nan)`` read this ``0.0`` as "nothing typed", failed the
    both-ends-finite test, and drew ``_AUTO_RANGE`` — the whole Fixed pair
    thrown away by the presence of its most ordinary value.
    """
    ax, _ = drawn(scale_mode="Fixed", vmin=0.0, vmax=900.0)
    assert _clim(ax) == (0.0, 900.0)


def test_a_fixed_vmax_of_zero_is_the_one_drawn(drawn):
    """The same reading applied to the upper limit, on data that is negative."""
    ax, _ = drawn(scale_mode="Fixed", vmin=-10.0, vmax=0.0)
    assert _clim(ax) == (-10.0, 0.0)


def test_a_fixed_vmax_alone_caps_the_scale(drawn):
    """vmax typed, vmin blank: the cap is honoured, the floor stays automatic."""
    ax, _ = drawn(scale_mode="Fixed", vmin=float("nan"), vmax=100.0)
    assert _clim(ax) == (_AUTO_RANGE[0], 100.0)


def test_a_fixed_vmin_alone_lifts_the_floor(drawn):
    """vmin typed, vmax blank: the mirror of the case above."""
    ax, _ = drawn(scale_mode="Fixed", vmin=50.0, vmax=float("nan"))
    assert _clim(ax) == (50.0, _AUTO_RANGE[1])


def test_a_fixed_range_with_neither_limit_typed_is_the_automatic_one(drawn):
    ax, _ = drawn(scale_mode="Fixed", vmin=float("nan"), vmax=float("nan"))
    assert _clim(ax) == _AUTO_RANGE


def test_auto_mode_draws_the_automatic_range_over_stale_fixed_limits(drawn):
    """Switching back to Auto must not keep colouring by the old edits.

    The limits persist in the state while the fields keep their text, so a fix
    that read them regardless of the mode would pass every test above and
    silently break the Auto half of the control.
    """
    ax, _ = drawn(scale_mode="Auto", vmin=0.0, vmax=900.0)
    assert _clim(ax) == _AUTO_RANGE


# ── a pair that cannot describe a range ─────────────────────────────────────

def test_an_inverted_fixed_range_is_refused_out_loud(drawn):
    ax, statuses = drawn(scale_mode="Fixed", vmin=900.0, vmax=10.0)
    assert _clim(ax) == _AUTO_RANGE
    assert any("vmax above vmin" in s for s in statuses), statuses


def test_a_flat_fixed_range_is_refused_out_loud(drawn):
    """vmin == vmax paints every cell one colour — a grid with no information."""
    ax, statuses = drawn(scale_mode="Fixed", vmin=42.0, vmax=42.0)
    assert _clim(ax) == _AUTO_RANGE
    assert any("vmax above vmin" in s for s in statuses), statuses


def test_a_fixed_vmin_above_the_data_is_refused_out_loud(drawn):
    """A typed floor over an automatic ceiling can invert just as easily.

    vmin 900 with a blank vmax gives (900, 500) — matplotlib's own
    ``Normalize`` raises on that, so the mixed pair has to be checked after the
    two ends are combined and not only when both were typed.
    """
    ax, statuses = drawn(scale_mode="Fixed", vmin=900.0, vmax=float("nan"))
    assert _clim(ax) == _AUTO_RANGE
    assert any("vmax above vmin" in s for s in statuses), statuses


# ── log scale ───────────────────────────────────────────────────────────────

def test_a_positive_fixed_range_reaches_the_log_norm(drawn):
    ax, _ = drawn(scale_mode="Fixed", vmin=1.0, vmax=1000.0, log_scale=True)
    assert _clim(ax) == (1.0, 1000.0)


def test_a_fixed_vmin_alone_reaches_the_log_norm(drawn):
    """Half a Fixed range is usable under a log scale too."""
    ax, _ = drawn(scale_mode="Fixed", vmin=10.0, vmax=float("nan"), log_scale=True)
    assert _clim(ax) == (10.0, _AUTO_RANGE[1])


def test_a_fixed_range_a_log_scale_cannot_use_says_so(drawn):
    """A log scale has no floor at zero, so this Fixed range cannot be drawn.

    Falling back to the data's positive range is the right picture; doing it
    without a word is what leaves the user staring at numbers the grid ignored.
    """
    _ax, statuses = drawn(scale_mode="Fixed", vmin=0.0, vmax=900.0, log_scale=True)
    assert any("above zero" in s for s in statuses), statuses


# ── the limits the resolver picks, named end by end ─────────────────────────

def _scale_ctx(mode, vmin, vmax, *, statuses=None):
    return types.SimpleNamespace(
        state=types.SimpleNamespace(
            heatmap=types.SimpleNamespace(scale_mode=mode, vmin=vmin, vmax=vmax)),
        runtime=types.SimpleNamespace(
            heatmap=types.SimpleNamespace(global_vmin=5.0, global_vmax=500.0)),
        notify=types.SimpleNamespace(
            set_status=(statuses.append if statuses is not None else (lambda _m: None))),
    )


@pytest.mark.parametrize("vmin,vmax,expected", [
    (0.0, 900.0, (0.0, 900.0)),
    (10.0, 900.0, (10.0, 900.0)),
    (float("nan"), 100.0, (5.0, 100.0)),
    (10.0, float("nan"), (10.0, 500.0)),
    (None, None, (5.0, 500.0)),
])
def test_resolve_color_scale_reads_each_end_on_its_own(vmin, vmax, expected):
    import numpy as np
    arr = np.array([[1.0, 2.0], [3.0, 4.0]])
    assert HM._resolve_color_scale(arr, _scale_ctx("Fixed", vmin, vmax)) == expected


@pytest.mark.parametrize("value,expected", [
    (0.0, 0.0),
    (-3.5, -3.5),
    (900.0, 900.0),
    (float("nan"), None),
    (float("inf"), None),
    (None, None),
    ("", None),
])
def test_a_blank_limit_is_none_and_every_finite_number_is_a_limit(value, expected):
    assert HM._fixed_limit(value) == expected


# ── from the typed characters to the drawn limits ───────────────────────────

def _type_limits(ctx, vmin_text: str, vmax_text: str) -> None:
    """Put *vmin_text* / *vmax_text* in the edits and run the tab's handler."""
    from review.features.heatmap import tab_view

    ctx.widgets.get_or_none("_heatmap_vmin_edit").setText(vmin_text)
    ctx.widgets.get_or_none("_heatmap_vmax_edit").setText(vmax_text)
    ctx.persistence = types.SimpleNamespace(schedule_save=lambda _s: None)
    # ``_on_vminmax_changed`` redraws at the end; the fixture draws again right
    # after, and the assertion is on the second figure either way.
    tab_view._on_vminmax_changed(ctx)


def test_a_zero_typed_into_the_vmin_field_is_the_limit_the_grid_draws(drawn):
    """The whole chain, from the characters in the field to ``imshow``.

    The unit tests above pin ``_resolve_color_scale``; this one is the reason
    they matter. It fails for a handler that parses the text wrongly, for a
    handler that leaves the mode on Auto, and for a resolver that drops the
    limits — the three places the typed range was lost between the field and
    the figure.
    """
    ax, _ = drawn(before_draw=lambda ctx: _type_limits(ctx, "0", "900"))
    assert _clim(ax) == (0.0, 900.0)


def test_typing_a_limit_switches_the_mode_to_fixed(drawn):
    """A number in the field is an instruction, whatever the Scale combo says.

    The fixture starts in Auto, and nothing here touches the combo.
    """
    seen: list[str] = []

    def _act(ctx):
        _type_limits(ctx, "0", "900")
        seen.append(ctx.state.heatmap.scale_mode)
        seen.append(ctx.widgets.get_or_none("_heatmap_scale_cb").currentText())

    drawn(before_draw=_act)
    assert seen == ["Fixed", "Fixed"]


def test_clearing_both_fields_hands_the_scale_back_to_the_data(drawn):
    ax, _ = drawn(scale_mode="Fixed", vmin=0.0, vmax=900.0,
                  before_draw=lambda ctx: _type_limits(ctx, "", ""))
    assert _clim(ax) == _AUTO_RANGE
