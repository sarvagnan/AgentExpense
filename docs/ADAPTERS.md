# Agent adapters (interface 1)

AgentExpense's server validates every proposed answer and owns every ledger write.
An adapter receives a prompt, images, read-only lookups, a JSON schema, a monotonic
deadline and a cancellation event. It returns JSON and usage/tool/error metadata.
The same runner handles Add and the second reading of statements.

## Machine selection

The default is Codex. Select `EXPENSES_AGENT=codex|claude|pi|opencode|openai|anthropic`,
or create application-root `local.json` (ignored by git):

```json
{"agent":{"adapter":"claude","model":"claude-opus-5-5","effort":"high"}}
```

`EXPENSES_AGENT` takes precedence over `ADAPTER` (used by make commands), then
`local.json`. `EXPENSES_AGENT_MODEL` and `EXPENSES_AGENT_EFFORT` override the corresponding
machine values. CLI defaults are Codex `gpt-6.1-terra`/`xhigh`, Claude Code
`claude-opus-5-5`/`high`, and Pi/OpenCode `anthropic/claude-opus-5-5`/`high`.
Anthropic API defaults to `claude-opus-5-5`/`high`; OpenAI-compatible requires an
explicit model and base URL. Restart the server after changing machine settings.
No machine settings or authentication belong in a ledger commit.

`EXPENSES_STATE_DIR` overrides the default `${XDG_STATE_HOME:-$HOME/.local/state}/agentexpense`
(`~/.local/state/agentexpense` when `XDG_STATE_HOME` is unset). Each CLI has
its own `<state>/<adapter>-home`; Codex also retains `EXPENSES_CODEX_HOME` compatibility.
Other CLIs receive a dedicated `HOME` and XDG config/data/state/cache roots there.
The server never copies or links another agent's authentication or migrates another
application's state directory. CLI executables
come from `review-dashboard/node_modules/.bin`, installed by `npm ci`; PATH is
not searched for an agent. `EXPENSES_CODEX` is the legacy test/candidate override;
`EXPENSES_AGENT_BINARY` is the equivalent explicit offline double/maintainer
candidate override for the other CLIs. Do not use those overrides in deployment.

## Pins, enforcement and offline evidence

| Adapter | Exact npm pin | Enforcement | Offline verification |
| --- | --- | --- | --- |
| Codex | `@openai/codex` **0.166.0** | Dedicated CODEX_HOME, ephemeral workspace, read-only sandbox, disabled execution/discovery features; existing narrow renderer allow-lists retained. Base code-mode tools remain constrained by the sandbox. | Existing feature-list, home-name and `debug prompt-input` checks; original protocol/gate regressions. |
| Claude Code | `@anthropic-ai/claude-code` **2.1.316** | Dedicated HOME/CLAUDE_CONFIG_DIR; `--safe-mode --restricted`; empty `--setting-sources`; `--tools ""`; exact lookup `--allowedTools`; strict MCP file; disabled slash commands/Chrome; no session persistence; noninteractive permission prompts denied. | Installed `--help` confirms every isolation flag. Generated MCP configuration, offered tool names and home configuration names are inspected. Managed settings at standard locations cause refusal. No no-model prompt renderer is exposed by this package. |
| Pi | `@earendil-works/pi-coding-agent` **1.4.0** | No built-in tools, extension discovery, skills, prompt templates, themes, context-file discovery, project trust or built-in MCP; only the application's `pi-lookup.mjs` extension and explicit lookup tools. Dedicated HOME/PI_CODING_AGENT_DIR. | Packaged argument/resource-loader/RPC/auth source and docs; actual resource-loader render registers the application extension without authentication or a model. Generated extension config and home names are checked; the sole allowed application hook is session-shutdown cleanup and no extension commands are allowed. |
| OpenCode | `@opencode/cli` **2.2.5** (v2) | Private `run --standalone`; dedicated XDG/HOME; no project/Claude discovery; ordered deny-all permissions followed by exact `ledger_<tool>` exceptions; skills/plugins denied; no warming/snapshots/updates; server validates final text JSON. | Installed run/debug/auth help and matching v2.2.5 source tag: config schema, MCP namespace/permissions, JSON events and standalone server. Generated policy/configuration and home names checked; actual no-model `debug config --standalone` must match the sole generated document/directory. |
| OpenAI-compatible / Anthropic API | Python lockfile's `httpx` **0.28.1** | Server-owned in-process lookup loop; no agent CLI, shell/file tools, hooks, skills or instruction discovery. | Both HTTP shapes exercised against local fake servers, including tool results, structured output, typed outages, cancellation, limits and secret redaction. Offline check makes no HTTP request. |

**OpenCode does not declare full isolation.** Its permissions deny execution, but
the CLI retains built-in shell/file tool definitions and can load managed
customizations. It cannot meet the requirement to remove those tools entirely.
The gate refuses it unless the operator explicitly sets:

```json
{"agent":{"adapter":"opencode","model":"anthropic/claude-opus-5-5","effort":"high","allowUnisolated":true}}
```

