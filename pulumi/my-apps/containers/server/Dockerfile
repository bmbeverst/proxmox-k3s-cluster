# Vintage Story server runtime image.

ARG DOTNET_TAG=10.0.12
FROM mcr.microsoft.com/dotnet/runtime:${DOTNET_TAG}

# .NET 10 runtime is the documented system dependency (dotnet-runtime-10.0).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates tar jq unzip \
 && rm -rf /var/lib/apt/lists/*

# Non-root runtime identity
RUN useradd --system --uid 1001 --home-dir /serverfiles --shell /bin/bash vints \
 && mkdir -p /serverfiles /entrypoints \
 && chown -R vints:vints /serverfiles
COPY entrypoints/ /entrypoints/
RUN chmod +x /entrypoints/*.sh
# Operator entry point for the server console (see start-vints.sh for the FIFO).
RUN ln -s /entrypoints/vints-console.sh /usr/local/bin/vints-console

ARG VS_VERSION
ARG VS_URL
ARG VS_MD5
RUN set -eux; \
    test -n "$VS_VERSION"; \
    test -n "$VS_MD5"; \
    case "$VS_URL" in https://*) ;; *) echo "VS_URL must be https, got '$VS_URL'" >&2; exit 1 ;; esac; \
    archive="/tmp/$(basename "$VS_URL")"; \
    curl -fsSL --retry 3 "$VS_URL" -o "$archive"; \
    echo "$VS_MD5  $archive" | md5sum -c -; \
    tar -xzf "$archive" -C /serverfiles; \
    rm -f "$archive"; \
    chmod +x /serverfiles/VintagestoryServer; \
    ver="$(cd /serverfiles && ./VintagestoryServer --version 2>/dev/null | tr -d '[:space:]')"; \
    test "$ver" = "$VS_VERSION"; \
    chown -R vints:vints /serverfiles

# What the tag promises: the game version baked in and the checksum it was built from.
LABEL org.opencontainers.image.version="${VS_VERSION}" \
      org.opencontainers.image.source="https://gitlab.com/proxmox-k3s/proxmox-k3s-cluster" \
      com.proxmox-k3s.vintagestory.md5="${VS_MD5}"

USER 1001
WORKDIR /serverfiles
ENV PORT=42420 DATA_PATH=/data
# The resolver reads the game minor from this; the ConfigMap must not be the source (between a
# VS pin bump and the new tag being deployed the CM would describe a game the pod is not running).
ENV VS_VERSION=${VS_VERSION}
# No ENTRYPOINT on purpose
