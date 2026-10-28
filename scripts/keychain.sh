#!/usr/bin/env bash
# macOS Keychain / Keystore manager for elasticsearch-chargeback
# Securely stores, loads, and manages credentials in macOS Keychain instead of .env files.
#
# Usage:
#   source scripts/keychain.sh              # Load credentials into current shell environment
#   ./scripts/keychain.sh list              # List stored keys
#   ./scripts/keychain.sh get <KEY>         # Print stored value
#   ./scripts/keychain.sh set <KEY> [VAL]   # Store or update value (prompts securely if VAL omitted)
#   ./scripts/keychain.sh delete <KEY>      # Remove key from Keychain
#   ./scripts/keychain.sh import [FILE]     # Import key=val lines from file (e.g. .env) into Keychain
#   ./scripts/keychain.sh export            # Print eval-compatible shell export commands

KEYCHAIN_SERVICE="${KEYCHAIN_SERVICE:-elasticsearch-chargeback}"

keychain_is_macos() {
  [[ "$(uname -s)" == "Darwin" ]] && command -v security >/dev/null 2>&1
}

keychain_get() {
  local key="$1"
  local service="${2:-$KEYCHAIN_SERVICE}"
  if ! keychain_is_macos; then
    return 1
  fi
  security find-generic-password -s "$service" -a "$key" -w 2>/dev/null || true
}

keychain_set() {
  local key="$1"
  local val="$2"
  local service="${3:-$KEYCHAIN_SERVICE}"
  if ! keychain_is_macos; then
    echo "ERROR: macOS security command not available." >&2
    return 1
  fi
  security add-generic-password -U -s "$service" -a "$key" -w "$val"
}

keychain_delete() {
  local key="$1"
  local service="${2:-$KEYCHAIN_SERVICE}"
  if ! keychain_is_macos; then
    return 1
  fi
  security delete-generic-password -s "$service" -a "$key" 2>/dev/null || true
}

