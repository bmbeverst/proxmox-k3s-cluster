# Vintage Story server image running the Stratum fork.

# renovate: datasource=docker depName=mcr.microsoft.com/dotnet/runtime
ARG DOTNET_TAG=10.0.12

# Game bake stage: only the bake apt line and the three args re-run here.
FROM mcr.microsoft.com/dotnet/runtime:${DOTNET_TAG} AS bake
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates unzip tar \
 && rm -rf /var/lib/apt/lists/*
# Pin mtimes; --stratum-prepare-only bakes the install for offline --stratum-skip-bootstrap.
ARG VS_VERSION
ARG STRATUM_VERSION
ARG STRATUM_SHA256
RUN set -eux; \
    [ "$(uname -m)" = x86_64 ] || { echo "FATAL: release zip is linux-x64 only"; exit 1; }; \
    test -n "$VS_VERSION"; \
    test -n "$STRATUM_VERSION"; \
    test -n "$STRATUM_SHA256"; \
    mkdir /serverfiles; \
    archive=/tmp/stratum.zip; \
    curl -fsSL --retry 3 --retry-all-errors -A 'Mozilla/5.0' \
      "https://github.com/StratumServer/Stratum/releases/download/${STRATUM_VERSION}/stratum-${STRATUM_VERSION#v}-linux-x64.zip" \
      -o "$archive"; \
    echo "$STRATUM_SHA256  $archive" | sha256sum -c -; \
    unzip -q "$archive" -d /serverfiles; \
    rm -f "$archive"; \
    chmod +x /serverfiles/StratumServer; \
    cd /serverfiles; \
    HOME=/serverfiles ./StratumServer --stratum-prepare-only; \
    rm -rf /serverfiles/.vanilla-cache; \
    baked="$(./StratumServer --stratum-version | sed -n 's/^Base game: Vintage Story //p')"; \
    test "$baked" = "$VS_VERSION"; \
    chown -R 1001:1001 /serverfiles; \
    find /serverfiles -exec touch -h -d @0 {} +; \
    touch -h -d @0 /tmp; \
    drift="$(find /serverfiles -newermt @1 -print -quit)"; \
    test -z "$drift" || { echo "FATAL: $drift still carries a build-time mtime, so this layer re-diffs every build" >&2; exit 1; }

# Runtime stage: only what the entrypoints and resolver use.
FROM mcr.microsoft.com/dotnet/runtime:${DOTNET_TAG}
# APT_REFRESH is stamped weekly by CI: max ~7 days of apt security lag.
ARG APT_REFRESH
RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends ca-certificates curl jq unzip \
 && rm -rf /var/lib/apt/lists/*
RUN groupadd --system --gid 1001 vints \
 && useradd --system --uid 1001 --gid 1001 --home-dir /serverfiles --shell /bin/bash vints \
 && mkdir -p /entrypoints
COPY --from=bake --chown=1001:1001 /serverfiles /serverfiles
COPY --chmod=0755 entrypoints/ /entrypoints/
# Console entry point; FIFO path in start-vints.sh.
RUN ln -s /entrypoints/vints-console.sh /usr/local/bin/vints-console

# Baked game + Stratum release; layer-mtimes smoke-job pin marker.
ARG VS_VERSION
ARG STRATUM_VERSION
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
