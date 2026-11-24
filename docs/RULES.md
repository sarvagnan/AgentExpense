# Recurring rules

Rules live in the selected ledger's `data/forecast.json`; they are commitments,
not recorded expenses. Only lifecycle `active` rules forecast or match typed words.
Missing lifecycle means active. `status` still means confirmed/pending and is
independent of lifecycle. Paused rules have no resume date.
Use the rule’s date and schedule for the next occurrence, and keep rule notes for other details.

The Python logic is `scripts/ledger/recurring.py`. A whole payment is identified by
its main expense or investment destinations and one paying account with bank/card/cash/wallet metadata.
Fees, taxes, discounts and rounding do not supply main expense destinations. Amounts
can vary. Detection additionally requires shared payee words and same-commodity
amounts within 10% of the earlier payment. A costed investment compares its money
cost (unit price times units, or total cost), not its unit count. Two consecutive calendar months, 25–35
days apart, with the latest payment within 40 days qualify. Older or irregular
patterns are suggestions. Subscription/membership/renewal words qualify directly;
cancellation words do not. For a subscription, an explicit billing cadence read
from attached evidence takes precedence over typed words. Yearly, monthly and
quarterly cadences use the existing calendar schedules. Without a stated cadence,
the rule starts monthly. The saved payment card shows the cadence beside Edit rule.

`data/recurring-settings.json` is optional:

```json
{"never-automatic": ["Expenses:Groceries", "Expenses:Transport:Fuel", "Expenses:Food"]}
```

Prefixes match whole account segments. Rules already covering the main payment
prevent automatic creation. Removed rules retain their removal date and evidence;
two qualifying payments dated strictly after removal are required to return, with
`origin.came-back: true`. Paused and ended patterns are not recreated automatically.
A confirmed hand or agent addition uses this same detector and links/advances its
unique whole-payment rule in the same checked commit. A paused match offers a
resume choice in the saved result without changing the lifecycle.

Calendar advancement skips every unrecorded period through the payment's date.
An early payment settles the next due occurrence when it falls within three days
of that date or is closer to it than to the previous occurrence.
Automatically detected rules start after any later matching payments that settle
their occurrences.
The schedule retains `anchor-day`, so January 31 → February 28 → March 31.
The generator emits a bounded set of yearly phases for anchors 29–31; February
uses `4 / gcd(year_step, 4)` leap phases. Node expansion uses the same anchor.

## API

Paths are relative to the configured base path. Existing write access checks
apply. Rule operations use the same per-ledger FIFO as additions and Review,
re-read policy under the ledger lock, require a clean ledger, keep recovery
journals, validate, audit and commit through the shared transaction service.

| Method/path | Contract |
| --- | --- |
| GET `/api/rules` | `rules`, `suggestions`, `history`; each rule includes lifecycle, `version`, `last_payment`, `next_due`, `rule_status`, latest outstanding `review`. Status is on-track, due, overdue, stale, paused, ended or removed. |
| GET `/api/rules/match?text=…&today=YYYY-MM-DD&currency=EUR` | Ranked `matches`, `pick_list`, unambiguous `rule_id` or null. A description/category name word is mandatory; date words are excluded from amount ranking. The page supplies local today and currency; browserless defaults use the server date and ledger default currency. |
| GET `/api/rules/from-entry/{entry-id}` | Complete owner-rule proposal and `entry_id`; no write. |
| POST `/api/drafts/{draft-id}/usual` | `rule_id`, optional `version`, `date`, positive decimal-string `amount`, `update_rule`, `account`, `remember_account`, `another_anyway`, `client_time`, `client_zone`. Returns the queued draft; poll the existing draft endpoint for completion. |
| POST `/api/drafts/{draft-id}/answer` | For a rule question, sends the displayed choice as `answer` and `rule_question.id` as `rule_choice`. An identical retry returns the draft. |
| POST `/api/rules/operations/{32-hex-operation-id}` | Operation body below. Identical retry returns `replayed: true`; a changed payload with the same ID is refused. |

