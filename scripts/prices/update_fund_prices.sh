#!/bin/sh
# Scheduled price update entry point; caller selects LEDGER_DIR.
set -eu
cd "$(dirname "$0")/../.."
LEDGER_DIR=$(uv run python scripts/project_paths.py)
export LEDGER_DIR
uv run python scripts/ledger_manifest.py
# Hold the shared ledger lock through the price update, build and partial commit.
# The internal argument is used only by the lock helper's child process.
if [ "${1:-}" != "--ledger-locked" ]; then
  exec uv run python scripts/ledger/ledger_lock.py -- /bin/sh scripts/prices/update_fund_prices.sh --ledger-locked
fi
prices="$LEDGER_DIR/data/prices/mutual-funds.prices"
before=$(cksum < "$prices")
uv run python scripts/prices/fetch_fund_navs.py
if [ "$(cksum < "$prices")" = "$before" ]; then
  echo "No new NAVs; the ledger and dashboard are unchanged."
  exit 0
fi
make dashboard > /dev/null
latest=$(awk '/^P / {day = $2} END {print day}' "$prices")
(
  cd "$LEDGER_DIR"
  jj --quiet commit -m "Add fund NAVs through $latest" data/prices/mutual-funds.prices
  jj --quiet bookmark set main -r @-
)
echo "Rebuilt the ledger and dashboard with the new NAVs, and committed them."
