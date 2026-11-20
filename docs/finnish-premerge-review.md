# Pre-merge Finnish wording review

Backlog item FI-COPY-02. Before a pull request that changes Finnish user-facing
text is merged, Claude asks `gpt-6.1-sol` to review the changed lines and records
the result in the pull request. This is a routine for the agent that prepares the
merge, not a required check or merge gate.

## Scope

- Public and regular-user UI: React sources, Flask templates and messages that
  reach visitors, Go API messages and the Finnish catalog.
- Admin is excluded: `admin/`, `admin_V2/`, the manuals, `GodScreen.tsx` and every
  source whose audit evidence in `finnish-copy-status.csv` is an admin audit.
- User emails are excluded until FI-COPY-01's UI pass is done. Then run with
  `--include-emails`.
- Tests, docs, scripts, generated frontend assets and `en`/`sv` catalogs are out of
  scope.

## Running it

From the pull request branch:

```sh
git fetch origin main
python3 scripts/finnish_premerge_review.py --report /tmp/finnish-review.md
```

The script diffs committed changes against `origin/main` and keeps the added lines
that look Finnish. For each line the model gets the surrounding source, the text it
replaces, sample renderings of its placeholders (1, 2 and 5 for counts) and a note
when count-dependent wording is nearby. The prompt includes
[the style guide](finnish-style.md) and asks for exact replacements with reasons as
JSON.

The model is called through the `codex` CLI
(`codex exec --model gpt-6.1-sol --sandbox read-only -`), or through the command in
`FINNISH_REVIEW_COMMAND`, which reads the prompt on stdin. Use `--packet FILE` to
write the prompt without calling a model.

## Results

| Result | Meaning | Exit code |
| --- | --- | --- |
| NO CHANGED FINNISH TEXT | Nothing in scope changed. | 0 |
| NO FINDINGS | The model reviewed every changed line and proposed nothing. | 0 |
| UNRESOLVED FINDINGS | The model proposed replacements; each is listed as unresolved. | 1 |
| NOT REVIEWED | The model was unavailable or did not answer with the requested JSON. | 2 |

NOT REVIEWED is never a pass. Do not write that the Finnish check passed when the
model could not run; rerun it later or record who reviewed the text instead.

## Recording it in the pull request

Paste the report into the pull request description under its own heading. For each
finding, either apply the replacement in the branch or change its status to
`rejected: <reason>` or `deferred: <issue>`. Rerun the script after applying
changes and replace the report. A finding is a proposal: check the rendered
sentence, the style guide's "Mitä ei muuteta" rules and the `suomi-finnish` skill
before accepting it.
