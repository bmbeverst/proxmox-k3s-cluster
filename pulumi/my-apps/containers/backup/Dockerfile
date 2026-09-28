# Small backup job image: tar + gzip + a Kubernetes client. NFS is mounted by the kernel
# (kubelet), so the container needs no S3/cloud client at all.
FROM alpine:3.24
RUN apk add --no-cache tar gzip ca-certificates
# kubectl is only for nightly.sh: it stops the Deployment for the backup and starts it
# again. Pinned to the cluster's server version (k3s v1.36.x) - bump with the cluster.
ARG KUBECTL_VERSION=1.36.4
RUN set -eux; \
    case "$(apk --print-arch)" in \
      x86_64) arch=amd64 ;; \
      aarch64) arch=arm64 ;; \
      *) echo "unsupported arch: $(apk --print-arch)" >&2; exit 1 ;; \
    esac; \
    url="https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/${arch}/kubectl"; \
    wget -q -O /usr/local/bin/kubectl "$url"; \
    wget -q -O /tmp/kubectl.sha256 "$url.sha256"; \
    echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum -c -; \
    rm -f /tmp/kubectl.sha256; \
    chmod 0755 /usr/local/bin/kubectl; \
    kubectl version --client
COPY backup.sh /backup.sh
COPY nightly.sh /nightly.sh
RUN chmod +x /backup.sh /nightly.sh
ENTRYPOINT ["/bin/sh", "/backup.sh"]