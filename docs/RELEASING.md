# Releasing and updating

A release is a `vX.Y.Z` tag on `main` whose `VERSION`, package files and changelog
agree. Installations update between release tags with `make update`.
[VERSIONS.md](VERSIONS.md) says what each kind of version change promises.

## Cutting a release

1. Start from a green `main`: CI passes (offline suites for every persona, the update
   test, the browser suites and the scans).
2. Choose the number: patch for fixes, minor for features or a ledger format change,
   major for removals (see [VERSIONS.md](VERSIONS.md)).
3. Put the number in `VERSION`, `review-dashboard/package.json`,
   `review-dashboard/package-lock.json` (both `version` fields at the top),
   `review-dashboard/server/pyproject.toml` and the `expense-dashboard-server` entry in
   `review-dashboard/server/uv.lock`. Run `make check-version`.
4. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` and start a
   new empty `## [Unreleased]` above it. The section must have **Upgrade notes**: numbered
   steps an agent can follow. A release that raises the ledger format says so there.
5. A release that raises the ledger format also:
   - adds `migrations/NNNN_name.py` with `SOURCE_FORMAT`, `TARGET_FORMAT`, `plan()`,
     `apply()` and, for intended dashboard changes, `OUTPUT_CHANGES` (see
     [migrations/README.md](../migrations/README.md));
   - raises `CURRENT_FORMAT` in `scripts/ledger_manifest.py` and
     `review-dashboard/scripts/ledger-manifest.mjs`, and `format.const` in
     `schema/ledger.schema.json`;
   - migrates the fixture ledgers in `fixtures/ledgers/`.
   `make test-update` builds exactly such a release (format 1 → 2) in a temporary
   repository, so it shows the steps a real one needs.
6. Run `make personas`, `make test-update` and `make doctor`, then commit
   "Release X.Y.Z" on `main`.
7. Tag and push: `git tag -a vX.Y.Z -m "AgentExpense X.Y.Z"` (an annotated tag; in a jj
   repository run it with git, which is colocated), then push `main` and the tag.
8. The **Release** workflow checks that the tag matches `VERSION`, that the versions agree
   and that the changelog section is dated, then publishes a GitHub release whose notes
   are that section (`python3 scripts/releases.py notes X.Y.Z`).

A pre-release is tagged `vX.Y.Z-rc.N` with the same steps; `make update` ignores it
unless run with `--pre`, and the GitHub release is marked as a pre-release.

## Updating an installation

From the application folder on the computer that runs AgentExpense, with the ledger
selected as usual (`LEDGER_DIR` or `ledger/`):

```sh
make update               # shows what changed, then asks
make update ARGS="--yes"  # no question
```

Other options: `--to vX.Y.Z` picks a release other than the newest (never an older
one), `--pre` includes pre-releases, `--remote NAME` reads tags from another remote.

`make update`:

1. runs `make doctor` and stops on blocking problems;
2. refuses when the application checkout has local changes or commits that the release
   doesn't contain. Customisations belong in your ledger (its `AGENTS.md`, rules,
   settings and personal readers) or in the ignored `local.json`;
3. fetches tags from the remote (`updates.remote` in `local.json`, default `origin`),
   picks the newest release above `VERSION` and shows the changelog sections in between;
4. asks for confirmation unless `--yes`; refuses when the release needs a Node version
   that fnm hasn't installed, or the ledger has uncommitted changes;
5. records where the checkout is and the ledger's checkpoint, then checks out the release
   **detached at its tag**: `jj new <tag>` in a jj clone colocated with git,
   `git switch --detach <tag>` in a git clone. Detached checkouts work the same in both
   and never move a branch you keep;
6. reinstalls dependencies only when the installed ones differ from the release's
   lockfiles (`make install`: `npm ci`, `uv sync --locked`);
7. runs the release's `make doctor`, then `make upgrade-ledger` for the selected ledger;
8. runs `make update-checks`: version agreement, `make verify` on the ledger, the
   dashboard data and build (`make build-dashboard`), and the ledger, migration and
   server tests. The browser suites aren't run;
9. restarts the server when `local.json` names a service, and waits until it stays up:

   ```json
   {"service": {"systemd": "expense-dashboard.service"}}
   {"service": {"launchd": "org.example.agentexpense"}}
   ```

   `systemd` restarts a `systemctl --user` unit; `launchd` runs
   `launchctl kickstart -k gui/<uid>/<label>`. Without `service`, no service is touched.

If any step after the checkout fails, or you press Ctrl-C, `make update` rolls back: it
returns the checkout to where it was (your branch, if you were on one), reinstalls that
release's dependencies where they differ, restores the ledger to its checkpoint
(`jj op restore` or `git reset --keep`) if nothing else has committed since the upgrade,
rebuilds the journal and dashboard, and restarts a configured service. It then prints
the step that failed and each part it restored, or each part it couldn't.

### Going back by hand

`make update` never downgrades. To return to an earlier release after a successful
update, stop the server, check out the earlier tag (`jj new vX.Y.Z` or
`git switch --detach vX.Y.Z`), restore the ledger to the checkpoint commit that
`make update` printed (in the ledger: `jj op restore` to the operation before the
upgrade, or `git reset --keep <checkpoint>`), then run `make install`, `make ledger`,
`make build-dashboard` and restart the server. Entries saved after the update are lost
by restoring the checkpoint; copy them out first.

## The update notice

The dashboard can show when a newer release exists. It is off unless `local.json`
turns it on:

```json
{"updates": {"notice": true, "remote": "origin"}}
```

When on, the server lists the remote's tags with `git ls-remote` at most once a day
(the time is kept in its state directory, so restarts don't add checks) and sends
nothing else. Settings then shows the newer version and the `make update` command.
Opening Settings never contacts the remote.

## Continuous integration

`.github/workflows/` holds four workflows, which run once the repository is published.
`make check-workflows` checks their structure offline, including that every third-party
action is pinned to a full commit SHA (the release it came from is noted beside it), that
every job has a time limit, and the trigger split below.
Downloaded tools are checked too: gitleaks against its release checksums, and jj and
hledger against the `jj-sha256` and `hledger-sha256` inputs of `.github/actions/setup`.
CI uses the hledger version development uses, not Ubuntu's older package. To move to a newer action or tool, look
up the release's commit SHA or archive digest and change both together.

- **CI** (`ci.yml`), on every push and pull request: the personal-data scan with an
  empty list made in the job (the real list stays private), the scanner's own tests, a
  pinned gitleaks secret scan, then for each fictional persona `make doctor`,
  `make dashboard`, the server tests, the dashboard tests and `make test-scanner`.
  These install no browser, so the browser cleanup probe in `make test-scanner` is
  skipped with its reason.
- **Browsers and update** (`browsers.yml`), on `v*` tags, by hand (`workflow_dispatch`)
  and weekly: one `make test-update` job, and the headless browser suites with
  Playwright 1.64.1 in Chromium, Firefox and WebKit, every persona and size, in Light
  only (`THEMES=light`; Dark is for the grading round). The Chromium job also runs the
  browser cleanup probe, which must not be skipped there. To check a change before a
  release, run it by hand from the Actions tab.
- **Agent CLI upgrades** (`agent-upgrade.yml`), weekly: for each pinned CLI adapter,
  `make agent-upgrade DRY_RUN=1` installs the newest release in a temporary folder and
  runs the offline allow-list checks; when they pass, the job pins it and opens a pull
  request. CI never signs in or runs a model: the owner runs
  `make agent-check LIVE=1 ADAPTER=<adapter>` on their own machine before merging.
- **Release** (`release.yml`), on a `v*` tag: see step 8 above.
