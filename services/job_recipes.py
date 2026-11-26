"""Job Queue recipes: a chain of queued jobs saved as a template.

A recipe is what is left of a chain in the queue — Watch folder → Convert →
Analyze, say — once everything that belonged to one particular acquisition has
been taken out of it. Applying it to a new folder or file queues the whole
chain again, linked the same way, with that input in place of the old one.

What is taken out, and why, decides whether a recipe is safe to reuse:

* **Paths are templated on the chain's input.** The input of the chain's first
  job (the watched folder, the converted file, the analysed folder) is the
  recipe's one parameter. Every path in every step that is that input, lies
  inside it, or sits beside it *and is named after it* is rewritten in terms
  of it — ``{source}``, ``{source_parent}`` and ``{source_name}`` — so a
  destination ``/scope/run7_dataset`` saved from ``/scope/run7`` comes back as
  ``/scope/run8_dataset`` for ``/scope/run8``. Any other path — a shared
  reference plate beside the input, say — is kept as it is, and a step whose
  new folder would not follow the input is refused when the recipe is saved.
* **Links are kept by position.** Each step's inputs that came from an earlier
  step in the chain are recorded as "step N's output" and become real links
  when the recipe is applied, so they are resolved when each job runs, from
  what its source actually wrote.
* **Settings read off one acquisition are dropped**, each for its own reason
  (:data:`DROPPED`): a converter's experiment name (re-derived from the file
  name), the timepoints it picked by their tokens (each file names its own),
  the wells a page scan planned, and an Analyze run's per-dataset repair list.
  Analyze steps are set to read their filename schema and channels from their
  input when they run (``services.run_detect``), since that input does not
  exist until the steps before it have run. What was dropped is recorded on
  the recipe as a note, so applying it can say so.

Qt-free, beside the queue it feeds.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from services.job_kinds import (
    CHAIN_KIND,
    KIND_LABEL,
    describe,
    linkable_targets,
    planned_output,
)
from services.job_queue import ChainLink, JobSpec, set_dotted
from services.remote_config import default_state_dir
from util.json_write import write_json_atomic

_LOG = logging.getLogger("allwell.services.job_recipes")

RECIPES_FILENAME = "job_recipes.json"

SOURCE = "{source}"
SOURCE_PARENT = "{source_parent}"
SOURCE_NAME = "{source_name}"

#: Where each kind reads the input that a recipe built from a chain starting
#: with it is applied to. Analyze reads ``opts.raw``; the rest name it.
ROOT_INPUT = {
    "analyze": "opts.raw",
    "watch_folder": "folder",
    "ome_import": "source",
    "czi_import": "source",
    "nd2_import": "source",
    "import_loose": "source",
    "validate": "dataset",
    "subset": "source",
    "concatenate": "dataset1",
    "merge_plates": "dataset1",
}

#: The kinds whose input is one file rather than a folder, so applying a
#: recipe that starts with one asks for a file. A CZI conversion can read
#: either; :func:`Recipe.wants_file` asks what the saved one read.
FILE_INPUT_KINDS = ("nd2_import", "ome_import")

_CONVERTERS = ("ome_import", "czi_import", "nd2_import")

#: What a recipe leaves out of each kind's settings, and why. A key here is
#: reset to its default when a chain is saved, and named in the recipe's notes.
DROPPED = {
    "converter": {
        "experiment": "the experiment name, which is re-derived from each file's name",
        "timepoint_subset": "the timepoints picked by name, which each file names for itself",
    },
    "analyze": {
        "force_wells": "the wells to redo, which describe one dataset's repair",
        "force": "Force re-run, which only means something for a dataset already analysed",
    },
}


class RecipeError(ValueError):
    """A chain cannot be saved as a recipe, or a recipe cannot be applied.
    The message is user-facing."""


@dataclass
class RecipeLink:
    """One input of a step that comes from an earlier step's output."""

    target: str
    step: int

    def to_json(self) -> dict:
        return {"target": self.target, "step": self.step}

    @classmethod
    def from_json(cls, data: dict) -> RecipeLink:
        return cls(target=str(data["target"]), step=int(data["step"]))


@dataclass
class RecipeStep:
    kind: str
    spec: dict = field(default_factory=dict)
    links: list[RecipeLink] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"kind": self.kind, "spec": self.spec,
                "links": [link.to_json() for link in self.links]}

    @classmethod
    def from_json(cls, data: dict) -> RecipeStep:
        return cls(kind=str(data["kind"]), spec=dict(data.get("spec") or {}),
                   links=[RecipeLink.from_json(d) for d in (data.get("links") or [])])


