.PHONY: scan test-scanner ledger test test-ledger test-server agent-check agent-login agent-upgrade service check dashboard verify verify-data format commit
export PYTHONDONTWRITEBYTECODE = 1
export npm_config_update_notifier = false
# Resolve the installed project Node once; helpers never install or download it.
# doctor, check-version and update need no Node; update runs make for each release.
ifneq ($(MAKECMDGOALS),$(filter doctor check-version update,$(MAKECMDGOALS)))
NODE_BIN := $(shell python3 "$(dir $(abspath $(firstword $(MAKEFILE_LIST))))scripts/node_toolchain.py")
ifeq ($(strip $(NODE_BIN)),)
$(error Cannot resolve .node-version; install its version with fnm install $$(cat .node-version))
endif
export PATH := $(NODE_BIN):$(PATH)
endif
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

test-inside: check-version test-ledger test-migrations test-server

.PHONY: doctor check-version
# Offline prerequisites table; exits non-zero only for blocking problems.
doctor:
	@HLEDGER="$(HLEDGER)" python3 scripts/doctor.py

check-version:
	@python3 scripts/check_version.py

# npm ci only when installed packages differ from package-lock.json by version.
.PHONY: node-modules install
node-modules:
	@python3 scripts/dependencies.py npm

# npm and Python dependencies, each reinstalled only when it differs from its lockfile.
install:
	@python3 scripts/dependencies.py install

test-server-inside: node-modules
	HLEDGER_BIN="$(HLEDGER)" uv run --locked --directory review-dashboard/server python tests/run_tests.py

agent-check: node-modules
	@PYTHONDONTWRITEBYTECODE=1 LIVE="$(LIVE)" ADAPTER="$(ADAPTER)" uv run --locked --directory review-dashboard/server python agent_check.py

agent-login: node-modules
	ADAPTER="$(ADAPTER)" uv run --locked --directory review-dashboard/server python agent_tools.py login

agent-upgrade: node-modules
	VERSION="$(VERSION)" DRY_RUN="$(DRY_RUN)" ADAPTER="$(ADAPTER)" uv run --locked --directory review-dashboard/server python agent_tools.py upgrade

service:
	python3 scripts/install_expense_service.py

test-ledger-inside:
	HLEDGER_BIN="$(HLEDGER)" uv run --with jsonschema python scripts/ledger/tests/run_tests.py

.PHONY: test-migrations
test-migrations-inside:
	HLEDGER_BIN="$(HLEDGER)" uv run --with jsonschema python scripts/test_python.py -m unittest discover -s scripts/ledger/migration_tests -v

# Saves validate data only; development verify and dashboard also test the code.
check:
	HLEDGER_BIN="$(HLEDGER)" uv run --with jsonschema python scripts/ledger/check_store.py
	"$(HLEDGER)" -f "$(LEDGER_DIR)/expense_ledger.ledger" check -s ordereddates

format:
	uv run --with jsonschema python scripts/ledger/store.py --format

dashboard-inside: node-modules verify test-migrations
	npm --prefix review-dashboard run data
	npm --prefix review-dashboard run build

# The installation's own dashboard data and build, without tests; make update uses it.
.PHONY: build-dashboard update-checks update test-update
build-dashboard: node-modules
	npm --prefix review-dashboard run data
	npm --prefix review-dashboard run build

# What make update runs after checking out a release: versions, the selected ledger,
# its dashboard, and the ledger, migration and server tests (not the browser suites).
# With no ledger selected there is no dashboard to build; the tests use the fixture.
update-checks:
	$(MAKE) --no-print-directory check-version
ifneq ($(wildcard $(LEDGER_DIR)/.),)
	$(MAKE) --no-print-directory verify
	$(MAKE) --no-print-directory build-dashboard
else
	@echo "No ledger selected: no ledger to verify or dashboard to build."
	$(MAKE) --no-print-directory test-ledger
endif
	$(MAKE) --no-print-directory test-migrations
	$(MAKE) --no-print-directory test-server

# The GitHub workflows' structure, checked offline with the server environment's PyYAML.
.PHONY: check-workflows
check-workflows:
	uv run --locked --directory review-dashboard/server python ../../scripts/check_workflows.py

# Update to the latest release tag; ARGS="--yes" skips the prompt. See docs/RELEASING.md.
update:
	@HLEDGER="$(HLEDGER)" python3 scripts/update.py $(ARGS)

verify-data: ledger
	$(MAKE) check

verify: test-ledger verify-data

commit:
	uv run --with jsonschema python scripts/ledger/commit.py

scan:
	@test -n "$(LIST)" || (echo "Usage: make scan LIST=/path/to/private-list.tsv" >&2; exit 2)
	python3 scripts/check_private.py --list "$(LIST)"

# SCANNER_TESTS optionally selects tests by unittest -k pattern.
test-scanner-inside:
	python3 scripts/test_python.py -m unittest discover -s scripts/tests $(if $(SCANNER_TESTS),-k "$(SCANNER_TESTS)") -v

.PHONY: upgrade-ledger personas
upgrade-ledger:
	uv run --with jsonschema python scripts/ledger/upgrade_ledger.py $(ARGS)

personas-inside:
	@node --version
	@set -e; output=$$(mktemp -d "$${EXPENSES_TEST_TMP}/expense-personas-data-XXXXXX"); server_tests=all; for persona in fixtures/ledgers/*; do \
	  DASHBOARD_DATA_DIR="$$output/$${persona##*/}" LEDGER_DIR="$(CURDIR)/$$persona" $(MAKE) dashboard; \
	  SERVER_TESTS="$$server_tests" DASHBOARD_DATA_DIR="$$output/$${persona##*/}" LEDGER_DIR="$(CURDIR)/$$persona" $(MAKE) test-server; \
	  server_tests=persona; \
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
browser-tests-inside:
	@node --version
	node review-dashboard/tests/browser/run.mjs

browser-capture-inside:
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

# Nested test targets share their parent run root and cleanup boundary.
.PHONY: test test-server dashboard personas browser-tests test-scanner test-ledger test-migrations browser-capture
test:
	@scripts/test-run.sh $(MAKE) --no-print-directory test-inside
test-server:
	@scripts/test-run.sh $(MAKE) --no-print-directory test-server-inside
dashboard:
	@scripts/test-run.sh $(MAKE) --no-print-directory dashboard-inside
personas:
	@scripts/test-run.sh $(MAKE) --no-print-directory personas-inside
browser-tests:
	@scripts/test-run.sh $(MAKE) --no-print-directory browser-tests-inside
test-scanner:
	@scripts/test-run.sh $(MAKE) --no-print-directory test-scanner-inside
test-ledger:
	@scripts/test-run.sh $(MAKE) --no-print-directory test-ledger-inside
test-migrations:
	@scripts/test-run.sh $(MAKE) --no-print-directory test-migrations-inside
# Releases v0.1.0 to v0.2.0 between disposable clones of a local bare repository.
test-update:
	@scripts/test-run.sh $(MAKE) --no-print-directory test-update-inside
test-update-inside:
	AGENTEXPENSE_UPDATE_TEST=1 python3 scripts/test_python.py -m unittest discover -s scripts/tests -p test_update.py -v
browser-capture:
	@scripts/test-run.sh $(MAKE) --no-print-directory browser-capture-inside

.PHONY: retention
retention:
	uv run --locked --directory review-dashboard/server python ../../scripts/ledger/retain.py $(ARGS)
