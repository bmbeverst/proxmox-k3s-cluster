# Paseo + Cline + toolchain; Renovate tracks PASEO_BASE_TAG.
# One stage per tool: a tool bump rebuilds only its stage plus the cheap COPYs.
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
ENV PULUMI_SKIP_UPDATE_CHECK=true
RUN set -eux; \
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
COPY --from=cline /opt/cline /opt/cline
COPY --from=pulumi /opt/bin/ /usr/local/bin/
COPY --from=uv /uv /uvx /usr/local/bin/
RUN set -eux; \
    ln -s /opt/cline/bin/cline /usr/local/bin/cline; \
    test -x /usr/local/bin/cline; \
    python3 --version; jq --version; pulumi version; uv --version

COPY plugins/ /opt/

# Panel compiles at load; finds the guard via PATH.
RUN set -eux; \
    cd /opt/paseo-ops-panel; \
    export npm_config_cache=/tmp/npmcache; \
    npm ci --no-audit --no-fund; \
    rm -rf /tmp/npmcache; \
    chmod +x /opt/dangerous-command-guard/cline-approve-dangerous; \
    ln -sf /opt/dangerous-command-guard/cline-approve-dangerous /usr/local/bin/cline-approve-dangerous; \
    chown -R paseo:paseo /opt/dangerous-command-guard /opt/paseo-ops-panel

COPY entrypoint.sh /usr/local/bin/paseo-ops-entrypoint
RUN chmod +x /usr/local/bin/paseo-ops-entrypoint

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/paseo-ops-entrypoint"]
CMD ["node"]
