"""What a plot tool may read and change of the Nanowells page's choices, and how it shows a nanowell there.

The Scatter Plot's nanowell scatter draws the nanowells the page draws (its Subset, excluded wells and Fixed cohort,
``observation_compute.nanowell_observations(page_scope=True)``) and its fold changes from the page's Baseline. So
that scatter offers the page's own **Subset** and **Baseline** as controls of its own, and a click on a nanowell
shows that nanowell on the page. None of them keeps a second copy: each choice is the page's setting, made through
the page's own call (``set_subset``, ``apply_saved_subset``, ``set_page_baseline``), so the page and the plot cannot
disagree and a choice made on either is the other's too.

Reached through registry services (``nanowell.scope_choices``, ``nanowell.set_subset_by_name``,
``nanowell.set_baseline``, ``nanowell.show_nanowell``), so the Scatter Plot imports nothing of this removable
feature.
"""

from __future__ import annotations

from review.features.nanowell import controller as nwc
from review.features.nanowell import subset_compute as ss
from review.features.nanowell.series_compute import BASELINES, baseline_at, baseline_text

#: The Subset choice that keeps every nanowell.
ALL_NANOWELLS = ""
#: The Subset choice standing for a Subset built on the page and not saved under a name.
UNSAVED = "\x00unsaved"
#: How each baseline rule is named in a menu (the page's own Baseline control's words).
_RULE_TEXT = {"earliest": "Earliest", "first_measurable": "First measurable"}


def scope_choices(ctx, timepoints=()) -> dict | None:
    """The page's Subset and Baseline as a plot offers them, or ``None`` when no dataset is open.

    ``subsets`` is ``[(key, text)]``: every nanowell, each saved subset by name, and — when the page's Subset is one
    built there and not saved — that one, named as its figures name it, so the menu always shows what is drawn.
    ``subset`` is the key of the page's Subset. ``baselines`` is ``[(code, text)]``: the two rules, then each of
    *timepoints* (the plot's, in time order); ``baseline`` is the page's."""
    st = ctx.state.feature_slice("nanowell")
    if st is None:
        return None
    subsets = [(ALL_NANOWELLS, "All nanowells")] + [(name, name) for name in st.saved_subsets]
    saved = ss.saved_name_of(st.saved_subsets, st.subset)
    if not st.subset.is_active():
        current = ALL_NANOWELLS
    elif saved:
        current = saved
    else:
        current = UNSAVED
        subsets.append((UNSAVED, f"{st.subset.label()} (not saved)"))
    baselines = [(rule, _RULE_TEXT.get(rule, rule)) for rule in BASELINES]
    baselines += [(baseline_at(tp), str(tp)) for tp in timepoints]
    if st.baseline not in {code for code, _text in baselines}:
        baselines.append((st.baseline, f"{baseline_text(st.baseline)} (not in these wells)"))
    return {"subsets": subsets, "subset": current, "baselines": baselines, "baseline": st.baseline}


def set_subset_by_name(ctx, key: str) -> bool:
    """Make the page's Subset every nanowell (:data:`ALL_NANOWELLS`) or the subset saved as *key*; whether it is.
    :data:`UNSAVED` changes nothing: it is the page's Subset already."""
    if key == UNSAVED:
        return True
    if key == ALL_NANOWELLS:
        return nwc.set_subset(ctx, ss.Subset())
    return nwc.apply_saved_subset(ctx, key)


def set_baseline(ctx, baseline: str) -> None:
    """Measure every fold change from *baseline*, on the page and on every plot of its nanowells."""
    nwc.set_page_baseline(ctx, baseline)


def show_nanowell(ctx, well: str, q: int, r: int, timepoint: str = "") -> bool:
    """Show the nanowell ``(q, r)`` of *well* on the Nanowells page: selected, so its detail and crops open, with the
    page's timepoint at *timepoint*; whether the page was shown.

    The well joins the plate selection (never replacing it) when it is not in it, because the page draws the
    selected wells only — the same rule as ``open_for_anchor``."""
    st = ctx.state.feature_slice("nanowell")
    notebook = getattr(ctx.window, "_notebook", None)
    if st is None or notebook is None or not well:
        return False
    selected = set(ctx.state.selection.selected_wells)
    if well not in selected:
        from review.core.selection_controller import set_selected_wells

        set_selected_wells(ctx, selected | {well})
    st.selected_sites = [[str(well), int(q), int(r)]]
    if timepoint:
        st.timepoint = str(timepoint)
    if not notebook.setCurrentByName("Nanowells"):
        return False
    nwc.selection_changed(ctx)
    return True
