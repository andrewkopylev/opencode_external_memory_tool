#!/usr/bin/env bash
set -euo pipefail

# =========================================================================
# install.sh — External Memory Tool installer for OpenCode
# Installs external_memory.py / external_memory.ts into
# ~/.config/opencode/tools/ with a dedicated venv.
# =========================================================================

OPENCODE_DIR="${HOME}/.config/opencode"
TOOLS_DIR="${OPENCODE_DIR}/tools"
VENV_DIR="${TOOLS_DIR}/venv"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()  { echo -e "${RED}[ERR]${NC}   $*"; }
info() { echo -e "${CYAN}[Q]${NC}    $*"; }

# -------------------------------------------------------------------
# Detect existing python3
# -------------------------------------------------------------------
find_system_python() {
    for py in python3 python; do
        if command -v "$py" &>/dev/null; then
            echo "$py"
            return
        fi
    done
    err "No python3 found on the system. Install python3 first."
    exit 1
}

# -------------------------------------------------------------------
# Ensure python3-venv is available (required for creating venv)
# -------------------------------------------------------------------
ensure_venv_module() {
    local py="$1"

    if "$py" -c "import ensurepip" 2>/dev/null; then
        return 0
    fi

    local py_version
    py_version="$("$py" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "")"

    warn "python3-venv (ensurepip) not installed."

    if command -v apt-get &>/dev/null; then
        log "Installing python${py_version}-venv via apt-get (may require sudo password)..."
        sudo -S apt-get update -qq
        sudo -S apt-get install -y -qq "python${py_version}-venv" python3-venv
    elif command -v dnf &>/dev/null; then
        log "Installing python${py_version}-venv via dnf..."
        sudo -S dnf install -y "python${py_version}-venv"
    elif command -v yum &>/dev/null; then
        log "Installing python${py_version}-venv via yum..."
        sudo -S yum install -y "python${py_version}-venv"
    elif command -v pacman &>/dev/null; then
        log "Installing python via pacman..."
        sudo -S pacman -S --noconfirm python
    else
        err "Cannot install python3-venv automatically — unknown package manager."
        err "Please install python3-venv manually and re-run this script."
        exit 1
    fi

    if ! "$py" -c "import ensurepip" 2>/dev/null; then
        err "python3-venv installation failed."
        exit 1
    fi
    log "python3-venv installed."
}

# -------------------------------------------------------------------
# Interactive: ask for embedding API config
# -------------------------------------------------------------------
ask_embedding_config() {
    echo "" >&2
    echo -e "${YELLOW}════════════════════════════════════════════════${NC}" >&2
    echo -e "${YELLOW}  Configure Embedding API (OpenAI-compatible)${NC}" >&2
    echo -e "${YELLOW}════════════════════════════════════════════════${NC}" >&2
    echo "" >&2

    read -r -p "  Base URL (e.g. https://base_url/api/v1): " EMBEDDING_BASE_URL
    EMBEDDING_BASE_URL="${EMBEDDING_BASE_URL:-https://base_url/api/v1}"

    read -r -p "  API Key: " EMBEDDING_API_KEY
    if [ -z "$EMBEDDING_API_KEY" ]; then
        warn "No API key provided. You can set it later in the config file."
        EMBEDDING_API_KEY=""
    fi

    echo -e "  ${CYAN}Recommended model: google/gemini-embedding-2 (fast & stable)${NC}" >&2
    read -r -p "  Model name: " EMBEDDING_MODEL
    EMBEDDING_MODEL="${EMBEDDING_MODEL:-google/gemini-embedding-2}"

    echo "" >&2
    log "Embedding config:"
    log "  Base URL: $EMBEDDING_BASE_URL"
    log "  Model:    $EMBEDDING_MODEL"
    if [ -n "$EMBEDDING_API_KEY" ]; then
        log "  API Key:  ****"
    else
        warn "  API Key:  (not set)"
    fi
}

# -------------------------------------------------------------------
# Check if required pip packages are installed in given python
# -------------------------------------------------------------------
check_packages() {
    local py="$1"
    "$py" -c "import sqlite_vec" 2>/dev/null && \
    "$py" -c "import openai" 2>/dev/null
}

