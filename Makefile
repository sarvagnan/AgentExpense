.PHONY: scan test-scanner ledger test test-ledger test-server agent-check agent-login agent-upgrade service check dashboard verify format commit
export PYTHONDONTWRITEBYTECODE = 1
ifneq ($(origin m),undefined)
export EXPENSES_COMMIT_MESSAGE = $(m)
endif

HLEDGER ?= $(shell command -v hledger 2>/dev/null || printf '%s' /opt/homebrew/bin/hledger)
LEDGER ?= ledger
LEDGER_DIR ?= $(LEDGER)
# Normalize before npm changes directory into review-dashboard.
override LEDGER_DIR := $(abspath $(LEDGER_DIR))
export LEDGER_DIR

ledger:
	uv run --with jsonschema python scripts/ledger/generate_ledger.py

test: test-ledger test-migrations test-server

review-dashboard/node_modules/.package-lock.json: review-dashboard/package-lock.json
	npm --prefix review-dashboard ci

test-server: review-dashboard/node_modules/.package-lock.json
	HLEDGER_BIN="$(HLEDGER)" uv run --locked --directory review-dashboard/server python -m unittest discover -s tests -v

agent-check: review-dashboard/node_modules/.package-lock.json
	@PYTHONDONTWRITEBYTECODE=1 LIVE="$(LIVE)" uv run --locked --directory review-dashboard/server python agent_check.py

agent-login: review-dashboard/node_modules/.package-lock.json
	uv run --locked --directory review-dashboard/server python agent_tools.py login

agent-upgrade: review-dashboard/node_modules/.package-lock.json
	VERSION="$(VERSION)" DRY_RUN="$(DRY_RUN)" uv run --locked --directory review-dashboard/server python agent_tools.py upgrade

service:
	python3 scripts/install_expense_service.py

test-ledger:
	HLEDGER_BIN="$(HLEDGER)" uv run --with jsonschema python scripts/ledger/tests/run_tests.py

.PHONY: test-migrations
test-migrations:
	HLEDGER_BIN="$(HLEDGER)" uv run --with jsonschema python -m unittest discover -s scripts/ledger/migration_tests -v

# Ledger checks run only the ledger tests, so verify, commit and dashboard stay fast.
check: test-ledger
	HLEDGER_BIN="$(HLEDGER)" uv run --with jsonschema python scripts/ledger/check_store.py
	"$(HLEDGER)" -f "$(LEDGER_DIR)/expense_ledger.ledger" check -s ordereddates

format:
	uv run --with jsonschema python scripts/ledger/store.py --format

dashboard: review-dashboard/node_modules/.package-lock.json verify test-migrations
	npm --prefix review-dashboard run data
	npm --prefix review-dashboard run build

verify: ledger
	$(MAKE) check

commit:
	uv run --with jsonschema python scripts/ledger/commit.py

scan:
	@test -n "$(LIST)" || (echo "Usage: make scan LIST=/path/to/private-list.tsv" >&2; exit 2)
	python3 scripts/check_private.py --list "$(LIST)"

test-scanner:
	python3 -m unittest discover -s scripts/tests -v

.PHONY: upgrade-ledger personas
upgrade-ledger:
	uv run --with jsonschema python scripts/ledger/upgrade_ledger.py $(ARGS)

personas:
	@set -e; for persona in fixtures/ledgers/*; do \
	  LEDGER_DIR="$(CURDIR)/$$persona" $(MAKE) dashboard test-server; \
	  LEDGER_DIR="$(CURDIR)/$$persona" npm --prefix review-dashboard test; \
	  LEDGER_DIR="$(CURDIR)/$$persona" $(MAKE) test-scanner; \
	  printf 'PASS %s\n' "$${persona##*/}"; \
	done

.PHONY: set-account-currency set-default-currency
set-account-currency:
	uv run --with jsonschema python scripts/ledger/set_account_currency.py $(ARGS)

set-default-currency:
	uv run --with jsonschema python scripts/ledger/set_default_currency.py $(ARGS)

.PHONY: weekly-review
weekly-review:
	uv run --with jsonschema python scripts/ledger/weekly_review.py

.PHONY: browser-tests browser-capture
browser-tests:
	node review-dashboard/tests/browser/run.mjs

browser-capture:
	CAPTURE=1 node review-dashboard/tests/browser/run.mjs