@dataclass
class Recipe:
    name: str
    steps: list[RecipeStep] = field(default_factory=list)
    #: What saving left out, in sentences, so applying it can say so.
    notes: list[str] = field(default_factory=list)
    #: The input the recipe was saved from, kept to describe it.
    saved_from: str = ""

    @property
    def root_kind(self) -> str:
        return self.steps[0].kind if self.steps else ""

    def wants_file(self) -> bool:
        """Whether applying this recipe asks for a file rather than a folder."""
        if self.root_kind in FILE_INPUT_KINDS:
            return True
        if self.root_kind == "czi_import":
            return Path(self.saved_from).suffix.lower() == ".czi"
        return False

    def summary(self) -> str:
        """The chain in one line: ``Watch folder → Convert CZI → Analyze``."""
        return " → ".join(KIND_LABEL.get(step.kind, step.kind) for step in self.steps)

    def to_json(self) -> dict:
        return {"name": self.name, "steps": [s.to_json() for s in self.steps],
                "notes": list(self.notes), "saved_from": self.saved_from}

    @classmethod
    def from_json(cls, data: dict) -> Recipe:
        return cls(name=str(data["name"]),
                   steps=[RecipeStep.from_json(d) for d in data.get("steps") or []],
                   notes=[str(n) for n in data.get("notes") or []],
                   saved_from=str(data.get("saved_from") or ""))


# ── templating ───────────────────────────────────────────────────────────────

def _get_dotted(spec: dict, dotted: str) -> object | None:
    cur = spec
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


#: The extensions :func:`source_name` takes off a file's name, longest first.
#: A fixed list rather than "everything after the first dot": a folder named
#: ``run_2026.11.26`` is called exactly that.
_NAME_SUFFIXES = (".ome.tiff", ".ome.tif", ".tiff", ".tif", ".czi", ".nd2", ".zip")


def source_name(path: str) -> str:
    """The input's name without its file extension — ``run7`` for
    ``run7.ome.tif`` — which is what a destination is usually named after."""
    name = Path(path).name
    lowered = name.lower()
    for suffix in _NAME_SUFFIXES:
        if lowered.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


def _replace_name(text: str, name: str) -> str:
    """*text* with every whole-token occurrence of *name* replaced — ``run7``
    in ``run7_dataset`` but not in ``run77``."""
    pattern = rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])"
    return re.sub(pattern, lambda _m: SOURCE_NAME, text)


def _relative_inside(path: str, folder: str) -> str | None:
    """*path* relative to *folder* when it lies strictly inside it, else ``None``.

    Written against a filesystem or drive root too: a folder of ``/`` or
    ``E:\\`` already ends in a separator, and adding another made every
    absolute path look like it was outside — or, stripped first, inside.
    """
    if not folder:
        return None
    prefix = folder if folder.endswith(("/", "\\")) else folder + os.sep
    if path.startswith(prefix) and len(path) > len(prefix):
        return path[len(prefix):]
    return None


def _template_value(value: str, source: str, parent: str, name: str) -> str:
    """*value* with the input, a path inside it, or a path beside it that is
    named after it replaced by placeholders; *value* itself otherwise.

    A path beside the input is templated **only when it carries the input's
    name** (``run7_dataset`` beside ``run7``). One that does not — a shared
    reference plate, a calibration file — is about the protocol, not this
    acquisition, and is kept verbatim. Rewriting everything under the input's
    folder sent a recipe saved from ``/scope/run7`` and applied to
    ``/other/run8`` looking for ``/other/reference_plate``, and with an input
    at a drive root it rewrote every absolute path in the chain.

    The placeholders are written with ``/`` and joined back with the platform's
    own rules in :func:`_fill_value`.
    """
    if not value:
        return value
    norm = os.path.normpath(value)
    if norm == source:
        return SOURCE
    inside = _relative_inside(norm, source)
    if inside is not None:
        return f"{SOURCE}/{inside.replace(os.sep, '/')}"
    beside = _relative_inside(norm, parent)
    if beside is not None and name:
        renamed = _replace_name(beside, name)
        if renamed != beside:
            return f"{SOURCE_PARENT}/{renamed.replace(os.sep, '/')}"
    return value


def _walk_strings(obj, fn: Callable[[str], str]):
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [_walk_strings(v, fn) for v in obj]
    if isinstance(obj, dict):
        return {k: _walk_strings(v, fn) for k, v in obj.items()}
    return obj


