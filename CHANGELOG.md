# Changelog

All notable changes to AgentExpense are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) as described in
[docs/VERSIONS.md](docs/VERSIONS.md).

Every release has an **Upgrade notes** section. Its numbered steps are written so a
person or a coding agent can follow them in order from the application root, with
the ledger selected by `LEDGER_DIR` (or kept at `ledger/`). Stop at the first step
that fails and keep its output.

## [Unreleased]

- Queue shows live lookup counts, agent steps and provider reconnection for additions and statement readings.

- Early recurring payments clear the occurrence they pay, including when a rule is detected from payment history.

- Browser fixture resets clear Add recovery after the app unloads, preventing
  late autosave replies from leaving draft IDs that fail in later cases.

- Hand ledger commits carry their diff, actor and jj identity in the audit log;
  backfill and weekly review cover earlier changes. Settings and first-run writes
  are audited. Interrupted writers name an audit-only restore command, and failed
  saves preserve files outside their own writes. Browser fixture seeding records
  evidence writes and removals in the same transaction.

- Forecast omits its chart while the measured container has no room inside its
  axis margins, then redraws it when space returns. Hidden and tiny containers
  no longer produce a negative SVG width.

- Hand-form Notes now captures native input and handles it on input, as Add
  does, so typed words reach the visible field, browser recovery and autosave
  for additions and corrections. A Notes save was once missed on a slower Linux
  WebKit run while a reopened editor's claim reply arrived; the exact trigger
  was not reproduced on unchanged code.

- Settings adopts published names after activation and when closing returns focus
  to its opener. The button still holds its target through a pointer click; a
  focused opener no longer keeps the previous name indefinitely.

- Fix timing races in Queue, Settings, setup and usual-payment Retry: an applied
  statement leaves its held Queue row, a published name keeps Settings still under
  the pointer, Import unmounts before scrolling to Add, and autosave leaves a failed
  usual request's submitted draft alone. Background browser captures no longer
  restyle or restore the page while the next gesture runs.

- Add rechecks draft ownership before a late autosave resumes, keeping next-expense
  words off an earlier usual payment. Browser receipt checks follow the accepted
  draft ID; fixture cleanup waits for browser-observed completion and correction
  reopening waits for Back navigation.

- Statement Apply links only the payments it creates, preserves earlier link
  decisions and never creates recurring rules. Saved bills retain their own
  occurrence for early and late payments; ambiguous bills stay unpaid without
  blocking Apply. Matching reuses one batch index and retains all financial checks.
- Forecast keeps unpaid occurrences after unrelated later entries, publishes
  contribution direction and separates costed investment principal from foreign
  fees. Withdrawals add no cash need; salary splits retain their income headline.
  Unit contributions display, sort and detect by money cost. The holdings setting
  leaves contribution commitments visible in Rules and Forecast.
- Edit entry announces an unknown Save result in its reserved footer area, keeping
  its naturally wrapped notice and ordinary error clear of the fields above.

- Browser account-creation checks verify the Saved acknowledgement before allowing
  statement assignment to replace its status, preventing a timing-dependent false failure.

- Regular investment contributions can be detected, suggested, edited and linked
  from Add, hand finish, statements and usual payments, including varying amounts
  and saved bills. Rules and Forecast show money set aside and upcoming cash needs
  separately from spending. Correction fields also stay in place when a refused
  Save hides its uncertain-result notice.

- Maintainer tooling: `make browser-box` runs browser cases in disposable,
  networkless Incus containers on a maintainer's server, from an uncommitted
  checkout. It packages tracked and untracked files but refuses ledgers, ledger
  fragments, receipts, statements, credentials and local settings. It builds one
  image with pinned, checksum-verified tools, keeps at most two containers, and
  gives a failed test one unchanged rerun. Each case runs detached inside its
  container with its own time limit, so a dropped connection pauses the
  controller instead of losing the case: it reconnects, resumes and collects,
  including DNS failures after a successful remote response and lost exec or
  transfer operations. Unexplained status errors retry before becoming
  infrastructure failures.
  Infrastructure failures are recorded apart from test failures and don't use
  the rerun, a finished test's outcome is never replaced, and `--stop` cancels
  running cases within seconds, keeping partial results. Cleanup deletes only
  exact containers carrying the run's owner tag. `make test` runs the runner's
  quick tests; `make test-browser-box` adds the slower process tests. No
  application upgrade steps are needed.

- Timed-out ledger fixes can be checked and saved through Continue editing or
  Ledger check's Open the entry. Unsettled correction journals keep their item
  protected and log the reason while allowing the server to start.
