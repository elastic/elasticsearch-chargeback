#!/usr/bin/env bash
# Create git tags and GitHub Releases for Chargeback integration package versions.
#
# Tag convention:  integration-<version>   (matches existing releases)
# Release title:   Chargeback Integration <version>
# Release asset:   integration/assets/<version>/chargeback-<version>.zip
#
# Usage:
#   ./scripts/create_github_release.sh --version 0.4.3
#   ./scripts/create_github_release.sh --missing
#   ./scripts/create_github_release.sh --changed-since <sha>
#   ./scripts/create_github_release.sh --dry-run --missing
#
# Environment (GitHub Actions sets these):
#   GH_TOKEN / GITHUB_TOKEN   required for gh
#   EVENT_NAME                push | workflow_dispatch
#   BEFORE_SHA / AFTER_SHA    push commit range
#   RELEASE_VERSION           workflow_dispatch version input
#
# On push to main, the workflow calls this with no flags; the script then
# releases versions whose zip was added or updated in that push.
# workflow_dispatch with a version releases that version; empty input backfills
# every version that has a zip but no GitHub Release yet.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO"

MODE=""
VERSION_ARG=""
SINCE_SHA=""
DRY_RUN=0
FORCE=0

usage() {
  sed -n '2,24p' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION_ARG="${2:-}"; MODE="version"; shift 2 ;;
    --missing) MODE="missing"; shift ;;
    --changed-since) SINCE_SHA="${2:-}"; MODE="changed"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

# Infer mode when invoked from GitHub Actions with no flags.
if [[ -z "$MODE" ]]; then
  EVENT_NAME="${EVENT_NAME:-}"
  if [[ "$EVENT_NAME" == "workflow_dispatch" ]]; then
    if [[ -n "${RELEASE_VERSION:-}" ]]; then
      MODE="version"
      VERSION_ARG="$RELEASE_VERSION"
    else
      MODE="missing"
    fi
  elif [[ "$EVENT_NAME" == "push" ]]; then
    MODE="changed"
    SINCE_SHA="${BEFORE_SHA:-}"
  else
    echo "Specify --version <x.y.z>, --missing, or --changed-since <sha>." >&2
    usage
    exit 1
  fi
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "gh is required (GitHub CLI)." >&2
  exit 1
fi

current_branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$FORCE" -eq 0 && "$DRY_RUN" -eq 0 && -z "${GITHUB_ACTIONS:-}" && "$current_branch" != "main" ]]; then
  echo "Refusing to create a GitHub Release off main (current branch: $current_branch)." >&2
  echo "Merge the version to main first, then run this script, or pass --force / --dry-run." >&2
  exit 1
fi

