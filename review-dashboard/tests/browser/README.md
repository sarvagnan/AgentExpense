# Headless browser matrix

Run from the application root:

```sh
make browser-tests
ENGINES=chromium,firefox,webkit PERSONA=sam make browser-tests
make browser-capture
```

The default runs every engine and all four fictional fixture personas. Both targets capture the shell, notices, Settings, opening balances, currency views,
account dialogs and all step 4B Add/Drafts states. `CAPTURE=0 make browser-tests` runs assertions without captures.
`ENGINES` and `PERSONA` accept comma-separated subsets.

Use the installed Playwright **1.64.1** at `~/.cache/expense-playwright`. Chromium
uses its explicit headless-shell executable; Firefox and WebKit also launch
headless. Install that exact package and its three engine binaries before running.
On macOS, Firefox’s CoreFoundation app-data home is also redirected to `/tmp`
with `CFFIXED_USER_HOME`, avoiding the real browser directory and the
[upstream macOS 27 launch issue](https://github.com/microsoft/playwright/issues/42768).
The harness refuses any other package version. It never launches a desktop browser.

Each run copies application sources and each selected fixture to a unique
`/tmp/agentexpense-4b-browser-*` directory. Package links reuse installed Node
dependencies, while Vite caches and builds stay in the private copy. The installed
server virtual environment supplies Python dependencies. Each fixture becomes its
own jj-colocated repository with an isolated jj configuration. Settings and account
currency tests make real locked, verified commits only there. No source fixture or
application repository is committed.

The fixture host `serve.py` wraps the real production app and FIFO worker. Its
`/__test/*` controls exist only in that host: they configure the offline fake CLI,
seed temporary draft/statement records, and install or remove fictional opening
balances through the verified settings transaction helper. Agent notices exercise
the real gate and its signed-out, policy-pause and diagnostic-failure paths. Gate
status reads never invoke an agent. No model, login, authentication file or real
agent home is used. Requests outside the fixture's loopback server are blocked.

Profiles, downloads, build logs, server logs, traces, failure images, fake CLI and
agent state all stay under `/tmp`. Each server uses a free loopback port and is
terminated in `finally`; browsers and contexts are also closed on failure. Artifacts
are retained for diagnosis, including a `results.json` assertion matrix.

Screenshots and their contact sheet are written to
`/tmp/agentexpense-4b-shots/index.html`. `manifest.json` records engine, persona,
state, theme, viewport and real route for every image. Exact 1440×900 and 412×915
captures include Light and Dark, with full-page companions for tall screens. The
suite also checks every route and both dialogs at 320px. It opens the approved
mockup index from disk read-only as a packaging check; mockup sources are never
copied into the product.

Coverage includes navigation and badges, keyboard order and modal focus return,
Settings commits and output preservation, all notice states and agent action
blocking, empty views, Sam's independent currency charts and account groups,
opening-only relabelling versus fixed account currency, and Asha's investment
holdings, value/cost/gain, chart keyboard inspection and account drill-down.

On macOS, WebKit follows the system keyboard-access preference. Shell traversal
uses Option-Tab to include links and buttons when full keyboard access is off;
other engines use Tab. Dialog focus tests use ordinary Tab and Shift-Tab in every
engine, exercising the app’s explicit focus trap. Focus-order assertions are the
same for all engines.


Step 4B adds `workflows.mjs`: every persona/engine/theme/viewport runs real fake-agent
expense submission, account questions, failure/retry, expense conversions and
cross-currency transfers through the verified commit path. Statement PDFs are
fictional and generated in memory. The suite checks matching currency, blocked
mismatches, account reassignment, inline account creation, checklist decisions,
explicit estimate replacement and Apply. Test-only scenario/reset endpoints use
locked verified changes inside disposable repositories. They never ship with the
production app. Working/queue capture records are ignored test data; completed
real operations and audits are awaited before fixture reset.

The capture manifest maps each named state to its actual route. It includes Add,
Draft list/question/working/failure/empty, blank/filled conversions and transfers,
statement checklist, currency mismatch and inline account creation in all engines,
both themes and both viewports. Full-page companions keep fixed phone controls at
the document foot for inspection. The historical standalone Add/statement browser
scripts are superseded by this pinned, isolated matrix.

`BROWSER_SHOTS=/tmp/<directory>` selects a separate capture directory. This allows
independent persona invocations to run concurrently without clobbering manifests;
each invocation still creates its own application, ledger, ports, homes and profiles.
The default capture index remains `/tmp/agentexpense-4b-shots/index.html`.
