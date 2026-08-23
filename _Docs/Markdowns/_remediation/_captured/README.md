# Captured evidence

Raw material for the remediation, copied here so that it survives the session
that produced it. None of it is documentation; it is evidence, kept until the
work it supports is finished.

| File | What it is |
|---|---|
| `CODEBASE_REVIEW.md.gz` | The original review: 177 findings across twelve themes |
| `findings.json` | The same 177, parsed — one record per finding, numbered. The numbers are the item numbers used everywhere else |
| `verdict_{A..F}.json` | The reconciliation. Six auditors, one per pair of themes, each verdict giving `FIXED` / `PARTIAL` / `DECLINED` / `OPEN`, the evidence at the tree, and the guard test where one exists |
| `RECONCILIATION.md.gz` | Those six merged into one ledger over all 177 |
| `brief_W{1,2,3}.md` | What each parallel group was asked to fix, with the verified state at the tree |
| `W{1,2,3}_transcript.jsonl.gz` | Each group's full working transcript, captured live. Every probe it ran and every result it saw |

The transcripts are the reason this directory exists. A group's reasoning —
which sites it checked, what its probes printed, which candidate causes it ruled
out — lives only in its transcript, and a report summarises it at heavy loss. If
an item has to be picked up cold, or a claim in a group's status file needs
checking, that transcript is the primary source.

**Delete this directory with the tracker**, in the pull request that closes the
tracker's last row. Nothing here should outlive the work.
