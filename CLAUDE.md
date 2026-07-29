# CLAUDE.md

## Never commit specific data

This repository is public/shared tooling; the data it processes is not.
Nothing checked into git — source, docs, comments, commit messages — may
contain figures, names or identifiers pulled from a real snapshot. Concretely,
never hardcode:

- **Secrets**: API tokens, `.envrc.local`, anything under `data/` (the cached
  snapshots), `*.parquet`, generated `*.html` reports.
- **Real usage numbers**: node hours, credit balances, spend, percentages,
  row counts, dates of a real pull — anything computed *from* a snapshot
  rather than *about* the code.
- **Real identifiers**: project names, project codes, usernames, customer
  names, invoice numbers — anything that names a specific real project,
  person or organisation's data.

It is fine to reference the *system* generically — "Isambard 3", "University
of Exeter", "UKRI", the portal API, SLURM concepts — since that is naming
what the tool talks to, not disclosing what it found. Illustrative examples
(docstrings, tests, worked examples in the docs) must use fabricated data:
made-up project codes, fictional usernames, round numbers clearly not lifted
from a real pull.

Once a report has actually run, it is fine for *that run's output* (a
generated HTML file, a CSV you hand to someone) to contain real figures —
those are gitignored, not committed. The rule is about what goes into git,
not about what the tool is allowed to compute or display at runtime.

If you're changing an example or a worked case in the docs and you're not
sure whether a number is real or fabricated, treat it as real and replace it.
