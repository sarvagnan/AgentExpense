# Headless browser matrix

Run from the application root:

```sh
make browser-tests
ENGINES=chromium,firefox,webkit PERSONA=sam make browser-tests
make browser-capture
```

The default runs every engine and all four fictional fixture personas. Both targets capture the shell, notices, Settings, opening balances, currency views,
account dialogs and all step 4B Add/Queue and group-5 Review states. `CAPTURE=0 make browser-tests` runs assertions without captures.
`TRACE_SCREENSHOTS=0` keeps trace DOM/network records without screenshot streams;
WebKit trace screenshots are always disabled.
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

`BROWSER_FOCUS=ledger-check` runs hledger's background second check (`ledger-check.mjs`),
also in the default matrix. `serve.py`'s `__test/ledger-check` control feeds the check a
canned hledger result on the real snapshot (never a broken fixture): `hold` keeps a check
open until `release`, `fail` takes a kind, an account, `data` (the JSON check refuses it
too) and `once` (only the commit checked now fails), and `fix` gives the fake agent a
proposal. The suite holds the check while a save commits (no banner), releases a failure
(the banner arrives without moving the heading, the words or Continue; Review apply
waits with its reason), shows the Queue item for every kind and the app-problem case,
passes Check again (banner and item gone, Apply works), and approves the fake agent's fix
in Edit entry. Its captures are `ledger-check-banner`, `ledger-check-apply-waiting`,
`ledger-check-<kind>`, `ledger-check-app-problem` and `ledger-check-fix-proposal`.

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

UI fix round 1 adds `BROWSER_FOCUS=shell`: the default case's own section only
(navigation and badges, the sidebar footer and phone Views sheet, Settings as it opens,
its agent status slot for every gate state, the update notice, every route at 320px,
empty views, the agent notices on Add, Settings and Queue, the empty Queue and the
account currency dialogs), without the workflow suites that follow. It adds the
`settings-320` capture. `add-timing.mjs` adds a controlled-clock check of the offline
notice: one timed-out health read schedules one follow-up check two seconds later and
changes nothing on Add; only a second consecutive failure shows "You’re offline".

UI fix round 1 part 2 (area B) adds assertions to existing suites rather than a new focus.
`add.mjs` waits for each Just added outcome before its capture and checks that Not an expense
shows its explanation with no Queue link, that Added rows carry their amount, and the save bar's
reason for a disabled Continue (ambiguous file); `workflows.mjs` checks the mismatch reason and
that a statement's quiet file actions never touch; `varying.mjs` checks the varying-card reason
and that a saved bill links to its rule; `readers.mjs` checks a statement's Queue tag and that an
applied statement leaves the Queue list while focus stays in its detail; `retention.mjs` checks
the deleted notice below the H1 (and absent on Trash), readable Queue chips and a one-line phone
file row; `corrections.mjs` checks one Edit entry heading and the Attach files button; `review.mjs`
checks the styled result notice. The fixture host's `reset-workflows` now waits for a Discard's
pending audit commit instead of failing, and transfer words use account labels.

