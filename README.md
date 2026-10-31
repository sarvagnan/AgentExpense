# AgentExpense

AgentExpense is an expense ledger application with a dashboard, evidence-backed drafts, statement reconciliation and an audit trail. A restricted Codex agent proposes entries; the server validates and applies them. JSON is the source of truth and hledger checks the generated journal. The current application uses INR and Asia/Kolkata, with fuel, investments and an example card statement reader.

The built-in [card statement reader](scripts/statements/README.md) uses a documented fictional layout.

This is early development. Setup by a coding agent comes in a later release.

## Application and ledger

Keep the application and personal ledger in two separate repositories. The default ledger directory is `ledger/`, which the application ignores. Set `LEDGER_DIR` to choose another directory; relative paths resolve from the caller's working directory. Use an absolute path across commands that change directories. Ledger Python CLIs also accept `--root`. Schemas belong to the application in `schema/`.

## Fictional example and checks

Install Python 3.11+, uv, Node.js 22.13+ and hledger on PATH. Every value in `examples/asha/` is invented. From the application root:

```sh
export PYTHONDONTWRITEBYTECODE=1
export LEDGER_DIR="$PWD/examples/asha"
make verify
make dashboard
make test-server
npm --prefix review-dashboard test
npm --prefix review-dashboard run build
python3 -m unittest discover -s scripts/tests -v
```

`make dashboard` installs the pinned npm dependencies when needed, generates dashboard data and builds static views. Server tests use disposable repositories and a fake agent; they run no model. Browser suites are separate and are not part of the commands above. See [dashboard documentation](review-dashboard/README.md) and [server documentation](review-dashboard/server/README.md).

Before committing, run `make scan LIST=/path/to/private-identifiers.tsv`. Keep that list outside the repository. Each line is `label<TAB>value`; output contains only labels and locations. Binary files are reported for manual inspection. Review the scanner's limits in `scripts/check_private.py`.

## Licence

MIT; see [LICENSE](LICENSE).
