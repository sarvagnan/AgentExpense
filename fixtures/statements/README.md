# Fictional statement inputs

Every file contains invented names, dates, references and values. Each persona
has a mapped bank CSV, XLSX, XLS, an example card PDF and `account.json` describing
the bank account configuration. They are reader inputs, not applied expenses.
Source persona stores are deliberately unchanged. Tests install configurations
in disposable ledgers; an unused card uses the matching persona's metadata.

Regenerate from the application root:

```sh
PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp uv run --locked --directory review-dashboard/server python ../../fixtures/generate_statements.py
```

The generator is the source of truth for binary content. It computes the bank
export's opening from that fictional persona, then writes three invented rows.
PDFs use the documented example card layout and cover each persona’s unbilled
fictional card activity plus two invented rows; long exports repeat the table
heading on continuation pages. They can be applied in disposable copies of every
persona while preserving existing postings. See [reader documentation](../../docs/READERS.md).