- Add retains words typed while a pending render restores the textarea's old
  value, including after leaving an Edit entry cancellation in flight.

- Resolved fix notices discard without opening an edit claim when there is no
  local work to preserve; browser-kept edits still go to Trash.

- Expired edit claims can be cancelled without leaving discarded changes behind.
  Reordered claim responses relinquish their own lease. Startup recovers unfinished
  edit-save markers; timed-out fixes keep Retry after Restore. Retry asks before
  replacing kept edits. Queue Discard explains pending/unknown Saves and hands a
  queued item's claim to Trash atomically, preventing a worker race.
- Edit entry now offers Save and Cancel, with a discard question for changed
  forms. Cancel removes temporary edits without ledger history; ledger-fix
  proposals remain available. Restore retains discarded edits and fix proposals.
- Queue opens details only on explicit selection, and kept edits show a summary.
- Rules sort by next due, name, native amount or paying account, kept in the URL.
- Calendar-day averages use spending through the device's local today for the
  current month, full past months, and no average for future months.

- Fix summaries keep a single set of actions and Retry after timeouts. Each editor
  has its own claim generation, and Cancel waits for a known Save outcome. Restore
  keeps the latest agent proposal separately from the owner’s discarded edits.
  Proposal Discard saves the complete on-screen edit before Trash; Queue recovery
  is isolated by draft. Back-forward-cache returns recheck the editor.

- Provider timeout messages pause as transient outages; only the local task deadline
  counts towards three tries. Preparation and Pi stdin writes respect cancellation
  and deadlines. Interrupted readings reset their streak after restart. A later pass
  resolves failed fix proposals and refuses Retry. Failed receipt readings offer
  Dismiss, keep payments/evidence and no longer inflate Needs you totals. Receipt
  Retry gets a fresh queue time; its failure audit contains only that attempt’s runs.
  Add and receipt diagnostic histories retain the latest 50 runs.

- Add accepts 50 files per item (10 per request), uploading three at a time. Every
  PDF page reaches the agent as an image and text. Codex/OpenCode prompts travel
  over stdin. Three consecutive task timeouts become Failed with a duration and
  Retry, retaining words/files and letting the next item run; provider outages
  still pause the agent.

- Hand Retry sends a claimed queued draft again; worker recovery checks acceptance for each operation. Statement Apply keeps other sessions usable, survives a lost Cancel response (including a return after off-screen confirmation) and announces cancellation once. Missing result records end polling with an honest unknown-outcome notice; temporary read failures keep retrying. Statements filed elsewhere point to their stored reconciliation.

- Page completion clears the local error belonging to a committed Retry, including receipt choices, Queue details, retention and first-run forms. Receipt completion refreshes once and retains newer composer words. Browser action coverage rejects Saved beside its own stale alert.

- Successful statement Discard replays clear the old refusal and show their Trash destination, including shelf and recovery retries. A connected control chosen during Discard keeps focus.

- Direct actions share immediate pending feedback and an ordered browser save
  queue, with explicit retry/reload recovery. Forms retain newer typing across
  late replies, including Add restoration. Cost refusals explain positive costs
  and negative refund amounts in plain words. First-run account forms retain
  newer fields through creation. Worker acceptance releases the page FIFO (except statement Apply) while
  committed-result polling stays pending; terminal refusals retain their reasons.
  Feedback is owned by its form/item, coalescing keeps the newest resource choice,
  and all retry paths share removal/restore completion effects. Background word
  interpretation reads only atomic accounts/locale documents without a writer lock. Retained form setters stay stable
  while typing, so opening-balance initialization does not restart each render.
  Recovery clears submitted values after unmount, expires after 24 hours, retains
  rule versions and clears on Cancel/Back. Setup opening operations replay their
  committed result, hand retries reclaim/check their form, and data reads fence
  document departure with keepalive transports. Review keeps acknowledged fields
  visible before refreshed decisions arrive; statement feedback and setup status
  text retain space through longer failure reasons. Bill removal retries its own
  operation, and a reopened failed account-currency form keeps its submitted choice.
  Late already-paid guards retain the usual-payment action’s position. Slot Retry
  replays its job instead of newer form values. Recovered retries retain worker
  callbacks, failed polls stay pending, cancellation keeps the checklist quietly,
  and statement Apply prevents later writes overtaking it. First-run replanning
  publishes no transient found state; kept fields and versions clear as a group,
  and typing hints survive rollback-time read failures.

- Saves share an in-process Python verifier with terminal `make commit`, reuse
  validated entries and account balance histories, and refresh whole dashboard
  generations in the background. External changes invalidate the baseline;
  rollback and startup repair retain the verification boundary. Queue work wakes
  on an event.
