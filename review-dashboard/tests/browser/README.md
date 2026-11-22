# Headless browser matrix

## Remote browser containers

`make browser-box` runs the existing harness on an already configured Incus
remote, with one throwaway container per case. The Mac runs only Python, Git and
the Incus client; it does not install dependencies or launch browsers. Requires
Python 3.11+, Incus and an amd64-capable Debian 13 container host. Set the remote
explicitly; the runner never changes client configuration or the active project.

```sh
export BROWSER_BOX_REMOTE=<configured-remote>
# Optional: BROWSER_BOX_PROJECT (default agentexpense-ci), BROWSER_BOX_POOL
# (existing pool, default default), BROWSER_BOX_BRIDGE (existing bridge, default incusbr0).
make browser-box ARGS='--build-only'
make browser-box ARGS='asha:chromium:light:1440 asha:firefox:light:412'
make browser-box ARGS='--checkout /path/to/checkout --jobs 2 sam:webkit:light:1440'
make browser-box ARGS='--target browser-capture --focus rules --capture-prefix rules- asha:chromium:dark:1440'
```

Commands start a **detached controller in its own session** and return after
launch. Use a new output folder when you need predictable follow-up commands:

```sh
make browser-box ARGS='--output /tmp/agentexpense-browser-box/comparison asha:chromium:light:1440'
make browser-box ARGS='--wait /tmp/agentexpense-browser-box/comparison'
make browser-box ARGS='--status /tmp/agentexpense-browser-box/comparison'
make browser-box ARGS='--stop /tmp/agentexpense-browser-box/comparison'
```

`runner.pid`, `runner.json`, `launcher.json`, `launch.json` and `runner.log`
belong to that output folder. `--wait` follows the log and returns the final job
exit code; launch success alone does not mean a case passed. Killing a shell or
follower leaves the controller running. `--stop` sends SIGTERM only after
verifying the local controller's identity and writing a durable stop request.
Sending SIGTERM, SIGHUP or SIGINT to the controller cancels active cases, collects
available partial logs/results/traces and cleans up its exact owned instances.
Cancellation records `stopped` (exit 130) without a rerun. Repeating `--stop`,
including during launch or collection, is safe. Evidence collection and cleanup
share a 45-second stop budget; a disconnected server can prevent cleanup, which
is recorded for recovery. A guest that finished before cancellation keeps its
test outcome, including when collection is interrupted. Summaries are
checkpointed after every attempt. An early `--stop` writes its request even
before controller state exists. An in-flight launch is allowed to return within
the stop budget before exact-name cleanup, so delayed creation is included.

Each case is `persona:engine:theme:width`: the four fixture personas, Chromium,
Firefox or WebKit, Light or Dark, and 1440 or 412. Pass any list of cases; there
are no implicit matrix expansions. `--jobs 1` runs serially; values above 2 are
refused. A test failure gets exactly one unchanged rerun using the same bundled
source and image. Infrastructure replacements do not spend that rerun;
`--infrastructure-retries` defaults to 2. `--timeout` defaults to 7200 seconds per
case, shared by its attempts, reconnects and capacity waits; evidence collection
and cleanup have a separate bounded grace. `--build-timeout` defaults to 3600
seconds. Project capacity is checked before launch;
full-project and quota-race responses wait two seconds and retry without using
up a case attempt. Cancellation interrupts that wait. Other launch errors still
record an infrastructure failure. Failed cleanup is recorded for the operator.

Inside each container, an idempotent detached supervisor runs the existing
harness with output in `run.log`, an atomic status file and a persisted exit
code. The controller polls that status; no browser output depends on an open
Incus exec stream. A guest watchdog enforces the remaining case budget even
without the controller, stops the process tree and collects available evidence.
Watchdog expiry records `infrastructure` (exit 124) and ends the exhausted case.

