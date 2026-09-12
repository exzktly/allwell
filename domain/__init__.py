"""Qt-free models and pure logic that more than one pane depends on.

The application's shared *subject matter*, as distinct from its plumbing. What
an image filename means, what makes two timepoints the same measurement, how a
text column is rendered — questions Review and Datasets both have to answer the
same way, or they disagree about the same plate.

**The rule that puts a module here** is scope, measured rather than judged: a
module belongs to the narrowest package that contains every one of its users.
Used by one pane, it is that pane's and lives under `review/`, `analyze/` or
`datasets/`. Used by two, it is the application's and lives here.
`tests/test_a_module_lives_at_its_widest_use.py` enforces exactly that, and
every module here arrived by failing it.

**The rule that distinguishes this package from its neighbours**, since Qt-free
alone does not place a module:

* `core/` is cross-cutting *infrastructure* — the settings wrapper, the logged
  swallow, the debug flags. It may import Qt and it owns no subject.
* `util/` is Qt-free *general* helpers, and every file in it is rsynced to a
  remote worker for a headless pipeline run. A module here is about All-Well's
  domain, so it would be dead weight on the wire; `util/` is about strings,
  paths, retries and disks.
* `pipeline/` is the analysis computation itself, which runs headless and must
  not import anything the GUI needs.
* A pane's own `domain/` is the same idea at pane scope. The name means the
  same thing at both levels and the path says which scope — `review/domain/`
  is Review's subject matter, this is the application's.

Nothing here may import a pane, `core/`, `ui/` or `widgets/`. The arrow runs
one way: panes depend on shared domain, never the reverse, and a module here
that needs a pane has been misfiled by that fact alone.
"""
