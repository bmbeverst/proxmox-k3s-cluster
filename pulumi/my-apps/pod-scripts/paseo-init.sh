#!/bin/sh
# Runs in the init container (see the "workspace" container's env in paseo.py) before the daemon:
# hand the PVCs to the app uid, then clone or fetch every repo in PASEO_REPOS into its own
# directory under PASEO_WORKSPACE.
#
# Manifest-carried, NOT baked into the image: paseo.py reads this file at run time, so edits ship
# from git with no image rebuild.
#
# Idempotent and non-destructive: nothing here deletes or exits, and one repo failing is logged
# without stopping the others, so the daemon always starts.
set -eux

chown -R "$PASEO_OWNER" "$PASEO_HOME_DIR" "$PASEO_WORKSPACE"

for URL in $PASEO_REPOS; do
  repo="$PASEO_WORKSPACE/${URL##*/}"
  repo="${repo%.git}"
  if [ -d "$repo/.git" ]; then
    git -C "$repo" fetch --prune origin || echo "ERROR: git fetch failed in $repo" >&2
  else
    git clone "$URL" "$repo" || echo "ERROR: git clone failed for $repo" >&2
  fi
  if [ -d "$repo/.git" ]; then
    git -C "$repo" rev-parse --verify -q HEAD >/dev/null \
      || echo "ERROR: $repo has no HEAD (incomplete clone)" >&2
    # The remote stays tokenless on disk; insteadOf adds the credential at use time.
    git -C "$repo" remote set-url origin "$URL" \
      || echo "ERROR: could not set the remote for $repo" >&2
    # Enable the repo's own hooks: core.hooksPath is local config, so a clone
    # starts without them. That is what keeps the agent's commits black/isort
    # clean; uv is in the image, so the hook's fallback path works here.
    if [ -x "$repo/.githooks/pre-commit" ]; then
      git -C "$repo" config core.hooksPath .githooks \
        || echo "ERROR: could not enable hooks in $repo" >&2
    fi
    chown -R "$PASEO_OWNER" "$repo"
  fi
done
