# Headless browser matrix

Run from the application root:

```sh
make browser-tests
ENGINES=chromium,firefox,webkit PERSONA=sam make browser-tests
make browser-capture
```

The default runs every engine and all four fictional fixture personas. Both targets capture the shell, notices, Settings, opening balances, currency views,
account dialogs and all step 4B Add/Queue and group-5 Review states. `CAPTURE=0 make browser-tests` runs assertions without captures.
`ENGINES` and `PERSONA` accept comma-separated subsets.

Use the installed Playwright **1.64.1** at `~/.cache/expense-playwright`. Chromium
uses its explicit headless-shell executable; Firefox and WebKit also launch
headless. Install that exact package and its three engine binaries before running.
On macOS, Firefox’s CoreFoundation app-data home is also redirected to `/tmp`
with `CFFIXED_USER_HOME`, avoiding the real browser directory and the
[upstream macOS 27 launch issue](https://github.com/microsoft/playwright/issues/42768).
The harness refuses any other package version. It never launches a desktop browser.

Each run copies application sources and each selected fixture to a unique
`/tmp/agentexpense-5-browser-*` directory. Package links reuse installed Node
dependencies, while Vite caches and builds stay in the private copy. The installed
server virtual environment supplies Python dependencies. Each fixture becomes its
own jj-colocated repository with an isolated jj configuration. Settings and account
currency tests make real locked, verified commits only there. No source fixture or
application repository is committed.

The fixture host `serve.py` wraps the real production app and FIFO worker. Its
`/__test/*` controls exist only in that host: they configure the offline fake CLI,
seed temporary draft/statement records, and install or remove fictional opening
balances through the verified settings transaction helper. Agent notices exercise
the real gate and its signed-out, policy-pause and diagnostic-failure paths. Gate
status reads never invoke an agent. No model, login, authentication file or real
agent home is used. Requests outside the fixture's loopback server are blocked.

Profiles, downloads, build logs, server logs, traces, failure images, fake CLI and
agent state all stay under `/tmp`. Each server uses a free loopback port and is
terminated in `finally`; browsers and contexts are also closed on failure. Passing runs remove their staging folder; failed runs keep only failing-case
trace ZIPs. `BROWSER_RESULTS` in stdout retains the assertion matrix.
`KEEP_ARTIFACTS=1` keeps the full folder, including the atomically checkpointed
`results.json` matrix and capture metadata after each complete case, which can
help diagnose interruptions. The final gallery is produced during normal cleanup.

Screenshots and their contact sheet are written to
`$AGENTEXPENSE_TEST_ROOT/shots/index.html` by default; set `BROWSER_SHOTS` to
export them for review after cleanup. `manifest.json` records engine, persona,
state, theme, viewport and real route for every image. Exact 1440×1000 and 412×915
captures include Light and Dark, with full-page companions for tall screens. The
suite also checks every route and both dialogs at 320px. It opens the approved
mockup index from disk read-only as a packaging check; mockup sources are never
copied into the product.

Coverage includes navigation and badges, keyboard order and modal focus return,
Settings commits and output preservation, all notice states and agent action
blocking, empty views, Sam's independent currency charts and account groups,
opening-only relabelling versus fixed account currency, and Asha's investment
holdings, value/cost/gain, chart keyboard inspection and account drill-down.

On macOS, WebKit follows the system keyboard-access preference. Shell traversal
uses Option-Tab to include links and buttons when full keyboard access is off;
other engines use Tab. Dialog focus tests use ordinary Tab and Shift-Tab in every
engine, exercising the app’s explicit focus trap. Focus-order assertions are the
same for all engines.


Step 4B adds `workflows.mjs`: every persona/engine/theme/viewport runs real fake-agent
expense submission, account questions, failure/retry, expense conversions and
cross-currency transfers through the verified commit path. Statement PDFs are
fictional and generated in memory. The suite checks matching currency, blocked
mismatches, account reassignment, inline account creation, checklist decisions,
explicit estimate replacement and Apply. Test-only scenario/reset endpoints use
locked verified changes inside disposable repositories. They never ship with the
production app. Working/queue capture records are ignored test data; completed
real operations and audits are awaited before fixture reset.

The capture manifest maps each named state to its actual route. It includes Add,
Draft list/question/working/failure/empty, blank/filled conversions and transfers,
statement checklist, currency mismatch and inline account creation in all engines,
both themes and both viewports. Full-page companions keep fixed phone controls at
the document foot for inspection. The historical standalone Add/statement browser
scripts are superseded by this pinned, isolated matrix.

`BROWSER_SHOTS=/tmp/<directory>` selects a separate capture directory. This allows
independent persona invocations to run concurrently without clobbering manifests;
each invocation still creates its own application, ledger, ports, homes and profiles.
The default capture index remains `/tmp/agentexpense-5-shots/index.html`.


Phase 5 adds `review.mjs`: every engine, theme and size checks server counts,
important ordering, empty Review and native amounts. Asha exercises separate,
separation history, merge confirm/cancel, receipt/note movement and agreement
between two independent browser contexts. Populated personas exercise Follow-up,
Resolve, recovery after an unreadable successful response, idempotent retry after
reload, actual version-2 export download/import preview/apply and legacy saved-work
migration. The fixture host injects 2026-11-01 for review age calculations and resets
only its disposable ledger between cases. Group-5 captures are review-asha,
review-jordan, review-empty, review-separate, review-confirm and review-merged.

Step 6a adds `finish.mjs`: the complete matrix includes the durable pause notice,
claim-protected hand form, retained edits after release, arbitrary posting controls,
receipt upload, strict checks, human commit/audit and the exact new-root account
question. Captures are `pause-add`, `pause-usage-limit`, `finish-by-hand` and
`new-account-question`; form overflow also runs at 320px. A development-only
`BROWSER_FOCUS=finish` selects just these workflows; the required default matrix
runs every existing suite as well. No copy filter excludes an unanchored `ledger`
name: the application `scripts/ledger` is always staged. Source dashboard builds
and server suites must run sequentially because the byte guard protects `dist/`
and generated app data.

The harness pins `DASHBOARD_TODAY=2026-11-01` by default (override explicitly to
exercise a different date). Source personas end in October. Every engine, theme
and viewport verifies Explorer's empty current month, no invented currency
figures, one-step navigation back to October, and one final Forecast variable row
per active currency. Captures include `explorer-current-empty` and
`forecast-variable-whole-month` and `forecast-variable-days-left`. The latter
follows a real checked current-month hand addition. The empty-month check also
serves a deliberately stale data snapshot to prove the browser chooses today.
Node tests also pin mid-month dates and verify
remaining-day labels; worker tests inject time for the daily rebuild/backoff.

Step 6b2 adds `month-cutoff.mjs` to every hand-finish workflow. Captures include
`explorer-current-recorded`, `explorer-same-day` and `forecast-overdue-at-today`,
in every engine, theme, persona and viewport. The first uses the real checked
hand addition; the others advance the published date to mid-month and include
an explicitly fictional overdue payment in a routed data snapshot. Assertions
check the Explorer/category comparison cut-off, retained expected payment,
remaining variable days, chart date and matching projected/table totals. Node
regressions independently build real fixture data for the full financial
arithmetic, refunds, shorter preceding months and separate currencies.

Each disposable persona also gets its own `/tmp` hledger oracle cache for nested
ledger tests. It reuses successful CLI results only for identical input journals,
arguments and executable bytes; every verification retains every assertion.
This avoids re-running the same large external calendar forecast for each
unrelated checked browser addition. No real financial verification is bypassed.

Step 6c part 2 adds `rules.mjs` to every default matrix case. It creates fictional
rules/history only in the disposable ledger, drives Add's guessed/pick-list/
amount/duplicate/missing-payer/conversion states, commits real usual payments,
checks saved payments and automatic/paused follow-ups, and exercises every Rules lifecycle
operation, simple/complex editing, New rule, suggestion confirmation/dismissal,
review Keep, and built read-only mode. Every checked write waits for its actual
HTTP/worker result; the production request deadline stays unchanged. Captures
include each named `rule-*` and `rules-*` state. `BROWSER_FOCUS=rules` is a focused
development option; the required default run retains every existing suite.
The host uses the current UTC instant for agent/retention expiry while review-age
checks retain their fixed November 1 clock. Payment dates and built-data windows
remain explicit fixture dates; the browser records its own local save context.

The runner selects `.node-version` through fnm and prints the Node version. Each staged persona passes its own `DASHBOARD_DATA_DIR` and disables npm update notifications. `BROWSER_CASE=light-1440` selects one theme/size for focused work; leave it unset for the complete matrix. `BROWSER_PROFILE=1` logs suite wall times and ledger commit counts (including fixture setup/reset commits). Currency formatter checks cover every supported code in each engine.

Step 6d adds `corrections.mjs` to every default case, covering typed dates across
local date boundaries, monotonic declines across changed words/reload/server
recovery/pick lists, saved-entry corrections from Queue/Explorer/Journal, Back
targets, statement locks and receipt disagreement/Keep. Captures include
`usual-typed-date`, `correction-from-drafts`, `correction-saved`,
`correction-from-explorer`, `correction-from-journal`,
`correction-statement-locked`, `correction-explorer-saved`,
`correction-journal-saved` and `receipt-question`. Saves from Explorer and Journal
retain the saved result through a data refresh before Back returns to the entry.
`BROWSER_FOCUS=corrections` runs that focused suite during development.

Step 6e adds `add.mjs` and the revision-3 Add capture states: one input for expenses,
receipts and statements, editable interpretation, Examples dialog, stable measured
input position, focus/caret preservation, explicit polite announcements, guarded
three-result visit recovery, entry links and retention and both receipt answers above
the phone save bar. Statement readers and the existing expense/currency workflows
now use that same Upload input and Continue. Account creation is staged until
Continue. Password and checked-failure paths retain their file. Desktop captures
now use 1440×1000; phones remain 412×915. `BROWSER_FOCUS=add` selects this suite for
focused development; default matrix runs retain every prior suite.

Step 7B extends the default reader suite with an unmapped CSV interpreted in Add,
a cited reading through the offline fake adapter, source/totals checks, checked
Apply and opt-in mapping saved in the same transaction. It also verifies the next
upload recognises that generic mapping without an agent. Captures add
`draft-agent-statement` and `applied-agent-statement` for every persona, engine,
theme and size. `BROWSER_FOCUS=readers` selects the complete reader suite during
development. PDF/XLSX/XLS inputs are generated in memory by the maintained
`server/tests/cited_statement_fixtures.py`; no binary fixture is checked in.

`THEMES=light` or `THEMES=dark` selects themes beside `ENGINES` and `PERSONA`;
the default remains `light,dark`. `BROWSER_CASE` can still select one size/theme.
The implementation-step matrix uses `THEMES=light`; inspect Chromium Dark captures
for changed states at both sizes. Passing runs remove their complete staging root.
Failed runs retain only failing-case trace ZIPs. `KEEP_ARTIFACTS=1` retains the full
staging root for debugging. Machine-readable `BROWSER_RESULTS` stays in stdout;
contact sheets and captures stay in `BROWSER_SHOTS`, outside the staging root.

`BROWSER_FOCUS=confirmed-balances` runs Part A's usual/agent/hand/receipt refusals,
duplicate link, retained words, absent Retry, correction locks and same-day boundary.
The default matrix includes it too. Its seven capture states use a disposable
monthly bank statement in each persona's own currency; no source ledger changes.

Step 6f B adds `varying.mjs`: amount-required estimates, explicit paid/unpaid
choice, saved bills, unusual-amount acknowledgement, Rules history/editor and
exact bill versus estimated Forecast rows. `BROWSER_FOCUS=varying` runs this
workflow alone; the default suite includes it for every persona/engine/size.
Sam's workflow uses the secondary account currency. Use `THEMES=light` for the
required assertion matrix, and Chromium Dark captures at both sizes.

## Temporary files

Each test command creates one `agentexpense-run-*` root, shared by nested make
commands, Python pools, Node helpers, oracle caches and pinned headless browsers.
`TMPDIR` and `EXPENSES_TEST_TMP` point inside it. It is removed on success,
failure, SIGINT, SIGTERM and SIGHUP. Before removal, only failing-case trace ZIPs
are copied to a printed `/tmp/agentexpense-failures-<timestamp>/` path. A failed run also prints the
last 120 lines of each persona's server log between `SERVER_LOG_TAIL <persona>` and
`END_SERVER_LOG_TAIL <persona>`, so a failed request's traceback survives the cleanup. Explicit
`BROWSER_SHOTS` exports captures outside the root; `KEEP_ARTIFACTS=1` retains the
whole root, with test-helper teardown snapshots under `kept/`. The scanner's cleanup regression runs a small server suite, Node
suite and deliberately failed/interrupted browser cases and checks only their
owned paths, never a global /tmp inventory. Production server scratch is unchanged.

Part C timing coverage (`BROWSER_FOCUS=retention-timing`) holds real Queue polls,
autosaves, Trash lists, retention saves and time-limit previews open with routes.
It acts before releasing them and checks retained words, completed actions, stable
pointer targets and absence of stale rows/errors. A receipt date arrives during
the Delete confirmation. Node timer doubles cover the 3/30-second Queue intervals
and 30-second Trash poll; a server clock double checks Saved/Trash expiry boundaries.

The timing sweep adds `view-timing.mjs` to every default case (`BROWSER_FOCUS=view-timing`
alone). A Queue poll that sees a new addition reloads the dashboard data; each case holds
that data read open with a route, acts, releases it and checks that nothing was lost:
the open inspector and its focused Edit button, typing in Explorer's search, back/forward
to Journal during the read, an open Account details dialog, and a Settings name save
that must end on the saved name rather than the older held snapshot. It also holds the
first agent-status read while `?settings=1` has Settings open (nothing under the pointer
moves) and holds Retry's read after an unreadable first load.

Step 9-2 adds `first-run.mjs` to every default case (`BROWSER_FOCUS=first-run` alone).
`/__test/first-run` turns the persona's ledger into a fresh template ledger in its own
currency and locale (and configures the fake agent's cited reading), then
`{restore:true}` puts every original file back. Both paths run through the real
endpoints: About you, Start from today with a second currency, More kinds, validation
and Finish; Bring in past statements with a built-in reading, an agent reading, an
unread file, inline account creation, a currency mismatch that blocks Finish and its
resolution, then Finish. Timing tests: a controlled clock (separate context, because
`page.clock` belongs to the whole context) checks the 1.5-second reading poll; a held
poll answer never brings back a removed file; a held account creation keeps another
file's typed account and position; Finish held in flight sends one request on both
paths. Captures: `first-about`, `first-start`, `first-accounts`,
`first-accounts-mixed`, `first-accounts-more`, `first-import`,
`first-import-mismatch`, `first-import-resolved`, `first-done-today`,
`first-done-import`.

Timing sweep part 1 holds the confirmed-balance refusal's Discard (a verified Trash
commit) open in `confirmed-balances.mjs`: the input is free at once, the notice cannot act
twice, and words typed meanwhile survive the late completion. Node tests in
`tests/screen-timing.test.mjs` hold Review's 2-second poll, the Queue statement poll
(1.5 seconds) across Apply, the hand form's claim, autosave and refusal Discard, the
correction form and the rule pick list.

Step 6g adds `account-groups.mjs` to every default case (`BROWSER_FOCUS=account-groups`
alone). `/__test/account-groups` installs fictional Rowan accounts in the persona's main
currency through the verified settings transaction: two banks (one with a filed bank
statement and its confirmed closing balance, and a transfer between them), a card with a
filed statement, a prepaid wallet and a person who is lent money and pays it back;
`{restore:true}` puts the baseline back and removes the two statement files. The suite
checks the list's single-line rows at one fixed height (36px desktop, 44px phone),
truncation with the full name as tooltip and accessible name, currency codes after
other-currency amounts and inset focus; every heading opening its group (Investments its
portfolio) with each kind's columns and figures; arrow keys through headings and rows; the
transfers switch; one chart and total per currency (Sam and Jordan hold two); the phone
picker's `<Group> · all accounts` options; back and forward; and a data reload held open
while the period and switch change. Captures: `accounts-list-rows`,
`accounts-group-{banks,cards,prepaid,people,cash}` (Cash where present), `accounts-group-transfers`,
`accounts-group-mixed` (Sam) and, on phones, `accounts-group-picker`.
