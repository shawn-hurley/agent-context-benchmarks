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

# Clone praxis-ai from GitHub. Pin to a specific commit if reproducibility
# across rebuilds becomes critical; using main head for latest features/fixes.
RUN git clone --depth 1 https://github.com/praxis-proxy/ai .

# Copy the praxis-vertex-anthropic filter from this repo's build context
# (the repo root). The build script auto-discovers filters via
# [package.metadata.praxis-filters] markers in Cargo.toml.
COPY praxis-vertex-anthropic ./praxis-vertex-anthropic

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
RUN cargo clean && cargo build --release \
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