Transport timeouts, refused/reset/closed connections, lost event listeners,
`context cancelled`/`context canceled` disconnects,
unreachable routes, TLS handshake timeouts, unexpected EOF and HTTP 502/503/504
responses retry with 1/2/4/8-second backoff, capped at 8 seconds. After the first
successful remote response, DNS failures (`no such host`, temporary name-resolution
failure, `server misbehaving` and lookup timeouts) also retry. Before that response,
a DNS failure stops startup promptly with a configuration diagnostic. Lost
server-side operations (`Operation not found` on exec or file transfer) retry the
whole operation; a status read with a nonzero exit is repeated even if it printed
valid JSON. The reconnect window defaults to 120 seconds (`--reconnect-timeout`),
bounded by the remaining
case budget. It limits starting another retry after a transport failure; an
operation admitted within that window can finish after it. Authentication, trust,
missing-project and missing-instance errors are not transient transport failures.
An uncertain launch is reconciled by exact name and owner before reissuing it. A surviving guest resumes
polling and collection; a lost guest records `infrastructure` before replacement.
After a started guest exhausts a reconnect window, transient status recovery
continues against that same guest until the case deadline; it is never stopped
or replaced just because the connection window expired. Collection and cleanup
of that survivor also retry transient failures until their enclosing case
deadline, rather than giving up after one reconnect window. Collection uses the
remaining case budget or a fixed ten-minute grace from completion, whichever is
longer; recovery shares that same deadline. Its archive stays in the guest until
collection completes while that budget allows. The existing
bounded evidence/cleanup grace applies if the case deadline has already elapsed.
Permanent poll errors (authorisation/trust failures or deleted projects) fail
promptly as infrastructure, retain the cause and set
`retryable=false`; they neither poll for the whole case budget nor relaunch the
same broken configuration. Other unexplained status errors, including invalid
JSON, allow three consecutive failures within the case budget before recording
retryable infrastructure; a successful status resets that count. A surviving
guest found during recovery resumes polling. A recovered terminal status is
persisted before collection/cleanup decisions. Test exit is already final while
the guest is `collecting`, including if the supervisor dies during collection.
Deferred cleanup runs in a separate worker and its errors never fail another
case. Every run ends with a sweep of its own owner tag. If the server stays unreachable, `cleanupPending` and
`cleanupError` remain in `runner.json`; the guest watchdog still ends the case,
and the next same-host controller performs the usual stale-owner sweep.

