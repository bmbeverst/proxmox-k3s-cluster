# Vintage Story server runtime image — slim by design.
ARG DOTNET_TAG=10.0.11
FROM mcr.microsoft.com/dotnet/runtime:${DOTNET_TAG}

# .NET 10 runtime is the documented system dependency (dotnet-runtime-10.0).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates tar jq \
 && rm -rf /var/lib/apt/lists/*

# Non-root runtime identity
RUN useradd --system --uid 1001 --home-dir /serverfiles --shell /bin/bash vints \
 && mkdir -p /serverfiles /entrypoints \
 && chown -R vints:vints /serverfiles
COPY entrypoints/ /entrypoints/
RUN chmod +x /entrypoints/*.sh

USER 1001
WORKDIR /serverfiles
ENV PORT=42420 DATA_PATH=/data
# No ENTRYPOINT on purpose
