#!/usr/bin/env bash
set -euo pipefail

# Bitwarden Upsert Secret Script
# Creates or updates a Bitwarden Login item in the local_passwords folder
# with a custom field project=fools

# Default values
FOLDER_NAME="local_passwords"
PROJECT_NAME="fools"
PASSWORD_LENGTH=41
AUTO_UPDATE=false

# Parse arguments
NAME=""
USERNAME=""
PASSWORD=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --name)
            NAME="$2"
            shift 2
            ;;
        --username)
            USERNAME="$2"
            shift 2
            ;;
        --password)
            PASSWORD="$2"
            shift 2
            ;;
        --yes)
            AUTO_UPDATE=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--name <name>] [--username <username>] [--password <password>] [--yes]"
            exit 1
            ;;
    esac
done

# Check Bitwarden session
if [[ -z "${BW_SESSION:-}" ]]; then
    echo "Bitwarden is locked. Run: bw unlock --raw"
    exit 1
fi

# Function to validate lower_snake_case
validate_name() {
    local name="$1"
    if [[ ! "$name" =~ ^[a-z0-9]+(_[a-z0-9]+)*$ ]]; then
        echo "Error: Name must be lower_snake_case (only lowercase letters, numbers, and underscores)"
        echo "Invalid name: $name"
        return 1
    fi
    return 0
}

# Function to generate password with validation
generate_password() {
    local max_retries=50
    local retry=0
    local generated_pw=""
    
    while [[ $retry -lt $max_retries ]]; do
        # Generate password with Bitwarden
        generated_pw=$(bw generate --length "$PASSWORD_LENGTH" --uppercase --lowercase --number --special --ambiguous 2>/dev/null)
        
        # Validate: contains at least 1 digit
        if [[ ! "$generated_pw" =~ [0-9] ]]; then
            ((retry++))
            continue
        fi
        
        # Validate: contains at least 1 special character
        # Common special characters that bw generate uses
        if [[ ! "$generated_pw" =~ [!@#\$%\^&\*\(\)_\+\-=\[\]\{\}\|;:,\.<>\?/] ]]; then
            ((retry++))
            continue
        fi
        
        echo "$generated_pw"
        return 0
    done
    
    echo "Error: Failed to generate valid password after $max_retries attempts" >&2
    exit 1
}

# Get name interactively if not provided
if [[ -z "$NAME" ]]; then
    read -rp "Enter secret name (lower_snake_case): " NAME
fi

# Validate name
if ! validate_name "$NAME"; then
    exit 1
fi

# Get or create the local_passwords folder
FOLDERS=$(bw list folders 2>/dev/null)
FOLDER_ID=$(echo "$FOLDERS" | jq -r '.[] | select(.name == "'"$FOLDER_NAME"'") | .id')

if [[ -z "$FOLDER_ID" ]]; then
    echo "Creating folder: $FOLDER_NAME"
    FOLDER_JSON=$(bw create folder "$(jq -n --arg name "$FOLDER_NAME" '{name: $name}')" 2>/dev/null)
    FOLDER_ID=$(echo "$FOLDER_JSON" | jq -r '.id')
    if [[ -z "$FOLDER_ID" ]]; then
        echo "Error: Failed to create folder"
        exit 1
    fi
fi

# Check if item exists in the folder
ITEMS=$(bw list items --folderid "$FOLDER_ID" 2>/dev/null || echo "[]")
EXISTING_ITEM=$(echo "$ITEMS" | jq -r '.[] | select(.type == 1 and .name == "'"$NAME"'")')

if [[ -n "$EXISTING_ITEM" ]]; then
    # Item exists, check if we should update
    EXISTING_ID=$(echo "$EXISTING_ITEM" | jq -r '.id')
    
    if [[ "$AUTO_UPDATE" == "false" ]]; then
        read -rp "$NAME already exists. Update it? (y/N): " CONFIRM
        if [[ ! "$CONFIRM" =~ ^[yY]$ ]]; then
            echo "Skipped"
            exit 0
        fi
    fi
    
    echo "Updating existing item: $NAME"
    
    # Get full item details
    FULL_ITEM=$(bw get item "$EXISTING_ID" 2>/dev/null)
    
    # Prepare updated item
    UPDATED_ITEM=$(echo "$FULL_ITEM" | jq --arg name "$NAME" '.name = $name')
    
    # Update username if provided
    if [[ -n "$USERNAME" ]]; then
        UPDATED_ITEM=$(echo "$UPDATED_ITEM" | jq --arg username "$USERNAME" '.login.username = $username')
    fi
    
    # Update password - generate if not provided
    if [[ -z "$PASSWORD" ]]; then
        PASSWORD=$(generate_password)
    fi
    UPDATED_ITEM=$(echo "$UPDATED_ITEM" | jq --arg password "$PASSWORD" '.login.password = $password')
    
    # Ensure project custom field exists
    HAS_PROJECT=$(echo "$UPDATED_ITEM" | jq '.fields // [] | any(.name == "project")')
    if [[ "$HAS_PROJECT" == "false" ]]; then
        UPDATED_ITEM=$(echo "$UPDATED_ITEM" | jq --arg project "$PROJECT_NAME" '.fields = ((.fields // []) + [{name: "project", value: $project, type: 0}])')
    else
        UPDATED_ITEM=$(echo "$UPDATED_ITEM" | jq --arg project "$PROJECT_NAME" '(.fields // []) |= map(if .name == "project" then .value = $project else . end)')
    fi
    
    # Update the item
    bw encode | bw edit item "$EXISTING_ID" <<< "$UPDATED_ITEM" >/dev/null 2>&1
    echo "✓ Updated: $NAME"
    
else
    # Create new item
    echo "Creating new item: $NAME"
    
    # Generate password if not provided
    if [[ -z "$PASSWORD" ]]; then
        PASSWORD=$(generate_password)
    fi
    
    # Create the login item JSON
    NEW_ITEM=$(jq -n \
        --arg name "$NAME" \
        --arg username "$USERNAME" \
        --arg password "$PASSWORD" \
        --arg folderId "$FOLDER_ID" \
        --arg project "$PROJECT_NAME" \
        '{
            type: 1,
            name: $name,
            login: {
                username: $username,
                password: $password
            },
            folderId: $folderId,
            fields: [{
                name: "project",
                value: $project,
                type: 0
            }]
        }')
    
    # Create the item
    bw encode | bw create item <<< "$NEW_ITEM" >/dev/null 2>&1
    echo "✓ Created: $NAME"
fi

# Sync secrets to Docker
echo "Syncing secrets to Docker..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$SCRIPT_DIR/bw_sync_docker_secrets.sh" --project "$PROJECT_NAME"