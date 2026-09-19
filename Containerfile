# syntax=docker/dockerfile:1
#
# Praxis-AI gateway with custom Vertex AI Anthropic filters.
#
# Self-contained build that:
# 1. Clones praxis-proxy/ai from GitHub
# 2. Copies the praxis-vertex-anthropic filter from this repo
# 3. Compiles praxis-ai with the custom filters baked in (via auto-discovery)
#
# The resulting `acb-praxis-ai:latest` image is a reverse proxy that:
# - Records all LLM requests and responses for token/cost accounting
# - Handles Anthropic<->OpenAI translation (claude-code on local models)
# - Applies custom vertex_anthropic_prepare and benchmark_metrics filters
#
# Built once and cached in podman images; rebuilds only when this
# Containerfile or praxis-vertex-anthropic/* changes (acb/runner.py handles
# automatic building on first use).

# ============================================================================
# Stage 1: Build
# ============================================================================

FROM rust:1.98-alpine AS builder

ENV OPENSSL_STATIC=1

RUN apk add --no-cache musl-dev openssl-dev openssl-libs-static pkgconf cmake make g++ git

WORKDIR /src

# Keep this revision in sync with acb/assets/praxis/Containerfile. Newer
# upstream revisions can change the Praxis filter API or dependency source.
RUN git init . && git remote add origin https://github.com/praxis-proxy/ai && git fetch --depth 1 origin 8f29d5df27b09d89dfcb9f62895d4067364d56e1 && git checkout --detach FETCH_HEAD

# Copy the praxis-vertex-anthropic filter from this repo's build context
# (the repo root). The build script auto-discovers filters via
# [package.metadata.praxis-filters] markers in Cargo.toml.
# Copy only build inputs, excluding local target/ artifacts.
COPY praxis-vertex-anthropic/Cargo.toml praxis-vertex-anthropic/Cargo.lock ./praxis-vertex-anthropic/
COPY praxis-vertex-anthropic/src ./praxis-vertex-anthropic/src

# Add praxis-vertex-anthropic as a workspace member so Cargo sees it.
# Insert it into the members array in the [workspace] section.
RUN sed -i '/^members = \[/a \    "praxis-vertex-anthropic",' Cargo.toml

# Strip workspace members not needed for the binary (tests, xtask).
RUN sed -i '/xtask/d; /tests\//d' Cargo.toml

# add a direct runtime dependency of server/Cargo.toml,
RUN sed -i '/^\[dependencies\]$/a praxis_vertex_anthropic = { package = "praxis-vertex-anthropic", path = "../praxis-vertex-anthropic"}' server/Cargo.toml

# ============================================================================
# Build
# ============================================================================

# Build praxis-ai with our custom filter included.
# The server's build.rs auto-discovers filter crates via the
# [package.metadata.praxis-filters] marker in praxis-vertex-anthropic/Cargo.toml
# and registers them at compile time.
# This upstream revision does not propagate the connection-time SSRF option
# into listener pipelines. Honor the configured value on startup and reload.
RUN sed -i '/pipeline.apply_insecure_options(&config.insecure_options);/a\    pipeline.set_allow_private_upstreams(config.insecure_options.allow_private_upstreams);' server/src/pipelines.rs

RUN cargo build --release -p praxis-ai-proxy --bin praxis-ai \
    && cp target/release/praxis-ai /usr/local/bin/praxis-ai

# ============================================================================
# Stage 2: Runtime
# ============================================================================

FROM alpine:3.24

LABEL org.opencontainers.image.source="https://github.com/praxis-proxy/ai" \
    org.opencontainers.image.description="Praxis AI proxy server with custom Vertex AI Anthropic filters" \
    org.opencontainers.image.licenses="MIT"

RUN apk add --no-cache ca-certificates wget \
    && addgroup -S praxis \
    && adduser -S -G praxis -h /nonexistent -s /sbin/nologin praxis \
    && mkdir -p /etc/praxis

COPY --from=builder --chown=root:root --chmod=0555 \
    /usr/local/bin/praxis-ai /usr/local/bin/praxis-ai

USER praxis:praxis

WORKDIR /etc/praxis

EXPOSE 8080 9901

HEALTHCHECK --interval=5s --timeout=3s --start-period=2s \
    CMD wget -qO- http://127.0.0.1:9901/healthy || exit 1

ENTRYPOINT ["praxis-ai"]