# -------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------
main() {
    echo ""
    echo "============================================"
    echo "  OpenCode External Memory Tool Installer"
    echo "============================================"
    echo ""

    log "Detecting system python..."
    SYSTEM_PYTHON="$(find_system_python)"
    log "System python: $SYSTEM_PYTHON"

    ask_embedding_config

    mkdir -p "$TOOLS_DIR"

    # --- Decide whether we need a venv ---
    NEED_VENV=false

    if [ -f "$VENV_DIR/bin/python3" ] || [ -f "$VENV_DIR/bin/python" ]; then
        if [ -f "$VENV_DIR/bin/python3" ]; then
            VENV_PYTHON="$VENV_DIR/bin/python3"
        else
            VENV_PYTHON="$VENV_DIR/bin/python"
        fi
        log "Existing venv found at $VENV_DIR"
        if check_packages "$VENV_PYTHON"; then
            log "Required packages already installed in venv."
        else
            log "Installing/updating required packages in existing venv..."
            "$VENV_PYTHON" -m pip install --upgrade pip -q
            "$VENV_PYTHON" -m pip install sqlite-vec openai -q
        fi
    elif check_packages "$SYSTEM_PYTHON"; then
        log "Required packages found in system python."
        VENV_PYTHON="$SYSTEM_PYTHON"
    else
        NEED_VENV=true
        warn "Required packages not found. Creating venv..."
    fi

    # --- Create venv if needed ---
    if [ "$NEED_VENV" = true ]; then
        log "Creating venv at: $VENV_DIR"

        if "$SYSTEM_PYTHON" -c "import ensurepip" 2>/dev/null; then
            "$SYSTEM_PYTHON" -m venv "$VENV_DIR"
        elif "$SYSTEM_PYTHON" -m venv --without-pip "$VENV_DIR" 2>/dev/null; then
            log "ensurepip not available — bootstrapping pip via get-pip.py..."
            local GET_PIP
            GET_PIP="$(mktemp /tmp/get-pip.XXXXXX.py)"
            if curl -fsSL --retry 3 https://bootstrap.pypa.io/get-pip.py -o "$GET_PIP"; then
                "$VENV_DIR/bin/python3" "$GET_PIP" --no-setuptools --no-wheel -q
                rm -f "$GET_PIP"
            else
                rm -f "$GET_PIP"
                warn "Cannot download get-pip.py. Trying system package..."
                ensure_venv_module "$SYSTEM_PYTHON"
                rm -rf "$VENV_DIR"
                "$SYSTEM_PYTHON" -m venv "$VENV_DIR"
            fi
        else
            warn "venv creation failed outright. Trying system package..."
            ensure_venv_module "$SYSTEM_PYTHON"
            rm -rf "$VENV_DIR"
            "$SYSTEM_PYTHON" -m venv "$VENV_DIR"
        fi

        if [ -f "$VENV_DIR/bin/python3" ]; then
            VENV_PYTHON="$VENV_DIR/bin/python3"
        elif [ -f "$VENV_DIR/bin/python" ]; then
            VENV_PYTHON="$VENV_DIR/bin/python"
        else
            err "Venv created but python not found inside it."
            exit 1
        fi

        log "Upgrading pip..."
        "$VENV_PYTHON" -m pip install --upgrade pip -q

        log "Installing sqlite-vec and openai..."
        "$VENV_PYTHON" -m pip install sqlite-vec openai -q
    fi

    # --- Verify installation ---
    log "Verifying installation..."
    if ! "$VENV_PYTHON" -c "import sqlite_vec; print('sqlite-vec:', sqlite_vec.__version__ if hasattr(sqlite_vec, '__version__') else 'OK')" 2>/dev/null; then
        err "sqlite-vec verification failed."
        exit 1
    fi
    log "sqlite-vec: OK"

    if ! "$VENV_PYTHON" -c "import openai; print('openai:', openai.__version__)" 2>/dev/null; then
        err "openai package verification failed."
        exit 1
    fi
    log "openai: OK"

    # --- Copy tool files ---
    log "Copying tool files to $TOOLS_DIR ..."
    cp -v "$SCRIPT_DIR/external_memory.py" "$TOOLS_DIR/external_memory.py"
    cp -v "$SCRIPT_DIR/external_memory.ts" "$TOOLS_DIR/external_memory.ts"

    # --- Write config ---
    log "Writing config..."
    cat > "$TOOLS_DIR/external_memory_config.json" <<EOF
{
  "db_path": "$TOOLS_DIR/external_memory.db",
  "embedding": {
    "base_url": "$EMBEDDING_BASE_URL",
    "api_key": "$EMBEDDING_API_KEY",
    "model": "$EMBEDDING_MODEL",
    "timeout_sec": 30
  },
  "search": {
    "default_limit": 10,
    "hybrid_text_weight": 0.3,
    "hybrid_semantic_weight": 0.7
  }
}
EOF
    log "Config written to $TOOLS_DIR/external_memory_config.json"
    log "Venv python: $VENV_PYTHON"

    echo ""
    echo ""
    echo -e "${GREEN}============================================${NC}"
    echo -e "${GREEN}  ✅  INSTALLATION COMPLETE!${NC}"
    echo ""
    echo -e "  ${GREEN}Available tools:${NC}"
    echo "    external_memory_save    — Save a new memory entry"
    echo "    external_memory_search  — Search entries (text / semantic / hybrid)"
    echo "    external_memory_get     — Get full entry by ID"
    echo "    external_memory_update  — Update an existing entry"
    echo "    external_memory_delete  — Delete an entry permanently"
    echo "    external_memory_list    — List all entries (paginated)"
    echo "    external_memory_tags    — List all unique tags"
    echo "    external_memory_stats   — Show memory store statistics"
    echo ""
    echo -e "  ${GREEN}Config:${NC} $TOOLS_DIR/external_memory_config.json"
    echo -e "  ${GREEN}DB:${NC}     $TOOLS_DIR/external_memory.db (auto-created on first use)"
    echo -e "${GREEN}============================================${NC}"
}

main "$@"
