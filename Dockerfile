# syntax=docker/dockerfile:1.7

# git-branchless is a Rust binary. Build it in an isolated stage so the final
# image does not carry the Rust toolchain. Match the runtime's Debian release
# (trixie) so the linked libsqlite3 ABI agrees between stages.
FROM rust:1-trixie AS branchless-builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends libsqlite3-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN cargo install --locked git-branchless

FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# git for the workspace clones, libsqlite3 because git-branchless stores its
# event log in a SQLite file under .git/branchless/ (the bot itself uses Redis
# exclusively), ca-certificates so httpx can verify ADO over TLS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git libsqlite3-0 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=branchless-builder /usr/local/cargo/bin/git-branchless /usr/local/bin/git-branchless

WORKDIR /app

# Copy package sources explicitly; tests and local venvs are excluded by .dockerignore.
COPY pyproject.toml README.md ./
COPY stack_core/ ./stack_core/
COPY stack_cli/ ./stack_cli/
COPY stack_bot/ ./stack_bot/

RUN uv pip install --system .

# Workspace base dir per BotConfig defaults; ephemeral git clones land here.
RUN mkdir -p /var/lib/stackbot/workspaces /etc/stackbot

EXPOSE 8080

CMD ["stackbot"]
