# Optional weekly review scheduling

Enable `modules.weeklyReview` in the ledger's `ledger.json`. From the application
root, `LEDGER_DIR=/absolute/path/to/ledger make weekly-review` prints the procedure
and the unreviewed count. It does not invoke a model or change the ledger.

The procedure is [WEEKLY-REVIEW.md](../../review-dashboard/server/WEEKLY-REVIEW.md).
Choose an agent CLI you already use, with its own login and settings. Replace
`AGENT_COMMAND`, `/path/to/app` and `/path/to/ledger` below. `AGENT_COMMAND` must
accept the procedure instruction as its final argument. Scheduling runs an agent;
these examples are documentation, never part of offline tests. The dashboard and
server add no weekly-review controls, routes, jobs or background activity.

Each example checks the current module flag before invoking the agent. They run
weekly on Monday at 09:00 in the scheduler's local zone. Keep logs private and
avoid overlapping runs. Recording verdicts uses the procedure's shared ledger lock.
Do not make corrections without the user's agreement.

- cron: [weekly-review.cron](weekly-review.cron)
- systemd: [weekly-review.service](weekly-review.service) and
  [weekly-review.timer](weekly-review.timer); install in the user's unit directory,
  reload the user daemon and enable the timer.
- launchd: [org.example.expense-weekly.plist](org.example.expense-weekly.plist);
  install in the user's LaunchAgents directory and bootstrap with launchctl.

Both service examples run [run-weekly-review.sh](run-weekly-review.sh). Make a
private copy, replace the paths and agent command, and point the scheduler at it.
