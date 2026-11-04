# Live expense server

For fictional local checks, select `LEDGER_DIR="$PWD/fixtures/ledgers/asha"` from the application root before changing directories. The server checks `ledger.json` at startup and on requests; older formats require `make upgrade-ledger` (see [migrations](../../migrations/README.md)).

Build the existing dashboard with `make dashboard` at the application root.
From this directory run `EXPENSES_DEV=1 uv run app.py`; it binds to 127.0.0.1.
On the server host, `make service` installs the tracked `expense-dashboard.service` when changed, reloads systemd and restarts it. It stops with an explanation on hosts without a user systemd session. Customize the service template paths and access settings for your installation before using it.
The template sets `EXPENSES_STATE_DIR=%S/agentexpense`, using systemd's user state
root. Override that setting in the unit if you need another dedicated directory.
For Vite development, enable `EXPENSES_DEV=1 EXPENSES_PORT=8701` in the dashboard
process as well to proxy only `/api/` to the local server. See `../README.md`.

| Environment | Default |
| --- | --- |
| `EXPENSES_PORT` | `8701` |
| `EXPENSES_BASE_PATH` | `/` (production `/expenses/`) |
| `EXPENSES_ALLOWED_HOSTS` | empty, comma-separated extra hostnames |
| `EXPENSES_ALLOWED_LOGINS` | empty, comma-separated Tailscale logins |
| `EXPENSES_DEV` | off (`1`, `true`, `yes`, `on` enable local writes) |
| `LEDGER_DIR` | application `ledger/`; relative values use caller cwd |
| `EXPENSES_AGENT` | `codex`; overrides `agent.adapter` in application `local.json` |
| `EXPENSES_AGENT_BINARY` | explicit offline-double/maintainer candidate override for non-Codex CLIs |
| `EXPENSES_CODEX` | app `review-dashboard/node_modules/.bin/codex` |
| `EXPENSES_STATE_DIR` | `${XDG_STATE_HOME:-$HOME/.local/state}/agentexpense` (`~/.local/state/agentexpense` without `XDG_STATE_HOME`) |
| `EXPENSES_CODEX_HOME` | `<state>/codex-home` |
| `EXPENSES_AGENT_MODEL` | adapter default; `local.json` model when supplied |
| `EXPENSES_AGENT_EFFORT` | Codex `xhigh`, other adapters `high`; machine setting when supplied |
| `EXPENSES_AGENT_TIMEOUT` | `600` seconds |
| `EXPENSES_OLD_LEDGER` | deprecated fallback; use `modules.history.oldLedger` in `ledger.json` |

127.0.0.1, localhost and testserver are always allowed hosts. Writes require an
allowed `Tailscale-User-Login`, except in dev mode. A supplied Origin must use an
allowed host. Only use identity headers behind the trusted local Tailscale proxy;
other local processes have the same trust as the user. No CORS permission is added.

All endpoints below are relative to the browser base path. Both `/api/…` after a
proxy strips the prefix and direct `/expenses/api/…` work. Static directories use
index.html, without an SPA fallback. `/data/` always reads `public/data/`, without
an HTTP Content-Encoding header for the already compressed .gz data; the browser
loader handles its gzip payload. Traversal and evidence links outside the allowed
ledger locations are refused.

| Method and path | Body / effect |
| --- | --- |
| GET `api/health` | Health, worker status and `agent: {state, reason}` |
| POST `api/drafts` | Optional JSON `{text, pieces, client_updated_at, client_time, client_zone}`; returns editing draft (201) |
| GET `api/drafts` | Active drafts; `?include_added=true` includes retained results |
| GET `api/drafts/{id}` | Full record, including runs, questions, result and error |
| PATCH `api/drafts/{id}` | Partial `{text, pieces, client_updated_at, client_time, client_zone}` while editing |
| POST `api/drafts/{id}/files` | Multipart `files` repeated; upload while editing (201) |
| GET `api/drafts/{id}/files/{file_id}` | Original upload for thumbnail/preview |
| DELETE `api/drafts/{id}/files/{file_id}` | Remove while editing |
| DELETE `api/drafts/{id}/uploads/{upload_id}` | Cancel one upload, including a late arrival |
| PATCH `api/drafts/{id}/unsent-answer` | `{question_index, answer, updated_at}`; keep an answer without sending |
| POST `api/drafts/{id}/restore` | Undo Discard within 30 seconds |
| POST `api/drafts/{id}/save` | JSON `{client_time, client_zone}`; save time and FIFO queue |
| POST `api/drafts/{id}/answer` | `{answer: "…"}` from question state; queue again |
| POST `api/drafts/{id}/retry` | Failed draft; reset check-repair allowance and queue |
| DELETE `api/drafts/{id}` | Soft discard except while working (204); Undo for 30 seconds |