UI fix round 2 adds `runViewCaptures` (in `capture.mjs`) to every default case, before the
workflow suites; `BROWSER_FOCUS=views` runs it alone. It captures the looking-back views as a
person first sees them: `explorer-month`, `explorer-inspector` (opened from a row),
`explorer-keyboard` (moved with ArrowDown), `explorer-no-match`, `atlas-month`, `journal-month`,
`journal-inspector`, `forecast-overview`, `forecast-rules` (both on first open),
`accounts-default` and, on the phone case, `explorer-320`, `accounts-320`, `journal-320`,
`forecast-320` and `atlas-320`. Every capture parks the pointer away from the control it just
used. Its assertions: the twelve-month chart is drawn at its rendered width on the caption
size and ends at the last complete month with whole ticks; one Show All with no match; one Atlas
title; the phone Journal row (amount on the title's line, route beneath, headings aligned);
Forecast's comparison select names the window its figures use, nothing says "0-month", line-end
labels clear each other and every dot, no month-table header or label is cut, and the selected
Rules tab keeps at least 4.5:1 contrast while hovered and pressed. `BROWSER_FOCUS=area-c` runs the
default case's shell section, then the view captures, `runFinish`, `runRules`, `runCorrections`,
`runConfirmedBalances`, `runRetention`, `runAccountGroups` and `runVarying`: the states a
looking-back regrade needs, without a grader-only harness copy.

CI round 1 adds `held.mjs`, the shared helpers for timing tests. Every held or forwarding
route uses `hold` or `forward`: a fetch that fails (a reset connection or a closed server)
aborts the request and fails only the test that waits on it, never the whole run, because
Playwright turns an error thrown in a route handler into an unhandled rejection. A GET whose
reused keep-alive connection was reset is sent once more; the test server also keeps idle
connections for 15 minutes. `settleTasks` lets the tasks a render has queued run before a
paused `page.clock` advances, so a timer the app starts in an effect is not missed.
`tests/held-routes.test.mjs` checks the helpers with route and page doubles. GitHub CI
runs the browsers in Light only (`THEMES=light`); see `docs/RELEASING.md`.


`BROWSER_FOCUS=immediate` forces a direct request to wait before reaching the
server, then exercises failure and Retry, newer typing, FIFO saves across closing
and reopening Settings, focus and 2 px geometry, and the completed-draft Add
restore race (ae-yu0). The deliberate 422 is excluded by its exact expected
console message only after its refusal assertion passes. Other console/page
errors remain failures. The full suite also holds every actual direct-write
request before forwarding it and records each action family’s pending state;
`CAPTURE=1` captures those states on their actual routes. The shared queue’s unit
tests force failures, replay identities, duplicate submissions, interrupted-page
recovery and asynchronous committed-result polls with a controlled 250 ms clock.
The first-run fixture reset clears only its test browser’s recovery copies.


`BROWSER_FOCUS=direct-states` forces held refusals and Retry through hand additions,
corrections, each rule lifecycle action and rule editing, unpaid bills, Delete,
Restore, permanent removal, statement Apply, Review decisions and first-run
About/opening writes. It captures pending and failed states and checks input,
focus and ≤2 px geometry across each refusal. It also checks zero/negative costs
through the actual Edit form. These cases run in the full suite.

`BROWSER_LARGE=1 BROWSER_FOCUS=latency CAPTURE=0 THEMES=light PERSONA=asha make browser-tests`
generates the large fictional ledger inside the browser test root. Five held
Settings saves per engine and size measure pointer-down to a computed pressed-state frame checkpoint and
click to a pending-DOM frame checkpoint (not physical display/GPU paint). `IMMEDIATE_LATENCY` output retains every sample; no CPU
or network throttling is applied.

Action timing captures use the exact viewport only: a full-page resize can
trigger the application’s mobile viewport observer. Other capture helpers
restore document scroll after their full-page companion. The forced stale-publication Settings regression also saves back to
the old published name before refreshed data arrives; both writes must occur.
A held usual-payment failure and shelf-completion regression checks that newer
words autosave to a different draft and never rewrite the original audit words.

`BROWSER_CAPTURE_PREFIX=immediate- CAPTURE=1` keeps captures for changed action
states while running every assertion in the full suite. It does not select or
skip tests. Without the prefix, every requested capture is retained.

The Add timing suite holds intake submission while newer words remain editable,
then separately focuses Examples before releasing a save. The ordinary
next-entry focus return must work without stealing that newer chosen focus.
Committed-result failures preserve structured confirmed-balance refusals, so
the existing recovery notice remains the authority rather than a duplicate
generic save-bar error.

The controlled-frame `useLiveFocus` regression checks that background bar or
refresh-notice measurements cannot scroll an older caret while the person is
pointing elsewhere. A new focus or explicit viewport resize still reveals it.
The held receipt-choice case checks actual phone Upload coordinates while
the result becomes shorter.

The immediate FIFO case trial-clicks Add's next Continue while Settings is held,
so a fixed shelf cannot cover that phone action. Local pending/failure feedback
keeps the shelf out of the current form's controls; the hand Retry case checks
the small feedback Retry as well as the main action. Response waiters attach a
rejection handler before clicking, so a failed target check retains its original
trace instead of surfacing only the later page-close rejection.

The first-run suite also holds account creation before the server, injects a
refusal, then retries while a newer account name is typed. It checks retained
fields/focus and 2 px input/button/heading positions, and captures
`immediate-setup-account-{pending,failed,saved-newer-fields}`. The server regression holds a writer and makes a full linked-store load fail:
words-only interpretation must still answer from its two atomic documents,
without taking the writer lock.
While the retried declaration is held, the first-run test also selects another
valid file account. The late declaration must preserve that server-side choice;
only an explicit subsequent selection assigns the newly created account. A held
hook test covers unchanged, newer-choice and unmounted import completions.
The retained-state unit regression checks that typing and adding opening rows
keep a stable setter, preventing dependent initialization effects from restarting.

`BROWSER_FOCUS=account-currency` creates a disposable opening-only account in each
persona and holds its real dialog save before the server. A refusal and Retry
must retain a newer selection/focus, keep controls within 2 px, acknowledge only
committed currency and permit an explicit save back. It captures
`immediate-account-currency-{pending,failed,saved-newer-selection}` and also runs
inside the immediate-action suite in the full pass.

Review regressions use `BROWSER_FOCUS=direct-states`, `account-create`,
`review-writes`, `data-departure`, `first-run` and `immediate` while developing in one engine and
persona. The action coverage table is `action-coverage.mjs`; each listed control
needs a held refusal and a held committed retry, exact URL/method/result checks,
its own pending notice, named usable controls and geometry/focus evidence across
two animation frames. Direct DELETEs and draft/statement restores are observed.
Injected HTTP errors are excluded only for their registered exact request URL
and exact status text. Worker result reads are also held, including cancelled
Apply returning to review.

The first-run fill helper asserts every value immediately and counts input events.
A data rebuild and opening-row insertion/removal are forced between typing and
blur. The fixture reset atomically writes empty Trash instead of unlinking a path
a concurrent list read may already have enumerated. Data departure cases hold a
read through full-document navigation, exercise back/forward and forced persisted
page events, and require a real load failure to remain visible and retryable.
Only if WebKit still logs the narrowly specified teardown message may its
navigation-only exception be used; ordinary load and console failures stay errors.

The complete case enforces all 42 action IDs in `action-coverage.mjs`.
`BROWSER_FOCUS=review-round` runs just these changed states, including lifecycle
and setup checks, for Dark captures; it also enforces the table.
`BROWSER_DIRECT_REMAINING=1 BROWSER_FOCUS=direct-states` skips the already checked
Hand/rule/bill group during focused development. It must not be set in the final
complete pass. The lifecycle case holds both status and generation-gzip reads.
Actual history navigation and explicitly dispatched persisted events are logged
separately; a forced event is not evidence that the browser used its BFCache.
No WebKit navigation console exception is currently installed.

For targeted development, `BROWSER_DIRECT_REVIEW_ONLY=1 BROWSER_FOCUS=direct-states`
runs the worker-result, Review and setup group. `BROWSER_FOCUS=recovery-settings`
runs lifecycle departure followed by Settings/reload recovery, including cleanup
through an opaque document. The lifecycle event logger is installed only on HTTP
documents; errors from the application are still collected normally.

The ordinary Review lost-response case checks owned failure/Retry in the active
dialog and the exact persisted request body. It expects the recovered-outbox
panel only after reopening, then retries the same request ID and requires one
resolve audit event. The usual-payment completion check keeps newer Add words
visible while paid history arrives; fixed and varying guards follow the action
row so that refresh cannot displace its controls.

Second-review coverage requires a visible owned Saved acknowledgement or the
actual destination, and checks state/action/commit against the real response or
final server read. Queue/statement DELETE instead requires 204 and the exact Trash
item. The table rejects event-only evidence. The first pending wait requires a
rendered visible slot. Only forms/rows that close may remove measured nodes.
Relevant controls are named per row; only Restore entry, Restore Queue item and
Restore statement use sidebar Queue because the lone Trash row and Empty Trash
actions are disabled during that operation. Rule cases use Refresh rules; bills
use Pay by; approval uses Answer the agent instead; account creation uses the
statement’s Discard, Queue/statement discard use Queue Refresh, and Review
separation uses Export decisions.

Hand addition holds a real added result. Statement cancellation waits for the
exact cancel response, holds its real review result without rewriting it, and
requires quiet acknowledgement. It queues a real Settings write behind accepted
Apply and proves Cancel bypasses it. `fillChecked` logs input-event counts before
its value assertion, including the full WebKit/Sam shard.

Immediate-state captures also retain full-page companions when content is taller
than the viewport, so a focused phone field cannot hide the action’s evidence.

The Hand result hold also injects one exact-URL HTTP 500 after POST acceptance,
requires Still in Queue with unchanged geometry, and asserts no claim request.
It then releases the real added result. `BROWSER_DIRECT_HAND_ONLY=1` selects this
Hand/correction group for development and must be unset in the full pass.

Statement discard completion requires its current card to disappear, the visible “Item moved to Trash” notice, and no stale alert. The permanent Statement details region is not expected to detach.

The `post-direct` focus resumes the suites after direct actions for a primary case that stopped earlier. Combine its evidence with the completed prefix; it does not rerun the matrix. New-rule outcome checks use explicit Refresh rules after the test closes its retained editor, so a still-running background list refresh cannot race the interaction snapshot. Import outcomes compare each session ID, state and commit after sorting by ID.
The held-action helper explicitly scrolls the chosen editable input into view before focusing and measuring its baseline. Release/failure geometry remains bounded by 2 px; this avoids measuring a deferred browser focus scroll as application layout movement.
The focused `BROWSER_DIRECT_RECEIPT_ONLY=1 BROWSER_DIRECT_REMAINING=1` probe selects the receipt group. Ordinary direct-state runs now enforce that every owning form clears its visible alert after commit, including receipt choices; the action table requires `ownAlertsCleared` for every row. Receipt rows also wait for their completed reading to replace the choice controls. The maintained `receipt-replay.test.mjs` contains the original stale-error regression.

`action-alerts.mjs` checks the owning form and the exact earlier refusal outside portals, without treating unrelated reader warnings as that job’s failure. Shared completion tests cover scoped subscriptions, coalesced owners, newer identical-message errors, and explicit opening Continue.


`BROWSER_FOCUS=route-targets` reruns the nine route/empty-state target checks at the selected size and 320px. It measures the DOM rectangle's rendered height against the same 44px minimum: Firefox's protocol bounding box can round a 44px control to 43.999969px at a fractional screen position. This does not change the 2px action geometry checks. Statement fixture filenames use the context's media color scheme, so Light and Dark never reuse an undefined theme filename. Review Separate waits for the resolved card to disappear and checks its real success destination, connected heading and outside focus.

`BROWSER_FOCUS=post-add` resumes only the suites after Add, through first run, with the complete action-table gate. Combine it with the retained primary prefix and an Add rerun; it does not restart the matrix.

Fourth-round targeted runs use `BROWSER_FOCUS=review-round` in every engine/persona at both sizes, plus one complete Chromium persona. Hand begins with a claimed queued draft and forces a failed-before-server POST before Retry. Statement Cancel forwards the actual POST then loses its response; a second locked statement in the same mounted view retains usable controls, newer password, focus and 2px geometry while the first job ends. Unit regressions additionally force reload replay, operation-specific markers, transient reads/404, known committed purged import results, late success/refusal/mapping ownership and one cancellation announcement.

The final cancellation probe holds the server-confirmed Cancel response while Apply finishes with another session selected, returns to the original session, then loses the reply. It requires the original Discard to remain busy until that reply ends, then become enabled without a false alert. Cancellation confirmation is tested independently of whether an announcement was shown.


`BROWSER_FOCUS=attachments` runs the attachment-only regressions (also included
in `BROWSER_FOCUS=add` and the default full case). It chooses thirteen fictional
files, holds the first three native upload responses in the fixture server, cancels the thirteenth while it
waits, checks the remaining row's document position and newer typing/focus, then
requires twelve completed uploads. It fills the same item to fifty through the
real upload endpoint and checks the next file's own refusal and Retry row.
Controlled fake timeout events produce a real Failed Queue item with the
configured duration; its actual Retry must reset the count. Captures are
`attachments-held`, `attachments-limit` and `attachments-timeout-failed`.

The upload hold stays in the disposable server rather than `route.fetch`: the
pinned WebKit interceptor replays multipart requests without their file bodies.
This keeps real browser uploads intact and forces the same held-response race in
every engine.

The attachment focus also checks affected Queue controls: real one-second local
deadlines, a held Retry after its first timeout (queued/count 1), receipt Dismiss and
saved-payment totals, and resolved ledger-fix notices/Discard. Provider error text is
never used to simulate a local deadline.
