"""Cross-cutting application infrastructure that every pane shares and none owns.

The application is three panes — Analyze, Review and Datasets — and each has a
package of its own. The modules here belong to none of them: the logged-swallow
helpers every module reaches for, the settings wrapper, the feature-profile
reader, the debug flags, the bridge that gets a worker thread's log lines onto
the GUI thread. Each was measured as reachable from more than one pane before it
was put here.

They lived in the Review pane's ``core/`` for as long as the application *was*
the Review pane. Two other panes then grew and reached into it — thirty-four
cross-pane imports, every one of them landing in that package and never in a
feature — which is what a shared module inside a pane looks like from the
outside. Nothing failed, because nothing can: an import resolves the same
wherever the file sits, and the whole cost falls on the next person trying to
work out who owns what.

**How this differs from its three neighbours**, since "shared" alone does not
place a module:

* ``ui/`` is app-level **UI** every pane shares and none owns — the design
  tokens, the page-to-manual-heading map. A module here is not UI.
* ``widgets/`` is reusable Qt widgets with **no** All-Well knowledge. A module
  here knows the application.
* ``util/`` is Qt-free general helpers, and every file in it is rsynced to a
  remote worker for a headless pipeline run. A Qt-importing helper cannot go
  there however general it looks — which is why ``core.safe`` and
  ``util.aw_safe`` are near-identical twins rather than one module.
* ``startup/`` is what runs **before the application exists**.

Nothing here may import a pane. The dependency runs one way — a pane reaches
into this package, never the reverse — and a module that needs to reach back
down into ``review/``, ``analyze/`` or ``datasets/`` has been misfiled by that
fact alone.
"""
