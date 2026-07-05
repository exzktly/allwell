"""Shared plot-toggle behaviour (SEM/SD spread, per-FOV replicates, FOV button
state).

These three helpers drive app-global plot controls: the SEM/SD error-band
toggle, the per-FOV replicate toggle, and the FOV button-bank refresh. They were
previously scattered across the lineplot / barplot / preview feature modules,
which meant a non-plot feature that wanted to drive the same toggles (the
batch-export inline Error-Band row) had to reach through the ``WellViewerApp``
god-object — a direct ``from well_viewer.features...`` call would have been a
banned cross-feature import.

Relocated here (A8 enabler E3) so any feature can call them through ``ctx`` with
a plain ``core.`` import. The bodies are ctx-native: they read/write typed plot
state (``ctx.state.plot``) and the shared button banks (``ctx.runtime.sem`` /
``ctx.runtime.fov``). The only feature-specific wiring — the bar renderer redraw
and the bar-layout button refresh — is reached through ``core.lazy`` proxies (a
call, not an ``import`` statement), so this core module imports no feature at
load time and the bar feature stays independently deletable.
"""

from __future__ import annotations

from well_viewer.core.plot_orchestrator import redraw_app
from well_viewer.core.tab_controller import is_tab_visible
from well_viewer.core.selection_controller import invalidate_stats_cache
from well_viewer.core.safe import swallowed
from well_viewer.core.lazy import lazy as _lazy

# Feature-specific wiring, bound as lazy proxies so importing this module never
# imports the bar feature (a ``lazy(...)`` binding is a call, not an import
# statement — see core/lazy.py).
_redraw_bars = _lazy("well_viewer.features.barplot.renderer", "redraw_bars")
_refresh_bar_layout_btn_state = _lazy(
    "well_viewer.features.barplot.controller", "refresh_bar_layout_btn_state"
)


def refresh_fov_btn_state(ctx) -> None:
    """Sync every per-FOV toggle button's text/variant state."""
    is_on = bool(ctx.state.plot.use_fov_replicates)
    text = "FOV ✓" if is_on else "FOV"
    variant = "toggle_active" if is_on else "toggle"
    tooltip = (
        "Compute error bands across per-FOV means.\n"
        "With replicate sets active, FOVs are pooled across all wells in each set;\n"
        "otherwise FOVs are pooled within each well. Pairs with the SEM/SD toggle."
    )
    for btn in list(ctx.runtime.fov.btns or []):
        btn.setEnabled(True)
        btn.setText(text)
        btn.setToolTip(tooltip)
        btn.setProperty("variant", variant)
        btn.style().unpolish(btn)
        btn.style().polish(btn)
    for obs in list(ctx.runtime.fov.observers or []):
        try:
            obs(is_on)
        except Exception:
            swallowed()  # was: pass (silent except)


def toggle_sem(ctx) -> None:
    invalidate_stats_cache(ctx)
    is_sem = ctx.state.plot.toggle_sem()
    text = "SEM" if is_sem else "SD"
    variant = "sem" if is_sem else "sem_warn"
    for btn in list(ctx.runtime.sem.btns or []):
        btn.setText(text)
        btn.setProperty("variant", variant)
        btn.style().unpolish(btn)
        btn.style().polish(btn)
    for obs in list(ctx.runtime.sem.observers or []):
        try:
            obs(is_sem)
        except Exception:
            swallowed()  # was: pass (silent except)
    redraw_app(ctx)
    # Redraw the bar pane too whenever it is on screen — in Split View it can be
    # visible alongside another pane without being the focused tab.
    if is_tab_visible(ctx, "Bar Plots"):
        _redraw_bars(ctx)


def toggle_fov_replicates(ctx) -> None:
    ctx.state.plot.toggle_fov_replicates()
    invalidate_stats_cache(ctx)
    refresh_fov_btn_state(ctx)
    _refresh_bar_layout_btn_state(ctx)
    redraw_app(ctx)
    # Split View: redraw the bar pane whenever it is on screen, even if unfocused.
    if is_tab_visible(ctx, "Bar Plots"):
        _redraw_bars(ctx)