Settings and the agent-status API disclose that choice. A custom adapter declaring
`isolation=false` has the same refusal/opt-in rule. Opt-in never bypasses offline
allow-lists, schema checks or ledger write validation.

Claude's **safe mode** preserves subscription login. `--bare` is deliberately
unused: in this pin it excludes OAuth/keychain authentication and requires an API
key (or helper). Safe mode retains admin-managed policy; the checker refuses known
managed settings rather than claiming it can ignore them. Claude images use
stream-json user image blocks and its final result uses `structured_output`.

Pi 1.4.0 now has built-in MCP support; this adapter explicitly disables it and
ships the small lookup extension specified by the application design. The extension
speaks the application's MCP stdio protocol, registers only the enabled lookup
names, and closes its child on shutdown. RPC input stays open until
`agent_settled`; cancellation sends `abort` and kills/reaps the process group.
Images use RPC image content. Final assistant text must be JSON and pass the server
schema. No regex extraction or fenced-JSON fallback is used.

OpenCode v2 uses plural `permissions`, action `shell`, and `mcp.servers` configuration;
v1's `permission`/`bash`/top-level MCP forms are incorrect for this pin. MCP tools are
`ledger_<name>`. Images use repeated `--file`; events are `tool_use`, `text` and
`step_finish`. There is no output-schema flag. A probe may observe an explicitly
denied shell call; a successful non-lookup call fails the gate.

