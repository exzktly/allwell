"""Turn a JupyterHub profile into an ``srun`` launcher the way the cluster does.

A JupyterHub profile does not hold scheduler options; it holds *values*
(``req_partition``, ``req_memory``, ``req_options`` …). batchspawner pours them
into a job template — the spawner's ``batch_script`` — and it is the template
that decides which ``#SBATCH`` option each value becomes. Mapping the values to
flags by their names alone guesses, and guesses wrong whenever the cluster's
template does something site-specific with one of them: a cluster whose
template reads ``#SBATCH --constraint={{options}}`` lets a profile say
``req_options='g4dn.12xlarge'``, and appending that value to the launcher made
``srun`` run ``g4dn.12xlarge`` as the program instead of the job.

:func:`launcher_from_batch_script` renders the cluster's template the way
batchspawner does — Jinja-style ``{{ var }}`` and ``{% if var %}…{% endif %}``
when the template uses them, ``{var}`` otherwise, with each ``req_<name>``
value available as ``<name>`` — and reads the ``#SBATCH`` lines that come out.
When the config sets no template, ``services.remote_config`` keeps mapping the
values by name, and passes the free-form ``req_options`` through
:func:`directive_flags` (batchspawner's stock template writes it as a bare
``#SBATCH {{options}}`` line).

Only resource options carry over (:data:`_SRUN_RESOURCE_OPTIONS`). The rest of
a job template is about the batch job JupyterHub submits, not about a step the
app runs: ``--output`` would send the run's stream to a file the app never
reads, ``--export={{keepvars}}`` would strip the job's environment down to
JupyterHub's variables. Those are left out quietly, because every template has
them. A task count is the one batch option that is translated rather than kept
or dropped: a template that asks for cores with ``#SBATCH -n {nprocs}`` gives a
batch job that many CPUs, while ``-n`` on the app's ``srun`` would run the
pipeline that many times, so :func:`single_task` turns it into
``--cpus-per-task``. Dropping it instead left the step with one CPU, and the
pipeline sizes its worker pool from the CPUs it is given.

Two losses are *reported*, in :attr:`ScriptLauncher.dropped`, because they
change which node the run gets: a word that is neither an option nor an
option's value (the word ``srun`` would otherwise have run as the program), and
a resource line that needs a variable the profile does not set.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Any

#: Long name → whether it takes a value, for every option that describes the
#: resources a step needs. Anything else in a job template is left out.
_SRUN_RESOURCE_OPTIONS: dict[str, bool] = {
    "partition": True, "qos": True, "account": True, "reservation": True,
    "constraint": True, "cpus-per-task": True, "mem": True, "mem-per-cpu": True,
    "mem-per-gpu": True, "time": True, "time-min": True, "gres": True,
    "gpus": True, "gpus-per-node": True, "gpus-per-task": True,
    "cpus-per-gpu": True, "nodelist": True, "exclude": True, "licenses": True,
    "clusters": True, "wckey": True, "exclusive": False,
    # Kept only to be folded into --cpus-per-task by single_task().
    "ntasks": True,
}

#: Short spellings of the options above (all of them take a value).
_SHORT: dict[str, str] = {
    "p": "partition", "q": "qos", "A": "account", "C": "constraint",
    "c": "cpus-per-task", "t": "time", "G": "gpus", "w": "nodelist",
    "x": "exclude", "L": "licenses", "M": "clusters", "n": "ntasks",
}

_IF_BLOCK = re.compile(r"\{%-?\s*if\s+(\w+)\s*-?%\}(.*?)\{%-?\s*endif\s*-?%\}", re.S)
_JINJA_VAR = re.compile(r"\{\{-?\s*(\w+)\s*-?\}\}")
_FORMAT_VAR = re.compile(r"(?<!\{)\{(\w+)\}(?!\})")
_WHOLE_VALUE_IS_A_VARIABLE = re.compile(
    r"^#SBATCH\s+-{1,2}[\w-]+(?:=|\s+)(?:\{\{-?\s*\w+\s*-?\}\}|\{\w+\})\s*$")


@dataclass
class ScriptLauncher:
    """A profile's launcher, and what was left out of it that mattered."""

    launcher: str
    dropped: list[str] = field(default_factory=list)


def template_vars(kwargs: dict[str, Any]) -> dict[str, Any]:
    """The names a template sees: each ``req_<name>`` as ``<name>``.

    batchspawner exposes its ``req_*`` traits with the prefix stripped. A key
    the profile sets without the prefix is accepted too, unless the prefixed
    form also sets it.
    """
    out: dict[str, Any] = {}
    for key, value in kwargs.items():
        if not key.startswith("req_"):
            out.setdefault(key, value)
    for key, value in kwargs.items():
        if key.startswith("req_"):
            out[key[4:]] = value
    return out


def _is_set(value: Any) -> bool:
    return value is not None and str(value).strip() not in ("", "0", "False")


