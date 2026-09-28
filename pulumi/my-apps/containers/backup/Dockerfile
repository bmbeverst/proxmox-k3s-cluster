# Small backup job image: tar + gzip + kubectl.
ARG KUBECTL_TAG=v1.36.4
FROM registry.k8s.io/kubectl:${KUBECTL_TAG} AS kubectl

FROM alpine:3.24
RUN apk add --no-cache tar gzip ca-certificates
# kubectl is only for stopping and starting the server around the backup.
COPY --from=kubectl /bin/kubectl /usr/local/bin/kubectl
COPY backup.sh /backup.sh
COPY nightly.sh /nightly.sh
RUN chmod +x /backup.sh /nightly.sh
# nightly.sh stops the server, backs up the world, starts the server again.
ENTRYPOINT ["/bin/sh", "/nightly.sh"]