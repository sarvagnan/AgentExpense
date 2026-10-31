# Weekly audit review

Run from the application root with an absolute `LEDGER_DIR`. Scheduling is external to the application. Review every unreviewed outcome and report findings to the user. Read first, then record all verdicts in one command. Corrections to entries or evidence require user agreement. Keep the ledger clean on failure.

1. Read the current policy and every line still waiting for a verdict:

   ```sh
   cat "$LEDGER_DIR/AGENTS.md"
   uv run --with jsonschema python scripts/ledger/audit.py list --unreviewed
   uv run --with jsonschema python scripts/ledger/audit.py list --unreviewed --json
   ```

   `--unreviewed` lists every draft and statement line without a verdict, whatever
   its age, so a skipped week is covered by the next run. If it lists nothing, say so
   in one line, name the model you are running as, and stop. Otherwise read every
   line, including failed, discarded and not-expense outcomes. Review lines record
   earlier verdicts; do not review a review line. Record the period covered.

2. For each added draft, find its permanent entry ID and read its evidence, the
   typed words, manual pieces, questions/answers, final proposal, doubts and runs.
   Check duplicate merchants/order IDs/references/amounts, established account
   naming and posting structure, printed dates, original currency and the actual
   payment account. Use the old ledger when the policy requires it. These commands
   locate an entry, its cited evidence and a merchant precedent (substitute the
   actual ID, path or merchant):

   ```sh
   uv run python -c 'import json,pathlib; wanted="ENTRY_ID"; print(json.dumps([e for p in (pathlib.Path(__import__("os").environ["LEDGER_DIR"])/"data/expenses").glob("*.json") for e in json.loads(p.read_text()) if e["id"]==wanted],ensure_ascii=False,indent=2))'
   ls -l -- "$LEDGER_DIR"/Bills/YYYY-MM-DD/EVIDENCE.pdf
   grep -n -i -- "MERCHANT" "$EXPENSES_OLD_LEDGER"
   ```

   Open receipts, PDFs and images directly with your file-reading tool and look at
   them; keep the originals in Bills. When a PDF's text matters and does not come
   through, extract it with the server's dependencies. Do not infer classifications
   from an amount alone.

   ```sh
   uv run --locked --directory review-dashboard/server python -c 'import pypdfium2 as p; pdf=p.PdfDocument(str(__import__("pathlib").Path(__import__("os").environ["LEDGER_DIR"])/"Bills/YYYY-MM-DD/EVIDENCE.pdf")); print("\n\f\n".join(page.get_textpage().get_text_range() for page in pdf))'
   ```

3. For each statement line, read the stored reconciliation, summary and affected
   entries. Check every serial/page/date/source membership, signed amounts,
   original-currency conversions (including provisional values replaced), new
   entries and user decisions. Confirm the four summary checks and chain. Replace the
   reconciliation filename with the actual month and year:

   ```sh
   cat "$LEDGER_DIR/data/card-statements.json" "$LEDGER_DIR"/data/statements/MonthYYYY-card-reconciliation.json
   uv run python scripts/ledger/ledger_lock.py -- make verify
   ```

   Verification is a safety net; it does not prove the classification is right.

4. Look for recurring failed checks, retries, questions, discards, misleading
   suggestions and missing evidence. Distinguish a discarded draft from removal
   of an expense: discarding a record leaves an added ledger entry in place.

5. Give every listed line one verdict: `ok` when checked, or `issue` with the
   concrete concern and what the user must decide. Write them all to
   `/tmp/expense-weekly-verdicts.json`, using the actual 32-hex line IDs:

   ```json
   [
     {"line": "LINE_ID", "verdict": "ok", "note": "Checked entry and evidence against policy"},
     {"line": "LINE_ID", "verdict": "issue", "note": "The exact unresolved fact, and what the user must decide"}
   ]
   ```

   Then record them in one step, naming the model you are running as:

   ```sh
   uv run --with jsonschema python scripts/ledger/audit.py review --verdicts /tmp/expense-weekly-verdicts.json --reviewer "MODEL"
   ```

   `review` holds the ledger lock throughout. It refuses a ledger with uncommitted
   changes (report them; never commit someone else's work), appends one review line
   per verdict, verifies and commits them as one ledger commit, and prints the commit
   ID. If anything fails it restores the log exactly and changes nothing, so the live
   page never waits on a half-written review; report the failure rather than working
   around it. Never rewrite earlier lines.

6. Report in the thread: the model you ran as, the period and number of lines
   reviewed, what was fine, each issue needing the user with what you suggest,
   recurring patterns, the verdict commit ID and the verification result. Clearly
   name unavailable evidence.