Add's create, edit and Save requests and statement uploads carry `client_time` (ISO local time with
an explicit ±HH:MM offset within ±14:00) and `client_zone` (a known IANA name).
Both must be supplied together; times more than 48 hours ahead are rejected, but
older save times remain valid for retries. Requests without these fields and old
stored drafts use the server's local zone, recorded in the same fields. Retries,
answers and statement handover retain the original save context. Receipt and
statement calendar dates remain as printed; undated entries and relative dates
use the draft's local save date. Displays use the viewer's zone and the ledger's
optional locale, otherwise the viewer's locale.

The commit's save line is `Saved: <YYYY-MM-DD HH:MM> <±HH:MM> <IANA zone>`.
Draft audit events optionally carry `client_time` and `client_zone`; version 1
lines written before these fields were introduced remain valid and unchanged.

`client_updated_at` and unsent-answer `updated_at` are client revision timestamps
used to choose between local recovery and a newer server copy. Keeping an unsent
answer never queues the agent; Send records the answer and clears that recovery
copy. The question index must identify the current unanswered question.

A discarded draft disappears from reads, lists and the worker queue immediately.
Its record and remaining files stay for 30 seconds, including across a restart.
Restore retains its prior state, words, pieces, answers and files, and wakes queued
work. The worker and list/restore requests purge expired records. Discarding an
added record never touches its ledger entry or filed evidence.

An individual file upload may include an `upload_id` multipart field (32 hex
characters). Cancellation records that token before removing its file. A late
request with the same token cannot recreate the file. Retrying a cancelled upload
needs a new token; other uploads are unaffected.

Files are recognised from contents, saved with generated names, and limited to
10 per draft and 25 MiB each. Supported: JPEG, PNG, WebP, PDF. Original names stay
in metadata. Multi-file requests validate the whole batch before writing files.
State conflicts return 409, missing resources 404, upload limits 413, unsupported
contents 415. Typed text and answers are limited to 100,000 characters; pieces is
an optional JSON object. Blank answers are refused.

One worker lease prevents two server instances processing the same ledger.
Queued and interrupted working drafts resume on restart. A ready proposal waits
for a clean ledger, retrying every 30 seconds and exposing `waiting` in the draft.
Do not edit ledger files concurrently outside the shared `.lock` protocol.
Transaction journals recover interrupted uncommitted writes or recognise a
completed dashboard commit before repeating work. Commit failures restore store,
evidence and jj state. Data refresh failure leaves an added result with
`data_stale` and `refresh_error`; rerun `npm --prefix review-dashboard run data`.

`not_expense` is a successful terminal result (`state=added`,
`result.outcome=not_expense`) with an explanation and a record-only audit commit. All
completed results leave the active list immediately; records last 24 hours and
uploads are deleted. Other states persist until discarded. Retry retains earlier
runs/questions and resets only the proposal/error/check-repair allowance.

## Adapter isolation and readiness

The server selects Codex, Claude Code, Pi, OpenCode, OpenAI-compatible or Anthropic
API through `EXPENSES_AGENT` or application-root ignored `local.json`. Custom modules
have the same version-1 contract. See [ADAPTERS.md](../../docs/ADAPTERS.md) for exact
pins, flags, machine/API configuration, login, offline evidence and custom registration.
All CLI copies are pinned in the dashboard npm lockfile, never taken from PATH.
`make dashboard`, `make test-server` and `make agent-check` run npm ci only when
installation is absent or the tracked lockfile is newer.

Adapters run in an empty disposable workspace and their own dedicated home under
`EXPENSES_STATE_DIR`; other agent authentication/configuration is never copied or
linked, and no existing state directory is migrated. The server validates every answer and owns every write. Only the enabled
read-only ledger lookups are offered; old history is optional. PDFs provide up to
12 PNG pages plus extracted text, and images reach each adapter's image protocol.
Runtime policy, browser save context, earlier answers and check errors stay in the
shared production prompt. Execution/schema errors retain the existing bounded retry.

