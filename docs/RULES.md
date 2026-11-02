# Recurring rules

Rules live in the selected ledger's `data/forecast.json`; they are commitments,
not recorded expenses. Only lifecycle `active` rules forecast or match typed words.
Missing lifecycle means active. `status` still means confirmed/pending and is
independent of lifecycle. Paused rules have no resume date.

The Python logic is `scripts/ledger/recurring.py`. A whole payment is identified by
its main expense accounts and one paying account with bank/card/cash/wallet metadata.
Fees, taxes, discounts and rounding do not supply main expense accounts. Amounts
can vary. Detection additionally requires shared payee words and same-commodity
amounts within 10% of the earlier payment. Two consecutive calendar months, 25–35
days apart, with the latest payment within 40 days qualify. Older or irregular
patterns are suggestions. Subscription/membership/renewal words qualify directly;
cancellation words do not. Yearly/annual words choose a yearly schedule.

`data/recurring-settings.json` is optional:

```json
{"never-automatic": ["Expenses:Groceries", "Expenses:Transport:Fuel", "Expenses:Food"]}
```

Prefixes match whole account segments. Rules already covering the main payment
prevent automatic creation. Removed rules retain their removal date and evidence;
two qualifying payments dated strictly after removal are required to return, with
`origin.came_back: true`. Paused and ended patterns are not recreated automatically.
A confirmed hand or agent addition uses this same detector and links/advances its
unique whole-payment rule in the same checked commit. A paused match offers a
resume choice in the saved result without changing the lifecycle.

Calendar advancement skips every unrecorded period through the payment's date.
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
| GET `/api/rules` | `rules`, `suggestions`, `history`, `undo`; each rule includes lifecycle, `version`, `last_payment`, `next_due`, `rule_status`, latest outstanding `review`. Status is on-track, due, overdue, stale, paused, ended or removed. |
| GET `/api/rules/match?text=…&today=YYYY-MM-DD&currency=EUR` | Ranked `matches`, `pick_list`, unambiguous `rule_id` or null. A description/category name word is mandatory; date words are excluded from amount ranking. The page supplies local today and currency; browserless defaults use the server date and ledger default currency. |
| GET `/api/rules/from-entry/{entry-id}` | Complete owner-rule proposal and `entry_id`; no write. |
| POST `/api/drafts/{draft-id}/usual` | `rule_id`, optional `date`, positive decimal-string `amount`, `update_rule`, `account`, `remember_account`, `another_anyway`, `client_time`, `client_zone`. Returns the queued draft; poll the existing draft endpoint for completion. |
| POST `/api/rules/operations/{32-hex-operation-id}` | Operation body below. Identical retry returns `replayed: true`; a changed payload with the same ID is refused. |

A usual payment keeps the original draft FIFO position. An explicitly paid
occurrence is consumed even when its payment date is a little earlier, so it
cannot remain in the forecast after being paid. New drafts use the local
save date unless `date` is supplied. The rule is frozen by hash and rechecked under
the lock; an intervening edit requires another explicit choice. A known payment
in that cadence bucket is refused unless `another_anyway` is true. `amount`
scales the balanced posting set; rounding must pass the ordinary strict checks.
`update_rule: false` changes this payment only. `true` retains its checked postings
on the rule. A missing payer asks for `account`; `remember_account: true` changes
the rule's payer. The account must use the planned payment currency. Complex
conversion changes require editing the balanced rule postings first.

Costs are retained as planned conversions. Foreign usual payments do not ask for
a rate; the entry note, saved result and audit mark the estimate and explain that
statement reconciliation will correct it. Attachments use the ordinary upload,
filing and compression path. No agent runs for Add as usual.

The added result includes `rule`, `next_due`, `made_rules`, `rule_changes`,
`resume_rules`, `weekly_review_note`, `audit_id`, optional `usual_payment`, and
server-computed `can_undo`. An automatically made rule may be edited with `edited`
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
- `{"action":"undone","undoes":"<draft-audit-id>"}` reverses an addition.
  Eligibility is at most 30 minutes after its audit timestamp, only while its
  addition commit remains the latest ledger change and the ledger is clean.
  The service checks again under the lock. A new reversing entry cancels the
  original postings on the original date and restores earlier rule schedules;
  an automatically made rule is retained as removed with suppression evidence.
  Original entry, evidence and audit remain. No repository history is rewritten.

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
make rules ARGS='undo <draft-audit-id>'
```

The underlying entry point is `scripts/ledger/rules.py`, with `list`, `match`,
`operations`, `backfill`, `catch-up`, `undo`. All return JSON. Write commands accept
`--op-id` for durable retry. They never invoke an adapter or model. The 0 → 1
upgrade prints the stale-rule count and catch-up command but changes no rule.

## Audit and format

Format 1 remains unreleased; no version bump is needed. Forecast rules add optional
`lifecycle`, `origin` (`kind`, optional `words`, `evidence`, `came_back`), `created`,
`removal` (`date`, `evidence`) and `schedule.anchor-day`. Actual entries optionally
link `rule: {id, occurrence}`. Origin/removal IDs and actual links are checked
against the store. Preferences have a closed schema.

Version-1 draft audit events optionally add `usual_payment` (rule_id, occurrence,
request_hash, estimated, update_rule, another_anyway, remember_account) and
`rule_changes` (id, before, after). This captures the addition and rule changes in
one line and one commit. Standalone `kind: rule` events require `rule_action`,
`request_id`, `request_hash`, `rule_changes`; they may include `rule_id`, `undoes`,
`reversal_id` and local save context. Actions are made, edited, paused, resumed,
ended, restored, removed, kept, undone, catch-up, backfill.

`kind: rule-review` events require `rule_id`, `rule_verdict` (confirm or
propose-removal), `reason` and `reviewer`. They never authorize an edit. The owner's
Remove/Keep is a distinct rule event. Existing audit lines are untouched. Draft
recovery and operation receipts live under ignored `drafts/`, outside the store.
