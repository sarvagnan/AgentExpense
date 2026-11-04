#!/bin/sh
# One owner for a make invocation; recursive targets inherit its root.
set -u
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -n "${AGENTEXPENSE_TEST_ROOT:-}" ]; then
  exec python3 "$script_dir/test_run.py" "$@"
fi
AGENTEXPENSE_TEST_ROOT=$(mktemp -d /tmp/agentexpense-run-XXXXXX)
export AGENTEXPENSE_TEST_ROOT
printf "Test run root: %s\n" "$AGENTEXPENSE_TEST_ROOT"
child_pid=
cleanup() {
  result=$?
  trap - EXIT HUP INT TERM
  python3 "$script_dir/test_run.py" --cleanup "$AGENTEXPENSE_TEST_ROOT" "$result"
  exit "$result"
}
forward() {
  if [ -n "$child_pid" ]; then kill -"$1" "$child_pid" 2>/dev/null || true; wait "$child_pid" 2>/dev/null || true; fi
  exit "$2"
}
trap cleanup EXIT
trap 'forward HUP 129' HUP
trap 'forward INT 130' INT
trap 'forward TERM 143' TERM
python3 "$script_dir/test_run.py" "$@" <&0 &
child_pid=$!
wait "$child_pid"