A usual payment keeps the original draft FIFO position. New drafts use the local
save date unless `date` is supplied. The rule is frozen by hash and rechecked under
the lock; an intervening edit requires another explicit choice. A known payment
for the occurrence is refused unless `another_anyway` is true, which leaves the next due date in place. `amount`
scales the balanced posting set; rounding must pass the ordinary strict checks.
`update_rule: false` keeps the rule's planned amount and day. `true` uses the
payment's changed fixed amount or day for future occurrences, keeping the cadence.
The card requires a choice for those changes. The existing early-payment window
and three days after the assigned occurrence are within the date grace period.
When an agent proposal matches one rule, Queue asks the same question before
saving. The server rechecks the answer against the proposal, rule and occurrence
under the ledger lock. A missing payer asks for `account`. `remember_account: true` changes
the rule's payer. The account must use the planned payment currency. Complex
conversion changes require editing the balanced rule postings first.

Costs are retained as planned conversions. Foreign usual payments do not ask for
a rate; the entry note, saved result and audit mark the estimate and explain that
statement reconciliation will correct it. Attachments use the ordinary upload,
filing and compression path. No agent runs for Add as usual.

The added result includes `rule`, `next_due`, `made_rules`, `rule_changes`,
`resume_rules`, `weekly_review_note`, `audit_id`, optional `usual_payment`.. An automatically made rule may be edited with `edited`
or suppressed with `removed` (the payment remains). Paused matches return
`resume_rules`; the owner chooses `resumed` or leaves the rule paused.

Operation bodies:

- `{"action":"made","rule":{…}}`, or `entry_id` to use an owner proposal.
  A missing rule ID is allocated. Owner creation is explicit.
- `{"action":"edited","rule_id":"…","version":"…","rule":{…}}` patches
  description, postings, schedule, date or status. A schedule sets date to `from`;
  date alone updates `schedule.from`. Optional `version` rejects stale editing.
- `paused`, `resumed`, `ended`, `restored`, `removed`, `kept`: `rule_id` and optional
  `version`. Paused resumes; ended/removed restores. Removing retains all actuals
  and records every covered payment as suppression evidence. Keep records the
  owner's separate answer and settles the latest review proposal.
- `{"action":"catch-up","rule_id":"…"}` moves one stale rule past its latest
  payment. Omit `rule_id` to catch up all rules in one commit.
- `{"action":"backfill"}` makes all qualifying historical rules in one commit
  with `origin.kind: history-pass`. Suggestions remain proposals. A no-op writes
  and commits nothing.

Page operations may carry `client_time` and `client_zone`; their local calendar
date supplies creation/removal dates. Browserless commands use the server date.

## CLI

The installed server Python environment supplies the shared dependencies. Select
the ledger before changing directories; `--root` is also supported before the
subcommand. Read commands take the lock too. Examples:

```sh
make rules ARGS='list'
make rules ARGS='match "Willow membership 20 EUR" --today 2026-11-01 --currency EUR'
make rules ARGS='operations /tmp/rule-operation.json --op-id 0123456789abcdef0123456789abcdef'
make rules-backfill
make rules-catch-up
```

The underlying entry point is `scripts/ledger/rules.py`, with `list`, `match`,
`operations`, `backfill`, `catch-up`. All return JSON. Write commands accept
`--op-id` for durable retry. They never invoke an adapter or model. The 0 → 1
upgrade prints the stale-rule count and catch-up command but changes no rule.

## Audit and format

In ledger format 2 ([STORE.md](STORE.md)) every rule holds `lifecycle`, `amount`,
`origin` (`kind`, `words`, `entry-ids`, `came-back`) or null, `created`, `removal`
(`date`, `entry-ids`) or null, `bill` or null and `schedule` with `interval` and
`anchor-day`. Each actual entry holds `rule: {id, occurrence}` or null. Origin/removal IDs and actual links are checked
against the store. Preferences have a closed schema.

