# Vintage Story server image running the Stratum fork.

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

# Pin mtimes; --stratum-prepare-only bakes the install for offline --stratum-skip-bootstrap.
# Before the entrypoints COPY: an entrypoint edit must not re-run this layer.
ARG VS_VERSION
ARG STRATUM_VERSION
ARG STRATUM_SHA256
RUN set -eux; \
    test -n "$VS_VERSION"; \
    test -n "$STRATUM_VERSION"; \
    test -n "$STRATUM_SHA256"; \
    archive=/tmp/stratum.zip; \
    curl -fsSL --retry 3 -A 'Mozilla/5.0' \
      "https://github.com/StratumServer/Stratum/releases/download/${STRATUM_VERSION}/stratum-${STRATUM_VERSION#v}-linux-x64.zip" \
      -o "$archive"; \
    echo "$STRATUM_SHA256  $archive" | sha256sum -c -; \
    unzip -q "$archive" -d /serverfiles; \
    rm -f "$archive"; \
    chmod +x /serverfiles/StratumServer; \
    cd /serverfiles; \
    ./StratumServer --stratum-prepare-only; \
    rm -rf /serverfiles/.vanilla-cache; \
    baked="$(./StratumServer --stratum-version | sed -n 's/^Base game: Vintage Story //p')"; \
    test "$baked" = "$VS_VERSION"; \
    chown -R vints:vints /serverfiles; \
    find /serverfiles -exec touch -h -d @0 {} +; \
    touch -h -d @0 /tmp; \
    drift="$(find /serverfiles -newermt @1 -print -quit)"; \
    test -z "$drift" || { echo "FATAL: $drift still carries a build-time mtime, so this layer re-diffs every build" >&2; exit 1; }

COPY entrypoints/ /entrypoints/
RUN chmod +x /entrypoints/*.sh
# Console entry point; FIFO path in start-vints.sh.
RUN ln -s /entrypoints/vints-console.sh /usr/local/bin/vints-console

# Baked game + Stratum release; layer-mtimes smoke-job pin marker.
LABEL org.opencontainers.image.version="${VS_VERSION}" \
      org.opencontainers.image.source="https://gitlab.com/proxmox-k3s/proxmox-k3s-cluster" \
      com.proxmox-k3s.stratum="${STRATUM_VERSION}" \
      com.proxmox-k3s.layer-mtimes="pinned"

USER 1001
WORKDIR /serverfiles
ENV PORT=42420 DATA_PATH=/data
# The resolver reads the game minor from this.
ENV VS_VERSION=${VS_VERSION}
# No ENTRYPOINT on purpose
