# Criba dev container: every quality check in CONSTRAINTS.md runs here, never on the host.
# Build context is ./src/docker/dev; the repository is bind-mounted at /workspace by the
# `dev` service in docker-compose.yml (profile `dev`). Run checks with src/scripts/check.sh.
FROM ghcr.io/astral-sh/uv:0.11.6 AS uv
FROM node:26.11.1-trixie-slim AS node

FROM python:3.12.15-slim-trixie

ARG TARGETARCH
ARG GITLEAKS_VERSION=8.30.1
ARG GITLEAKS_SHA256_AMD64=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb
ARG GITLEAKS_SHA256_ARM64=e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080
ARG OSV_SCANNER_VERSION=2.6.0
ARG OSV_SCANNER_SHA256_AMD64=ca69b3d3cd08f889a49dc0a383122f71cc528b83803671df5fd874d97485b108
ARG OSV_SCANNER_SHA256_ARM64=2c71403eb443d05891c4f268c3ad771cf4f16e5443463fd7851ef8f454d3c7e4
ARG SEMGREP_VERSION=1.178.0

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git libatomic1 sqlite3 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) gl_arch=x64;   gl_sum=$GITLEAKS_SHA256_AMD64; osv_sum=$OSV_SCANNER_SHA256_AMD64 ;; \
      arm64) gl_arch=arm64; gl_sum=$GITLEAKS_SHA256_ARM64; osv_sum=$OSV_SCANNER_SHA256_ARM64 ;; \
      *) echo "unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/gitleaks.tgz "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_${gl_arch}.tar.gz"; \
    echo "$gl_sum  /tmp/gitleaks.tgz" | sha256sum -c -; \
    tar -xzf /tmp/gitleaks.tgz -C /usr/local/bin gitleaks; \
    curl -fsSLo /usr/local/bin/osv-scanner "https://github.com/google/osv-scanner/releases/download/v${OSV_SCANNER_VERSION}/osv-scanner_linux_${TARGETARCH}"; \
    echo "$osv_sum  /usr/local/bin/osv-scanner" | sha256sum -c -; \
    chmod 0755 /usr/local/bin/osv-scanner; \
    rm -f /tmp/gitleaks.tgz

ENV UV_TOOL_DIR=/opt/uv-tools \
    UV_TOOL_BIN_DIR=/usr/local/bin \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    SEMGREP_SEND_METRICS=off \
    CRIBA_DEV_CONTAINER=1
RUN uv tool install "semgrep==${SEMGREP_VERSION}"

# The repository is bind-mounted; its owner differs from the container user.
RUN git config --system --add safe.directory /workspace

WORKDIR /workspace
# Runs as root on purpose: bind-mounted files must stay writable on Linux CI runners.
# nosemgrep: dockerfile.security.missing-user.missing-user -- exception W1 (CONSTRAINTS.md)
CMD ["sleep", "infinity"]