def _drop_run_specific(kind: str, spec: dict) -> list[str]:
    """Reset the settings :data:`DROPPED` names; returns what was dropped."""
    dropped: list[str] = []
    if kind in _CONVERTERS:
        spec.pop("planned_wells", None)
        params = spec.get("params")
        if isinstance(params, dict):
            for key, why in DROPPED["converter"].items():
                if params.get(key):
                    dropped.append(why)
                if key in params:
                    params[key] = [] if isinstance(params[key], list) else ""
    if kind == "analyze":
        spec.pop("upstream_layout", None)
        opts = spec.get("opts")
        if isinstance(opts, dict):
            for key, why in DROPPED["analyze"].items():
                if opts.get(key):
                    dropped.append(why)
                if key in opts:
                    opts[key] = [] if isinstance(opts[key], list) else False
        spec["auto_detect"] = True
        spec.pop("add_detected_channels", None)
    return dropped


def connected(jobs: list[JobSpec], job_id: str) -> set[str]:
    """Every job reachable from *job_id* through links, either way, itself included."""
    neighbours: dict[str, set[str]] = {j.id: set() for j in jobs}
    for j in jobs:
        for source in j.sources():
            if source in neighbours:
                neighbours[j.id].add(source)
                neighbours[source].add(j.id)
    found = {job_id}
    stack = [job_id]
    while stack:
        for other in neighbours.get(stack.pop(), ()):
            if other not in found:
                found.add(other)
                stack.append(other)
    return found


def _check_outputs_follow(steps: list[RecipeStep], source: str) -> None:
    """Refuse a step whose new folder would be the same for every input.

    A conversion set up on an earlier acquisition proposes a destination named
    after *that* file (``run6_dataset``). Kept verbatim in a recipe, every
    application would convert into it — and a converter refuses a destination
    already holding another plate's wells, so every use after the first would
    fail. Said when saving, with the fix, rather than on the second night.
    """
    for step in steps:
        out = planned_output(step.kind, step.spec)
        if not out or out != step.spec.get("destination"):
            continue        # writes in place, or into a folder a link fills
        if SOURCE in out or SOURCE_PARENT in out:
            continue
        label = KIND_LABEL.get(step.kind, step.kind)
        raise RecipeError(
            f"{label} writes to {out}, which isn't named after the chain's input "
            f"({source_name(source)}), so every use of the recipe would write "
            f"there. Set its destination to a folder named after "
            f"{source_name(source)} — for example "
            f"{os.path.join(os.path.dirname(source), source_name(source) + '_dataset')} "
            "— and save the recipe again.")


def recipe_from_chain(name: str, jobs: list[JobSpec], job_id: str) -> Recipe:
    """The chain *job_id* belongs to, saved as a recipe called *name*.

    The chain is every job linked to *job_id* through any number of links in
    either direction — its connected group in the queue — in queue order, so
    saving from any job of a chain saves the same recipe. (Its lineage alone
    was not enough: saved from one of two watches feeding a Concatenate, the
    other watch was left out and dataset 2 kept a fixed path.) The chain must
    start from one job — a recipe has one input — and that job's input must be
    filled in. Every step that writes a new folder must name it after the
    input (:func:`_check_outputs_follow`).
    """
    name = name.strip()
    if not name:
        raise RecipeError("A recipe needs a name.")
    by_id = {j.id: j for j in jobs}
    if job_id not in by_id:
        raise RecipeError("That job is no longer in the queue.")
    ids = connected(jobs, job_id)
    chain = [j for j in jobs if j.id in ids]
    index = {j.id: i for i, j in enumerate(chain)}
    roots = [j for j in chain if not any(link.source in index for link in j.links())]
    if len(roots) != 1:
        raise RecipeError(
            f"This chain starts from {len(roots)} jobs; a recipe starts from one "
            "input. Save a chain that begins with a single job.")
    root = roots[0]
    if root.kind not in ROOT_INPUT:
        raise RecipeError(f"A recipe can't start with {KIND_LABEL.get(root.kind, root.kind)}.")
    source = str(_get_dotted(root.spec, ROOT_INPUT[root.kind]) or "").strip()
    if not source:
        raise RecipeError(
            f"“{root.label or root.kind}” has no input set, so there is nothing "
            "to build the recipe around. Set its input first.")
    source = os.path.normpath(source)
    parent = os.path.dirname(source)
    stem = source_name(source)

    steps: list[RecipeStep] = []
    notes: list[str] = []
    for job in chain:
        spec = json.loads(json.dumps(job.spec))
        dropped = _drop_run_specific(job.kind, spec)
        spec = _walk_strings(spec, lambda v: _template_value(v, source, parent, stem))
        links: list[RecipeLink] = []
        for link in job.links():
            # The chain is a connected group, so every source is in it.
            if link.target:
                links.append(RecipeLink(target=link.target, step=index[link.source]))
        label = KIND_LABEL.get(job.kind, job.kind)
        notes.extend(f"{label}: left out {why}." for why in dropped)
        steps.append(RecipeStep(kind=job.kind, spec=spec, links=links))
    _check_outputs_follow(steps, source)
    if any(step.kind == "analyze" for step in steps):
        notes.append("Analyze reads its filename schema and channels from its "
                     "input when it runs.")
    return Recipe(name=name, steps=steps, notes=notes, saved_from=source)


