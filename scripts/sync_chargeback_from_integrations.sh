#!/usr/bin/env bash
# Build the chargeback package from elastic/integrations and copy the zip into this repo.
#
# Usage (from elasticsearch-chargeback repo root):
#   ./scripts/sync_chargeback_from_integrations.sh
#   INTEGRATIONS_REPO=/path/to/integrations ./scripts/sync_chargeback_from_integrations.sh
#   ./scripts/sync_chargeback_from_integrations.sh --skip-build   # copy existing build artifact only
#
# Output: integration/assets/<version>/chargeback-<version>.zip (+ SHA-256 on stdout)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHARGEBACK_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
INTEGRATIONS_REPO="${INTEGRATIONS_REPO:-$CHARGEBACK_REPO/../integrations}"
SKIP_BUILD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build) SKIP_BUILD=1; shift ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

if [[ ! -d "$INTEGRATIONS_REPO/packages/chargeback" ]]; then
  echo "INTEGRATIONS_REPO not found: $INTEGRATIONS_REPO" >&2
  echo "Set INTEGRATIONS_REPO to your elastic/integrations clone." >&2
  exit 1
fi
INTEGRATIONS_REPO="$(cd "$INTEGRATIONS_REPO" && pwd)"

if command -v elastic-package >/dev/null 2>&1; then
  EP_CMD="elastic-package"
else
  EP_CMD="go run github.com/elastic/elastic-package"
fi

VERSION="$(grep -E '^version:' "$INTEGRATIONS_REPO/packages/chargeback/manifest.yml" | awk '{print $2}')"
if [[ -z "$VERSION" ]]; then
  echo "Could not read package version from manifest.yml" >&2
  exit 1
fi

ZIP_NAME="chargeback-${VERSION}.zip"
BUILD_ZIP="$INTEGRATIONS_REPO/build/packages/$ZIP_NAME"
DEST_DIR="$CHARGEBACK_REPO/integration/assets/$VERSION"
DEST_ZIP="$DEST_DIR/$ZIP_NAME"

echo "=== Sync chargeback package ==="
echo "Integrations: $INTEGRATIONS_REPO"
echo "Version:      $VERSION"
echo "Destination:  $DEST_ZIP"

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  echo "--- elastic-package build ---"
  # Chargeback ships kibana/links nav panels; package validation rejects them (acknowledged).
  (cd "$INTEGRATIONS_REPO/packages/chargeback" && $EP_CMD build --skip-validation)
else
  echo "--- skip build (--skip-build) ---"
fi

if [[ ! -f "$BUILD_ZIP" ]]; then
  echo "Build artifact missing: $BUILD_ZIP" >&2
  exit 1
fi

mkdir -p "$DEST_DIR"
cp "$BUILD_ZIP" "$DEST_ZIP"
echo "--- copied ---"
ls -lh "$DEST_ZIP"
if command -v shasum >/dev/null 2>&1; then
  echo "SHA-256: $(shasum -a 256 "$DEST_ZIP" | awk '{print $1}')"
elif command -v sha256sum >/dev/null 2>&1; then
  echo "SHA-256: $(sha256sum "$DEST_ZIP" | awk '{print $1}')"
fi
