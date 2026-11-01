#!/bin/sh
# Scheduled price update entry point; caller selects LEDGER_DIR.
set -eu
# Resolve caller-relative LEDGER_DIR before changing directory.
app=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
LEDGER_DIR=$(uv run python "$app/scripts/project_paths.py")
export LEDGER_DIR
cd "$app"
uv run python scripts/ledger_manifest.py
# No source means no lock, file read, fetch, build or commit.
if uv run python scripts/prices/fetch_fund_navs.py --configured; then
  :
else
  status=$?
  [ "$status" -eq 1 ] && exit 0
  exit "$status"
fi
if [ "${1:-}" != "--ledger-locked" ]; then
  exec uv run python scripts/ledger/ledger_lock.py -- /bin/sh scripts/prices/update_fund_prices.sh --ledger-locked
fi
prices="$LEDGER_DIR/data/prices/mutual-funds.prices"
before=missing
[ ! -f "$prices" ] || before=$(cksum < "$prices")
uv run python scripts/prices/fetch_fund_navs.py --ledger-locked
if [ ! -f "$prices" ] || [ "$(cksum < "$prices")" = "$before" ]; then
  exit 0
fi
make dashboard > /dev/null
latest=$(awk '/^P / {day = $2} END {print day}' "$prices")
(
  cd "$LEDGER_DIR"
  jj --quiet commit -m "Add fund prices through $latest" data/prices/mutual-funds.prices
  jj --quiet bookmark set main -r @-
)
echo "Rebuilt the ledger and dashboard with the new prices, and committed them."
