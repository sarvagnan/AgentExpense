# Running the dashboard

The dashboard server (`review-dashboard/server/app.py`) always listens on
**127.0.0.1 only**. Choose how it starts and who can reach it:

| Example | Starts it | Reached from |
| --- | --- | --- |
| [Local only](local.md) (default) | `make serve` in a terminal | this computer |
| [systemd user service](systemd.md) | at login, on Linux | this computer, or through one of the next two |
| [launchd agent](launchd.md) | at login, on macOS | this computer, or through one of the next two |
| [Reverse proxy with a base path](reverse-proxy.md) | one of the above | your network, through a proxy that signs people in |
| [Tailnet or VPN](tailnet.md) | one of the above | your own devices on a private network |

Build the dashboard before starting the server (`make build-dashboard`), and again
after every update (`make update` does this itself). Examples use placeholders such
as `/path/to/AgentExpense`, `/path/to/ledger`, `expenses.example.internal` and
`person@example.invalid`; replace every one.

## Access settings

The server reads these environment variables when it starts. Set them in the service
definition, never in the application tree. The agent and update settings live in the
application's ignored `local.json` (see [ADAPTERS.md](../ADAPTERS.md) and
[RELEASING.md](../RELEASING.md)).

| Variable | Default | Meaning |
| --- | --- | --- |
| `LEDGER_DIR` | application `ledger/` | the ledger to serve (absolute path) |
| `EXPENSES_PORT` | `8701` | port on 127.0.0.1 |
| `EXPENSES_BASE_PATH` | `/expenses/` in `make serve`, `make demo` and these examples (`/` if unset) | the path the dashboard is served under. The dashboard build loads its files from `/expenses/`, so keep that value; `/` works only behind a proxy that removes the `/expenses` prefix |
| `EXPENSES_ALLOWED_HOSTS` | empty | extra host names browsers may use, comma-separated; `127.0.0.1` and `localhost` are always allowed |
| `EXPENSES_ALLOWED_LOGINS` | empty | sign-in names allowed to change anything, comma-separated (see below) |
| `EXPENSES_DEV` | off (on for `make serve`) | `1` lets any request that reaches the server save changes, without a sign-in name |
| `EXPENSES_STATE_DIR` | `~/.local/state/agentexpense` | queue workers, agent home and caches for this machine |

## Security notes

- **Reading** needs only a reachable, allowed host name. The dashboard shows every
  amount and serves your evidence files, so never expose the port beyond people
  you trust.
- **Changes** (adding, editing, deleting, saving settings) are refused unless either
  `EXPENSES_DEV=1`, or the request carries a `Tailscale-User-Login` header whose value
  is in `EXPENSES_ALLOWED_LOGINS`. A request from a browser must also come from an
  allowed host (its `Origin`). Tailscale's own proxy sets that header after signing
  the person in; any other proxy that signs people in can set it too, and must
  replace any value the browser sent.
- `EXPENSES_DEV=1` trusts every process and person that can reach the port. Use it
  only when the server is reached from this computer alone (the local examples).
  With a proxy or a tailnet, leave it off and list allowed sign-in names instead.
- Host names are checked on every request (`Host` header); add the names your
  proxy or tailnet uses to `EXPENSES_ALLOWED_HOSTS`.
- The server runs the in-app agent in its own home under `EXPENSES_STATE_DIR`, signed
  in once with `make agent-login`. Keep that folder private to your user.
- Restart the service after changing any of these settings. For `make update` to
  restart it for you, name the service in `local.json` (`{"service": {"systemd":
  "expense-dashboard.service"}}` or `{"service": {"launchd": "<label>"}}`).
