# Optional ledger modules

The application reads these settings from the selected ledger's `ledger.json` at
run time. Omitted modules use the defaults below; file presence never enables a
module. No ledger format migration is needed.

```json
{
  "modules": {
    "fuel": false,
    "investments": { "prices": null },
    "history": { "oldLedger": null },
    "weeklyReview": false
  }
}
```

With fuel off, the checker warns about a stray `data/fuel-log.json` without reading
it. Data builds remove stale generated fuel JSON and gzip files. The loader makes
no fuel request. Review, Help, Add patterns, physical-reading pieces, links and
fuel styles are absent. The Add agent's schema omits the two fuel fields and the
server rejects proposals containing them, including null values. With fuel on,
provide `data/fuel-log.json` with an `entries` array; missing or malformed logs fail
verification and the data build. Existing confirmed links keep their explicit IDs
and readings. Monetary fuel expenses still work with the fuel-log module off.

Investments are enabled by default: units, recorded costs and contributions use
account kind `investment`. Set `investments` to `false` to hide investment views
and sections. Recorded transactions and postings remain part of the ledger.
`prices` selects only the automatic fetch source; `prices: null` disables fetching.
With investments enabled, the optional `data/prices/mutual-funds.prices` is always
used when present, including manually maintained hledger `P` directives. The
generated journal includes it, verification checks its directives, and the
dashboard publishes its prices and values holdings at market. Without recorded
prices, holdings show their recorded cost with a short note; no market gain is
inferred. Initial-price coverage is required only with a configured fetch source.
Setting investments to `false` ignores the price file entirely.

Price sources implement [interface version 1](../scripts/prices/README.md).
The built-in `mutual-fund-nav` uses the ledger's
`data/prices/fund-sources.json` mapping. The public job entry point remains
`scripts/prices/update_fund_prices.sh`; with no configured source it exits without
fetching, reading prices, building or committing. Its internal locked argument is
reserved for the shared-lock helper.

History uses `modules.history.oldLedger`. Relative paths resolve from the ledger
root and `~` expands to the operator's home. Only a readable file enables
`search_old_ledger` in both the prompt and MCP tool list. An unreadable path does
not expose a tool. `EXPENSES_OLD_LEDGER` is a deprecated fallback when the manifest
has no path: it is read once per process with a warning. Move its value into the
manifest and remove it from service configuration. The isolation cache includes
the offered tool set and checks it against the matching allow-list.

Enable `weeklyReview` for `make weekly-review` to print the procedure path,
unreviewed outcome count and resolved history path. Off prints only the disabled
message, without opening audit records. The command never runs an agent or changes
the ledger. The dashboard and server add no weekly jobs or controls.
[Scheduler templates](scheduling/README.md) let the operator choose an agent CLI.
