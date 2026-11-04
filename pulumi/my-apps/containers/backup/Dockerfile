# Small backup job image: tar + gzip + kubectl.
ARG KUBECTL_TAG=v1.36.4
FROM registry.k8s.io/kubectl:${KUBECTL_TAG} AS kubectl

FROM alpine:3.24
RUN apk add --no-cache tar gzip ca-certificates
# kubectl via RUN: pins dir mtimes in one layer.
RUN --mount=type=bind,from=kubectl,source=/bin/kubectl,target=/kubectl \
    cp /kubectl /usr/local/bin/kubectl \
 && chmod 0755 /usr/local/bin/kubectl \
 && touch -h -t 197001010000 /usr/local/bin/kubectl /usr/local/bin /usr/local /usr
COPY backup.sh /backup.sh
COPY nightly.sh /nightly.sh
RUN chmod +x /backup.sh /nightly.sh
# nightly.sh stops, backs up, restarts the server.
ENTRYPOINT ["/bin/sh", "/nightly.sh"]