keychain_list_keys() {
  local service="${1:-$KEYCHAIN_SERVICE}"
  if ! keychain_is_macos; then
    return 0
  fi
  # Scan keychain items for matching service attribute
  security dump-keychain 2>/dev/null | awk -v svce="$service" '
    /class: "genp"/ { in_item=1; cur_svce=""; cur_acct="" }
    in_item && /"svce"<blob>=/ {
      sub(/.*"svce"<blob>="/, "")
      sub(/".*/, "")
      cur_svce=$0
    }
    in_item && /"acct"<blob>=/ {
      sub(/.*"acct"<blob>="/, "")
      sub(/".*/, "")
      cur_acct=$0
    }
    in_item && (/^keychain:/ || /^$/) {
      if (cur_svce == svce && cur_acct != "") {
        print cur_acct
      }
      cur_svce=""; cur_acct=""
    }
    END {
      if (in_item && cur_svce == svce && cur_acct != "") {
        print cur_acct
      }
    }
  ' | sort -u || true
}

keychain_load_env() {
  local service="${1:-$KEYCHAIN_SERVICE}"
  if ! keychain_is_macos; then
    return 0
  fi

  local loaded_count=0
  local keys
  keys=$(keychain_list_keys "$service")

  # Known standard keys to always check even if dump-keychain missed any:
  local known_keys=(
    "ES_URL" "sl_url" "ES_HOST" "KIBANA_URL" "KIBANA_HOST"
    "ES_API_KEY" "KIBANA_API_KEY" "HUB_KIBANA_API_KEY" "HUB_ES_API_KEY"
    "ORGANIZATION_ID" "BILLING_API_KEY" "USAGE_API_KEY"
    "PROJECT_ID" "project_id" "SL_API_KEY" "sl_api_key"
    "BILLING_API_BASE" "CLOUD_API_BASE"
    "CONF_RATE" "CONF_RATE_UNIT" "CONF_CURRENCY_SYMBOL" "CURL_INSECURE"
  )

  local all_keys
  all_keys=$(printf "%s\n%s\n" "$keys" "$(printf "%s\n" "${known_keys[@]}")" | sed '/^$/d' | sort -u)

  while IFS= read -r k || [[ -n "$k" ]]; do
    [[ -z "$k" ]] && continue
    # Only export if not already explicitly set in environment
    if [[ -z "${!k:-}" ]]; then
      local val
      val="$(keychain_get "$k" "$service" || true)"
      if [[ -n "$val" ]]; then
        export "$k"="$val"
        ((loaded_count++)) || true
      fi
    fi
  done <<< "$all_keys"

  # Standard alias derivation and sensible defaults
  [[ -z "${ES_URL:-}" && -n "${sl_url:-}" ]] && export ES_URL="$sl_url"
  [[ -z "${sl_url:-}" && -n "${ES_URL:-}" ]] && export sl_url="$ES_URL"

  [[ -z "${PROJECT_ID:-}" && -n "${project_id:-}" ]] && export PROJECT_ID="$project_id"
  [[ -z "${project_id:-}" && -n "${PROJECT_ID:-}" ]] && export project_id="$PROJECT_ID"

  [[ -z "${SL_API_KEY:-}" && -n "${sl_api_key:-}" ]] && export SL_API_KEY="$sl_api_key"
  [[ -z "${sl_api_key:-}" && -n "${SL_API_KEY:-}" ]] && export sl_api_key="$SL_API_KEY"

  if [[ -z "${KIBANA_URL:-}" ]]; then
    if [[ -n "${KIBANA_HOST:-}" ]]; then
      export KIBANA_URL="$KIBANA_HOST"
    elif [[ -n "${ES_URL:-}" && "$ES_URL" =~ \.es\. ]]; then
      export KIBANA_URL="${ES_URL//.es./.kb.}"
    fi
  fi

  local hub_key="${HUB_KIBANA_API_KEY:-${HUB_ES_API_KEY:-}}"
  if [[ -z "${ES_API_KEY:-}" && -n "$hub_key" ]]; then
    export ES_API_KEY="$hub_key"
  fi
  if [[ -z "${KIBANA_API_KEY:-}" && -n "$hub_key" ]]; then
    export KIBANA_API_KEY="$hub_key"
  fi
  if [[ -z "${HUB_ES_API_KEY:-}" && -n "${ES_API_KEY:-}" ]]; then
    export HUB_ES_API_KEY="$ES_API_KEY"
  fi

  if [[ "${CBS_KEYCHAIN_SILENT:-0}" != "1" && "$loaded_count" -gt 0 ]]; then
    echo "Loaded $loaded_count credential(s) from macOS Keychain (service: $service)."
  fi
}

keychain_import_file() {
  local env_file="${1:-.env}"
  local service="${2:-$KEYCHAIN_SERVICE}"

  if [[ ! -f "$env_file" ]]; then
    echo "ERROR: File '$env_file' not found." >&2
    return 1
  fi

  echo "Importing credentials from '$env_file' into macOS Keychain (service: $service)..."
  local count=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    if [[ -z "$line" || "$line" =~ ^# ]]; then
      continue
    fi
    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      local k="${BASH_REMATCH[1]}"
      local v="${BASH_REMATCH[2]}"
      # Remove enclosing quotes if any
      v="$(echo "$v" | sed 's/^["'"'"']//;s/["'"'"']$//')"
      keychain_set "$k" "$v" "$service"
      echo "  ✓ Stored: $k"
      ((count++)) || true
    fi
  done < "$env_file"

  echo "Successfully imported $count credential(s) into macOS Keychain."
}

keychain_export() {
  local service="${1:-$KEYCHAIN_SERVICE}"
  local keys
  keys=$(keychain_list_keys "$service")
  for k in $keys; do
    [[ -z "$k" ]] && continue
    local v
    v=$(keychain_get "$k" "$service")
    # Escape single quotes in value
    local escaped_v="${v//\'/\'\\\'\'}"
    printf "export %s='%s'\n" "$k" "$escaped_v"
  done
}

keychain_cli() {
  local cmd="${1:-help}"
  shift || true

  case "$cmd" in
    get)
      local key="${1:-}"
      if [[ -z "$key" ]]; then
        echo "Usage: $0 get <KEY>" >&2
        return 1
      fi
      local val
      val=$(keychain_get "$key")
      if [[ -n "$val" ]]; then
        echo "$val"
      else
        echo "Key '$key' not found in Keychain (service: $KEYCHAIN_SERVICE)." >&2
        return 1
      fi
      ;;
    set)
      local key="${1:-}"
      local val="${2:-}"
      if [[ -z "$key" ]]; then
        echo "Usage: $0 set <KEY> [VALUE]" >&2
        return 1
      fi
      if [[ -z "$val" ]]; then
        read -r -s -p "Enter value for $key: " val
        echo ""
      fi
      keychain_set "$key" "$val"
      echo "✓ Stored '$key' in Keychain (service: $KEYCHAIN_SERVICE)."
      ;;
    delete|del|rm)
      local key="${1:-}"
      if [[ -z "$key" ]]; then
        echo "Usage: $0 delete <KEY>" >&2
        return 1
      fi
      keychain_delete "$key"
      echo "✓ Deleted '$key' from Keychain (service: $KEYCHAIN_SERVICE)."
      ;;
    list|ls)
      echo "Keys stored in macOS Keychain for service '$KEYCHAIN_SERVICE':"
      local found=0
      local keys
      keys=$(keychain_list_keys "$KEYCHAIN_SERVICE")
      for k in $keys; do
        [[ -z "$k" ]] && continue
        local status="[in keychain]"
        if [[ -n "${!k:-}" ]]; then
          status="[in keychain, active in env]"
        fi
        echo "  • $k $status"
        found=1
      done
      if [[ "$found" -eq 0 ]]; then
        echo "  (No keys found)"
      fi
      ;;
    import)
      local file="${1:-.env}"
      keychain_import_file "$file" "$KEYCHAIN_SERVICE"
      ;;
    export)
      keychain_export "$KEYCHAIN_SERVICE"
      ;;
    load)
      keychain_load_env "$KEYCHAIN_SERVICE"
      ;;
    help|-h|--help)
      cat <<EOF
macOS Keychain Manager for elasticsearch-chargeback

Commands:
  source scripts/keychain.sh          Load stored credentials into current shell
  ./scripts/keychain.sh list          List all credentials stored in Keychain
  ./scripts/keychain.sh get <KEY>     Get credential value for KEY
  ./scripts/keychain.sh set <KEY>     Store or update KEY in Keychain
  ./scripts/keychain.sh delete <KEY>  Remove KEY from Keychain
  ./scripts/keychain.sh import [FILE] Import KEY=VAL pairs from file (.env by default)
  ./scripts/keychain.sh export        Print shell export commands

Environment:
  KEYCHAIN_SERVICE                    macOS Keychain service name (default: elasticsearch-chargeback)
  CBS_KEYCHAIN_SILENT=1               Suppress informative messages when loading
EOF
      ;;
    *)
      echo "Unknown command: $cmd" >&2
      echo "Run '$0 help' for usage." >&2
      return 1
      ;;
  esac
}

# Determine if file is being sourced or executed
if (return 0 2>/dev/null); then
  # Sourced in shell or another script
  if [[ "${1:-}" != "--no-auto-load" ]]; then
    keychain_load_env
  fi
else
  # Executed directly from command line
  chmod +x "${BASH_SOURCE[0]}" 2>/dev/null || true
  keychain_cli "$@"
fi
