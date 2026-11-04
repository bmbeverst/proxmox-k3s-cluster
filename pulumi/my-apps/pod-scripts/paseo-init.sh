#!/bin/sh
# Init container: chown PVCs, clone PASEO_REPOS into PASEO_WORKSPACE.
# Manifest-carried: paseo.py reads this from git, no rebuild.
# Idempotent, non-destructive: failures are logged, never fatal.
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
    # Remote stays tokenless; insteadOf adds credentials at use time.
    git -C "$repo" remote set-url origin "$URL" \
      || echo "ERROR: could not set the remote for $repo" >&2
    # Install pre-commit hooks so agent commits stay black/isort clean.
    if [ -f "$repo/.pre-commit-config.yaml" ]; then
      (cd "$repo" && uv run --only-group lint pre-commit install) \
        || echo "ERROR: could not install pre-commit hooks in $repo" >&2
    fi
    chown -R "$PASEO_OWNER" "$repo"
  fi
done
