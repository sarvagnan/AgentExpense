# Live expense server

For fictional local checks, select `LEDGER_DIR="$PWD/fixtures/ledgers/asha"` from the application root before changing directories. The server checks `ledger.json` at startup and on requests; older formats require `make upgrade-ledger` (see [migrations](../../migrations/README.md)).

Build the existing dashboard with `make dashboard` at the application root.
From this directory run `EXPENSES_DEV=1 uv run app.py`; it binds to 127.0.0.1.
On the server host, `make service` installs the tracked `expense-dashboard.service` when changed, reloads systemd and restarts it. It stops with an explanation on hosts without a user systemd session. Customize the service template paths and access settings for your installation before using it.
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
| `EXPENSES_CODEX` | app `review-dashboard/node_modules/.bin/codex` |
| `EXPENSES_STATE_DIR` | `~/.local/state/expense-dashboard` |
| `EXPENSES_CODEX_HOME` | `<state>/codex-home` |
| `EXPENSES_AGENT_MODEL` | `gpt-6.1-terra` |
| `EXPENSES_AGENT_EFFORT` | `xhigh` |
| `EXPENSES_AGENT_TIMEOUT` | `600` seconds |
| `EXPENSES_OLD_LEDGER` | empty (history lookup disabled) |

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
| POST `api/drafts` | Optional JSON `{text, pieces, client_updated_at}`; returns editing draft (201) |
| GET `api/drafts` | Active drafts; `?include_added=true` includes retained results |
| GET `api/drafts/{id}` | Full record, including runs, questions, result and error |
| PATCH `api/drafts/{id}` | Partial `{text, pieces, client_updated_at}` while editing |
| POST `api/drafts/{id}/files` | Multipart `files` repeated; upload while editing (201) |
| GET `api/drafts/{id}/files/{file_id}` | Original upload for thumbnail/preview |
| DELETE `api/drafts/{id}/files/{file_id}` | Remove while editing |
| DELETE `api/drafts/{id}/uploads/{upload_id}` | Cancel one upload, including a late arrival |
| PATCH `api/drafts/{id}/unsent-answer` | `{question_index, answer, updated_at}`; keep an answer without sending |
| POST `api/drafts/{id}/restore` | Undo Discard within 30 seconds |
| POST `api/drafts/{id}/save` | Save time and FIFO queue |
| POST `api/drafts/{id}/answer` | `{answer: "…"}` from question state; queue again |
| POST `api/drafts/{id}/retry` | Failed draft; reset check-repair allowance and queue |
| DELETE `api/drafts/{id}` | Soft discard except while working (204); Undo for 30 seconds |

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

Codex runs with commands off, read-only sandbox, ephemeral temporary workspace,
closed stdin and the approved MCP settings. Images are passed unchanged; PDFs
provide up to 12 PNG pages at scale 2 and text from those pages. The runtime
policy, pieces, save time, earlier answers and server-check errors are in its
prompt. Execution/schema failures retry once; entry validation errors get one
agent correction. MCP sees all history by default; `--cutoff` and `--exclude`
remain available for offline tests. Missing old-ledger history is visible in the
lookup response; set the path if historical lookup is desired.

The app pins its own Codex through npm, independently of the operator's host Codex
and nightly updates. `make dashboard`, `make test-server` and `make agent-check`
install with `npm ci` only when the tracked lockfile is newer than the installed
lockfile (or no install exists). A missing binary asks for `make dashboard`.

The server and checker create `<state>/codex-home` if needed, and each Codex child
uses it without changing the server's environment. The operator's one manual step,
from the application root, is:

```sh
make agent-login
```

This runs the app's Codex `login --device-auth` interactively under the dedicated
home. Authentication is never copied or linked, and there is no fallback to the
owner's home. Keep the dedicated home free of config, AGENTS instructions, rules,
plugins and custom skills.

The server starts its isolation check in the background. Before each agent run,
the shared Add/Statements runner waits for that check. A key hashes the Codex
version, isolation flags, model and allow-lists. A changed key requires the offline
check followed by exactly one low-effort probe on a fresh fictional ledger.
`<state>/agent-check.json` retains a passing key or a verified policy failure; matching keys
skip both checks, including after a restart. A lightweight login-status check still
protects a cached pass if the agent has since signed out. A version change is detected before
the next run without restarting the server. A failed login is never cached:
signing in fixes the next draft without a restart.

`GET api/health` exposes `agent.state`: `checking`, `ready`, `signed out`,
`paused`, or `unavailable`, with `agent.reason` on failures. Signed-out drafts say
`The agent isn't signed in. Run: make agent-login`. Paused drafts include the
changed input categories and safe section/tool identifiers. Temporary diagnostic
failures report `unavailable` without changing the cache; the next draft or server
start checks again. Shutdown cancels diagnostic process groups before waiting for
the worker, and an interrupted check is never cached. These errors also
appear in draft run records and statement extraction failures. Visible Add/Drafts
notices belong to the later screens round; this round adds no UI notice.

