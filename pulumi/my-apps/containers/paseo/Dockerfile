# Paseo + Cline + toolchain; Renovate tracks PASEO_BASE_TAG.
# One stage per tool: a tool bump rebuilds only its stage plus the cheap COPYs.
# renovate: datasource=docker depName=ghcr.io/getpaseo/paseo
ARG PASEO_BASE_TAG=0.11.2
# renovate: datasource=docker depName=ghcr.io/astral-sh/uv
ARG UV_VERSION=0.13.0

# Cline stage: reuses the paseo base (node + npm); no extra pull.
FROM ghcr.io/getpaseo/paseo:${PASEO_BASE_TAG} AS cline
# renovate: datasource=npm depName=cline
ARG CLINE_VERSION=3.0.61
RUN set -eux; \
    npm_config_cache=/tmp/npmcache npm install -g --prefix /opt/cline "cline@${CLINE_VERSION}"; \
    rm -rf /tmp/npmcache

# Pulumi CLI stage: tarball fetch; all pulumi/* binaries, old layout kept.
FROM ghcr.io/getpaseo/paseo:${PASEO_BASE_TAG} AS pulumi
# renovate: datasource=github-releases depName=pulumi/pulumi
ARG PULUMI_VERSION=3.261.0
RUN set -eux; \
    [ "$(uname -m)" = x86_64 ] || { echo "FATAL: tarball is linux-x64 only"; exit 1; }; \
    mkdir -p /opt/bin; \
    curl -fsSL "https://get.pulumi.com/releases/sdk/pulumi-v${PULUMI_VERSION}-linux-x64.tar.gz" \
    | tar -xz -C /opt/bin --strip-components=1

# Upstream uv image: binaries only, nothing to build.
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM ghcr.io/getpaseo/paseo:${PASEO_BASE_TAG} AS final
USER root
ENV PULUMI_SKIP_UPDATE_CHECK=true
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends python3 jq ca-certificates unzip tar; \
    rm -rf /var/lib/apt/lists/*
# Big, stable COPY first; a cline bump re-commits only its small layer.
COPY --from=pulumi /opt/bin/ /usr/local/bin/
COPY --from=uv /uv /uvx /usr/local/bin/
COPY --from=cline /opt/cline /opt/cline
RUN set -eux; \
    ln -s /opt/cline/bin/cline /usr/local/bin/cline; \
    test -x /usr/local/bin/cline; \
    python3 --version; jq --version; pulumi version; uv --version

# npm ci only re-runs when the manifests change; sources copy over it.
COPY --chown=paseo:paseo plugins/paseo-ops-panel/package.json \
     plugins/paseo-ops-panel/package-lock.json /opt/paseo-ops-panel/
USER paseo
RUN set -eux; cd /opt/paseo-ops-panel; \
    npm_config_cache=/tmp/npmcache npm ci --no-audit --no-fund; \
    rm -rf /tmp/npmcache
USER root
COPY --chown=paseo:paseo plugins/ /opt/
RUN set -eux; \
    chmod +x /opt/dangerous-command-guard/cline-approve-dangerous; \
    ln -sf /opt/dangerous-command-guard/cline-approve-dangerous /usr/local/bin/cline-approve-dangerous

COPY entrypoint.sh /usr/local/bin/paseo-ops-entrypoint
RUN chmod +x /usr/local/bin/paseo-ops-entrypoint

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/paseo-ops-entrypoint"]
CMD ["node"]
