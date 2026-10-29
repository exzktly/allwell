"""A run Slurm refused outright ends with a headline that says so, not with an exit code.

``srun``'s "Requested node configuration is not available" means no node the cluster can give matches the
scheduler command — a profile asking for more CPUs, memory, GPUs or a node type than the partition has. The run's
evidence (``services.run_evidence``) turns it into the failure headline, unless a later grant shows a retry got
past it (the srun wrapper's own powered-on-node preference is refused that way and then retried).
"""

from __future__ import annotations

from services.run_evidence import RunEvidence

REFUSED = "srun: error: Unable to allocate resources: Requested node configuration is not available\n"


def _evidence(*lines: str) -> RunEvidence:
    ev = RunEvidence()
    for line in lines:
        ev.note(line)
    return ev


def test_a_refusal_with_no_grant_after_it_is_the_run_s_failure():
    said = _evidence("[remote] scheduler: requesting allocation via: srun -p gen\n", REFUSED).termination()
    assert said is not None
    assert said.startswith("Remote pipeline could not start: Slurm has no node that matches the scheduler command")
    assert "change the scheduler command in the Remotes dialog" in said


def test_a_refusal_a_later_grant_got_past_is_not_a_failure():
    ev = _evidence(REFUSED, "[remote] scheduler: no powered-on node can take this request — letting Slurm boot one\n",
                   "[remote] scheduler: allocation granted — job 4812 on n02 (partition gen)\n")
    assert ev.termination() is None


def test_a_run_with_no_refusal_is_unaffected():
    assert _evidence("[remote] scheduler: allocation granted — job 1 on n1 (partition p)\n").termination() is None
