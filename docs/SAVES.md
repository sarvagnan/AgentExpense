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


## Direct actions in the browser

The page owns a FIFO for direct writes (`review-dashboard/app/live/direct-writes.ts`), shared by
hand additions, corrections, retention, rules, bills, statement Apply, Review,
setup and settings. Reads and draft autosaves stay independent. A reserved status
slot shows Saving… immediately; each rule has its own reserved slot and retry
request. A later write waits until the earlier HTTP request is accepted. A synchronous
write holds that place through its commit; an accepted statement Apply also holds
it through completion. Other worker-accepted writes release it while
its own result stays pending. The
server's lock and verification remain the authority across pages and devices.
Identical in-flight requests share one result only when they are the newest choice
for that path and resource; EUR → USD → EUR retains all three choices. A second correction with an older
version is refused by the server rather than overwriting the first correction.

A queued worker response is not a saved result. Hand additions, usual payments,
bills and statement Apply keep their pending state until a result read reports
`added` with a result or `applied`. Receipt choices require their own completed
receipt correction, and nested receipt refusals remain structured refusals. Only
`queued`, `working` and `applying` keep polling. Network errors and 5xx result reads stay pending and retry. A 404 ends polling and releases the page FIFO with an interrupted, non-retryable notice: the result is unavailable and may already have saved; check the entry, statement history or Queue. It does not claim failure or Saved, and retains the form. Imports retain already known committed results without rereading purged records. Any known terminal state ends the wait with
its reason, including an unread or already-filed import, an
editing draft or an item in Trash. Polls wait 250 ms; after 480 waits the status
becomes “Still in Queue” and polls once a second until a result arrives. This is
not a failure and does not authorize another write. A refusal keeps its reason
and recovery controls; an impossible replay does not offer Retry. A successful
financial commit with stale views remains saved while data publication catches up.

Statement busy state and late replies belong to their session: another statement stays usable while Apply waits. A cancellation mark survives a lost Cancel response until the Apply job ends; cancellation is announced once. Confirmation remains known when another statement was shown, even if no announcement was made there. An already-filed refusal directs the person to the stored reconciliation in statement history.

Navigation does not cancel a queued write. Pending saves remain available in the
page's save shelf. A local pending or failed form slot carries feedback while
that form is shown, leaving its controls clear. Only that same job is omitted
from the shelf; unrelated work remains reachable. Feedback belongs to the mounted
form/item or Review request ID, so a reopened form never adopts an old Saved notice.
Long reasons are keyboard-scrollable, with a visible scroll cue on phones. Settings, rule edits and first-run fields have ledger-specific
session recovery; hand forms also keep their existing server claim/recovery path,
and Review keeps its existing browser outbox. Leaving a hand form during a
queued or retried hand save keeps its claim for that write. Kept correction forms retain their
original entry version: reopening after a different version was saved shows the
kept edits for copying, and refuses to silently rebase them. Discard kept edits
explicitly starts from the current saved entry. A late Add restore or save cannot
clear newer words. An ordinary Add save returns focus to the input only while
its initiating interaction is still current; a newer chosen focus stays put.
A late Settings or rule save cannot close a form that now holds
newer typing or a newer pointer/keyboard/focus interaction. Correction acknowledgement
retains the original form in place. Background bar/refresh-notice measurement
updates clearance without scrolling an older caret into view. Add retains its
measured page height while scrolled; returning to the top, explicitly clearing
the input, or an unchanged own save releases that reach.
Retained form setters keep the same identity across renders, so effects that
initialize untouched opening rows run only when their inputs change.

Reload warns while a write is in flight and retains its exact method, path, body
and operation identity in session storage. Recovery copies expire after 24 hours. The next page labels unfinished work
interrupted, preserves an existing refusal reason and its retry policy, and does
not silently start a new financial write. The person checks the entry or
Queue and can explicitly Retry the original request. Worker-backed retries first
read an operation-specific accepted or completed result. Hand accepts queued/working only with `finished_by` and no claim, or added with a result; account approval accepts queued only after its question is removed, or added with a result. Statement Apply accepts applying/applied. Usual payments and bills replay the exact POST so the server validates the original request body and returns its idempotent result. Other direct paths retain their original operation identities and server replay checks. Metadata retries read committed
settings under the ledger lock before deciding whether another write is needed.
Settings saves use explicitly edited fields instead of comparing against a
possibly older published dashboard. Every edited field enters the FIFO together,
so a later save cannot overtake a field waiting for an earlier response.
A successful shelf retry also acknowledges
the active hand form, composer or Review outbox. Newer usual-payment words start
a separate draft, so autosave cannot change the submitted payment’s audit text.
Ordinary saves, shelf retries and reload recovery share the same API completion
path for tombstones and notifications. A Queue discard keeps its last visible row
in a distinct pending-removal slot until the committed response, even when a poll
omits it. New composer words during that removal use another draft identity.
Restore clears its tombstone through that same completion path.
Server version, claim, consent and operation replay checks still apply. Closing the tab discards session recovery;
server-accepted work continues in Queue. No password or authentication is stored
by this direct-write recovery path.