The gate runs offline checks, then one fictional live probe for a new fingerprint
(adapter/interface/pin/version, flags, model, effort, allow-lists and policy source).
The probe verifies a successful account lookup, unavailable/refused shell execution
and schema-valid JSON. Passing/policy-failed fingerprints persist in
`<state>/agent-check.json`; temporary diagnostics and sign-out are not cached.
Shutdown cancels process groups before joining the worker. Owner commands:

```sh
make agent-check ADAPTER=claude          # offline, no model
make agent-login ADAPTER=claude          # owner signs in under dedicated home
make agent-check LIVE=1 ADAPTER=claude   # owner only; fixture probe and retained gate result
```

The defaults remain Codex when ADAPTER is omitted. Pi login opens its isolated CLI;
the owner types `/login` then exits. Direct API login prints key-configuration
instructions. Never copy authentication. Maintainers move exact CLI pins with
`make agent-upgrade ADAPTER=claude VERSION=x.y.z` (`DRY_RUN=1` checks without changing
the pin). The candidate's home is under `/tmp`; this command never signs in or runs
a model. Review any widened allow-list before shipping.

Codex retains its established read-only sandbox, disabled shell features,
`--ignore-user-config`, `--ignore-rules`, disabled skills/plugins/hooks/apps and
narrow `debug prompt-input` allow-lists. `agents.max_threads=1` prevents spawning;
code-mode exec/base/subagent definitions remain under the sandbox. The offline
renderer omits exec-only ignore flags and checks dedicated-home names instead.
It verifies configured features and allows only known prompt sections, top-level
exec/wait/collaboration, nested sandboxed apply_patch/resource/clock functions and
the enabled ledger MCP names. Authentication contents are never inspected.

Claude's safe mode retains subscription login; bare mode is not used. Pi disables
all discovery and built-in MCP and loads only the application lookup extension.
OpenCode cannot remove its built-in tool definitions completely: it declares that
limit, requires explicit `agent.allowUnisolated:true`, and discloses it in Settings.
Its generated deny-all policy still allows only exact ledger execution exceptions.
The direct APIs run a bounded in-process read-only tool loop with no CLI tools.

`GET api/health` keeps readiness state/reason. `GET api/agent-status` additionally
returns adapter description/pin/isolation, model/effort, selected login command and
an isolation warning when applicable. Reads never run diagnostics or a model.
States remain checking, ready, signed out, paused and unavailable. The existing
Add/Drafts/Settings notice explains the selected login/key action. Signed-out and
paused states block agent submission; edits/uploads and built-in statement parsing
remain usable. The signed-out watcher checks every 30 seconds. Unavailable checks
retry on the next draft. Both threads stop with the application.

Run the fast offline tests from the application root:

```sh
make test-server
make test
make verify
```

For a run that writes no bytecode or environment updates in the application,
use `PYTHONDONTWRITEBYTECODE=1 UV_NO_SYNC=1 make test-server` after dependencies are
installed. `EXPENSES_TEST_TMP=/tmp/expense-checks` groups fixture applications and
ledgers. `make test-server` activates the installed server venv through `uv`, so
nested `make commit`/`make verify-data` subprocesses reuse its `jsonschema` even with
`UV_OFFLINE=1` and an empty `/tmp` `UV_CACHE_DIR`. Browser fixture APIs activate
the same environment explicitly while using its Python directly; neither path
needs pre-filled caches or a venv resync.

The suite uses unittest, TestClient, temporary fixture ledgers with their own jj
repositories and `tests/fake_agent.py`. It never runs a model or contacts an external network. Direct API protocols use
local loopback fake HTTP servers.
Complete discovery runs in three isolated worker processes. Whole test classes
stay together; modules with module fixtures stay together as well. Each worker
uses normal unittest fixture/error/subtest handling, and the runner checks the
executed test inventory against discovery before reporting success. No writable
repository or process environment is shared between workers. Set
`EXPENSES_TEST_WORKERS=1` for serial diagnosis or another positive value to tune
the worker count. Builds still wait until every worker has finished.
Each fixture checks the source dashboard data, build and expense JSON at cleanup,
and fails with the changed paths. As in the Node guard, a changed regular expense
file is accepted only when its new bytes equal that file at the ledger repository's
`refs/heads/main`, read with read-only git commands and pinned to one commit ID.
This permits concurrent ledger syncs and live commits; each accepted file prints
a note with its path and commit. Missing git, repository or `main`, uncommitted
content, deletions and symlinks stay strict, as do all dashboard data and build
files. `tests/test_isolation.py` tests these cases with git fixtures under `/tmp`.
Browser fixture APIs likewise receive disposable
application copies for data refresh and serving; production settings are unchanged.
Commit verification skips only the server suite to avoid recursive integration
tests; `make verify` runs the ledger suite plus data verification. Saves run `make verify-data` under the lock without code tests. `EXPENSES_FAKE_CONFIG` belongs to tests
only and has no effect on the production agent runner.