Native Claude/OpenCode packages do not ship their full JavaScript source under
node_modules. Their installed help/package metadata were inspected; OpenCode's
matching [v2.2.5 source](https://github.com/anomalyco/opencode/tree/v2.2.5) supplies
its config, permission and event definitions. Pi's checked source is under
`node_modules/@earendil-works/pi-coding-agent/dist/` and its docs under `docs/`.
Relevant primary API references: [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create),
[Anthropic structured output](https://platform.claude.com/docs/en/build-with-claude/structured-outputs),
and [OpenCode v2 permissions](https://dev.opencode.ai/v2/docs/permissions/).

## Direct API configuration

OpenAI-compatible uses Chat Completions (`/chat/completions`), with function tools,
`tool` result messages, and `response_format.json_schema`. Anthropic uses Messages
(`/messages`), `tool_use`/`tool_result` blocks and `output_config.format`.

```json
{
  "agent": {
    "adapter": "openai",
    "model": "your-model-id",
    "effort": "high",
    "api": {
      "baseUrl": "https://api.example.invalid/v1",
      "keyEnv": "EXPENSE_API_KEY",
      "structuredOutput": true,
      "sendEffort": true,
      "maxTurns": 12,
      "maxTools": 40,
      "maxTokens": 100000,
      "maxOutputTokens": 8192,
      "retries": 2
    }
  }
}
```

Use the actual provider base URL/model. Anthropic defaults to
`https://api.anthropic.com/v1`. Defaults for keys are `OPENAI_API_KEY` and
`ANTHROPIC_API_KEY`, respectively. `keyFile` may name a dedicated API-key text file
(absolute, or relative to the application); it is used when the environment key is
absent. Conventional agent authentication stores are refused. Never put the key
itself in local.json. The application does not log keys, headers or HTTP response
bodies and excludes them from run records and gate fingerprints.

HTTPS is required except for loopback HTTP fake servers. Environment proxies and
redirects are disabled. Set `structuredOutput:false` only when a compatible
endpoint lacks schema output; the prompt still requests the schema and the server
always validates. Set `sendEffort:false` for endpoints without the effort field.
Effort maps to OpenAI `reasoning_effort` or Anthropic `output_config.effort`.
Unknown tools and invalid arguments are refused before invocation. The optional
old-ledger lookup is offered only when configured/readable. Transport, 429 and 5xx failures return typed errors immediately to the shared
ledger pause. Authentication is rechecked through the gate. Validation and policy
failures are never hidden by local transport retries.

## Gate, owner login and upgrades

```sh
make agent-check ADAPTER=claude        # offline only, no model or sign-in
make agent-login ADAPTER=claude        # owner only: isolated interactive login
make agent-check LIVE=1 ADAPTER=claude # owner only: fixture probe, result retained
```

Use `codex`, `claude`, `pi`, `opencode`, `openai` or `anthropic` as appropriate.
For Pi, `make agent-login ADAPTER=pi` opens its isolated CLI; the owner types
`/login`, chooses a provider, completes login and exits. For API adapters the login
target prints the key-configuration instruction; it does not launch a CLI.
Other login commands are Codex `login --device-auth`, Claude `auth login`, and
OpenCode `auth login --standalone`, always under the dedicated child environment.

The shared gate fingerprints adapter/interface/pin/version, model, effort,
isolation declaration/opt-in, source policy, launch flags and allow-lists. New
fingerprints require the offline check and one deliberate live fixture probe:
look up an account, refuse shell execution and return schema-valid JSON with tool
result evidence. A passing fingerprint or verified policy failure is retained in
`<state>/agent-check.json`. Manual LIVE checks use this same cache; repeating a
matching passing check does not spend another probe. Sign-out is checked again
before each run and never cached as a permanent failure. Shutdown cancels a check;
transient/incomplete diagnostics do not replace the last cache.

Agent status continues through the existing Add/Queue/Settings components.
Settings names the adapter/model/effort; signed-out notices give the selected
`make agent-login ADAPTER=…` command (API notices explain key configuration).
Paused checks block agent submission; built-in statement parsing remains usable.

Maintainers, after reviewing a new CLI's help/source, use:

```sh
make agent-upgrade ADAPTER=claude VERSION=x.y.z DRY_RUN=1
make agent-upgrade ADAPTER=claude VERSION=x.y.z
```

Omit VERSION to query npm's candidate version. A candidate is installed/checked
under `/tmp`; no model or login is run, and the only tolerated Codex failure is an
empty-home login. A successful non-dry run updates the exact npm manifest/lock pin;
a failure preserves both. Review any allow-list widening, then have the owner
rerun the live check. Host/global CLI updates never change the application pin.

Offline evidence does **not** establish subscription/model entitlement, live image
interpretation, provider schema compatibility or runtime tool removal. Those are
what the owner-run probe and a subsequent fictional image draft must confirm.
No real live probe or login was performed while implementing this phase.

## Custom adapters

Register trusted operator code in machine local.json:

```json
{"agent":{"adapter":"custom","module":"ledger:adapters/my_adapter.py","model":"custom-model","effort":"high"}}
```

`ledger:` resolves in the selected ledger. An absolute module path is also allowed;
other relative paths resolve from the application. Registration is explicit and
never discovered from model output, instructions or uploaded files. The module
exports `create_adapter(settings)`. Operator modules execute in the server process;
they must themselves honor the server's isolation/secret contract.

Use the dataclasses in `review-dashboard/server/adapters/base.py`:

- `describe() -> Description`: `id` must match selection, `interface_version=1`,
  display name, pinned version, truthful `isolation`, and an enforcement explanation.
- `run(Task) -> Result`: task has `prompt`, `images`, `lookups.command` (MCP stdio),
  `lookups.definitions`, `lookups.call(name, arguments)` (in-process), `schema`,
  monotonic `deadline`, `cancel` (`threading.Event`), and a disposable `work` directory.
  Call `task.remaining()` around bounded I/O/tool work. Return `answer`, numeric
  `usage`, normalized `lookups`, safe `errors` and `duration`. Raise `AdapterError`
  with partial usage/lookups, never raw provider diagnostics. The server revalidates
  all answers and does all writes.
- `offline_check(work, stop=None)`: no model; render/inspect every offered tool,
  MCP server, instruction, skill/plugin/hook and settings source; fail closed on any
  extra/missing source using `OfflineFailure` or `PolicyFailure`.
- `live_probe(work, account, stop=None)`: deliberate fictional probe only; validate
  both final JSON and the successful account lookup's actual tool-result evidence.
- `signed_in(work, stop=None) -> bool`, `login_command() -> list[str]`, and
  optional `environment() -> dict`: only dedicated homes, never copied/linked authentication.
- Optional gate hooks `version(work, stop=None) -> str` and `fingerprint() -> JSON object`:
  include exact executable/package identity, all launch settings, tool allow-lists
  and custom dependency source hashes; omit secrets and volatile work paths. Without
  these hooks, the gate uses the declared pinned version, description and custom
  module source hash. The six required contract methods suffice. Login defaults to
  a dedicated HOME/XDG environment when no environment hook is provided.

Do not expose write tools through lookup callables. `Result.lookups` uses the
existing `{type, server, tool, arguments, status, error, result}` shape. Run records
add optional `adapter`/`errors` fields to audit version 1; old records remain valid
and are never rewritten. A custom non-isolating adapter needs the same explicit
`allowUnisolated:true` as OpenCode and is disclosed in Settings.

Add offline doubles/tests before registration. All tests, fake credentials and
homes must remain under `/tmp`; no test may run a real model or sign in.

### Failure contract

`AdapterError` carries `kind` (`usage_limit`, `auth`, `transient`, `bad_answer`),
optional ISO `reset_at`, bounded provider `errors`, `stderr_tail`, usage and lookup
metadata. CLI error events fail a run even with exit status zero. Adapters classify
provider events and HTTP statuses; the worker never classifies diagnostic text.
Credentials are redacted from retained diagnostics. Custom adapters should use
these fields; an omitted kind means `bad_answer` for compatibility.

The ledger's ignored `drafts/agent-pause.json` retains one outage episode across
restarts. Usage limits retry after 15, 30 and 60 minutes (or the supplied reset
instant); transient episodes use 5, 15, 45 seconds then 15 minutes. Authentication
and isolation-check pauses recheck every 30 seconds. A successful answer resets
the episode. Outages retain the original queue position, run record and repair
allowance. Health/status and draft reads expose `agent_pause` separately from the
isolation gate. Parser-only statements and human proposals remain available.

API 429, authentication, server and transport failures now return immediately to
the shared pause; adapters do not perform rapid local outage retries. The legacy
`agent.api.retries` option remains accepted for configuration compatibility. Turn,
tool, token, output and deadline bounds remain enforced.
