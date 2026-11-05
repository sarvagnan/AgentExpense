# launchd agent (macOS)

[`org.example.agentexpense.plist`](org.example.agentexpense.plist) starts the server
at login and restarts it if it stops. Make a private copy outside the application:

```sh
cp docs/deploy/org.example.agentexpense.plist ~/Library/LaunchAgents/org.example.agentexpense.plist
```

In the copy, replace:

- `/path/to/AgentExpense` with the application folder and `/path/to/ledger` with
  your ledger (absolute paths);
- `/path/to/uv` with the output of `command -v uv`;
- the `PATH` value with a list that includes the project's Node
  (`dirname "$(fnm exec --using=.node-version -- which node)"` from the application
  root), and the folders holding `hledger`, `jj` and `git`;
- `/Users/you` with your home folder, for the logs;
- the access settings, as in [README.md](README.md). The example is local only
  (`EXPENSES_DEV` is `1`); for a proxy or a tailnet, remove `EXPENSES_DEV` and set
  `EXPENSES_BASE_PATH`, `EXPENSES_ALLOWED_HOSTS` and `EXPENSES_ALLOWED_LOGINS`.

Build the dashboard and load the agent:

```sh
export LEDGER_DIR=/path/to/ledger
make build-dashboard
mkdir -p ~/Library/Logs/agentexpense
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/org.example.agentexpense.plist
```

Restart it with `launchctl kickstart -k gui/$(id -u)/org.example.agentexpense`, and
remove it with `launchctl bootout gui/$(id -u)/org.example.agentexpense`. You may
rename the label; use the same label in the file name, the `Label` key and the
commands.

So that `make update` restarts it after an update, add to the application's
`local.json`:

```json
{"service": {"launchd": "org.example.agentexpense"}}
```