- Dashboard rebuilds require their own durable save receipt, retain forced day
  and builder invalidations, and keep superseded generations for ten minutes.
  Canonical caches include model identity. Waiting imports, agent pauses and
  claims resume at their deadlines; refresh failures remain retryable.

### Upgrade notes

- Use `make commit m="…" by=user` or `by=agent` for hand data changes. Run
  `make upgrade-ledger` to initialize an older ledger's audit boundary (format 2
  stays unchanged). Ask who made earlier changes, then backfill with
  `uv run --locked --project review-dashboard/server python scripts/ledger/audit.py record --commit COMMIT --actor user`
  (or `--actor agent`; `--changes FILE` batches them). After an interrupted append,
  run the refusal's exact audit-only restore command, then retry.

- Rebuild with `make dashboard` and reload for the Forecast chart-width fix.
  No ledger-format migration is needed.

- Rebuild with `make dashboard` and reload for the hand-form Notes input fix.
  No ledger-format migration is needed.

- Rebuild with `make dashboard` and reload for the Settings identity-label fix.
  No ledger-format migration is needed.

- Rebuild with `make dashboard` and reload for the Queue, Settings, setup and
  usual-payment Retry fixes. No ledger format change or migration is needed.

- Rebuild with `make dashboard` and reload the page for the Add autosave ownership
  fix. No ledger format change or migration is needed.

- Rebuild with `make dashboard` and restart the server for conservative statement
  bill linking and the Forecast/correction fixes. Review Forecast's restored
  unpaid commitments; unrelated later actuals no longer hide them. No ledger
  format change or migration is needed.

- Run `make dashboard` and restart the server for investment rules and statement
  payment linking. Existing format 2 rules need no migration. Review suggested
  contributions or use `make rules-backfill` for qualifying history.

- Rebuild the dashboard for the Add input fix and restart the server for
  failed-fix Save and isolated correction recovery. No
  ledger format migration is needed; an unsettled journal still needs manual
  reconciliation and is preserved for that work.

- Rebuild the dashboard with `make dashboard` and restart the server for explicit
  Edit entry intent, Queue selection, Rules sorting and daily averages. No ledger
  format migration is needed. Existing kept edits and proposals remain in Queue.

- Restart the server to load deadline/cancellation and recovery fixes. No data-format
  migration is needed. Existing failed receipt readings can now be dismissed;
  payments and their evidence remain saved. Recent diagnostic histories are capped
  at 50 runs when next written; audit records remain intact.

- Restart the server and rebuild the dashboard together for the new attachment
  limits and bounded upload scheduling. Allow temporary disk space for every PDF
  page. Prompt transport changes the agent fingerprint: run `make agent-check`
  and the operator's deliberate readiness probe before agent work resumes. No
  ledger migration or proxy body-limit increase is needed. Custom adapters should
  report deadline expiry as `AdapterError(kind="timeout")`.

Rebuild the dashboard and restart the server. No ledger-format migration is
required. Generated data is repaired at startup when its durable marker is stale.

Kept edit recovery now belongs to its draft rather than just the entry. Server-kept
forms remain in Queue; legacy browser-only entry-keyed copies are not guessed into
a different draft. Fixes with an older cancellation snapshot adopt that stored
proposal on their next claim. No financial store migration is required.

## [0.1.0] - Unreleased

The first release. The date is set when the release is tagged.

### Added

- **Ledger and checks.** JSON in a separate ledger repository is the source of
  truth; every save passes the JSON strict check (`make verify-data`), and hledger,
  when installed, checks the generated journal again in the background. Ledger format 2 is a typed store
  ([docs/STORE.md](docs/STORE.md)): every entry, posting and rule is written in
  full, in kebab-case, with related facts grouped; Python reads and writes every
  document through pydantic models, Node validates against the schema generated
  from them, and each entry balances exactly in every commodity. `ledger.json`
  holds settings and `data/accounts.json` account metadata. Format 0 and 1 ledgers
  upgrade with `make upgrade-ledger`, which compares outputs and rolls back on
  failure; the 1 → 2 step prints counts only, turns inexact unit costs into exact
  totals and stops, writing nothing, on anything the models can't hold.
- **Extensions.** Ledger-specific entry fields are named, typed extensions
  ([docs/EXTENSIONS.md](docs/EXTENSIONS.md)): the built-in `fuel`
  (`extensions/fuel.json`) and a ledger's own in its `extensions/`, listed in
  `ledger.json`. The Add agent fills an extension's agent fields when asked; entry
  details and Edit entry show every field generically; the journal prints each as
  a comment tag. `make extension-update NAME=…` brings every entry in step after a
  definition or listing changes. Format 1's policy term becomes a ledger extension.
