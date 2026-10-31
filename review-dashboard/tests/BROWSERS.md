# Browser tests

Browser suites are separate from `npm test`. The public snapshot retains suites whose inputs are synthetic and helpers that contain no private data. Private history, evidence and revision replay suites were removed. Phase 4 rebuilds full UI coverage against fictional ledgers.

Use a disposable application copy and explicit fictional `LEDGER_DIR`. Keep profiles, downloads, screenshots and fixture repositories under `/tmp`; use the isolation guard. Use a separately managed test browser, with Playwright supplied externally; never use an everyday browsing profile. `run-cdp.mjs` accepts an explicit `CDP_SCRIPT` path to a fictional suite or capture script. Set `STAGE` to a directory under `/tmp` for suites that save logs. The removed history suites must be rebuilt before their former coverage can run.

No browser suite was run during phase 1. Node server rendering checks use in-process Vite middleware without listening on a port.