Nested verification in the offline server suite shares a temporary, immutable
hledger calendar oracle. `EXPENSES_TEST_ORACLE_CACHE` selects its `/tmp` directory;
the fixture helper creates and cleans it for the test process. The key includes
the exact journal, CLI arguments, executable path and executable bytes. Only a
successful hledger result is cached: every verification still renders all 3,534
schedules, recomputes expected dates and runs every header/date assertion. Every
test retains its own writable application and ledger repository, real checked
commits, rollback and data refresh. Standalone ledger tests, without this explicit
test variable, always run hledger afresh. No production verification is skipped.
For per-test wall timings, run
`EXPENSES_TEST_TIMINGS='/tmp/server-{persona}.json' make personas` (or set an
explicit `/tmp` JSON path with `make test-server`). The runner still discovers
the complete unittest suite and preserves its normal failure/skip reporting;
`{persona}` is replaced with the selected ledger directory's name.

The daily price job also holds the shared lock through its update, build and
partial jj commit. Schedule it externally when desired. Other
manual writers should use `uv run python scripts/ledger/ledger_lock.py -- COMMAND`
when they can write concurrently with the server.

If an interrupted transaction encounters later human edits or commits, automatic
rollback stops with an error and preserves `transaction.json` for reconciliation.
Compare its backups and the jj history before resuming; recovery must preserve
work made after the interruption.

## Structured audit and Statements

Live actions append version 1 records to `LEDGER_DIR/data/additions/YYYY.jsonl`.
`schema/additions.schema.json` and `scripts/ledger/audit.py` define and check every
line. An addition and its audit line share one commit. Failed and not-expense
outcomes get record-only commits; a discarded draft is recorded after its
30-second Undo period, before its record is purged. These commits wait for a clean
ledger and use the same lock and rollback journal. Failed audit commits remain
pending in the draft, with `audit_error`, for the worker to retry. See
[WEEKLY-REVIEW.md](WEEKLY-REVIEW.md) for exact reviewer commands.

Statements uses the same FIFO worker and ledger lock. A version-1 reader registry
loads built-ins from the application and trusted personal readers from the ledger.
The [example card PDF layout](../../scripts/statements/README.md) retains PDFium
extraction. Unknown formats use a dedicated cited-reading adapter task; code
checks each source value, printed total and required running balance before
matching. Recognised reader failures never fall through to an agent. Generic CSV, XLSX and XLS readers use
explicit account mappings, exact decimals, printed totals where available, running
balances and continuity; their errors never invoke an agent. No system PDF
executable is needed. A wrong-file result never imports rows. Already-filed
statements link to stored rows and cannot be applied again. See
[READERS.md](../../docs/READERS.md) for the precise interface, configuration,
source citations, signed bank summaries, overlap and start-date rules.

| Method/path | Purpose |
| --- | --- |
| GET `api/statements` | Sessions, filed summaries, declared accounts and reader descriptions |
| POST `api/statements` | Multipart `file` (configured PDF/CSV/XLSX/XLS or personal format, at most 25 MiB), `client_time`, `client_zone` |
| GET `api/statements/filed/{end}` | Stored summary and reconciliation rows |
| GET `api/statements/{id}` | Durable state, checks, checklist and result |
| POST `api/statements/{id}/unlock` | JSON `{password}`; memory only, never echoed |
| POST `api/statements/{id}/account` | Select a compatible account for `account_needed` or `currency_mismatch` |
| POST `api/statements/{id}/mapping` | `{enabled:boolean}` stages a verified reusable mapping until Apply |
| POST `api/statements/{id}/review` | Found statement → checklist |
| PATCH `api/statements/{id}/rows/{serial}` | `{action:"match",entry_id}`, `{action:"account",account}`, `{action:"pending"}` or `{action:"reset"}` |
| POST `api/statements/{id}/apply` | Queue checked apply, 202; all needs-you rows must be decided |
| DELETE `api/statements/{id}` | Discard/cancel reading, 204; recovery for 24 hours |
| POST `api/statements/{id}/restore` | Restore a discarded session |
| POST `api/drafts/{id}/statements` | Hand recognised evidence over; soft discard draft |

