# Vintage Story server runtime image, running the Stratum server fork.

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

# Every file this RUN creates would otherwise carry the build's wall-clock time as mtime, which re-diffs
# this whole game layer on every build: a fresh ~50 MB blob per tag, and the old ones are never freed.
# Pin the mtimes so identical inputs give an identical layer (the build fails below if pinning is lost).
#
# Stratum's launcher owns the install: --stratum-prepare-only downloads the vanilla archive for the
# pinned game version through Anego's release manifest, verifies it, unpacks it, and writes its patched
# DLLs on top. Preparing here is what lets start-vints.sh use --stratum-skip-bootstrap at runtime, so a
# pod start needs no outbound HTTPS and writes nothing into the image layers. .vanilla-cache only exists
# to serve --stratum-refresh and is dropped so it does not double the image.
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

# What the tag promises: the game version baked in and the Stratum release that patched it.
# layer-mtimes is the marker the smoke job uses to assert pinning end to end.
LABEL org.opencontainers.image.version="${VS_VERSION}" \
      org.opencontainers.image.source="https://gitlab.com/proxmox-k3s/proxmox-k3s-cluster" \
      com.proxmox-k3s.stratum="${STRATUM_VERSION}" \
      com.proxmox-k3s.layer-mtimes="pinned"

USER 1001
WORKDIR /serverfiles
ENV PORT=42420 DATA_PATH=/data
# The resolver reads the game minor from this; the ConfigMap must not be the source (between a
# VS pin bump and the new tag being deployed the CM would describe a game the pod is not running).
ENV VS_VERSION=${VS_VERSION}
# No ENTRYPOINT on purpose
