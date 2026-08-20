# Tiny backup job image: tar + gzip only. NFS is mounted by the kernel (kubelet),
# so the container needs no S3/cloud client at all.
FROM alpine:3.21
RUN apk add --no-cache tar gzip ca-certificates
COPY backup.sh /backup.sh
RUN chmod +x /backup.sh
ENTRYPOINT ["/bin/sh", "/backup.sh"]