.PHONY: scan test-scanner ledger test test-ledger test-server agent-check agent-login agent-upgrade service check dashboard verify verify-data format commit
export PYTHONDONTWRITEBYTECODE = 1
export npm_config_update_notifier = false
# Resolve the installed project Node once; helpers never install or download it.
NODE_BIN := $(shell python3 "$(dir $(abspath $(firstword $(MAKEFILE_LIST))))scripts/node_toolchain.py")
ifeq ($(strip $(NODE_BIN)),)
$(error Cannot resolve .node-version; install its version with fnm install $$(cat .node-version))
endif
export PATH := $(NODE_BIN):$(PATH)
ifneq ($(origin m),undefined)
export EXPENSES_COMMIT_MESSAGE = $(m)
endif

HLEDGER ?= $(shell command -v hledger 2>/dev/null || printf '%s' /opt/homebrew/bin/hledger)
LEDGER ?= ledger
LEDGER_DIR ?= $(LEDGER)
# Normalize before npm changes directory into review-dashboard.
override LEDGER_DIR := $(abspath $(LEDGER_DIR))
export LEDGER_DIR
ifneq ($(strip $(DASHBOARD_DATA_DIR)),)
override DASHBOARD_DATA_DIR := $(abspath $(DASHBOARD_DATA_DIR))
export DASHBOARD_DATA_DIR
endif

ledger:
	uv run --with jsonschema python scripts/ledger/generate_ledger.py

test: test-ledger test-migrations test-server

review-dashboard/node_modules/.package-lock.json: review-dashboard/package-lock.json
	npm --prefix review-dashboard ci

test-server: review-dashboard/node_modules/.package-lock.json
	HLEDGER_BIN="$(HLEDGER)" uv run --locked --directory review-dashboard/server python tests/run_tests.py

agent-check: review-dashboard/node_modules/.package-lock.json
	@PYTHONDONTWRITEBYTECODE=1 LIVE="$(LIVE)" ADAPTER="$(ADAPTER)" uv run --locked --directory review-dashboard/server python agent_check.py

agent-login: review-dashboard/node_modules/.package-lock.json
	ADAPTER="$(ADAPTER)" uv run --locked --directory review-dashboard/server python agent_tools.py login

agent-upgrade: review-dashboard/node_modules/.package-lock.json
	VERSION="$(VERSION)" DRY_RUN="$(DRY_RUN)" ADAPTER="$(ADAPTER)" uv run --locked --directory review-dashboard/server python agent_tools.py upgrade

service:
	python3 scripts/install_expense_service.py

test-ledger:
	HLEDGER_BIN="$(HLEDGER)" uv run --with jsonschema python scripts/ledger/tests/run_tests.py

.PHONY: test-migrations
test-migrations:
	HLEDGER_BIN="$(HLEDGER)" uv run --with jsonschema python -m unittest discover -s scripts/ledger/migration_tests -v

# Saves validate data only; development verify and dashboard also test the code.
check:
	HLEDGER_BIN="$(HLEDGER)" uv run --with jsonschema python scripts/ledger/check_store.py
	"$(HLEDGER)" -f "$(LEDGER_DIR)/expense_ledger.ledger" check -s ordereddates

format:
	uv run --with jsonschema python scripts/ledger/store.py --format

dashboard: review-dashboard/node_modules/.package-lock.json verify test-migrations
	npm --prefix review-dashboard run data
	npm --prefix review-dashboard run build

verify-data: ledger
	$(MAKE) check

verify: test-ledger verify-data

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
	@node --version
	@set -e; output=$$(mktemp -d /tmp/expense-personas-data-XXXXXX); trap 'rm -rf "$$output"' EXIT HUP INT TERM; for persona in fixtures/ledgers/*; do \
	  DASHBOARD_DATA_DIR="$$output/$${persona##*/}" LEDGER_DIR="$(CURDIR)/$$persona" $(MAKE) dashboard test-server; \
	  DASHBOARD_DATA_DIR="$$output/$${persona##*/}" LEDGER_DIR="$(CURDIR)/$$persona" npm --prefix review-dashboard test; \
	  DASHBOARD_DATA_DIR="$$output/$${persona##*/}" LEDGER_DIR="$(CURDIR)/$$persona" $(MAKE) test-scanner; \
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
	@node --version
	node review-dashboard/tests/browser/run.mjs

browser-capture:
	CAPTURE=1 node review-dashboard/tests/browser/run.mjs

.PHONY: rules rules-backfill rules-catch-up
rules:
	uv run --locked --directory review-dashboard/server python ../../scripts/ledger/rules.py $(ARGS)

rules-backfill:
	uv run --locked --directory review-dashboard/server python ../../scripts/ledger/rules.py backfill $(ARGS)

rules-catch-up:
	uv run --locked --directory review-dashboard/server python ../../scripts/ledger/rules.py catch-up $(ARGS)

.PHONY: correct
correct:
	uv run --locked --directory review-dashboard/server python ../../scripts/ledger/correct.py $(ARGS)
