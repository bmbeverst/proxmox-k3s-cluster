# Small backup job image: tar + gzip + kubectl.
ARG KUBECTL_TAG=v1.36.4
FROM registry.k8s.io/kubectl:${KUBECTL_TAG} AS kubectl

FROM alpine:3.24
RUN apk add --no-cache tar gzip ca-certificates
# kubectl is only for stopping and starting the server around the backup. It is copied inside a RUN, not
# a COPY, so the dirs it lands in can be pinned in the same layer: a build-time dir mtime would otherwise
# re-diff this 60 MB layer and store a fresh copy of it for every tag.
RUN --mount=type=bind,from=kubectl,source=/bin/kubectl,target=/kubectl \
    cp /kubectl /usr/local/bin/kubectl \
 && chmod 0755 /usr/local/bin/kubectl \
 && touch -h -t 197001010000 /usr/local/bin/kubectl /usr/local/bin /usr/local /usr
COPY backup.sh /backup.sh
COPY nightly.sh /nightly.sh
RUN chmod +x /backup.sh /nightly.sh
# nightly.sh stops the server, backs up the world, starts the server again.
ENTRYPOINT ["/bin/sh", "/nightly.sh"]