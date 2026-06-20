#!/usr/bin/env bash
# Grants hedgefund user READ-ONLY access to the Obsidian fortress via ACL, explicit DENY on write.
# Idempotent: re-running is safe.
#
# Two problems this script must solve:
#   1. /Users/sarvesh/Library (and iCloud sub-paths) are mode 700 — hedgefund can't
#      traverse them.  Fix: add "search" (traverse-only) ACL on each intermediate dir.
#   2. ACL file_inherit/directory_inherit only applies to *new* content.  Existing
#      files/dirs inside the fortress have bare POSIX 700.
#      Fix: chmod -R +a to retroactively stamp every existing item.

source "$(dirname "$0")/_lib.sh"
require_sarvesh
ensure_sudo

if [[ ! -d "$FORTRESS" ]]; then
    fail "Fortress not found at: $FORTRESS"
fi

# ── 1. Traversal ACLs on intermediate directories ──────────────────────────
# hedgefund needs "search" (cd/traverse) on every directory component between
# / and the fortress.  We grant search only — not list — so hedgefund cannot
# enumerate other contents of ~/Library.
log "Granting traversal ACL on intermediate iCloud path components..."
INTERMEDIATE=(
    "/Users/sarvesh/Library"
    "/Users/sarvesh/Library/Mobile Documents"
    "/Users/sarvesh/Library/Mobile Documents/iCloud~md~obsidian"
    "/Users/sarvesh/Library/Mobile Documents/iCloud~md~obsidian/Documents"
)
for d in "${INTERMEDIATE[@]}"; do
    if [[ -d "$d" ]]; then
        sudo chmod +a "hedgefund allow search" "$d"
        note "  search ACL → $d"
    else
        warn "Intermediate dir not found (iCloud may not have synced): $d"
    fi
done

# ── 2. Fortress root + recursive propagation to existing content ────────────
log "Granting hedgefund read-only ACL on fortress (recursive for existing files)..."

# Root: allow read+list+search+inherit
sudo chmod +a "hedgefund allow read,list,search,file_inherit,directory_inherit" "$FORTRESS"
# Root: deny all writes+inherit
sudo chmod +a "hedgefund deny write,delete,append,add_file,add_subdirectory,file_inherit,directory_inherit" "$FORTRESS"

# Retroactively stamp every existing file and directory inside the fortress.
# file_inherit/directory_inherit only fires on *new* items; existing ones must
# be updated explicitly.
log "Propagating ACL to existing fortress contents (chmod -R +a) ..."
sudo chmod -R +a "hedgefund allow read,list,search,file_inherit,directory_inherit" "$FORTRESS"
sudo chmod -R +a "hedgefund deny write,delete,append,add_file,add_subdirectory,file_inherit,directory_inherit" "$FORTRESS"

log "Verifying ACL on fortress root..."
ls -led "$FORTRESS" | sed 's/^/    /'

# ── 3. Smoke tests ──────────────────────────────────────────────────────────
log "Smoke test 1: hedgefund can traverse intermediate dirs ..."
for d in "${INTERMEDIATE[@]}"; do
    if [[ -d "$d" ]]; then
        if sudo -u hedgefund test -x "$d" 2>/dev/null; then
            note "  OK  $d"
        else
            warn "  FAIL traverse: $d"
        fi
    fi
done

log "Smoke test 2: hedgefund can list fortress root ..."
if sudo -u hedgefund ls "$FORTRESS" >/dev/null 2>&1; then
    ok "List access works."
else
    fail "List access failed — check ACL and iCloud materialization."
fi

log "Smoke test 3: hedgefund can read a file inside fortress ..."
# Pick the first regular file we can find
sample_file=$(sudo -u hedgefund find "$FORTRESS" -maxdepth 3 -type f ! -name "*.icloud" 2>/dev/null | head -1)
if [[ -n "$sample_file" ]]; then
    if sudo -u hedgefund test -r "$sample_file" 2>/dev/null; then
        ok "File read OK: $sample_file"
    else
        fail "hedgefund cannot read file: $sample_file — ACL propagation incomplete."
    fi
else
    warn "No sample file found (fortress may be empty or fully evicted to iCloud)."
fi

log "Smoke test 4: hedgefund cannot write to fortress ..."
probe="$FORTRESS/.hermes-write-probe"
if sudo -u hedgefund touch "$probe" 2>/dev/null; then
    sudo rm -f "$probe"
    fail "Write SUCCEEDED — deny ACL is not effective. Investigate before proceeding."
else
    ok "Write correctly denied."
fi

ok "Step 2 complete. Fortress is read-only to hedgefund."