def _fill_value(value: str, source: str, parent: str, name: str) -> str:
    """A templated *value* for the input *source*: the placeholder that starts
    a path is joined with :func:`os.path.join`, so a parent at a drive root
    does not lose or double its separator; ``{source_name}`` is replaced
    wherever it appears."""
    for head, base in ((SOURCE_PARENT + "/", parent), (SOURCE + "/", source)):
        if value.startswith(head):
            rest = value[len(head):].replace(SOURCE_NAME, name)
            return os.path.normpath(os.path.join(base, *rest.split("/")))
    if value == SOURCE:
        return source
    return value.replace(SOURCE_NAME, name)


def instantiate(recipe: Recipe, source: str,
                new_id: Callable[[], str]) -> list[tuple[JobSpec, list[ChainLink]]]:
    """The jobs *recipe* queues for the input *source*, each with its links.

    Returned in order, each linking only to jobs before it, so adding them one
    at a time in this order never refers to a job not yet in the queue. A
    linked input shows the folder its source step is set up to write until
    the queue replaces it, when the job runs, with the folder that was written.
    """
    source = os.path.normpath(str(source).strip())
    if not recipe.steps:
        raise RecipeError(f"The recipe “{recipe.name}” has no steps.")
    if not source or source == ".":
        raise RecipeError("Choose the folder or file to apply the recipe to.")
    parent = os.path.dirname(source)
    name = source_name(source)
    out: list[tuple[JobSpec, list[ChainLink]]] = []
    ids: list[str] = []
    for position, step in enumerate(recipe.steps):
        spec = _walk_strings(json.loads(json.dumps(step.spec)),
                             lambda v: _fill_value(v, source, parent, name))
        links: list[ChainLink] = []
        for link in step.links:
            if not 0 <= link.step < position:
                raise RecipeError(
                    f"The recipe “{recipe.name}” is damaged: step {position + 1} "
                    f"reads from step {link.step + 1}, which does not come before it.")
            if link.target not in linkable_targets(step.kind):
                raise RecipeError(
                    f"The recipe “{recipe.name}” links an input "
                    f"{KIND_LABEL.get(step.kind, step.kind)} does not have ({link.target}).")
            earlier = out[link.step][0]
            planned = planned_output(earlier.kind, earlier.spec) or ""
            set_dotted(spec, link.target, planned)
            links.append(ChainLink(ids[link.step], link.target, CHAIN_KIND))
        job_id = new_id()
        ids.append(job_id)
        out.append((JobSpec(id=job_id, kind=step.kind,
                            label=describe(step.kind, spec), spec=spec), links))
    return out


# ── persistence ──────────────────────────────────────────────────────────────

def recipes_path(state_dir: Path | None = None) -> Path:
    """``job_recipes.json`` beside the queue, in *state_dir* or the app's folder."""
    return (state_dir if state_dir is not None else default_state_dir()) / RECIPES_FILENAME


def load_recipes(state_dir: Path | None = None) -> list[Recipe]:
    """The saved recipes, in the order they were saved.

    A missing file is no recipes. A malformed one is too — the Job Queue page
    builds at app start, and a hand-edited file must not take the app down —
    but it is logged, and a malformed entry in an otherwise sound file is
    skipped on its own rather than costing every other recipe.
    """
    path = recipes_path(state_dir)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text())
        entries = list(data.get("recipes") or [])
    except (OSError, json.JSONDecodeError, TypeError, AttributeError, ValueError) as exc:
        _LOG.warning("Could not read %s (%s); starting with no recipes.", path, exc)
        return []
    recipes: list[Recipe] = []
    for entry in entries:
        try:
            recipes.append(Recipe.from_json(entry))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            _LOG.warning("Skipped a malformed recipe in %s (%s).", path, exc)
    return recipes


def save_recipes(recipes: list[Recipe], state_dir: Path | None = None) -> None:
    write_json_atomic(recipes_path(state_dir),
                      {"recipes": [r.to_json() for r in recipes]}, durable=True)


def with_recipe(recipes: list[Recipe], recipe: Recipe) -> list[Recipe]:
    """*recipes* with *recipe* added, replacing one of the same name."""
    kept = [r for r in recipes if r.name != recipe.name]
    return [*kept, recipe]
