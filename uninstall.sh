#!/usr/bin/env bash
# =========================================================================
# uninstall.sh — Remove External Memory Tool from ~/.config/opencode/tools/
# =========================================================================

OPENCODE_DIR="${HOME}/.config/opencode"
TOOLS_DIR="${OPENCODE_DIR}/tools"
VENV_DIR="${TOOLS_DIR}/venv"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()  { echo -e "${RED}[ERR]${NC}   $*"; }

FILES_TO_REMOVE=(
    "$TOOLS_DIR/external_memory.py"
    "$TOOLS_DIR/external_memory.ts"
    "$TOOLS_DIR/external_memory_config.json"
)

echo ""
echo "============================================"
echo "  OpenCode External Memory Tool Uninstall"
echo "============================================"
echo ""

REMOVED=0
SKIPPED=0

for target in "${FILES_TO_REMOVE[@]}"; do
    if [ -e "$target" ] || [ -L "$target" ]; then
        rm -rf "$target"
        log "Removed: $target"
        ((REMOVED++))
    else
        warn "Not found (skip): $target"
        ((SKIPPED++))
    fi
done

# Database is user data — ask before removing
DB_FILE="$TOOLS_DIR/external_memory.db"
DB_WAL="$TOOLS_DIR/external_memory.db-wal"
DB_SHM="$TOOLS_DIR/external_memory.db-shm"
if [ -f "$DB_FILE" ]; then
    echo ""
    warn "Database file found: $DB_FILE"
    echo "  It contains your stored memory entries."
    read -r -p "  Delete database? [y/N]: " delete_db
    if [ "${delete_db,,}" = "y" ] || [ "${delete_db,,}" = "yes" ]; then
        rm -f "$DB_FILE" "$DB_WAL" "$DB_SHM"
        log "Removed database: $DB_FILE"
        ((REMOVED++))
    else
        log "Database kept at: $DB_FILE"
    fi
fi

# Venv is shared with other tools — ask before removing
if [ -d "$VENV_DIR" ]; then
    echo ""
    warn "Venv directory found: $VENV_DIR"
    echo "  It may be shared with other OpenCode tools (e.g. playwright)."
    read -r -p "  Remove venv? [y/N]: " delete_venv
    if [ "${delete_venv,,}" = "y" ] || [ "${delete_venv,,}" = "yes" ]; then
        rm -rf "$VENV_DIR"
        log "Removed venv: $VENV_DIR"
        ((REMOVED++))
    else
        log "Venv kept at: $VENV_DIR"
    fi
fi

echo ""
if [ "$REMOVED" -gt 0 ]; then
    echo -e "${GREEN}============================================${NC}"
    echo -e "${GREEN}  ✅  Done. Removed $REMOVED item(s), skipped $SKIPPED.${NC}"
    echo -e "${GREEN}============================================${NC}"
else
    echo -e "${YELLOW}  Nothing to remove.${NC}"
fi
