#!/usr/bin/env bash
set -euo pipefail
# Build + push both images. Adjust REGISTRY to taste; then paste the resulting
# *_IMAGE strings into pulumi/my-apps/__main__.py. Run from the repo root with
# a logged-in registry (e.g. ghcr.io).
REGISTRY="${REGISTRY:-ghcr.io/$(whoami)}"

docker build -t "$REGISTRY/vints-server:latest"  pulumi/my-apps/containers/server
docker build -t "$REGISTRY/vints-backup:latest"  pulumi/my-apps/containers/backup
docker push "$REGISTRY/vints-server:latest"
docker push "$REGISTRY/vints-backup:latest"

echo "Set in __main__.py:"
echo "VINTS_IMAGE        = '$REGISTRY/vints-server:latest'"
echo "VINTS_BACKUP_IMAGE = '$REGISTRY/vints-backup:latest'"