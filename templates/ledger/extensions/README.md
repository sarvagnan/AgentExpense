# Your own extensions

An extension adds a group of typed fields to every entry, for facts the core
entry has no place for: the client a work expense was for, a warranty end date,
a reading from a meter. The application's `docs/EXTENSIONS.md` describes the
format, the field types and how the journal prints the values.

Each extension is one file here, `extensions/<name>.json`, for example:

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

List it in `ledger.json` to use it, `"extensions": {"work": {"ask": true}}`, then
run `make extension-update NAME=work` from the application root. That gives every
entry an empty group and commits the change. The application's built-in
extensions (such as `fuel`) are listed the same way, without a file here.
