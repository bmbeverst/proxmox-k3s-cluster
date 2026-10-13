# Paseo + Cline + repo toolchain for k3s `paseo`; Renovate tracks PASEO_BASE_TAG.
ARG PASEO_BASE_TAG=0.11.2
FROM ghcr.io/getpaseo/paseo:${PASEO_BASE_TAG}

USER root
ARG CLINE_VERSION=3.0.61
ARG PULUMI_VERSION=3.261.0
ARG UV_VERSION=0.13.0
ENV PULUMI_SKIP_UPDATE_CHECK=true

# One layer for everything heavy: each podman RUN commit costs 1-3 min.
RUN set -eux; \
    export npm_config_cache=/tmp/npmcache; \
    apt-get update; \
    apt-get install -y --no-install-recommends python3 jq ca-certificates unzip tar; \
    rm -rf /var/lib/apt/lists/*; \
    npm install -g "cline@${CLINE_VERSION}"; \
    rm -rf /tmp/npmcache; \
    curl -fsSL https://get.pulumi.com/install.sh -o /tmp/pulumi-install.sh; \
    HOME=/tmp/pulhome sh /tmp/pulumi-install.sh --version "${PULUMI_VERSION}"; \
    cp -a /tmp/pulhome/.pulumi/bin/. /usr/local/bin/; \
    rm -rf /tmp/pulumi-install.sh /tmp/pulhome; \
    curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | env UV_INSTALL_DIR=/usr/local/bin sh; \
    rm -rf /tmp/npmcache; \
    python3 --version; jq --version; pulumi version; uv --version

COPY plugins/ /opt/

# Panel compiles at load: deps must resolve, and it finds the guard via PATH.
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
