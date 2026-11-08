# Verified saves and fresh views

Every server writer calls the same Python commit implementation as terminal
`make commit`: `scripts/ledger/commit.py`. The server imports it into its existing
process. It does not start make, uv or Python for a save. The ledger lock covers
verification, the commit and the main bookmark. Existing transaction journals
retain file/evidence backups and the jj operation needed to roll back a failure.
Commit messages, audit lines and one commit per operation are unchanged.

## Verification state

`review-dashboard/server/save_service.py` owns one validated baseline per ledger.
Only the ledger lock protects it, before `drafts.mutex` and `statements.mutex`.
It never acquires either mutex. The model memo has a short leaf lock and acquires
no other lock. Previews use ordinary model and strict checks in memory.

A full store check runs at startup. If another writer holds the lock, startup
keeps reads available and defers that check until it can acquire the lock, before
any save can use an index. An invalid or interrupted working copy leaves
the index disabled: recovery and explicit corrections remain available, but their
commits must pass the full gate. A clean-working-copy check precedes writes. Before
using the baseline, the service compares every verification input's path, inode,
size, mtime and ctime, directory additions/removals, and repository head metadata
(including the contents of `.jj/repo/op_heads/heads`).
An external change forces a full reload. Writers must cooperate with `.lock`.

The baseline keeps IDs, statement membership and ordered account/currency posting
histories with exact running balances. Unchanged entries reuse their validated
models, keyed by composed model and exact JSON. Every changed entry still passes
its model; all cross-document identity/reference checks run. Byte-identical,
previously verified canonical documents are left unwritten only for the same
applicable model/extension definition. Audit lines reuse
schema/consent verdicts only for identical line bytes and schema; year placement
and ID uniqueness still run on every read. Strict posting checks
inspect changed entries, rules when changed, declarations when changed, and prices.
Assertions recompute only changed account/currency histories from their identical
prefix onward, including same-day posting order and assertions in another commodity.
Statement checking projects affected bank/card accounts, falling back to the full
shared checker when a transfer needs more context or the manifest's `startDate`
changes. Evidence, audit, canonical
bytes, price coverage and other store checks retain their shared implementations.

The index advances only after commit. Rollback restores its previous checkpoint
and invalidates its source stamp. Incremental refusals run through the full strict
checker to retain its exact reason and structured balance refusal. If the incremental
strict checker refuses something the full checker accepts, the save fails closed.
A valid incremental result is not redundantly checked in full at runtime; the
mutation and mixed-save agreement suites check that direction. `make verify` always
runs the full check without server indexes.

Add and Review share their loaded documents and audit lines within one locked
apply instead of rereading them at each helper. The final gate still reads and
checks the written candidate, including new audit lines; no verification is removed.

## Dashboard publication

`review-dashboard/server/data_refresh.py` writes `drafts/data-refresh.json` durably
before commit, marking the views stale. It copies builder inputs under the ledger
lock into a private immutable snapshot in `LEDGER_DIR/drafts/dashboard-inputs/`.
Raw store documents stay with the ledger. No evidence files or authentication homes
are copied. A successful commit queues an ineligible candidate. Only its own
durable success receipt and completed recovery-journal boundary authorize that
candidate to build or publish; an older request cannot authorize a replacement.
Result reads and the response refresh helper never activate a candidate.
The save then answers while the build runs;
hledger's existing independent background check is still requested after every
server commit, including bank-statement Apply.

At most one dashboard build runs and one waits. A newer waiting snapshot replaces
the previous waiting one. Only the newest requested snapshot can publish. The Node
builders share one process and one validated store load. Dependencies select the
builders affected by changed inputs; unchanged output documents are carried forward.
Every generation still runs dashboard data validation and gzip publication
(level 6; decompressed JSON is unchanged).
Published `provenance.json` records the build day and builder identity, including
shared Node helpers. A forced rebuild remains outstanding across superseding
candidates until a generation satisfying it publishes. Old outputs are reused
only when their day and builder match. Unexpected startup failures become refresh
errors while the server remains available. Async daily failures back off for ten
minutes, and maintenance never replaces an in-progress Retry.

The browser first reads `GET api/data-status`, then pins every file to the same
immutable `GET api/data/<generation>/<name>.json.gz`. Whole files and a complete
successful generation are published before readiness changes. The page keeps the
last complete views labelled stale while work runs, polls readiness every 250 ms
only while stale, and reloads automatically. It clears the stale label only after
all files load. Older health/status responses and superseded data loads cannot
replace newer state. Observing a new server epoch retires the previous epoch, so
its late ready reply cannot relabel old totals as current. Hidden pages pause
readiness polling. The normal live health
poll also discovers data changes from another tab or device.

A fresh page waits for an active rebuild before choosing setup or entry routes.
An existing page keeps its complete prior views. Stale data cannot reopen
completed setup; the active setup flow retains its acknowledged name/currency
across Back while its generation is still building.

A committed Add remains in Just added while data catches up; Open entry becomes
available when that generation contains it. Queue results, statement outcomes and
corrections acknowledge the commit immediately. A failed build retains the old
complete generation and a visible failure; `POST api/data-refresh` retries without
making a financial commit. A killed process leaves the durable stale marker; the
next start rebuilds. A terminal commit or sync is detected by maintenance within
30 seconds. A local-day change rebuilds all date-sensitive data.

Old generation inputs are removed after building. Complete output generations
remain for at least ten minutes **after being superseded** for in-flight readers;
the newest generation is
always retained. Generated immutable outputs live under `DASHBOARD_DATA_DIR/.live-data/`; the usual
`app/data` and `public/data` locations remain compatibility outputs, replaced one
file at a time. Source provenance is published atomically and continues to guard
the output root. Cleanup/status I/O failures cannot strand a dead builder as
running; a failed thread start keeps the receipted financial commit and exposes a
retryable refresh error.

## Queue wakeup and measurement

The FIFO worker wakes on an event from producers. It clears the event before
inspecting work, so a notification arriving during inspection is retained. Its
idle deadline is the earliest of maintenance (30 seconds), agent-pause expiry,
claim expiry, or 0.5 seconds while a statement waits for another ledger change or
check. Completed ledger and pause checks signal the worker directly. This is not
a shorter queue poll. Later first-run statements on the same account wait for
earlier imports before replanning their history.
Expiry maintenance does not traverse addition history. Deletion eligibility and
limit previews refresh the read-only history cache under the ledger lock before
using it, so outside commits are current at the decision that needs them.

Save timing lines report the actual commit boundary separately from the response.
The benchmark waits for the background build outside its response measurement and
reports build duration and commit-to-fresh-data time. Its Add pickup measurement
and the held-worker event test check the 200 ms target.