- **Accounts and currencies.** Each account keeps its own currency; totals and
  charts stay separate per currency, and conversions are asked for only when an
  expense's currency differs from the paying account's. Accounts lists one line per
  account; each group heading (Banks, Credit cards, Prepaid, People, Loans, Cash)
  opens a group view with its total over time per currency, a table of the
  accounts' figures for the period (including the last statement-confirmed balance
  or filed card statement) and the combined activity, with transfers within the
  group behind a switch. The dashboard data carries the confirmed balances and card
  statement summaries this needs.
- **Dashboard.** Add, Queue, Review, Explorer, Atlas, Journal, Forecast and
  Accounts, with Rules, Trash and Settings, in Light and Dark, built from generated
  data (`DASHBOARD_DATA_DIR` chooses where it goes).
- **Add with an agent.** Typed words, receipts and statements become drafts in a
  per-ledger Queue. A restricted agent proposes entries; the server validates and
  writes them with an audit line and a reviewable commit. Adapters for Codex,
  Claude Code, Pi, OpenCode and a direct API, each pinned and checked offline.
  Drafts survive agent outages and can be finished by hand.
- **Recurring rules and bills.** Rules guessed while typing (two distinctive shared
  words, or one with the rule's amount; a single distinctive word is only a
  suggestion, and category, account-kind and widely shared words count only as a
  rule's whole name) or
  made from repeated payments; rules whose amount varies, with saved bills that
  stay unpaid until a payment is recorded.
- **Corrections, Delete and Trash.** Saved entries can be corrected, deleted within
  the ledger's time limits and restored from Trash with their evidence.
- **Statements.** A reader interface (version 1) with generic CSV and spreadsheet
  readers, an example card statement reader for a fictional layout, bank statement
  summaries and confirmed balances.
- **Review on the server.** Possible duplicates and other decisions are applied by
  the server with audit lines; periodic reviews follow
  `review-dashboard/server/WEEKLY-REVIEW.md`.
- **Optional modules.** Investments with price sources, an older-ledger lookup and
  the weekly review, each set in `ledger.json`; fuel is the built-in extension.
- **Tooling.** `VERSION` as the single application version (`make check-version`),
  `make doctor` for offline prerequisite checks (it warns when jj is missing or the
  selected ledger is a git repository without jj, since saves need jj), fictional fixture ledgers
  (`make personas`), offline test suites with fake agents, headless browser
  suites, and a personal-data scanner (`make scan`). `npm ci` runs only when the
  installed packages differ from `package-lock.json` by version (`make install`
  covers both dependency sets).
- **The JSON strict check.** `scripts/ledger/strict_check.py` checks on the store
  what hledger's strict check finds on the journal (balance exactly, no implicit
  conversion, balance assertions, declared accounts and commodities, date order,
  recurring rules and their forecasts) without starting a process. It is the gate
  in `make verify-data` and in the server's previews and saves. Agreement tests run both on
  every fixture, on generated large ledgers and on one mutation per check kind.
  Node's validator refuses the same documents as the Python models.
