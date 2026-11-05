# Personal statement readers

Put a statement reader here when the application's built-in readers (the example
card statement layout, generic CSV and generic spreadsheet) cannot read one of your
statements. Each reader is one Python module, `readers/<name>.py`, implementing
interface 1 described in the application's `docs/READERS.md`, which includes a
worked example.

Readers run inside the dashboard server with its permissions. Keep only code you
have read and trust. A reader never stores passwords: the server passes a decrypted
copy of a protected PDF and keeps the password only in memory.

Name the reader on the account it reads, in `data/accounts.json`:

```json
"statement": { "reader": "<reader ID>", "identifier": "<last digits printed on the statement>" }
```

Unknown formats can also be read by the dashboard's agent, with every value checked
against the file by code, so a reader is optional.