def _render_lines(
        template: str, values: dict[str, Any]) -> list[tuple[str, str, set[str]]]:
    """``(rendered line, line as written, names it needs that are unset)``."""
    jinja = "{{" in template or "{%" in template
    if jinja:
        template = _IF_BLOCK.sub(
            lambda m: m.group(2) if _is_set(values.get(m.group(1))) else "",
            template)
    var_re = _JINJA_VAR if jinja else _FORMAT_VAR
    out: list[tuple[str, str, set[str]]] = []
    for line in template.splitlines():
        missing: set[str] = set()

        def _sub(m: re.Match[str], missing: set[str] = missing) -> str:
            name = m.group(1)
            if not _is_set(values.get(name)):
                missing.add(name)
                return ""
            return str(values[name]).strip()

        out.append((var_re.sub(_sub, line), line.strip(), missing))
    return out


def _option_name(tok: str) -> str | None:
    """The long name of option token *tok*, or ``None`` if it is not one."""
    if tok.startswith("--"):
        return tok[2:].partition("=")[0]
    if tok.startswith("-") and len(tok) > 1:
        return _SHORT.get(tok[1], tok[1])
    return None


def directive_flags(tokens: list[str]) -> tuple[list[str], list[str]]:
    """``(srun flags, stray words)`` from the words of one ``#SBATCH`` directive.

    Resource options come back as ``--name=value``. Other options are left out
    without comment, a separately written value going with its option. A word
    that is neither is a stray word.
    """
    kept: list[str] = []
    stray: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        name = _option_name(tok)
        i += 1
        if name is None:
            stray.append(tok)
            continue
        if tok.startswith("--"):
            attached, value = "=" in tok, tok.partition("=")[2]
        else:
            attached, value = len(tok) > 2, tok[2:]
        takes = _SRUN_RESOURCE_OPTIONS.get(name)
        if (not attached and takes is not False and nxt is not None
                and not nxt.startswith("-")):
            value = nxt
            i += 1
        if takes is False:
            kept.append(tok)
        elif takes and value:
            kept.append(f"--{name}={value}")
    return kept, stray


def single_task(flags: list[str]) -> list[str]:
    """*flags* with ``--ntasks`` folded into ``--cpus-per-task``.

    A job template asks for cores the batch way: ``#SBATCH -n 16`` gives the
    job sixteen CPUs, and the one process JupyterHub starts in it uses them
    all. The same flag on the ``srun`` the app launches would start the
    pipeline sixteen times, each with one CPU. The app runs one process that
    spreads its own workers across the CPUs it is given, so the step asks for
    one task with *tasks × cpus-per-task* CPUs. A task count that is not a
    whole number is dropped rather than guessed at.
    """
    def _value(name: str) -> str | None:
        for f in flags:
            if f.startswith(f"--{name}="):
                return f.split("=", 1)[1]
        return None

    tasks = _value("ntasks")
    if tasks is None:
        return flags
    out = [f for f in flags if not f.startswith(("--ntasks=", "--cpus-per-task="))]
    per_task = _value("cpus-per-task") or "1"
    if tasks.isdigit() and per_task.isdigit():
        out.append(f"--cpus-per-task={int(tasks) * int(per_task)}")
    elif _value("cpus-per-task") is not None:
        out.append(f"--cpus-per-task={per_task}")
    return out


def launcher_from_batch_script(
    template: str, kwargs: dict[str, Any], *, program: str = "srun",
) -> ScriptLauncher:
    """Render *template* with a profile's *kwargs* and keep its resource options.

    The launcher is empty when the template asks for no resource at all.
    """
    values = template_vars(kwargs)
    flags: list[str] = []
    dropped: list[str] = []
    for line, written, missing in _render_lines(template, values):
        stripped = line.strip()
        if not stripped.startswith("#SBATCH"):
            continue
        body = stripped[len("#SBATCH"):].strip()
        if not body:
            continue
        try:
            tokens = shlex.split(body)
        except ValueError:
            dropped.append(body)
            continue
        if missing:
            # A line that needs an unset variable cannot be rendered. Losing
            # --chdir={{homedir}} does not change the node, and neither does a
            # resource option whose whole value is the unset variable: the
            # profile simply does not ask for that resource (batchspawner
            # renders it empty). A value with more around the variable —
            # --gres=gpu:{{ngpus}} — was meant to ask for something.
            if (tokens and _option_name(tokens[0]) in _SRUN_RESOURCE_OPTIONS
                    and not _WHOLE_VALUE_IS_A_VARIABLE.match(written)):
                dropped.append(f"{written} (the profile sets no "
                               f"{', '.join(sorted(missing))})")
            continue
        kept, stray = directive_flags(tokens)
        flags.extend(kept)
        dropped.extend(f"“{w}” (not attached to any option)" for w in stray)
    flags = single_task(flags)
    if not flags:
        return ScriptLauncher("", dropped)
    return ScriptLauncher(shlex.join([program, *flags]), dropped)
