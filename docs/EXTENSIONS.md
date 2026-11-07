# Extensions

The core entry ([STORE.md](STORE.md)) holds what every ledger records. Facts that
only some people record — fuel readings, the client a work expense was for, a
policy's term — are **extensions**: a named group of typed fields that a ledger
adds to every entry by listing the extension in `ledger.json`.

- **Built-in extensions** live in the application's `extensions/<name>.json`.
  Today there is one, `fuel`; its name is reserved.
- **A ledger's own extensions** live in the ledger's `extensions/<name>.json`,
  beside `readers/` and `checks/`.

## A definition

A definition is a JSON file that `schema/extension-definition.schema.json`
describes (generated from `ExtensionDefinition` in
[`scripts/ledger/store_models.py`](../scripts/ledger/store_models.py)):

<!-- example: ExtensionDefinition -->
```json
{
  "name": "work",
  "description": "Expenses for client work.",
  "fields": {
    "client": {"type": "text", "filled-by": "agent", "description": "The client this was for.", "journal-tag": "work-client"},
    "billable": {"type": "yes-no", "filled-by": "person", "description": "Whether the client is billed.", "journal-tag": "work-billable"}
  }
}
```

| Key | Meaning |
|---|---|
| `name` | Kebab-case; it matches the file name. |
| `description` | What the extension is for, on one line. |
| `fields` | At least one field, keyed by kebab-case name, in display and journal order. |

Each field has:

| Key | Meaning |
|---|---|
| `type` | `decimal` (exact decimal text, like an amount: `"8.50"`), `text` (one line), `whole-number` (0 or more), `date` (`YYYY-MM-DD`), `yes-no` (`true` or `false`; the journal prints `yes` or `no`) or `choice` (one of `values`). |
| `values` | For `choice` only: the allowed values, in display order. |
| `filled-by` | `agent`: the Add agent may fill it from the typed words and the evidence, and the person can edit it. `person`: only Edit entry sets it. Statements never set extension fields. |
| `description` | What the field holds. The agent reads it for `agent` fields. |
| `journal-tag` | The comment tag the journal prints the value under. It must not clash with a core label (`id`, `source`, `serial-number`, `source-row`, `source-page`, `statement-page`, `statement-end`, `reference`, `order-id`, `classification`, `original amount`, `reimbursement-group`, `reimbursement-label`, `variable-estimate`, `forecast item`), the tag hledger reads from a core label (`amount` from `original amount:`, `item` from `forecast item:`) or another listed extension's tag. |

## Using one

List it in `ledger.json`, in the order the journal should print them:

```json
"extensions": {"fuel": {"ask": true}, "work": {"ask": true}}
```

`ask` says whether the Add agent fills the extension's `agent` fields. With
`"ask": false` the agent isn't asked, and stored values stay valid, visible and
editable in Edit entry.

Then run, from the application root:

```sh
make extension-update NAME=work
```

It gives every entry the extension's group (`null`), under the ledger lock, in
one verified commit with a reviewable message (`Update extension work via make
extension-update`).

## In an entry

An entry's `extensions` has one key per listed extension, in `ledger.json` order;
`{}` when there are none. Each value is the extension's group (every field, each
`null` when unknown) or `null` when none of its fields is known. Rules have no
extensions.

The journal prints extension values after `original amount` and before
`reimbursement-group`, in `ledger.json` order and then field order, each as
`; <journal-tag>: <value>`.

The dashboard's entry details show each extension's values with their field
names, and Edit entry (and the hand form) has a control per field type. That is
generic: no extension has screens of its own, except fuel's (below).

## The agent

The answer's `entry.extensions` holds only the listed extensions with `"ask":
true`, each with only its `agent` fields. The prompt lists those fields with their
descriptions. The server fills `person` fields with `null` when it stores a new
entry. An answer with an unlisted extension, an `ask: false` extension or a
`person` field fails validation and goes back to the agent like any other check
failure.

## Changing one

Change the definition or `ledger.json`, then run `make extension-update NAME=…`:

- **an added field** gets `null` in every entry;
- **a removed or renamed field** is refused while any entry holds a value in it,
  with the number of entries; with no values it goes;
- **an extension removed from `ledger.json`** is refused while any entry holds its
  values; with none it is removed from every entry.

`make verify` reports entries whose groups don't match `ledger.json`, with a count
and the command to run.

## Fuel

`extensions/fuel.json` has `litres` (journal tag `fuel-filled-litres`) and
`trip-km` (`trip-km`), both `decimal` and `filled-by: agent`. Its screens stay in
the application: the Fuel Log panel in Review, the Fuel Add pattern and its litres
suggestions, the Help paragraph, `copy-fuel-log.mjs` (the ledger's
`data/fuel-log.json`), the fuel styles and the "confirmed from the fuel log"
marking. They switch on when `ledger.json` lists `fuel`, and read the readings
from `extensions.fuel`.

## The policy term

Format 1 had a core `policy-term` field. The 1 → 2 migration moves it into a
ledger extension, `policy` (`extensions/policy.json`, field `term-years`,
`whole-number`, `filled-by: person`, journal tag `policy-term`), made only when an
entry has a policy term, and lists it with `"ask": false`.
