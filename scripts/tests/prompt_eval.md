# Compare the in-app prompts

After the owner signs in to the configured agent, run from the application
root. Select the local commit immediately before 6i for the baseline.

```sh
uv run --locked --project review-dashboard/server python scripts/tests/prompt_eval.py \
  --live --baseline 5a4fe8bdf007e1fe0928f48804ad63c31780acc1 > tmp/prompt-comparison.md
```

Use repeatable `--case` flags for a smaller run. The case names are the stems
of the [golden prompt files](../../review-dashboard/server/tests/prompts/).
The output puts the answers, input word counts and any reported token usage or
validation failures side by side. Both runs use the current adapter, model,
effort, isolation gate and answer schemas. The script reads the original prompt
code and policy from local Git and uses the same temporary fictional ledger and
attachments on both sides. It sends one request per variant, with no ledger
save or retry. Running the script with `--live` may incur model usage. The
offline test targets never run it.

Review cases for the following behavior.

- **Ready and question** use the stated payment facts, and ask for the account
  when it is missing. **Answered** uses the person's earlier answer.
- **Currency expense and transfer** ask for the missing amount or rate and
  keep the known amount alongside the incomplete posting.
- **Duplicate** asks about the matching payment. **Not expense** identifies the
  statement upload as a statement.
- **Wallet, person, investment and cashback** choose postings consistent with
  the Add policy. **Points earned, spent and expired** use the points commodity
  and the recorded money cost for redemptions.
- **Ledger fix** proposes only a justified correction or asks what changed.
  **Balance refusal** leaves the confirmed balance alone.
- **Fuel on and off** respect the answer model's extension fields. **Older
  ledger** can use the older-history lookup. **PDF** receives both images
  and extracted text. **Receipt and statement** use their reading schemas.
