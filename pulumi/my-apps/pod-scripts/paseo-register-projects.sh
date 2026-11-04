#!/bin/sh
# Register cloned repos: project plus workspace per repo.
#
# postStart hook: always exit 0; non-zero kills the container.
#
# Additive only: existing projects and workspaces are never touched.
set -u

if [ -z "${PASEO_REPO_DIRS:-}" ]; then
  echo "paseo-register: no repo dirs given, nothing to do"
  exit 0
fi

# Every default matters: set -u exits on unset variables.
home="${PASEO_HOME:-${HOME:-/home/paseo}/.paseo}"
projects_file="$home/projects/projects.json"
workspaces_file="$home/projects/workspaces.json"
wait_seconds="${PASEO_REGISTER_WAIT_SECONDS:-30}"

# postStart logs to the PVC, not the container log.
if : >>"$home/paseo-register.log" 2>/dev/null; then exec >>"$home/paseo-register.log" 2>&1; fi

# No CLI would spin the wait loop; bail out.
if ! command -v paseo >/dev/null 2>&1; then
  echo "ERROR: no paseo CLI on PATH, skipping registration"
  exit 0
fi

# Daemon usually listens within seconds; timeouts are daemon problems.
tries=0
while ! paseo project ls >/dev/null 2>&1; do
  tries=$((tries + 1))
  if [ "$tries" -ge "$wait_seconds" ]; then
    echo "ERROR: daemon not reachable after ${wait_seconds}s, skipping registration" >&2
    exit 0
  fi
  sleep 1
done

# Read daemon records: the CLI hides archived workspaces.
records() {
  if [ -f "$1" ]; then jq -c . "$1" 2>/dev/null || echo '[]'; else echo '[]'; fi
}

for dir in $PASEO_REPO_DIRS; do
  if records "$projects_file" | jq -e --arg p "$dir" 'any(.[]; .rootPath == $p)' >/dev/null 2>&1; then
    echo "paseo-register: project already exists for $dir"
  else
    paseo project create "$dir" || echo "ERROR: could not register project $dir" >&2
  fi

  if records "$workspaces_file" | jq -e --arg c "$dir" 'any(.[]; .cwd == $c)' >/dev/null 2>&1; then
    echo "paseo-register: workspace already exists for $dir"
  else
    branch=$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null || echo local)
    paseo workspace create --isolation local --path "$dir" --title "$branch" \
      || echo "ERROR: could not create a workspace for $dir" >&2
  fi
done

echo "paseo-register: done"
# Explicit: last command's status must not fail the hook.
exit 0
