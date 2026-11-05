# systemd user service (Linux)

The application ships a generic unit,
[`review-dashboard/server/expense-dashboard.service`](../../review-dashboard/server/expense-dashboard.service).
`make service` fills in this installation's path, the selected ledger and `uv`, adds
the project's Node to `PATH`, installs it as
`~/.config/systemd/user/expense-dashboard.service` when it changed, reloads systemd
and restarts the service:

```sh
export LEDGER_DIR=/path/to/ledger
make build-dashboard
make service
systemctl --user enable expense-dashboard.service
loginctl enable-linger "$USER"     # optional: keep it running while you are logged out
```

The unit listens on 127.0.0.1:8701 with base path `/expenses/` and **no write access**.
Keep your machine settings in a drop-in, which `make service` and `make update`
never replace. Create it with `systemctl --user edit expense-dashboard.service`, or
as `~/.config/systemd/user/expense-dashboard.service.d/local.conf`:

```ini
[Service]
# Local only: this computer may save changes.
Environment=EXPENSES_DEV=1
```

or, behind a proxy or on a tailnet (see [reverse-proxy.md](reverse-proxy.md) and
[tailnet.md](tailnet.md)):

```ini
[Service]
Environment=EXPENSES_ALLOWED_HOSTS=expenses.example.internal
Environment=EXPENSES_ALLOWED_LOGINS=person@example.invalid
```

Then `systemctl --user daemon-reload && systemctl --user restart expense-dashboard.service`.
Logs: `journalctl --user -u expense-dashboard.service`.

So that `make update` restarts the service after an update, add to the application's
`local.json`:

```json
{"service": {"systemd": "expense-dashboard.service"}}
```

Run `make service` again after selecting another ledger. Never edit the tracked
unit in the application: `make update` refuses to run when the application has
local changes.
