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

# Pulumi CLI stage: tarball fetch, verified against upstream checksums.
FROM ghcr.io/getpaseo/paseo:${PASEO_BASE_TAG} AS pulumi
# renovate: datasource=github-releases depName=pulumi/pulumi
ARG PULUMI_VERSION=3.261.0
RUN set -eux; \
    [ "$(uname -m)" = x86_64 ] || { echo "FATAL: tarball is linux-x64 only"; exit 1; }; \
    mkdir -p /opt/bin; \
    curl -fsSL --retry 3 --retry-all-errors \
      "https://get.pulumi.com/releases/sdk/pulumi-v${PULUMI_VERSION}-linux-x64.tar.gz" \
      -o /tmp/pulumi.tar.gz; \
    curl -fsSL --retry 3 --retry-all-errors \
      "https://github.com/pulumi/pulumi/releases/download/v${PULUMI_VERSION}/pulumi-${PULUMI_VERSION}-checksums.txt" \
      -o /tmp/checksums.txt; \
    grep "pulumi-v${PULUMI_VERSION}-linux-x64.tar.gz" /tmp/checksums.txt \
      | sed "s|pulumi-v${PULUMI_VERSION}-linux-x64.tar.gz|pulumi.tar.gz|" \
      | (cd /tmp && sha256sum -c -); \
    tar -xzf /tmp/pulumi.tar.gz -C /opt/bin --strip-components=1; \
    rm -f /tmp/pulumi.tar.gz /tmp/checksums.txt

# Panel deps: own stage, so tool bumps never re-run npm ci.
FROM ghcr.io/getpaseo/paseo:${PASEO_BASE_TAG} AS panel
WORKDIR /opt/paseo-ops-panel
COPY plugins/paseo-ops-panel/package.json plugins/paseo-ops-panel/package-lock.json ./
RUN set -eux; \
    npm_config_cache=/tmp/npmcache npm ci --no-audit --no-fund; \
    rm -rf /tmp/npmcache

# Upstream uv image: binaries only, nothing to build.
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM ghcr.io/getpaseo/paseo:${PASEO_BASE_TAG} AS final
USER root
ENV PULUMI_SKIP_UPDATE_CHECK=true
# APT_REFRESH is stamped weekly by CI: max ~7 days of apt security lag.
ARG APT_REFRESH
RUN set -eux; \
    apt-get update; \
    apt-get upgrade -y; \
    apt-get install -y --no-install-recommends python3 jq ca-certificates unzip tar; \
    rm -rf /var/lib/apt/lists/*
# Ordered stable -> volatile; each COPY is a re-commit, not a rebuild.
COPY --from=pulumi /opt/bin/ /usr/local/bin/
COPY --from=uv /uv /uvx /usr/local/bin/
COPY --from=panel --chown=paseo:paseo /opt/paseo-ops-panel /opt/paseo-ops-panel
COPY --from=cline /opt/cline /opt/cline
RUN set -eux; \
    ln -s /opt/cline/bin/cline /usr/local/bin/cline; \
    test -x /usr/local/bin/cline; \
    python3 --version; jq --version; pulumi version; uv --version

# Guard script ships 0755 in git; panel sources land paseo-owned.
COPY --chown=paseo:paseo plugins/ /opt/
RUN ln -sf /opt/dangerous-command-guard/cline-approve-dangerous /usr/local/bin/cline-approve-dangerous

COPY entrypoint.sh /usr/local/bin/paseo-ops-entrypoint

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/paseo-ops-entrypoint"]
CMD ["node"]
