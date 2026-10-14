#!/bin/sh
# Registers the cloned repos with Paseo once the daemon is listening: one project per repo dir,
# plus a local workspace, so they appear in the UI without a manual step.
#
# Runs as the app container's postStart hook, which cannot be used for this in the init
# container: the daemon is started by the entrypoint, so nothing can be registered before it.
# Hence it must ALWAYS exit 0 - a non-zero postStart hook kills the container, and a missing
# project is not worth a CrashLoop.
#
# Additive only. An existing project is never touched, and a repo that already has a workspace
# record - archived ones included - is left alone, so a restart cannot resurrect a workspace the
# user put away or overwrite a title they set.
set -u

if [ -z "${PASEO_REPO_DIRS:-}" ]; then
  echo "paseo-register: no repo dirs given, nothing to do"
  exit 0
fi

# Every default here matters: under `set -u` an unset variable makes this script exit non-zero,
# and a non-zero postStart hook gets the container killed.
home="${PASEO_HOME:-${HOME:-/home/paseo}/.paseo}"
projects_file="$home/projects/projects.json"
workspaces_file="$home/projects/workspaces.json"
wait_seconds="${PASEO_REGISTER_WAIT_SECONDS:-30}"

# A postStart hook writes nothing to the container log, so keep this on the PVC instead.
# The write test is deliberate: a failed `exec` redirect would kill the container, which is
# the CrashLoop this script exists to avoid.
if : >>"$home/paseo-register.log" 2>/dev/null; then exec >>"$home/paseo-register.log" 2>&1; fi

# Without the CLI the wait loop below would spin for wait_seconds; bail out immediately.
if ! command -v paseo >/dev/null 2>&1; then
  echo "ERROR: no paseo CLI on PATH, skipping registration"
  exit 0
fi

# The daemon is usually listening a few seconds in; past wait_seconds this is a daemon problem,
# not a registration problem, so say so and leave the pod alone.
tries=0
while ! paseo project ls >/dev/null 2>&1; do
  tries=$((tries + 1))
  if [ "$tries" -ge "$wait_seconds" ]; then
    echo "ERROR: daemon not reachable after ${wait_seconds}s, skipping registration" >&2
    exit 0
  fi
  sleep 1
done

# Read the daemon's own records rather than `paseo ... ls`: the CLI hides archived workspaces,
# and an archived record must count as "exists" so restarts never resurrect it.
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
# Explicit, so whatever the last command returned cannot fail the hook.
exit 0