Version-1 draft audit events optionally add `usual_payment` (rule_id, occurrence,
request_hash, estimated, update_rule, another_anyway, remember_account) and
`rule_changes` (id, before, after). Queue saves also record the owner's rule answer
in `questions`. This captures the addition and rule changes in
one line and one commit. Standalone `kind: rule` events require `rule_action`,
`request_id`, `request_hash`, `rule_changes`; they may include `rule_id`, `undoes`,
`reversal_id` and local save context. Actions are made, edited, paused, resumed,
ended, restored, removed, kept, catch-up, backfill.

`kind: rule-review` events require `rule_id`, `rule_verdict` (confirm or
propose-removal), `reason` and `reviewer`. They never authorize an edit. The owner's
Remove/Keep is a distinct rule event. Existing audit lines are untouched. Draft
recovery and operation receipts live under ignored `drafts/`, outside the store.

## Dashboard

Forecast has Overview and Rules views (`forecast/?section=rules`). Rules shows
native amounts, full payment/category, cadence, next due, last paid, lifecycle and
weekly-review state. With no live server the built host snapshot is read-only.
Simple two-posting rules have amount/account/destination fields; multi-posting rules
and conversions retain their full balanced postings as read-only facts. The CLI
can edit those postings. Lifecycle actions and new rules use the same checked
operation API. Dismissing a suggestion uses `removed` with its proposed `rule`
and no `rule_id`; the server recomputes the current suggestion and retains a
removed rule with suppression evidence, so a reload does not bring it back.

