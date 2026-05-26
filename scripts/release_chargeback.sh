#!/usr/bin/env bash
# Automation: integrations chargeback package → zip in this repo → optional E2E proof.
#
# Typical flow after editing packages/chargeback in elastic/integrations:
#
#   ./scripts/release_chargeback.sh
#   ./scripts/release_chargeback.sh --cleanup-first --replace-dashboard
#   ./scripts/release_chargeback.sh --skip-e2e          # build + copy only
#
# Environment:
#   INTEGRATIONS_REPO   path to elastic/integrations (default: ../integrations)
#   STACK_VERSION       default 9.2.2 (passed to run_e2e_tests.sh)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHARGEBACK_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

SKIP_E2E=0
SKIP_SYNC=0
RUN_CLEANUP=0
REPLACE_DASHBOARD=0
SKIP_BUILD=0

usage() {
  sed -n '2,18p' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-e2e) SKIP_E2E=1; shift ;;
    --skip-sync) SKIP_SYNC=1; shift ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    --cleanup-first) RUN_CLEANUP=1; shift ;;
    --replace-dashboard) REPLACE_DASHBOARD=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

echo "========== Chargeback release automation =========="
echo "Repo: $CHARGEBACK_REPO"

if [[ "$RUN_CLEANUP" -eq 1 ]]; then
  echo ""
  echo ">>> Step 0: cleanup testing env"
  "$SCRIPT_DIR/cleanup_testing_env.sh"
fi

if [[ "$SKIP_SYNC" -eq 0 ]]; then
  echo ""
  echo ">>> Step 1: build integrations package + copy zip"
  if [[ "$SKIP_BUILD" -eq 1 ]]; then
    "$SCRIPT_DIR/sync_chargeback_from_integrations.sh" --skip-build
  else
    "$SCRIPT_DIR/sync_chargeback_from_integrations.sh"
  fi
else
  echo ""
  echo ">>> Step 1: skipped (--skip-sync)"
fi

if [[ "$SKIP_E2E" -eq 0 ]]; then
  echo ""
  echo ">>> Step 2: E2E tests (install + issue #99 proof)"
  export REPLACE_CHARGEBACK_DASHBOARD="$REPLACE_DASHBOARD"
  E2E_LOG="${CHARGEBACK_E2E_LOG:-/tmp/chargeback_e2e_pass.log}"
  "$SCRIPT_DIR/run_e2e_tests.sh" 2>&1 | tee "$E2E_LOG"
  E2E_EXIT="${PIPESTATUS[0]}"
  if [[ "$E2E_EXIT" -ne 0 ]]; then
    echo "E2E failed (exit $E2E_EXIT). See $E2E_LOG" >&2
    exit "$E2E_EXIT"
  fi
else
  echo ""
  echo ">>> Step 2: skipped (--skip-e2e)"
  echo "Run later: REPLACE_CHARGEBACK_DASHBOARD=1 ./scripts/run_e2e_tests.sh"
fi

echo ""
echo "========== Done =========="
PROOF_LOG="${CHARGEBACK_E2E_PROOF:-/tmp/chargeback_e2e_issue_99_proof.log}"
E2E_LOG="${CHARGEBACK_E2E_LOG:-/tmp/chargeback_e2e_pass.log}"
if [[ -f "$E2E_LOG" ]]; then
  {
    echo "E2E proof for elasticsearch-chargeback#99"
    echo "Run: REPLACE_CHARGEBACK_DASHBOARD=1 ./scripts/release_chargeback.sh"
    echo ""
    sed -n '/--- 12\. Issue #99 proof/,/Issue #99 proof: PASS/p' "$E2E_LOG"
    ZIP="$(ls -1 "$CHARGEBACK_REPO"/integration/assets/*/chargeback-*.zip 2>/dev/null | tail -1)"
    if [[ -f "$ZIP" ]]; then
      echo ""
      if command -v shasum >/dev/null 2>&1; then
        echo "ZIP SHA-256: $(shasum -a 256 "$ZIP" | awk '{print $1}')"
      fi
    fi
  } > "$PROOF_LOG" 2>/dev/null || true
  echo "E2E proof excerpt (local only, not for git): $PROOF_LOG"
fi
echo "Companion PR (elasticsearch-chargeback): commit zip + doc/script changes; paste E2E output into the PR comment."
echo "  git add integration/assets/ integration/docs/ README.md CHANGELOG.md integration/README.md integration/Instructions.md scripts/"
echo "  git commit -m \"chargeback 0.3.2: zip and E2E proof for ES|QL dual-write (#99)\""
echo "Integrations PR should be merged first (or same day); zip is built from that branch head."
echo "See scripts/PR_AND_RELEASE_CHECKLIST.md"