Passwords are used only to decrypt the PDF in memory. The readable copy stays in
`drafts/statements/{id}/read.pdf` until apply or the discarded session’s recovery expires. Passwords are absent
from session metadata, audit events, subprocess arguments, logs and responses.
A failed password response is generic, including malformed password values.
The encrypted original is kept only for this session. Applying files the readable
statement, membership and explicitly approved conversion changes, new entries, summary, reconciliation rows
and audit line in one verified commit. A failure restores file bytes and jj state.
Balance-style Apply adds a closing hledger assertion and explicit per-row membership;
ambiguous overlaps remain important Review items without booking a second movement.
Apply waits for a clean ledger; changed store or manifest contents invalidate an old checklist.
Completed uploads are removed, and completed session records expire after 24 hours.

Add detects statement text before running the expense agent. A locked PDF can be
opened in Statements to establish its contents. An agent `not_expense` statement
result also offers handover. Statement attachments remain until handover/discard;
Undo handover restores the draft and invalidates its session. Once applying has
started the handover cannot be undone.

Checked commits explicitly allow large new evidence in Jujutsu snapshots. The
cleanliness check also sees large unrelated files and waits for them to be
resolved. This avoids jj's default new-file limit silently omitting a readable
PDF from an otherwise successful commit. No repository/global jj setting changes.

### Statements recovery and cancellation

The session list contains work in progress. Applied/already-filed results remain
addressable by session URL and appear through Past statements, rather than in the
work list. The list also returns `recoverable`, including expiry times. Discarded
statements retain their source file and decisions for 24 hours; `POST statements/{id}/keep`
extends recovery by another day, and `restore` reopens the session. Handover Undo
has the same 24-hour interval and can be extended with `keep` while it is valid.

Upload may include a 32-hex `upload_id`. `DELETE statements/uploads/{token}`
records cancellation before or after arrival; the same token cannot create a late
session. Upload ownership stays in the app across route changes. A full browser
departure during transmission warns about the unfinished upload.

`DELETE statements/{id}` also cancels reading without waiting for extraction;
late reader results cannot resurrect a discarded or restored generation.
`POST statements/{id}/cancel` cancels queued Apply until `apply_started` is true.
The worker rechecks that state under the ledger lock immediately before mutation.
Once the checked transaction starts it finishes or rolls back, preserving the
existing transaction guarantees. Stale fingerprints return `failure_reason:
stale_checklist`, keep the old choices, and refuse another Apply on that checklist.
Stored rows include entry links and conversion notes only where actual membership
and recorded notes establish them; no historical matching rule is inferred.

## Currency settings

The currency endpoints, opening-balance lock rules and generated amount contracts are documented in [Account currencies](../../docs/CURRENCIES.md). Both settings writes use the existing write-access checks and the locked, verified commit path.

Optional fuel fields, investment price sources, history tools and weekly review follow the selected manifest. See [module settings](../../docs/MODULES.md) and [scheduling examples](../../docs/scheduling/README.md).


## Shell and Settings

`GET /api/agent-status` returns the gate’s current `state` and safe `reason`.
It is read-only: it never starts a check or a model run. The dashboard polls with
the existing draft refresh cadence (30 seconds idle, three seconds while working,
paused while hidden). In-flight read-only requests finish with keepalive transport;
caller cancellation is immediate and departed views cannot publish stale data.
This avoids WebKit teardown errors. Writes retain transport cancellation and all
requests retain their network deadlines. Only Add, Drafts and Settings explain agent readiness.
Signed-out and paused states disable agent submission, answer and retry controls;
editing and uploads remain available. An unavailable check retries on the next
draft. While signed out, a separate server watcher checks for login every 30
seconds, including after a previously ready login expires. Both check threads
stop with the application. Isolation pauses are rechecked every 30 seconds as well; unchanged failed
checks are rerun so corrected configuration can recover. Unavailable checks retry
with the next draft.

`POST /api/settings/name` accepts `{"name":"River Ledger"}`. Names are trimmed,
nonblank, single-line and at most 200 characters. It shares the clean-copy guard,
ledger lock, recovery journal, verification, commit and dashboard refresh used by
currency writes. The Settings dialog saves changed fields through their respective
endpoints; each field makes its own verified commit. A failed later field leaves
the dialog open and reloads the committed state before retrying.

