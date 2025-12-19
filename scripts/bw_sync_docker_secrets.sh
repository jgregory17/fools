#!/usr/bin/env bash
set -euo pipefail

# Bitwarden Sync Docker Secrets Script
# Syncs all Bitwarden items with custom field project=<value> to Docker secret files

# Default values
PROJECT="fools"
DRY_RUN=false
OUT_DIR=".secrets"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --project)
            PROJECT="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --out-dir)
            OUT_DIR="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--project <project>] [--dry-run] [--out-dir <dir>]"
            exit 1
            ;;
    esac
done

# Check Bitwarden session
if [[ -z "${BW_SESSION:-}" ]]; then
    echo "Bitwarden is locked. Run: bw unlock --raw"
    exit 1
fi

# Function to write secret file atomically with proper permissions
write_secret_file() {
    local filepath="$1"
    local content="$2"
    
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  Would write: $filepath"
        return
    fi
    
    # Create temp file with restricted permissions
    local tempfile=$(mktemp "${filepath}.XXXXXX")
    chmod 600 "$tempfile"
    
    # Write content to temp file
    printf "%s" "$content" > "$tempfile"
    
    # Move atomically
    mv -f "$tempfile" "$filepath"
    chmod 600 "$filepath"
}

# Create output directory with proper permissions
if [[ "$DRY_RUN" == "false" ]]; then
    mkdir -p "$OUT_DIR"
    chmod 700 "$OUT_DIR"
fi

# Get all items and filter by project custom field
echo "Fetching items for project: $PROJECT"
ALL_ITEMS=$(bw list items 2>/dev/null || echo "[]")

# Filter items with matching project field
MATCHING_ITEMS=$(echo "$ALL_ITEMS" | jq -r --arg project "$PROJECT" '
    .[] | 
    select(.fields // [] | any(.name == "project" and .value == $project)) |
    @json
')

# Track counts
ITEM_COUNT=0
FILE_COUNT=0
FILES_WRITTEN=()

# Process each matching item
while IFS= read -r item_json; do
    [[ -z "$item_json" ]] && continue
    
    # Parse item fields
    ITEM=$(echo "$item_json" | jq -r '.')
    BITWARDEN_NAME=$(echo "$ITEM" | jq -r '.name // ""')
    LOGIN_USERNAME=$(echo "$ITEM" | jq -r '.login.username // ""')
    LOGIN_PASSWORD=$(echo "$ITEM" | jq -r '.login.password // ""')
    
    # Skip if no name or password
    if [[ -z "$BITWARDEN_NAME" ]] || [[ -z "$LOGIN_PASSWORD" ]]; then
        continue
    fi
    
    ((ITEM_COUNT++))
    
    # Write secret files based on username presence
    if [[ -n "$LOGIN_USERNAME" ]]; then
        # Has username: write both _user and _password files
        USER_FILE="${OUT_DIR}/${BITWARDEN_NAME}_user"
        PASS_FILE="${OUT_DIR}/${BITWARDEN_NAME}_password"
        
        write_secret_file "$USER_FILE" "$LOGIN_USERNAME"
        write_secret_file "$PASS_FILE" "$LOGIN_PASSWORD"
        
        FILES_WRITTEN+=("${BITWARDEN_NAME}_user")
        FILES_WRITTEN+=("${BITWARDEN_NAME}_password")
        ((FILE_COUNT+=2))
    else
        # No username: write password only
        PASS_FILE="${OUT_DIR}/${BITWARDEN_NAME}"
        
        write_secret_file "$PASS_FILE" "$LOGIN_PASSWORD"
        
        FILES_WRITTEN+=("${BITWARDEN_NAME}")
        ((FILE_COUNT++))
    fi
    
done <<< "$MATCHING_ITEMS"

# Ensure .gitignore includes .secrets/
if [[ "$DRY_RUN" == "false" ]]; then
    GITIGNORE_FILE=".gitignore"
    if [[ -f "$GITIGNORE_FILE" ]]; then
        # Check if .secrets/ is already in .gitignore
        if ! grep -q "^\.secrets/$" "$GITIGNORE_FILE" && ! grep -q "^\.secrets$" "$GITIGNORE_FILE"; then
            echo "" >> "$GITIGNORE_FILE"
            echo "# Bitwarden synced secrets" >> "$GITIGNORE_FILE"
            echo ".secrets/" >> "$GITIGNORE_FILE"
            echo "✓ Added .secrets/ to .gitignore"
        fi
    else
        # Create .gitignore with .secrets/
        echo "# Bitwarden synced secrets" > "$GITIGNORE_FILE"
        echo ".secrets/" >> "$GITIGNORE_FILE"
        echo "✓ Created .gitignore with .secrets/"
    fi
fi

# Print summary
if [[ "$DRY_RUN" == "true" ]]; then
    echo ""
    echo "DRY RUN: Would sync $ITEM_COUNT items ($FILE_COUNT secret files) to $OUT_DIR/"
    if [[ ${#FILES_WRITTEN[@]} -gt 0 ]]; then
        echo "Files that would be written:"
        printf "  %s\n" "${FILES_WRITTEN[@]}"
    fi
else
    echo ""
    echo "✓ Synced $ITEM_COUNT items ($FILE_COUNT secret files) to $OUT_DIR/"
fi