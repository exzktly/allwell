"""The Saved Cells page's actions: view a population's cells, and change what it does.

Every action is one the app already had, reached from here rather than
re-implemented: a set is highlighted, excluded, renamed and deleted through
``review/core/cell_sets.py`` (the same calls the Cell Gating panel makes), and a
population's cells are shown by the Cell Browser through its
``cell_browser.browse`` service, so a build without the Cell Browser says so
instead of failing. Gates are drawn and edited on the Scatter and applied on
Cell Gating; the page shows them and takes the user to Cell Gating to change
which layer applies them, rather than keeping a second copy of those controls.
"""

from __future__ import annotations

from review.core import cell_sets as cs

#: The page's handle in the widget registry; ``cell_sets`` refreshes it on every change.
PAGE_KEY = "_saved_cells_page"


def refresh(ctx) -> None:
    """Re-read both stores into the page, when it has been built."""
    page = ctx.widgets.get_or_none(PAGE_KEY)
    if page is not None:
        page.refresh()


def _browse(ctx, source: str, *, gate: str | None = None) -> bool:
    """Ask the Cell Browser to show *source*, and say why when it does not.

    ``browse`` answers ``None`` when there is no Cell Browser to show anything
    in (and so does the registry, in a build without it), and ``False`` when
    the population it was asked for is no longer there — a set deleted or
    renamed, a gate removed, since this page last refreshed. The two are told
    apart: blaming the build for a stale row sent the user looking for a tab
    they have.
    """
    shown = ctx.features.query("cell_browser.browse", ctx, source, gate=gate, default=None)
    if shown is None:
        ctx.notify.set_status("This build has no Cell Browser to show the cells in.")
    elif not shown:
        name = gate if gate is not None else source.removeprefix("set:")
        ctx.notify.set_status(f"“{name}” no longer exists — the list has been refreshed.")
        refresh(ctx)
    return bool(shown)


def view_set(ctx, name: str) -> bool:
    """Open the Cell Browser on saved set *name*."""
    return _browse(ctx, f"set:{name}")


def view_gate(ctx, name: str) -> bool:
    """Open the Cell Browser on the cells inside gate *name* alone."""
    return _browse(ctx, "gates", gate=name)


def toggle_highlight(ctx, name: str) -> None:
    """Highlight *name* on the per-cell Scatter, or stop when it already is."""
    current = getattr(ctx.state.cell_sets, "highlight", None)
    cs.set_highlighted(ctx, None if current == name else name)


def toggle_exclude(ctx, name: str) -> None:
    """Make *name* the set excluded from analysis, or exclude none when it already is."""
    current = getattr(ctx.state.cell_sets, "exclude", None)
    cs.set_excluded(ctx, None if current == name else name)


def rename(ctx, old: str, new: str) -> bool:
    return cs.rename_set(ctx, old, new)


def delete(ctx, name: str) -> None:
    cs.delete_set(ctx, name)


def open_cell_gating(ctx) -> None:
    """Take the user to Home → Cell Gating, where gates are applied to the analysis."""
    if not ctx.features.query("cell_gating.show", ctx, default=False):
        ctx.notify.set_status("This build has no Cell Gating page.")


def use_subset(ctx, name: str) -> bool:
    """Make the subset saved as *name* the Nanowells page's Subset and show that page."""
    shown = ctx.features.query("nanowell.use_saved_subset", ctx, name, default=None)
    if shown is None:
        ctx.notify.set_status("This build has no Nanowells page.")
    return bool(shown)
