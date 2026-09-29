# Mod bundle: the CI-resolved, CI-verified mod archives as a tiny image. The cluster seeds its PVC
# depot from this so a pin bump never makes the server fetch from the Mod DB.
#
# Built from the repo root (`build -f pulumi/my-apps/containers/mods/Dockerfile .`) because the build
# context is the staged `.ci-bundle/`, which only the bake job creates.
ARG BUSYBOX_TAG=1.37.0
FROM busybox:${BUSYBOX_TAG}
COPY .ci-bundle/ /bundle/
