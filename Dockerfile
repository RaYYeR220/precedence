# syntax=docker/dockerfile:1.7

# --- build: the Rust harness (Linux build includes the entry recorder) ------
FROM rust:1.95-bookworm AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock rust-toolchain.toml ./
COPY crates ./crates
ARG GIT_COMMIT=unknown
ENV PRECEDENCE_GIT_COMMIT=${GIT_COMMIT}
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/src/target \
    cargo build --release --locked && cp target/release/precedence /usr/local/bin/precedence

# --- test: Rust and Python suites --------------------------------------------
FROM build AS test
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv python3-pip && rm -rf /var/lib/apt/lists/*
COPY analysis ./analysis
RUN python3 -m venv /venv && /venv/bin/pip install --no-cache-dir -e ./analysis[test]
CMD ["sh", "-c", "cargo test --release --locked && cd analysis && /venv/bin/python -m pytest -q"]

# --- runtime: binary + analysis ---------------------------------------------
FROM python:3.12-slim-bookworm AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=build /usr/local/bin/precedence /usr/local/bin/precedence
COPY analysis /opt/analysis
RUN pip install --no-cache-dir /opt/analysis
WORKDIR /work
ENV PRECEDENCE_DATA=/work/data \
    PRECEDENCE_KEYPAIR=/keys/burner.json \
    PREC_TEMPLATES=/opt/analysis/templates \
    RUST_LOG=info
CMD ["precedence", "--help"]

# --- localnet: positive control against a real Agave validator --------------
FROM runtime AS localnet
ARG AGAVE_VERSION=v4.3.2
RUN apt-get update && apt-get install -y --no-install-recommends curl bzip2 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSL "https://github.com/anza-xyz/agave/releases/download/${AGAVE_VERSION}/solana-release-x86_64-unknown-linux-gnu.tar.bz2" \
    | tar -xj -C /opt \
 && ln -s /opt/solana-release/bin/* /usr/local/bin/
COPY localnet/run.sh /usr/local/bin/localnet-run
RUN chmod +x /usr/local/bin/localnet-run
CMD ["localnet-run"]
