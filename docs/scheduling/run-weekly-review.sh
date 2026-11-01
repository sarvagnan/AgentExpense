#!/bin/sh
# Template: replace the paths and the agent command before installing privately.
set -eu
cd /path/to/app
export LEDGER_DIR=/path/to/ledger
if uv run python -c 'import sys; sys.path.insert(0,"scripts"); from ledger_manifest import read_manifest,module_settings; sys.exit(0 if module_settings(read_manifest())["weeklyReview"] else 3)'; then
  :
else
  status=$?
  [ "$status" -eq 3 ] && exit 0
  exit "$status"
fi
make weekly-review
exec AGENT_COMMAND "Follow /path/to/app/review-dashboard/server/WEEKLY-REVIEW.md using LEDGER_DIR=/path/to/ledger. Report findings; corrections require user agreement."