First-run account forms retain their fields per statement session. A late
creation cannot close a form holding a newer name or kind, or overwrite another
mounted form's recovery. Its local Retry resumes creation and assignment; the
reserved feedback is scoped to the submitted account. Independent failed account
declarations and Review decisions remain retryable when a later action uses the
same endpoint. A fresh value supersedes an earlier edit of the same setting or a newly checked
attempt on that same draft; independent declarations and decisions stay separate.
An open account form stays mounted through statement re-reading. File headers
and account options wait while the person uses their controls; leaving the
controls or taking an explicit file action releases that snapshot. A newer
account draft must be saved or cancelled before finishing the import, and an
explicitly edited currency is never replaced by a later reading.
An account declaration still completes after leaving Import. Its follow-up file
assignment runs only in the same mounted import with the same file version and
account choice. A newer choice stays in place; after leaving, choose the created
account for the file when returning.

Words-only Add interpretation reads only atomically replaced `accounts.json` and
the manifest, without a full linked-store load or an exclusive lock on a typing
pause. If those reads straddle a commit, a hint may pair old account aliases with
the new locale. It writes nothing; the eventual save validates current linked
documents under the ledger lock.

Form and agent answer refusals translate model failures into practical field
instructions. A non-positive cost explains that refunds use a negative posting
amount with a positive cost. Model validation, strict checks and consent are
unchanged.

Local form recovery is created by edits, never by loading server defaults. Cancel
and Back clear browser copies; a successful write clears only the submitted
storage bytes, including after unmount. Newer edits survive. Kept rule drafts
retain their base version and expose the same stale/discard recovery as entry
corrections. Hand worker failures reclaim and recheck the form before Retry; an
uncertain correction with newer edits asks the person to compare the saved entry.
First-run completion clears its setup keys. Opening requests carry a durable
operation ID; explicit recovery reads the original committed result before
resubmitting. An accepted/imported set of statement sessions is also recognised.
Moving focus cannot strand a committed setup step; newer typing exposes Continue
without writing the opening balances twice.

Runtime data fetches use keepalive and a departure generation. Every asynchronous
continuation checks that generation before decoding, starting another fetch or
publishing. Persisted pagehide/pageshow resumes readiness checks, while a new
document still loads normally and real load failures retain their visible Retry.

Review keeps acknowledged form values mounted until the person closes or edits
them, even before the refreshed decision arrives. A further edit uses the returned
entry version; its submitted browser recovery is still cleared at commit. Merge
and import have their own request-scoped feedback. A recovery panel is shown for
a recovered outbox, rather than inserted above cards for each live request.
Statement feedback has a separate header row, and setup status/error text keeps
space so longer refusal text cannot wrap or move the action.

A failed bill removal retries that removal operation, rather than the rule
editor’s Save action. An account-currency form reopens with its own unfinished
submitted choice, so its local Retry cannot silently use an older server default.
Hand reclaim/recheck keeps the existing check-result space while disabling save;
a failed reclaim invalidates that result and directs the person back to Queue.
Review retains the Discard button’s place as a disabled Edits saved acknowledgement
until the person closes or edits the form.

An already-paid usual-payment guard appears after the action row, so a late
payment refresh cannot push the action down while newer Add words keep the card
open. The disabled action describes that guard; explicit consent still gates
another payment for the same period.

Slot Retry replays its displayed job ID and exact submitted body. Newer form
values are a separate Save. Kept fields, including an absent field and its base
version, clear together only when every captured value still matches. A form
mounted before recovery can adopt its own recovered owner when jobs publish.

Accepted statement Apply retains the page FIFO through its committed or terminal
result so later metadata/Hand writes cannot stale its checklist. Cancel queued
apply bypasses that FIFO. This page’s successful cancellation ends quietly with
“Apply cancelled. Your checklist is kept.” A review result carrying an error is
still a refusal. Other worker writes release the page FIFO at acceptance, including
recovered Retry. Failed result reads remain pending in Still in Queue and retry
after one second; only known terminal refusals authorize Hand reclaim/recheck.
First-run replanning publishes its final applying/terminal snapshot from a private
copy, never a transient found state. Hint reads return a blank hint during rollback
read/parse errors; locked save validation is unchanged.

Setup import consumes the same committed-session event for ordinary saves, local
Retry, shelf Retry and recovery. It merges the real applied sessions before
continuing. Newer files remain in Queue with a clear Continue, without another
import POST. Account replay also retains a newer statement-account choice.

A statement Discard replay completes the same local transition from its slot, shelf or recovery: clear its earlier refusal, remove the current statement card and show the Trash destination. The surrounding statement history region stays mounted.
Discard completion preserves focus on a connected control chosen outside the removed statement card. If the focused card control disappears, the Trash destination receives focus.


Page completion is observed through `live/write-completion.ts` for ordinary saves,
slot and shelf Retry, and recovered Retry. `useCommittedError` records the failed
job that produced a form's local error; only that job's committed result clears it.
A later failed job with the same words keeps its own error. Read and validation
errors remain separate. Coalesced declarations retain every participating owner.
Receipt choices use the shared committed callback to clear their local refusal and
refresh the real reading once, while newer composer words and focus remain intact.