- **hledger as the background second check.** hledger is optional. Saves no longer
  wait for it: `make commit` sets `EXPENSES_HLEDGER_CHECK=background`, and the server
  checks each commit afterwards, coalesced (one running, one waiting, always the latest
  commit), from a snapshot of the committed store without the ledger lock, and at
  start-up. The result is kept in the ledger's ignored `drafts/ledger-check.json`. When
  hledger finds a problem, every screen shows a banner, Queue › Needs you shows a Ledger
  check item in plain words (with hledger's own text under Details and the entries
  changed since the last good check), and statement Apply and confirmed balances wait
  for the accounts involved. The item offers Ask the agent to fix it (a proposal in Edit
  entry that changes nothing until the person saves it), Edit the entry and Check again,
  or Copy details when the app and hledger disagree; it closes when a check passes.
  `make verify-data` and `make verify` run by hand still run hledger at once and report
  a failure in the same words; without hledger they skip it with a note, and so does
  `make upgrade-ledger` (it skips hledger's check and report comparison only). `make
  doctor` warns instead of failing when hledger is missing.
  Continue creates accounts outside the draft mutex and revalidates before dispatch,
  avoiding a deadlock with Trash polling. Apply reads refusal and pending state
  together, passing checks resolve concurrent fix answers safely, and first-run
  import follows late check results. Queue holds new check rows during interaction;
  Check again retains the explanation when a last-good commit is missing.
- **Save timings.** Each save writes one `save-timing` line to the server log with
  the duration of every step (pickup, prepare, lock wait, journal, store check,
  the JSON strict check, jj, data rebuild, total; hledger only when kept in the commit);
  nothing is written into the ledger.
  `make bench-save` times representative saves on a large fictional ledger from
  `scripts/fixtures/generate_large.py` in `/tmp`, with the fake agent, and exits
  non-zero when a target is missed by more than 50%; `BENCH_ENTRIES=100000` is a
  stress run without targets.
- **Updates and releases.** `make update` moves an installation to the newest
  release tag: doctor, a clean checkout, the changelog since the installed version,
  confirmation (or `--yes`), a detached checkout of the tag, the ledger upgrade,
  verification, the dashboard build and the offline tests, and a restart of a
  service named in `local.json`; any failure after the checkout restores the previous
  checkout, the ledger checkpoint and the dashboard. `make upgrade-ledger` runs a
  chain of migrations, rehearsing it on a scratch copy first. An optional update
  notice in Settings (off unless `local.json` turns it on) reads the remote's tags at
  most daily. GitHub workflows run the scans and offline suites on every push, the
  headless browsers (in Light) and the update test on tags, by hand and weekly,
  propose agent CLI pins weekly and publish releases from tags; see
  [docs/RELEASING.md](docs/RELEASING.md).
- **Setup.** [SETUP.md](SETUP.md) walks a coding agent through setting up a ledger.
  `make new-ledger` creates one from `templates/ledger/` as its own repository with a
  verified first commit; `make add-account` and `make opening-balances` declare
  accounts and opening balances under the ledger lock; `make demo` runs a fictional
  ledger from `/tmp` with an offline fake agent; `make serve` starts the dashboard
  locally at `http://127.0.0.1:8701/expenses/` (`/expenses/`, where the build loads
  its files, is also the server's own default base path); `make private-checks` runs a
  ledger's own `checks/`. A ledger with no
  accounts opens the first-run screens in the dashboard: About you, then either
  Start from today (accounts and opening balances) or Bring in past statements
  (files read by built-in readers or the agent, each with its account and currency),
  then Add. Deployment examples
  for local use, systemd, launchd, a reverse proxy and a tailnet are in
  [docs/deploy/](docs/deploy/README.md).
- **General policy.** The general bookkeeping policy is
  [policy/BOOKKEEPING.md](policy/BOOKKEEPING.md); a ledger's `AGENTS.md` keeps only
  personal rules. The server gives the dashboard's agent both, read on every run.

### Upgrade notes

There is no earlier release. For a ledger made during development before this
release:

1. Run `make install`. Format 2 needs `pydantic` from the server's locked
   environment, which the ledger commands now run in (`LEDGER_PYTHON`); after this
   they work offline.
2. Run `make doctor`. Fix every row marked `FAIL`; a `warn` on the Ledger row is
   expected for an older format, and on the hledger row when hledger isn't installed
   (it is optional).
3. Finish or discard drafts waiting in the Queue: their answers are in the format 1
   shape, which the format 2 answer check refuses.
4. Make sure the ledger's working copy is clean (`jj st` or `git status` in the
   ledger) and that nothing else is writing to it (stop the server).
5. Run `make upgrade-ledger ARGS="--dry-run"` and read the plan; the 1 → 2 step
   prints counts only. If it stops and lists accounts, supply metadata with
   `--accounts`, as [migrations/README.md](migrations/README.md) describes. If it
   stops with counts (for example "entries or rules repeating a label other than
   order-id in their notes"), fix those entries, commit with `make commit` and run
   it again; the ledger is unchanged until then.
6. Run `make upgrade-ledger` with the same arguments. Note the checkpoint commit it
   prints. A ledger that had `modules.fuel` now lists the `fuel` extension, and one
   with policy terms has `extensions/policy.json`.
7. Run `make verify`, then `make dashboard`.
8. Run `make doctor` again; the Ledger row should now be `ok`.
9. The general policy now ships with the application. If the ledger's `AGENTS.md`
   repeats general rules, keep only personal rules there (see
   `templates/ledger/AGENTS.md`) and commit with `make commit`. A full older policy
   still works; the agent then reads some rules twice.
10. If you ran the dashboard from an edited copy of
    `review-dashboard/server/expense-dashboard.service`, move your access settings
    into a drop-in as [docs/deploy/systemd.md](docs/deploy/systemd.md) describes,
    restore the tracked file, and run `make service` with `LEDGER_DIR` set.

To add or change an extension later, edit it and run `make extension-update NAME=…`
([docs/EXTENSIONS.md](docs/EXTENSIONS.md)).

To go back, use the previous application checkout with the checkpoint commit from
step 6.