Add guesses a rule under the words. A rule's name is its description and its
destination's last name. A word is distinctive when no more than two rules' names use
it and it names no category group (an `Expenses` segment with accounts under it),
account kind or paying account. A guess needs two shared distinctive words, or one
with the rule's amount (within 10%). The paying account only ranks guesses; most
payments share it. One distinctive word alone is only a quiet suggestion ("Is this
your …?", `suggestions` in `/api/rules/match`) that opens the card when chosen;
shared words alone suggest nothing. A rule whose name has no distinctive word but two
or more shared ones counts its whole name as one word: typed in full it is a
suggestion, and with its amount a guess. While a
fixed-amount card shows, Add as usual is the one primary action and Continue
becomes "Send to the agent instead". Not this only records `declined_rules` on the
editing draft and hides that guess. It never submits a payment or invokes an
agent. Live matching owns the choice; the built snapshot mirrors matching for
instant display. Add as usual supports a payment date, changed amount with either
scope, missing payer, remembering that payer, duplicate-period override, receipts
and planned conversion. Completed draft receipts supply next due, automatic-rule removal/editing and paused-match choices. These cards are
independent of Add's current two-panel layout.

Weekly review shows rule-removal proposals under **Queue › Needs you**. Open the
linked rule and choose **Remove** or **Keep**. See [weekly review help](../review-dashboard/server/WEEKLY-REVIEW.md#needs-you).

For recovery, `audit.py review --verdicts FILE --reviewer NAME` accepts outcome verdicts
`{line, verdict: ok|issue, note}` and rule verdicts
`{rule, verdict: confirm|propose-removal, reason}` in one batch. Every rule verdict
names an existing rule and needs a concrete reason. The single locked, verified
commit appends verdicts only. It never changes rules or entries. Owner Remove/Keep
is a separate rule-operation line. Both review kinds are excluded from
`list --unreviewed`; rule-operation outcomes remain included until reviewed.

The built snapshot also derives review state and paid periods from the append-only
audit: undone payments/reversals cannot appear as paid, and an owner's Keep/Remove
answer survives offline viewing. A completed payment's paused-match prompt is
settled by its subsequent audited Resume/Keep answer, including after a reload.

## Varying recurring rules and saved bills (6f B)

A rule may have `"amount": "varies"`; absence means fixed. Only balanced simple
two-posting payments in one currency with one paying account can vary. Costs,
split postings and assertions are refused. Automatic detection and matching
rank/tolerance remain unchanged. Existing rule notes remain untouched.

The estimate is stored in the postings. `recurring.recompute_estimate` computes
the exact Decimal median of the latest three payments (or all available payments;
none keeps the existing amount). Explicit links count; older unlinked history
requires the same main category, payer, commodity and shared payee words. Entries
linked to another rule and explicitly undone payments are excluded. No amount
tolerance excludes an outlier. Link, correction and switching to Varies call the
same service in the checked transaction. Part C can call it with its final actual
set for Delete and Restore; those operations are not added here.

A rule's `bill` is `{amount, received, pay-by, occurrence, evidence}` or null.
Amount is a positive decimal string in the rule's currency; dates are calendar dates
and `pay-by` may be null. Occurrence must be the rule's current occurrence.
`evidence` lists the filed bill paths.
Saving a bill leaves actual entries, totals and the schedule unchanged. The
hledger generator emits one bounded daily periodic rule at `pay-by` (otherwise
`occurrence`) and continues the main estimate from the following occurrence.
The Node forecast expands the same dates. A clamped multi-month cadence uses
unbounded calendar phases when needed to keep all main dates after the bill. Even a pay-by date after the following
occurrence does not consume that following estimate.

`POST /api/drafts/{id}/bill` accepts `rule_id`, `amount`, optional `date` (received),
`pay_by`, `amount_anyway`, `client_time` and `client_zone`. The draft ID is the
operation ID. Its files use ordinary evidence filing/compression. The FIFO runs
`operate`, the ledger lock, rollback journal, strict checks and `make commit`.
Identical retries recover the same bill result even after it is paid; changed
payloads are refused. Completed bill items expire like
other completed drafts, without expiring the stored bill.

The CLI uses the same `operations` command with `action: bill-saved`, `rule_id`,
`bill`, optional `text`, `amount_anyway` and local context; sources must already
exist in the ledger. `action: bill-removed` removes a saved bill and retains its
filed evidence. `edited` accepts `amount: varies` or `amount: null` (fixed);
remove an existing bill before switching to fixed or changing its occurrence.

The usual-payment endpoint requires an explicit amount for Varies unless a bill
is saved. With a bill, its exact amount and occurrence own the payment; paying it
clears the bill, retains sources on the entry, advances exactly its recorded
occurrence and recomputes the median. An early or late payment date does not
repeat that occurrence or skip the following estimate. Corrections retain the
saved bill’s occurrence, including when the payment date crosses a month. Delete restores the saved bill and recomputes the estimate; Restore pays it again and recomputes the estimate.
Varying payments keep their exact typed or billed decimal amount. Their
`update_rule` choice applies only to the day, and the estimate is still the
median. Fixed rules retain their scaling and rounding path.

An amount above twice the highest or below half the lowest of the last six
payments returns structured HTTP 409 `{kind: amount_check, amount, min, max,
currency}` until `amount_anyway: true`. Equality at either boundary is allowed.
Acceptance records the doubt in the audit and commit. It does not change the
matching tolerance or permanently block saving. With no history there is no check.

Version-1 `kind: rule` adds `bill-saved`, `bill-removed`, `bill-paid` actions,
optional `bill` and `amount_check`. Bill events retain typed words, evidence and
doubts. Paying appends its bill event in the same verified commit as the existing
addition audit. Rule changes retain complete before/after snapshots. Existing
audit lines remain valid.

Add shows about/estimate values and requires Paid or Bill only, neither selected
by default; no-amount estimates cannot be added. Saved bills offer one payment
action. Rules shows bill status/pay-by or the estimate and recent range. Its
editor switches fixed/varying mode and charts up to 12 payments, highlighting the
three used for the estimate with existing chart tokens. Forecast badges exact
bills and marks varying estimates/ranges separately from variable spending.

## Delete and Restore

A filed card statement covers its members, including purchases dated before its
period, so Delete is refused for them. Restore is refused while a card statement
covers the payment date, from its earliest member through its end, or a bank
statement covers it with its period or confirmed balance. A card's billed total is
not a confirmed balance on its closing date; see [statement readers](READERS.md).

Payment removal uses the shared retention service (`make retention`), with the
ordinary FIFO, ledger lock, rollback journal, strict verification and commit.
A deleted linked payment makes that occurrence due again. A paid saved bill
returns with its evidence. Restore pays the same occurrence and clears that bill.
Both call `recurring.recompute_estimate` on the final actual entries, excluding
entries in Trash. Historical reversal audit records remain valid and continue to
exclude their original/reversing entries from payment history. There is no current
reversal operation or reversing-entry command.

Rules can be sorted by Next due (default), Name, Amount or Paying account, in
ascending or descending order. `sort=due|name|amount|account` and
`direction=asc|desc` preserve the choice in the URL. Changing either select
replaces the current history entry, so keyboard selection does not add Back steps. Amount orders currency groups
first, then native amounts within each group; saved bills use their exact amount
and varying rules use the stored median. Multi-currency rules compare their sorted
currency lists before their respective amounts. Suggested rules retain their own
order. A `#rule-<id>` link scrolls to and focuses the rule independently of sorting.

## Regular investment contributions

A rule's destination is its positive main `Expenses:` postings or positive postings
into accounts declared with `kind: investment`. Expense fees, taxes, discounts and
rounding remain ancillary; investment account names do not use that exclusion.
The payer is still exactly one bank, card, cash or wallet account. Transfers
between those paying accounts are not candidates, even with subscription words.
Investment account metadata is required: an account's path alone does not make it
an investment.

Contributions use the same conservative detection, suggestions, suppression,
removal memory, payment linking and calendar advancement as expenses. Ordinary
Add, hand finish, statement Apply (card and balance-style), and Add as usual link
the unique matching investment rule in their checked transaction. Statement Apply
only links entries it creates. Every existing entry retains its link or absence of
a link, including a declined guess or a correction that removed a link. Apply
never creates rules; ordinary additions and explicit history detection still do.
Statement audit lines retain the rule changes; paying a saved bill also records
its bill-paid event with the filed statement and doubts.

A statement payment consumes a unique saved bill only when its payee, simple
payment signature, currency and exact amount fit. The bill's recorded occurrence
owns the payment, including an early or late payment date. It clears the bill,
retains its evidence, advances that occurrence and recomputes a varying estimate.
Ambiguous bills or a payment that does not fit remain unlinked with the rules
unchanged; they never prevent filing the statement.
The agent answer and prompt are unchanged: the server owns the rule link.

The Rules editor offers expense categories and investment destinations labelled
“Invest in”. Simple contributions in the paying currency support fixed amounts,
Varies, the median estimate and unpaid bills. An investment in units or another
currency keeps its full balanced postings in the read-only editor; use the rules
CLI to edit those conversions. Rules displays and sorts costed unit contributions
by their money cost; Add as usual scales from that same money amount. Incompatible investment currencies are disabled
when choosing a simple destination.

Rules labels contributions as money set aside. Rules and Forecast keep showing
contributions when the optional holdings view is disabled: cash still leaves the
paying account. The setting controls the holdings view, not those commitments.
Forecast's Money set aside figure is the sum of the
upcoming contributions in the selected currency and month; Cash still needed
(or Cash needed for a future month) adds those contributions to upcoming spending
commitments and the selected variable estimate. This is a payment budget, not a
bank balance projection. Recorded spending, the spending chart and comparisons,
category totals and the variable-spending estimate exclude contributions.

Every forecast item publishes its contribution amount explicitly, including zero
for withdrawals and other non-contributions. A salary split retains its income
label and full salary headline; only its investment portion counts as set aside.
A withdrawal adds nothing to set-aside or cash needed. Costed destinations consume
funding before foreign fees are allocated, so principal is never spending or
counted twice in cash needed.

Within Forecast's current month and twelve following months, each active rule
expands from its own stored next date. An unrelated later recorded entry cannot
hide an unpaid occurrence. Recorded-through dates and remaining variable estimates
still follow the existing per-currency calendar rules; recorded spending is unchanged.

The format 2 rule shape is unchanged; existing investment rules work without a
migration or upgrade command.
