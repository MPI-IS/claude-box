FROM docker.io/library/ubuntu:24.04

RUN set -eux; \
    export DEBIAN_FRONTEND=noninteractive; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg \
        git nodejs npm \
        ripgrep less jq nano \
        procps file unzip xz-utils bash-completion \
        nftables; \
    \
    # claude-box's firewall needs nft, while setpriv is used to drop to the \
    # invoking user's UID after initialization. Fail the build if missing. \
    nft --version; \
    command -v setpriv; \
    \
    # --- Claude Code apt repository -------------------------------------- \
    install -d -m 0755 /etc/apt/keyrings; \
    curl -fsSL https://downloads.claude.ai/keys/claude-code.asc \
        -o /etc/apt/keyrings/claude-code.asc; \
    gpg --show-keys --with-colons /etc/apt/keyrings/claude-code.asc \
        | awk -F: '$1 == "fpr" { print $10 }' \
        | grep -qx 31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE \
        || { echo "ERROR: Claude Code signing key fingerprint mismatch" >&2; exit 1; }; \
    \
    # Channel "stable" lags behind the newest releases and skips known-bad \
    # releases. Change both words to "latest" to track every release. \
    echo "deb [signed-by=/etc/apt/keyrings/claude-code.asc] https://downloads.claude.ai/claude-code/apt/stable stable main" \
        > /etc/apt/sources.list.d/claude-code.list; \
    apt-get update; \
    apt-get install -y claude-code; \
    claude --version; \
    \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*; \
    \
    # The wrapper mounts a per-project host directory here. \
    mkdir -p /claude; \
    chmod 0777 /claude

# Both agents run behind the same container boundary.
ARG CODEX_VERSION=latest
RUN npm install -g "@openai/codex@${CODEX_VERSION}" \
    && codex --version \
    && npm cache clean --force

# --- Python toolchain --------------------------------------------------------
# Anything Claude runs executes inside this container. Ubuntu 24.04 provides
# Python 3.12.
RUN set -eux; \
    export DEBIAN_FRONTEND=noninteractive; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        python3 python3-dev python3-venv python3-pip \
        build-essential pkg-config; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# Pin the tag instead of "latest" if reproducible builds are required.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Poetry is installed in its own virtual environment.
ARG POETRY_VERSION=2.5.1
RUN set -eux; \
    uv venv --no-cache /opt/poetry --python /usr/bin/python3; \
    uv pip install --no-cache --python /opt/poetry/bin/python \
        "poetry==${POETRY_VERSION}"; \
    ln -s /opt/poetry/bin/poetry /usr/local/bin/poetry; \
    poetry --version
# -----------------------------------------------------------------------------

# --- LiteLLM proxy for OpenAI-compatible gateways ---------------------------
# Claude Code speaks the Anthropic Messages API. LiteLLM translates those
# requests for OpenAI-compatible gateways such as SAIA and Bifrost.
#
# It is installed in a separate shared virtual environment under /opt.
ARG LITELLM_VERSION=1.103.2
RUN set -eux; \
    uv venv --no-cache /opt/litellm --python /usr/bin/python3; \
    uv pip install --no-cache --python /opt/litellm/bin/python \
        "litellm[proxy]==${LITELLM_VERSION}"; \
    LITELLM_LOCAL_MODEL_COST_MAP=True \
        /opt/litellm/bin/python -c "import litellm.proxy.proxy_server"; \
    test -x /opt/litellm/bin/litellm
# -----------------------------------------------------------------------------

# PATH includes sbin because nft is normally installed under /usr/sbin.
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    HOME=/claude \
    DISABLE_AUTOUPDATER=1 \
    PATH=/claude/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin \
    XDG_CACHE_HOME=/claude/.cache \
    PIP_CACHE_DIR=/claude/.cache/pip \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_CACHE_DIR=/claude/.cache/uv \
    UV_PYTHON_INSTALL_DIR=/claude/.local/share/uv/python \
    UV_LINK_MODE=copy

# Used when the image is run directly. claude-box supplies its own entrypoint
# when initialization is required.
ENTRYPOINT ["claude"]