`GET /api/accounts/details?account=…` returns metadata, `currencyEditable`, a
fixed-currency reason, and an opening balance for the relabelling preview. Its
read-only calculation uses the same transaction, forecast and filed-statement
rules as the currency write endpoint. Fixed currency is plain text in the dialog;
account name and kind are informational. Opening-only currency edits use the
existing `POST /api/accounts/currency` endpoint.

The legacy Statements route remains available while statement intake moves to
Add in the next UI step. The shell has no separate Statements navigation item.
Headless fixture browser tests and captures are documented in
[the browser harness](../tests/browser/README.md).


### Add and Drafts currency workflows

Add owns expense/receipt composition and statement uploads. The legacy Statements
page redirects to Add; sessions, checklists, filed reconciliations and recovery now
live in Drafts (`?session=…` / `?filed=…&account=…`). Statements remain independent
of agent readiness when the built-in reader can read them.

An agent question with an incomplete two-posting foreign expense or transfer yields
`currency_request` on the durable draft. `POST /api/drafts/{id}/currency` accepts
exactly one positive decimal `amount` or `rate` and an `estimate` boolean. The server
rounds half up using `schema/currency-minor-units.json`, adds the total cost to the
proposed original posting, retains both native amounts, records the answer, and
queues the ordinary validated transaction. It does not ask an agent to write.
An expense estimate is disclosed in the entry notes; replacing it with the statement
amount requires an explicit checklist decision (`action: conversion`).

The prompt specifies the incomplete proposal explicitly: only when the charged or
received amount is the sole missing detail, return a question with that posting's
amount `"0"` in its declared account currency and both costs null. This is an
unknown-amount marker, never a valid ready entry. Known charges and other questions
keep their ordinary answer workflow; the server checks the marker and currencies
before presenting the linked form.

A printed currency mismatch produces a blocked `currency_mismatch` session, never
an agent reinterpretation. `POST /api/statements/{id}/account` only accepts a declared
bank/card account in that printed currency, then rereads and validates the PDF.
`POST /api/accounts` takes kind, name and currency, and uses the settings writer's
ledger lock, clean-copy guard, recovery snapshot, verification, commit and refresh.
The inline form creates an account before assigning the session. These two actions
can be retried independently; an account that was created remains available if
assignment fails.

For an unused explicitly assigned account, the first statement proposes its printed
previous balance as a starting entry on the period start. The checklist and Apply
confirmation disclose it. Nothing financial is written until Apply, which files the
PDF, starting entry, rows, membership, summary and audit together. Existing activity
still requires its original opening/statement chain.

Draft storage exposes a condition notified after each durable write. Offline lifecycle
tests synchronize on states and audit completion through that condition; the timeout
is solely a deadlock guard. They still check the HTTP response and every commit,
evidence, refresh, rollback and purge assertion.

### Server Review

`GET /api/review` returns the authoritative queue, plain waiting count, important
count, entry versions, saved decisions and change history. Pending entries, pairs
linked in the store, and unreviewed doubtful Add outcomes from the last seven days
are included; an entry represented by a pair/doubt is not counted again as pending.
Only duplicate pairs and recent doubts are important. The clock is injectable by
offline tests and defaults to UTC now; amounts never affect importance.

`POST /api/review/actions` accepts a 32-hex `request_id`, `action`, entry IDs and
`versions` keyed by entry ID. Resolve/followup also take a version-2 `decision`
(status, account, label, notes, updatedAt). Resolve replaces the first review
account's matching postings, confirms the entry, uses a nonblank label as its
description and appends notes as lines. Followup changes only the audit log and
leaves the entry waiting. Acknowledge settles a doubt without changing postings.
Separate removes a canonical pair; undo-separate takes its audit ID in `undoes`.
Merge keeps statement membership first, then the earliest committed/audited
addition (ID breaks a same-commit tie), unions sources and notes in retained-first
order and retargets other unresolved links. Both-statement pairs are refused until
their membership is reconciled. The confirmation identifies both entries by
merchant, date, account and amount, and lists moved sources/notes.

Every action re-reads policy, holds the ledger lock, requires a clean working copy,
validates expected hashes, atomically publishes JSON, appends a version-1
`review-action` audit event, verifies and commits through `make commit`. Durable
rollback journals live in ignored `drafts/review/`; interrupted operations recover
before the next action. Reusing an identical request ID returns its audit receipt;
reusing it with a different payload is refused. Original audit lines remain intact. Decision changes retain their original export identity so a resolved decision can still be imported into an unchanged ledger copy.

