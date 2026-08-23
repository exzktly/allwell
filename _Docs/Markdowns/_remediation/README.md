# Per-group working status

One file per parallel work group (`W1.md`, `W2.md`, `W3.md`), written by the
group that owns it and holding its per-item state: what is done, what is still
in flight and precisely what remains of it, what is blocked and on what, and the
test that pins each closed item.

**This is working state, not the record.** The record is
`../REVIEW_REMEDIATION.md`, whose rows move only once a claim here has been read
against the code and its test watched failing without the fix. A group reporting
an item done is a hypothesis until then.

These files exist so that work survives a session ending. A group's report would
otherwise live only in the conversation that spawned it, which is exactly the
failure the whole remediation is a response to — see CLAUDE.md section 6.1.

**Delete this directory with the tracker**, in the pull request that closes the
tracker's last row.
