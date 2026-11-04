# Weekly audit review

Enable `modules.weeklyReview` in `ledger.json`, then run `make weekly-review` from the application root with an absolute `LEDGER_DIR`. Scheduling is external to the application. Review every unreviewed outcome and report findings to the user. Read first, then record all verdicts in one command. Corrections to entries or evidence require user agreement. Keep the ledger clean on failure.

1. Read the current policy and every line still waiting for a verdict:

   ```sh
   cat "$LEDGER_DIR/AGENTS.md"
   uv run --with jsonschema python scripts/ledger/audit.py list --unreviewed
   uv run --with jsonschema python scripts/ledger/audit.py list --unreviewed --json
   ```

   `--unreviewed` lists every draft, statement, correction and rule-operation line without a verdict, whatever
   its age, so a skipped week is covered by the next run. If it lists nothing, say so
   in one line, name the model you are running as, and stop. Otherwise read every
   line, including failed, discarded and not-expense outcomes. Review and rule-review lines record
   earlier verdicts; do not review either kind of review line. Record the period covered.

2. For each added draft, find its permanent entry ID and read its evidence, the
   typed words, manual pieces, questions/answers, final proposal, doubts and runs.
   Check duplicate merchants/order IDs/references/amounts, established account
   naming and posting structure, printed dates, original currency and the actual
   payment account. Use the readable path configured by `modules.history.oldLedger` when the policy requires it. Relative paths resolve from the ledger root and `~` expands to the user home. These commands
   locate an entry, its cited evidence and a merchant precedent (substitute the
   actual ID, path or merchant):

   ```sh
   uv run python -c 'import json,pathlib; wanted="ENTRY_ID"; print(json.dumps([e for p in (pathlib.Path(__import__("os").environ["LEDGER_DIR"])/"data/expenses").glob("*.json") for e in json.loads(p.read_text()) if e["id"]==wanted],ensure_ascii=False,indent=2))'
   ls -l -- "$LEDGER_DIR"/Bills/YYYY-MM-DD/EVIDENCE.pdf
   uv run python -c 'import sys; sys.path.insert(0,"scripts"); from project_paths import ledger_root; from ledger_manifest import old_ledger_path; p=old_ledger_path(ledger_root()); print("\n".join(line for line in p.read_text().splitlines() if "MERCHANT".casefold() in line.casefold()) if p else "Old ledger not configured or unreadable")'
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

4. Review every made rule and every rule-linked addition. Read `data/forecast.json`,
   `data/recurring-settings.json` (when present), each rule's origin/evidence and
   the complete balanced payment. The account prefixes in `never-automatic` are
   the owner's settings. A fee, tax, discount or rounding line alone must not
   justify a rule, and a payment already covered by a rule never justifies a
   second rule. Check amount/currency, planned costs, payment account, schedule
   anchor and next date against the full payment. Read duplicate-period overrides,
   undos (including reversing entries), suppression evidence and paused matches.
   Check that a paused payment offered the owner Resume / Keep paused; review
   never chooses for them.

   Record `confirm` or `propose-removal` for each rule checked, with a concrete
   reason. A proposal asks the owner: never remove, pause or resume a rule, and
   never change an entry. Remove / Keep on Forecast → Rules is the owner's
   separate audited answer. `audit.py list` includes rule operations and rule
   verdicts; `list --unreviewed` excludes verdict lines themselves. Give the
   originating operation/addition its ordinary line verdict as well.

5. Review every correction's before/after entry, changed fields, reason, actor and
   operation ID. Check retained identity, statement membership and locked postings,
   rule links/next date, and every added or moved evidence path. For receipt details,
   read the extracted answer and the owner's Use / Keep choice. Review records
   verdicts only: never edit an entry, move evidence or apply another correction.

6. Look for recurring failed checks, retries, questions, discards, misleading
   suggestions and missing evidence. Distinguish a discarded draft from removal
   of an expense: discarding a record leaves an added ledger entry in place.

7. Give every listed line one verdict: `ok` when checked, or `issue` with the
   concrete concern and what the user must decide. Write them all to
   `/tmp/expense-weekly-verdicts.json`, using actual 32-hex audit-line IDs and 16-hex rule IDs:

   ```json
   [
     {"line": "LINE_ID", "verdict": "ok", "note": "Checked entry and evidence against policy"},
     {"line": "LINE_ID", "verdict": "issue", "note": "The exact unresolved fact, and what the user must decide"},
     {"rule": "RULE_ID", "verdict": "confirm", "reason": "Two complete monthly payments match this balanced rule"},
     {"rule": "ANOTHER_RULE_ID", "verdict": "propose-removal", "reason": "The cited payment is already covered by another active rule"}
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

7. Report in the thread: the model you ran as, the period and number of lines
   reviewed, what was fine, each issue needing the user with what you suggest,
   recurring patterns, the verdict commit ID and the verification result. Clearly
   name unavailable evidence.

## Varying recurring rules and bills (6f B)

Review `bill-saved`, `bill-removed` and `bill-paid` rule events with their typed
words, evidence and amount-check doubts. A saved bill is unpaid and contributes
only to forecasts. Verify its payment retains its sources and clears the bill in
the same commit. Check the estimate against the latest three payments including
outliers, and accepted unusual amounts against the latest six. Record a verdict
or propose a rule change; a verdict authorizes no entry or bill correction.