is_semver() {
  [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

zip_path_for() {
  local version="$1"
  echo "$REPO/integration/assets/${version}/chargeback-${version}.zip"
}

# Skip 0.0.x and versions before the first GitHub Release (integration-0.1.5).
should_skip_version() {
  local version="$1"
  local oldest="0.1.5"
  [[ "$version" == 0.0.* ]] && return 0
  if [[ "$version" != "$oldest" && "$(printf '%s\n%s\n' "$version" "$oldest" | sort -V | head -1)" == "$version" ]]; then
    return 0
  fi
  return 1
}

list_all_versions() {
  local zip version
  for zip in "$REPO"/integration/assets/[0-9]*/chargeback-*.zip; do
    [[ -f "$zip" ]] || continue
    version="$(basename "$(dirname "$zip")")"
    is_semver "$version" || continue
    should_skip_version "$version" && continue
    [[ "$(basename "$zip")" == "chargeback-${version}.zip" ]] || continue
    git ls-files --error-unmatch "integration/assets/${version}/chargeback-${version}.zip" >/dev/null 2>&1 || continue
    echo "$version"
  done | sort -t. -k1,1n -k2,2n -k3,3n
}

list_changed_versions() {
  local before after files zip version
  after="${AFTER_SHA:-HEAD}"
  before="${SINCE_SHA:-}"
  if [[ -z "$before" || "$before" =~ ^0+$ ]]; then
    files="$(git diff-tree --no-commit-id --name-only -r "$after" -- 'integration/assets/*/chargeback-*.zip' || true)"
  else
    files="$(git diff --name-only --diff-filter=AM "$before" "$after" -- 'integration/assets/*/chargeback-*.zip' || true)"
  fi
  while IFS= read -r zip; do
    [[ -n "$zip" ]] || continue
    version="$(basename "$(dirname "$zip")")"
    is_semver "$version" || continue
    should_skip_version "$version" && continue
    echo "$version"
  done <<< "$files" | sort -u
}

release_exists() {
  gh release view "integration-$1" >/dev/null 2>&1
}

extract_notes() {
  local version="$1"
  local changelog="$REPO/CHANGELOG.md"
  local body
  body="$(awk -v ver="$version" '
    $0 ~ "^### \\[" ver "\\]" { found=1; next }
    found && /^### \[/ { exit }
    found && /^---[[:space:]]*$/ { next }
    found { print }
  ' "$changelog" | sed -E 's/^#### /## /')"
  # Drop leading and trailing blank lines without touching list indentation.
  body="$(printf '%s\n' "$body" | awk '
    NF { for (i = 1; i <= pending; i++) if (started) print ""; pending = 0; started = 1; print; next }
    started { pending++ }
  ')"

  if [[ -z "$(printf '%s' "$body" | tr -d '[:space:]')" ]]; then
    body="Chargeback integration package ${version}."
  fi

  cat <<EOF
${body}

**Full Changelog**: https://github.com/elastic/elasticsearch-chargeback/blob/main/CHANGELOG.md
EOF
}

target_sha_for() {
  local version="$1"
  local zip="integration/assets/${version}/chargeback-${version}.zip"
  local sha
  sha="$(git log -1 --format=%H -- "$zip" 2>/dev/null || true)"
  if [[ -n "$sha" ]]; then
    echo "$sha"
  else
    git rev-parse HEAD
  fi
}

create_release() {
  local version="$1"
  local zip tag title notes_file sha rel_zip
  zip="$(zip_path_for "$version")"
  tag="integration-${version}"
  title="Chargeback Integration ${version}"

  if [[ ! -f "$zip" ]]; then
    echo "Skip ${version}: zip not found at $zip" >&2
    return 1
  fi

  rel_zip="integration/assets/${version}/chargeback-${version}.zip"
  if [[ "$FORCE" -eq 0 ]] && ! git ls-files --error-unmatch "$rel_zip" >/dev/null 2>&1; then
    echo "Skip ${version}: ${rel_zip} is not tracked in git."
    return 0
  fi

  if release_exists "$version"; then
    echo "Skip ${version}: GitHub Release ${tag} already exists."
    return 0
  fi

  sha="$(target_sha_for "$version")"
  notes_file="$(mktemp)"
  extract_notes "$version" > "$notes_file"

  echo "Release ${tag}  title=${title}  sha=${sha}"
  echo "  asset: $zip"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  [dry-run] would run: gh release create ${tag} --title \"${title}\" --prerelease --target ${sha} ${zip}"
    echo "  notes:"
    sed 's/^/    /' "$notes_file"
    rm -f "$notes_file"
    return 0
  fi

  gh release create "$tag" \
    --title "$title" \
    --notes-file "$notes_file" \
    --prerelease \
    --target "$sha" \
    "$zip"
  rm -f "$notes_file"
  echo "Created ${tag}: https://github.com/elastic/elasticsearch-chargeback/releases/tag/${tag}"
}

versions=()
case "$MODE" in
  version)
    if ! is_semver "$VERSION_ARG"; then
      echo "Invalid version: ${VERSION_ARG:-<empty>} (expected x.y.z)" >&2
      exit 1
    fi
    versions=("$VERSION_ARG")
    ;;
  missing)
    while IFS= read -r version; do
      [[ -n "$version" ]] || continue
      if release_exists "$version"; then
        echo "Already released: integration-${version}"
        continue
      fi
      versions+=("$version")
    done < <(list_all_versions)
    ;;
  changed)
    while IFS= read -r version; do
      [[ -n "$version" ]] || continue
      versions+=("$version")
    done < <(list_changed_versions)
    ;;
  *)
    echo "Unknown mode: $MODE" >&2
    exit 1
    ;;
esac

if [[ ${#versions[@]} -eq 0 ]]; then
  echo "No Chargeback versions to release."
  exit 0
fi

echo "Versions to release: ${versions[*]}"
failed=0
for version in "${versions[@]}"; do
  if ! create_release "$version"; then
    failed=1
  fi
done
exit "$failed"