Each local client has a separate watchdog that enforces its absolute deadline
and checks its controller's PID/start identity. Client workers register their
own exact PID/start before exec. A later same-host controller reaps registered
orphans by those identities, preserving unknown/reused PIDs; process names are
never used to find or kill clients. The local registry is under
`/tmp/agentexpense-browser-box/client-registry/`.
New registries use mode 0700. Creation, use and reaping require the current UID,
no group/other write permission, a real directory and trusted ancestors (apart
from the system's sticky temporary root). Registry records must also be regular,
operator-owned and protected from group/other writes before they can cause a
signal. Run folders remain trusted operator-owned state; these checks do not
make a writable shared run tree a multi-user control interface.

Transfers, instance launch/image use, supervisor launch and status/list requests
use the enclosing case/build budget, without a separate short request timeout.
This also applies to a healthy transfer after a reconnect: backoff never shrinks
its transfer deadline. Cancellation still interrupts client waits. Restarted
pulls use a separate incoming file and retain the largest received prefix until
the complete file replaces it. The guest archive stays intact during retries.

A finished guest's outcome and exit are persisted before collection. Collection
and cleanup errors cannot change that test outcome into an infrastructure
failure or launch another guest. `collectionError` records unavailable evidence;
collection retries address the same guest/archive. A failed test with such an
error ends without an unchanged rerun; successfully collected test failures
retain the usual rerun. The overall job exits nonzero for unresolved collection
errors even when its test outcome is `passed`. After the bounded collection and
fallback grace, exact-name cleanup still runs and local partial files remain.

Everything created lives in the selected project (default `agentexpense-ci`),
with independent profiles/images, aggregate limits of 4 CPUs, 6 GiB and two
containers. An existing project must already have those settings; the runner
refuses to alter it. No profile, network, pool, server setting or default project
is changed. The selected pool and bridge must exist already. Every Incus command
names its remote and project. Cleanup lists and checks an exact instance's
invocation token before removing it.

Startup reports the project's instance owners. New instance tags record a
random owner token, hashed stable host identity, controller PID and process
birth identity. A same-host owner proven dead (including a reused PID) is stale;
the runner rechecks its tag and liveness before exact-name removal. SIGKILL or a
host restart cannot run a controller's cleanup, so the next controller performs
this sweep. Alive, foreign-host, legacy and uncertain owners, and untagged
instances, are preserved. Darwin uses libproc start timestamps; Linux combines
machine, boot and PID-namespace identity, with proc start ticks for processes.
Old Linux boots/namespaces intentionally remain unknown. Permission failures
mean unknown, never dead. Concurrent stale sweeps tolerate already removed
instances. SIGKILL or an unreachable server beyond the stop budget can leave an
unacknowledged creation/instance for the next same-host sweep; another host
preserves it as foreign rather than guessing ownership.
A slow launch on a reachable server can also consume the shared stop budget,
leaving too little time for cleanup. There is currently no separate cleanup
reserve for an unacknowledged creation: cutting the client off earlier can lose
the creation response and itself leave an instance. Such cases remain recorded
for the next same-host sweep; guaranteeing more requires server-operation
cancellation/reconciliation, rather than a shorter local request timeout.

Images remain unless pruning is explicitly requested. Only run pruning after
other controllers have finished:

```sh
make browser-box ARGS='--prune-images --output /tmp/agentexpense-browser-box/prune-1'
make browser-box ARGS='--wait /tmp/agentexpense-browser-box/prune-1'
```

Pruning lists this project's `browser-box-*` aliases, retains the current
checkout's image hash and removes obsolete images only by exact alias after a
fresh provenance check. Images with unknown provenance, other aliases or the
current alias are preserved. A nonempty project refuses pruning. `prune.json`
records the actions; ordinary runs never prune images.

The networked build container installs Node from `.node-version` through fnm
1.39.0, uv 0.12.13, Python 3.14.7 with the server's locked dependencies, hledger
1.52.4, jj 0.47.1, and Playwright 1.64.1 with Chromium's headless shell, Firefox,
WebKit and their system dependencies. Distribution sources are Debian, the
tools' official releases, Node's distribution, Playwright's distribution and the
npm/PyPI registries required by the package locks. Application package lifecycle
scripts are disabled. These downloads happen only in the builder. The alias
`browser-box-<hash>` includes `.node-version`, `package-lock.json`, `uv.lock`, the
installer and tool pins. An unchanged hash reuses the image. The builder is
deleted after publishing; build failures also clean it up. Node, Python and
package dependencies remain at stable absolute paths in the image.

fnm, uv and jj archives are checked before extraction against SHA-256 values
pinned beside the tool versions, as hledger already is. Pin/installer changes
change the image hash; unchanged inputs still reuse the image.

The builder includes `build-essential` for locked Python packages that need a
C linker and system `python3` for Make's toolchain resolver. Before publishing,
the version step requires `python3`, `make`, `cc` and `git`, executes the actual
`scripts/node_toolchain.py` and checks its selected Node version. Those two
toolchain source files are also image-hash inputs. Image metadata records these
capability checks. The server maps browser-emitted IANA aliases such as
`Asia/Calcutta` to current names itself; the image still installs Debian's
`tzdata-legacy`, and the version step checks that alias and every persona's
timezone with the server Python. Assertions and browser locale settings remain
unchanged.

Run containers have **no profiles and no network device**, with 2 CPUs and 3 GiB
each. They inherit neither the builder's NIC nor any default-profile NIC. The
runner checks their expanded devices before sending source. Loopback serves the
headless harness; package managers run offline with Python sync disabled. The
system timezone is `Asia/Kolkata`, and locale is `en_US.UTF-8`; the browser
contexts retain each persona's existing locale/timezone. DejaVu, Liberation and
Noto fonts cover fixture text and currency symbols. Linux system-font geometry
can differ from macOS; assertions are unchanged. Trace screenshots are disabled
for every engine; no video, screencast or visible browser is enabled.

Source comes from `--checkout` (default caller cwd), using
`git ls-files -co --exclude-standard -z`, including uncommitted and untracked
non-ignored files and excluding deleted files. Packaging refuses `ledger/`,
`tmp/`, `local.json`, any `auth.json`, `.codex`, other agent homes, credentials,
private-key files, symlinks, special files and ledgers outside `fixtures/ledgers/`.
Ignored files are never enumerated or read. `scripts/ledger/` is application code
and remains included. The inert `templates/ledger/` skeleton is also required by
the first-run harness; its manifest must retain the `{{id}}` placeholder. A
populated template is refused. No user agent environment or credentials are
forwarded into containers.

Outside `fixtures/ledgers/` (and the validated inert template), packaging also
refuses any `Bills/` or `drafts/` component, `data/additions/`, `data/expenses/`,
`data/prices/`, `data/trash-files/`, ledger/journal/price exports, ledger manifests,
and store basenames at any depth: accounts, card-statements, forecast,
recurring-settings, fuel-log, trash, bank-statements, balances and prices JSON.
These can contain filed evidence, uploads, audit words, transactions or values.
Untracked PDF, raster/vector images and spreadsheets (CSV/TSV included) are
allowed only in `review-dashboard/public/`, `fixtures/statements/` and fixture
ledgers. Elsewhere they are refused as possible receipts/statements. Tracked
application documents remain allowed; Git's tracked/untracked tags make that
distinction. The hard ledger/credential refusals still apply to tracked files.

Results default to a unique folder under `/tmp/agentexpense-browser-box/`;
`--output` can select a new folder under that same root. `source.json` identifies
the source files and archive digest. `image.json`, `versions.json` and `build.log`
record the image, byte size, tool versions and build wall time. Each case's
`attempt-N` folders contain `result.json`, the exact guest script,
Incus log, `results.tar.gz` and extracted `results/` (run/server/build/data logs,
results matrices, failure traces and capture gallery). Pulling finishes before
ephemeral stop. The bounded collector prioritises logs and partial traces before
captures; `collection.json` records any files skipped for time, and a pulled
archive is retained even if cancellation interrupts unpacking. `summary.json`
and `CASE_RESULT` log records give case, outcome (`passed`, `failed`,
`infrastructure` or `stopped`), exit, seconds, container, result folder,
`testAttempt` and unchanged-rerun status. A retry pass remains visible as a flaky
first attempt. `--wait` exits nonzero if any final attempt fails. Source and result
archives are retained locally for review; remove only folders you own.

Normal collection uses the case budget; only a stop introduces the short
collection cap. Partial/skipped metadata is copied into `result.json`,
`summary.json` and `CASE_RESULT` as `collection`, with `collected=false` and a
collection error for incomplete evidence. Guest collector errors also remain
visible without changing the frozen test result.

`make test-browser-box` covers packaging/refusals, image hash, case parsing,
two-worker scheduling, retries, detached sessions, caller/controller signals,
stale ownership, capacity waits, explicit pruning, project scoping and safe
cleanup. Fault-injecting local Incus doubles exercise dropped exec responses,
transient list/pull errors, lost containers, deferred cleanup and repeated stop
during launch or collection. Shell-only guest tests exercise detached output,
idempotent launch, independent watchdog expiry and partial logs; no local
browser runs. `make test` runs only the quick packaging, refusal, image-hash and
parsing tests (`make test-browser-box-quick`); run `make test-browser-box`, which takes
several minutes, after changing the runner (it sets `BROWSER_BOX_SLOW_TESTS=1`;
without it the slow files skip themselves, including under scanner discovery). Remote
image creation and browser validation are separate maintainer operations.

## Local headless runner

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

`BROWSER_FOCUS=account-create` isolates inline account creation. It holds the
subsequent statement-assignment request until the account’s visible `Saved.`
acknowledgement is checked, then releases the real assignment and checks its
destination, currency and the still-usable Discard target. Viewport geometry stays
fixed while assignment is held; after the explicit destination change, Discard
remains enabled and keeps focus. The new statement header and a shorter phone
document can reflow and clamp scroll as that destination appears. This prevents the
assignment from removing the status between the harness’s visibility probe and
text assertion. The focused run records a trace; `KEEP_ARTIFACTS=1` retains it
and the server log. `BROWSER_FOCUS=post-ledger-check` resumes at account creation
and includes all remaining suites and the final action-coverage audit.
Investment captures await published data and live Rules controls; Forecast checks
the additional contribution changes money set aside from 5,000 to 5,600 while
recorded spending, commitments and variable estimates stay unchanged.
The same fictional Forecast includes a withdrawal: it has no set-aside class or
Money set aside label and adds nothing to those cash facts. Contributions require
the positive `set-aside` class and an unstruck title, rather than checking an old
class that no longer exists.

Correction states check the empty polite live region after the footer, inserted
unknown-result text, the hidden sizing copy and unchanged wrapped height/field
positions through held Save, refusal and Retry. Their full-page companions retain
the footer evidence when the focused field is above it.

`BROWSER_FOCUS=identity-publication` holds a real dashboard generation with a
shorter ledger name between native pointerdown and click on Settings. The other
layout's hidden identity proves publication reached React; the held button keeps
its text, geometry and focus and opens Settings with the new value. A second
publication stays held until Cancel returns focus to the opener, then the name
and accessible label must update with focus still there. Newer Add words remain
intact. `view-timing` also requires the renamed identity with returned focus after
saving Settings through a held older data read. Run both timing coverage and a
complete case when changing this shared shell interaction.

`BROWSER_FOCUS=capture-interference` tests the capture harness with a small native
fixture document and fixed phone controls. It holds a screenshot reply across
pointerdown, then holds any full-page screenshot. Upload must stay within 2px and
the date click, editing and newer words must survive. Background observer captures
are viewport-only: they never add the full-page companion's layout styles or
restore an earlier scroll. Explicit awaited captures retain their full-page
companions. The native capture regression runs in the full suite when captures
are enabled.

The reader suite holds Queue list replies through a committed Apply and newer
detail focus. The applied row must disappear before release and stay gone after
the late replies, with focus and link geometry retained. First-run Import holds
its native transition scroll and checks that the old controls have unmounted
before the scroll was requested; its original geometry and focus assertions stay
in force. Add hook tests hold autosave edits around bill Retry completion and
reject the obsolete queued-draft alert. Existing assertions, timeouts and console
filters remain unchanged.

`BROWSER_FOCUS=fixture-isolation` holds a real committed worker-result reply.
Fixture cleanup must retain recovery until the page's direct save has completed,
then unload the app on an inert same-origin page and clear recovery there before
deleting ignored records. The next Add document must never fetch a deleted result
ID. `writesSettled` skips the network-idle wait for
deliberately blocked health checks; it never skips the pending-save barrier.

Receipt-choice checks follow the accepted usual-payment request's draft ID. The
shared retry driver deliberately keeps newer typing, so draft text is not an
identity. Correction reopening first observes Back's native Queue navigation;
otherwise the pending Back navigation can overtake the test's next `goto`.
Existing console, geometry, focus and acknowledgement assertions and timeouts
remain in force.

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
terminated in `finally`; browsers and contexts are also closed on failure. Passing runs remove their staging folder; failed runs keep failing-case
trace ZIPs and the server logs. `BROWSER_RESULTS` in stdout retains the assertion matrix.
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

`BROWSER_FOCUS=ui-intent` exercises explicit Queue selection, the desktop empty
detail prompt, Edit entry Back/Cancel, the discard question, a held autosave during
Cancel, reload recovery, edit Trash Restore, URL-persisted Rules sorting and hash
focus, and the calendar-day average with a fixed local date and future spending.
The default suite includes it. Captures prefixed `intent-` cover each new state.
It also forces a late Add autosave response at fixture departure and checks that
the next Add document never requests the deleted draft.
The held autosave is released explicitly; tests never depend on browser speed.
The reload regression holds the first claim response after the server claims the
edit, reloads before delivering that response, then reclaims and cancels the edit.
This forces the lost-claim-reply condition in every browser engine.
`UI_INTENT_NO_NAVIGATION_TIMING=1 BROWSER_FOCUS=ui-intent` also forces navigation
timing metadata to be absent across reload. The editor's history marker must
recover Queue without using a browser speed or timing-API assumption.

The Cancel-acknowledgement regression holds the completed server response, leaves
with browser Back, types newer Add words, then releases the response and checks
that the new page and words remain. `intent-cancelling` captures the locked form.


The clear-intent review regressions also use controlled hook/API holds for
same-document editor reopening, Cancel versus an accepted/applying Save, complete
proposal Discard, per-draft recovery isolation, and back-forward-cache return.
Cancel browser assertions read the draft directly (404 even for Trash reads),
not just the ordinary list that omits trashed items. Fix summary actions use
Continue editing and one Discard proposal control.


Second-round regressions cover expired-claim Cancel, reversed claim arrival,
Retry confirmation including browser-kept edits, crash recovery after/before an
edit commit, timed-out fix Restore, Save-aware Queue Discard and the atomic
claimed-discard/worker handoff. The controlled hook/API tests run offline before
the browser matrix; the second-round load gate applies to browser and benchmark
execution.


Third-round clear-intent validation uses `BROWSER_FOCUS=clear-intent`: the full
shell/Queue/Explorer section and the complete views, hand-finish, Rules,
corrections, confirmed-balance refusals, retention, account-group and varying-rule
suites, followed by UI intent, Ledger check and attachment Queue coverage. It
reuses each suite's assertions and console/error checks. Run it in Light across
all four personas, three engines and both sizes; check the load gate before each
job. Failed-fix coverage checks Save through both Continue editing and Ledger
check's Open the entry, retaining the same draft and entry IDs.
The reordered claim regression also holds the reopened editor's second reply,
types Notes through an injected controlled native-input writeback, then releases
that reply. The injected writeback proves the capture path; it is not a
reproduction of the single box failure, whose exact trigger is unknown.
The typed Notes must remain visible, focused and within 2px geometry and reach
the real hand-form autosave before the obsolete first reply is released. Its
original 5-second saved-Notes assertion is unchanged. Component/API regressions
also cover hand additions, corrections, the existing 500 ms autosave and clearing
Notes without reusing captured words.
The deliberately reordered claim test verifies the obsolete token's real 409
refusal before allowing its one exact release-URL console message. Other console
errors, including other conflicts, still fail the case.
The held-Cancel test also restores the textarea's previous controlled value
between input capture and bubbling, then checks the new words in both the field
and recovery before releasing Cancel. This reproduces the observed WebKit input
writeback deterministically in every engine.


### Forecast chart sizing

`BROWSER_FOCUS=chart-sizing` changes the real observed Forecast container through
hidden, zero, 1, 32 and 70 pixel widths, then restores 71, 320 and 520 pixels.
A container without plotting space must omit its SVG and tooltip. Positive plots
recover with finite nonnegative dimensions, working Home/End/Escape inspection
and unchanged financial table values. The same regression runs in the full pass;
the normal console/page-error assertions remain in place.

## Flow stability

Run `make browser-flows` for the main tasks at 390 × 844, 720 × 450 and 1440 × 900,
in Light and Dark. The runner prints the path under `/tmp` containing
`index.html`, `measurements.json` and `summary.md`. Each stability finding names
the measured row and the screenshots that show the change. Flow failures and
incomplete runs return a failing exit status. Responsiveness timings are reported
in the summary.

Use `PERSONA`, `ENGINES` and `THEMES` as for the browser suite. `FLOWS` accepts
comma-separated task names: `first-run`, `time-limits`, `add-expense`,
`add-as-usual`, `receipt-question`, `queue-question`, `finish-by-hand`,
`statement`, `review-duplicate`, `edit-entry`, `delete-restore`, `explorer`,
`new-rule` and `opening-balances`. Use `BROWSER_CASE=light-390` to debug one
viewport. Chromium records five undelayed desktop samples per selected task and
appearance. `FLOW_TIMING=0` skips those repeats during focused debugging and
`FLOW_TIMING=only` runs the timing samples by themselves.
`FLOW_ARTIFACT_DIR` sets the parent folder for retained evidence. For a secondary
checkout with dependencies already installed elsewhere, `BROWSER_NODE_MODULES`
and `BROWSER_VENV` point the shared runner at those read-only inputs.

```sh
ENGINES=chromium PERSONA=asha THEMES=light FLOWS=add-expense make browser-flows
```