`agent.py:ISOLATION_FLAGS` is shared by Add and Statements. The flags ignore user
config and execpolicy rules, remove skills and collaboration-mode instructions,
disable plugins/apps, user-input and sleep tools, file-image inspection, hosted
web/image generation, runtime memories, hooks and browser/computer automation.
`agents.max_threads=1` prevents spawning; the sub-agent tools and their role text
remain. The base prompt and code-mode `exec` remain too; its `apply_patch` tool
is constrained by the read-only sandbox. The ledger MCP approval mode stays
`approve`. For explicit diagnostics, from the application root:

```sh
make agent-check          # offline prompt-input diagnostic, no model
make agent-check LIVE=1   # one low-effort model probe, disposable fixture only
```

Both modes use a fresh fictional ledger, closed stdin and the real `command_for`
configuration under the configured dedicated home. Output contains names and counts, never
home file contents. Offline mode checks `codex login status`, home names and every
feature switch against `codex features list`. It rejects `config.toml`, `AGENTS.md`,
`AGENTS.override.md`, `rules/`, `plugins/` and skills outside bundled `.system`.
It renders the server model and supported flags in a temporary cwd. Codex 0.166.0's
`debug prompt-input` lacks exec's `--ignore-user-config` and `--ignore-rules`; these
are omitted from the render and covered by the home check.

The render allow-lists the base prompt, permissions, multi-agent role/mode,
environment context and deliberate prompt; top-level `functions.exec`,
`functions.wait`, `collaboration.*`; and nested `apply_patch`, the three MCP
resource tools, `clock__curr_time`, `mcp__ledger__*`. Extra sections or tools and
missing tool inventories fail. Debug does not start MCP servers, so ledger tools
may be absent. A failed login still allows the remaining offline checks to run.
Live mode checks the fixture account returned by a
successful lookup, valid schema JSON, a shell-unavailable result, and all JSONL
events for non-ledger tools. With shell tools disabled, unavailable is the expected
failure to execute; there can be no shell-call event. It never applies an expense
or commits. Rendering cannot certify runtime tool behavior; the automatic gate runs the
fixture probe on the service host after signing in. The dedicated home must be authenticated before a live run.

## Codex versions

Users never upgrade the app's Codex manually. The checked-in exact npm pin and
lockfile control the agent; updating the host Codex has no effect. Maintainers use:

```sh
make agent-upgrade VERSION=x.y.z  # exact version; omit VERSION for latest on npm
make agent-upgrade DRY_RUN=1     # latest offline check, keep the pin unchanged
```

The command installs the candidate under `/tmp`, runs every offline check under
an empty temporary home, and accepts only the expected login failure. On a pass
it updates the exact dependency and lockfile; on a failure it changes neither and
prints section/tool identifiers for review. It never runs a model or touches an
owner/service home. Review and commit any allow-list widening. Later, a scheduled
CI job in the public repository will run this command and open a pull request.

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
nested `make commit`/`make verify` subprocesses reuse its `jsonschema` even with
`UV_OFFLINE=1` and an empty `/tmp` `UV_CACHE_DIR`. Browser fixture APIs activate
the same environment explicitly while using its Python directly; neither path
needs pre-filled caches or a venv resync.

The suite uses unittest, TestClient, temporary fixture ledgers with their own jj
repositories and `tests/fake_agent.py`. It never runs a model or uses the network.
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
tests; `make verify` runs only the ledger suite. `EXPENSES_FAKE_CONFIG` belongs to tests
only and has no effect on the production agent runner.

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

Statements uses the same FIFO worker and ledger lock. It reads card statement
PDFs in the [example layout](../../scripts/statements/README.md) with Python PDFium character positions, then checks debits, credits, balance
arithmetic and the preceding filed balance. Extraction/check failures use the
restricted agent reader and repeat the same checks. No system PDF executable is
needed. A wrong-file result never imports rows. Already-filed statements link to
stored rows and cannot be applied again.

| Method/path | Purpose |
| --- | --- |
| GET `api/statements` | Sessions, filed summaries, declared accounts |
| POST `api/statements` | Multipart `file` (PDF, at most 25 MiB) |
| GET `api/statements/filed/{end}` | Stored summary and reconciliation rows |
| GET `api/statements/{id}` | Durable state, checks, checklist and result |
| POST `api/statements/{id}/unlock` | JSON `{password}`; memory only, never echoed |
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
PDF, membership and conversion changes, new entries, summary, reconciliation rows
and audit line in one verified commit. A failure restores file bytes and jj state.
Apply waits for a clean ledger; changed store contents invalidate an old checklist.

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
statements retain their PDF and decisions for 24 hours; `POST statements/{id}/keep`
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
