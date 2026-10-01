#!/usr/bin/env bash
# Console client for the running server.
#
#   vints-console                       interactive: follow the log, type commands
#   vints-console /list clients         one-shot: send one command and print its reply
#
# The server reads console commands from $CONSOLE_FIFO (see start-vints.sh), which is
# write-only from our side; replies are read back from the server log.
set -euo pipefail

# The pod env sets the path; the default keeps the CI smoke test and local runs working.
FIFO="${CONSOLE_FIFO:-/tmp/vints-console.fifo}"
LOG="${DATA_PATH:-/data}/Logs/server-main.log"
REPLY_TIMEOUT=10  # seconds to wait for the server to log that it handled our command

[[ -p "$FIFO" ]] || { echo "vints-console: $FIFO is not a FIFO - is the server running?" >&2; exit 1; }

log_lines() {
  [[ -f "$LOG" ]] && wc -l <"$LOG" || echo 0
}

print_lines_from() {  # $1 = 1-based line to start printing at
  [[ -f "$LOG" ]] || return 0
  tail -n "+$1" "$LOG"
}

send() {
  printf '%s\n' "$*" >"$FIFO"
}

# Print what the server logs for our command, stopping once it says it handled it (plus a
# short settle time for the reply lines that follow).
print_reply_from() {
  local start=$1 i new
  for ((i = 0; i < REPLY_TIMEOUT * 2; i++)); do
    sleep 0.5
    new="$(print_lines_from "$start" || true)"
    [[ -n "$new" ]] || continue
    start=$((start + $(printf '%s\n' "$new" | wc -l)))
    # Lines logged before the server says it handled the command are unrelated noise
    # (autosaves, players joining), so start the output at that line.
    if [[ "$new" == *"Handling Console Command"* ]]; then
      printf '%s\n' "$(printf '%s\n' "$new" | sed -n '/Handling Console Command/,$p')"
      sleep 1
      new="$(print_lines_from "$start" || true)"
      [[ -z "$new" ]] || printf '%s\n' "$new"
      return 0
    fi
  done
  echo "vints-console: nothing in $LOG about the command after ${REPLY_TIMEOUT}s - is it still loading?" >&2
  return 1
}

if (($# > 0)); then
  start="$(log_lines)"
  send "$@"
  if print_reply_from "$start"; then exit 0; else exit 1; fi
fi

if [[ -t 0 ]]; then
  echo "vints-console: commands -> $FIFO, output -> $LOG. Ctrl-D or Ctrl-C exits." >&2
  [[ -f "$LOG" ]] || echo "vints-console: $LOG does not exist yet - the world may still be loading." >&2
fi
tail -n 15 -F "$LOG" 2>/dev/null &
tail_pid=$!
trap 'kill "$tail_pid" 2>/dev/null || true' EXIT

prompt=()
[[ -t 0 ]] && prompt=(-p '> ')
while IFS= read -r "${prompt[@]}" line; do
  [[ -n "${line//[[:space:]]/}" ]] || continue
  send "$line"
done
# Piped input ends the session as soon as the last line is read; give the reply time to
# reach the tail before exiting. An interactive session already showed it.
[[ -t 0 ]] || sleep 2
