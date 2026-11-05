# Local only (default)

The simplest setup: the server runs in a terminal on the computer that holds your
ledger, and only that computer can open the dashboard.

From the application root:

```sh
export LEDGER_DIR=/path/to/ledger
make build-dashboard
make serve
```

Open <http://127.0.0.1:8701/>. Press Ctrl-C to stop the server. Unfinished items stay
in the Queue and continue when the server starts again.

`make serve` sets `EXPENSES_DEV=1`, which lets this computer save changes without a
sign-in name. That is safe only because the server listens on 127.0.0.1: every
program running as you could already change your ledger files. Other settings work as
usual, for example `EXPENSES_PORT=8702 make serve`. Run `EXPENSES_DEV=0 make serve` for
a read-only dashboard.

To start it at login instead, use the [systemd](systemd.md) or [launchd](launchd.md)
example with `EXPENSES_DEV=1` and no other access settings.