`POST /api/review/import-preview` accepts the existing reviewVersion-2 export and
returns valid/unknown/changed/already counts and a preview token. Import actions
include `payload` and `preview_token`; all valid decisions share one audited commit.
A changed preview requires another preview. Legacy derived IDs require a unique
match on date, description, amount, source, reference, account and currency when
present. Browser storage contains only unsent work, failed requests and legacy work
awaiting migration. Retry and discard are explicit; Export retains version 2.

### Shared pauses and finishing by hand

Agent failures have typed kinds; see [failure contract](../../docs/ADAPTERS.md#failure-contract).
Health, agent status and draft responses include `agent_pause`. A statement needing
a second reading waits as `agent_waiting` and resumes automatically. Built-in
readers and checked human proposals continue during agent outages.

A queued, failed or question draft can be claimed for 30 minutes. Claim renewal,
expiry, selection and transitions use the draft mutex. The page stores its token
in session storage; ordinary reads expose only the expiry. Activity renews the
claim. Release/expiry retain the form and FIFO position and revoke consent.

| Method/path | Body/effect |
| --- | --- |
| POST `api/drafts/{id}/claim` | Optional `claim_token`; acquire/renew, return token and prefilled form |
| POST `api/drafts/{id}/hand-form` | `claim_token`, `proposal`; persist even invalid work, return checks/new accounts/hash |
| POST `api/drafts/{id}/hand-add` | `claim_token`, `proposal_hash`, `approved_accounts`; queue checked human proposal |
| POST `api/drafts/{id}/release` | `claim_token`, optional `proposal`; retain form and release |
| POST `api/drafts/{id}/approve-accounts` | `proposal_hash`; approve exact retained agent proposal and queue without an agent call |

Uploads use the existing files endpoint and checks with multipart `claim_token`.
Removal accepts that token as a query parameter. A hand proposal uses the ordinary
answer shape, with optional `account_metadata` for new balance-sheet accounts;
metadata must pass the store schema. The server discovers new accounts from its
postings and every new account requires a separate tick. Agent proposals creating
a new root become a question; an account-check failure also asks a person.
Free-text answers clear retained consent. Consent hashes the complete proposal,
including metadata and attachment names; the audit reader recomputes it.

`Transaction.prepare(answer)` is the shared side-effect-free interface for 6c/6d:
it returns the converted `entry`, `declarations`, `new_accounts`, `new_roots` and
`proposal_hash`, after schema and strict hledger validation in scratch files.
`Transaction.apply(answer, lock_fd)` repeats preparation under the ledger lock,
checks exact consent, files/compresses evidence, appends audit, journals rollback,
verifies/commits and durably saves its receipt. Callers own FIFO selection and the
clean-ledger wait. `Worker.call(operation)` schedules checked human services on the
same FIFO independently of agent readiness; Review actions use it. Offline hosts
without a worker invoke the identical service synchronously. `verified_commit(lock_fd, message)` is shared with Review (including
merge); snapshot/recover/rollback remain the common mutation foundation. Human
proposals follow the same worker and apply method. `finished_by` retains actor and
reason; the commit trailer identifies the human. No agent runs for a human save.

The FIFO worker also checks the dashboard's `meta.today` at startup and every
30 seconds. A stale or missing date triggers the existing data-refresh path only
when a nonblocking ledger lock is available and the ledger is clean. The worker
cannot run this maintenance during one of its transactions; external writers
share the same lock. Build/read failures are logged, leave the worker running,
and defer the next attempt for ten minutes. Production dates use the server's
local calendar; tests may set `DASHBOARD_TODAY=YYYY-MM-DD`.

### Recurring rule services

The [recurring API and CLI reference](../../docs/RULES.md) documents read routes,
`POST api/drafts/{id}/usual`, idempotent `POST api/rules/operations/{op-id}`, rule
lifecycles, detection, suppression, history backfill, catch-up and checked Undo.
These services use the shared transaction/FIFO without an agent call. Additions
link/advance or create rules in their existing verified addition commit. UI for
these services is implemented separately; no due-payment list is added to Add.

Recurring dashboard UI and weekly review: see `docs/RULES.md` at the application
root. Editing drafts accept optional `declined_rules`, a unique list of 16-hex
rule IDs; this is ignored runtime state and never queues a payment. Completed
recurring receipts expose `can_undo`, `undo_until` (30 minutes from the permanent
audit timestamp) and `undone`. Eligibility is recomputed server-side on every
read and checked again under the ledger lock on Undo. Receipt reads acquire the
ledger lock with one bounded one-second wait for the entire receipt batch. This
lets brief competing reads finish without starving eligibility checks. While a
writer stays busy, `can_undo` is false and `undo_until` is omitted, and the page
keeps polling every three seconds until the check settles.
This prevents Jujutsu's working-copy snapshot from racing a payment write.
The built rule snapshot is a fallback: live reads start independently, and a
late snapshot cannot replace a known live result. Rules keeps its edit state
across dashboard refreshes; Overview retains its existing data-version reset.
GET rules also returns
compact payments for the page's chosen-period disclosure. Suggestion dismissal
uses a removed-rule suppression record, recomputed from current evidence under
the normal checked operation; no caller-supplied postings are trusted.

Data refresh and `/data/` use the `DASHBOARD_DATA_DIR` captured at server startup (default `review-dashboard/`). Refresh selects the installed `.node-version` through fnm, disables npm update notifications, and reports provenance refusal as a refresh failure. It preserves the existing output. The private source stamp is never served. The daily check uses this same output root and provenance. The service installer writes the resolved Node binary directory into its PATH.


### Correcting entries and reading filed receipts

`POST /api/entries/{id}/correction-form` opens a hand form with a saved entry hash.
`POST /api/entries/{id}/corrections/{32-hex operation}` accepts `version`, a checked
wire `proposal`, existing `sources`, optional `reason`, `approved_accounts` and an
optional upload `draft_id`. Corrections and Review merges share the replacement
service and transaction publication: hash checks, preserved IDs/hidden membership,
strict checks, evidence move planning, rollback of both file ends, audit and one
verified commit in the FIFO worker. A changed date moves exclusively cited evidence
from its old date folder, using a fresh filename on collision. Files are never
removed without retaining them at their new location. Statement payment fields
are locked; correcting again provides the way back.

`make correct ARGS='<entry-id>'` prints a prefilled correction JSON. Save the edited
JSON and run `make correct ARGS='<entry-id> --file /tmp/correction.json --op-id <32-hex-id>'`.
HTTP and CLI use the identical locked service. Select a ledger with `LEDGER_DIR` or
CLI `--root`. A repeated ID with the same payload replays the audit result.

Usual payments file their receipt and save without a model call, then queue a
separate adapter task using a strict receipt schema. Only contained, non-symlink
files under `Bills/` can be read. The shared agent pause delays readings while human
work continues. Unknown facts are null. Matching details pass through corrections;
disagreements retain structured dates/amounts/accounts for page formatting and ask
Use the receipt / Keep the entry (`POST /api/drafts/{id}/receipt-choice`, `choice` =
`use` or `keep`). Either choice retains receipt notes. Changed split totals or
conversions require the correction form; statement locks also apply. Unreadable
receipts change nothing. Pending readings/questions bypass the completed-draft
24-hour expiry. Restart retains extraction results and operation identity.

Add uses one editing draft for words and attachments. `POST /api/add/interpret`
recognises statement words; `POST /api/drafts/{id}/interpret` uses the reader
registry and returns file choices, account, period, currency and checks to come.
Interpretation never starts an agent or writes financial data. PDF passwords are
accepted in an unvalidated JSON body to avoid echoing secrets in validation errors;
they remain in memory and are never part of a draft or response.

`POST /api/drafts/{id}/continue` rechecks the interpretation on the ledger's FIFO
worker. Each statement file gets its own existing statement session. Receipts and
expense words stay together in the expense draft. The response supplies both sets
of IDs. A failed reading leaves its file available while other sessions continue;
`POST /api/statements/{id}/retry` retries a failed reading. Ambiguous PDFs require
Statement / Receipt; currency mismatches require a matching account or a staged
new account. Account creation uses the existing checked settings service only on
Continue. Internal completed intake containers are omitted from draft lists and
expire after 24 hours. Retries return the retained intake receipt.

Add remembers up to three result IDs in guarded, ledger-specific session storage.
It fetches those records explicitly, including applied statements omitted by normal
queues. Result rows use the records' actual states. Undo uses the server's
eligibility and expiry; `undo_checked_at` lets its 30-minute countdown tolerate a
browser clock that differs from the server's. More never pauses that timer.
