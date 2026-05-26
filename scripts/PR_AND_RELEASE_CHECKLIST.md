# PR and release checklist (Chargeback)

Use this when cutting a release across **elastic/integrations** and **elastic/elasticsearch-chargeback**.

## Branch line (integrations)

**Base branch for Chargeback PRs:** `wip-johannes-chargeback` (not `main`) until Chargeback is GA.

## PR map (less convoluted)

| PR | State | What it is | What to do |
|----|--------|------------|------------|
| [integrations#18102](https://github.com/elastic/integrations/pull/18102) | Open, **CONFLICTING** | 0.3.1 chargeable-unit rename (old branch `wip-johannes-chargeback-chargable-units`) | **Close** — 0.3.1 is already on `wip-johannes-chargeback`; branch is stale after #18269 merged |
| [integrations#18269](https://github.com/elastic/integrations/pull/18269) | **Merged** | 0.3.2 `monitoring-indices*` for usage transforms | Done — this is the correct integrations PR for 0.3.2 |
| **New PR** (you) | — | 0.3.2 bugfix: dual-write ECU fields for dashboard ES|QL ([#99](https://github.com/elastic/elasticsearch-chargeback/issues/99)) | Open **one** PR: `fix/chargeback-99-esql-dual-write` → `wip-johannes-chargeback` |

Do **not** reopen or stack more commits on #18269’s merged branch name. Do **not** try to merge #18102; resolve by closing with a short comment.

### Suggested integrations workflow for #99 fix

```bash
cd /path/to/integrations
git fetch origin wip-johannes-chargeback
git checkout -b fix/chargeback-99-esql-dual-write origin/wip-johannes-chargeback

# Apply package changes (dual-write fields, ingest pipelines, changelog → links to elasticsearch-chargeback#99)
# Do not reference integrations#18102 in new changelog lines.

git add packages/chargeback/
git commit -m "fix(chargeback): dual-write legacy ECU fields for dashboard ES|QL (0.3.2)"
git push -u origin fix/chargeback-99-esql-dual-write
gh pr create --base wip-johannes-chargeback --title "Chargeback 0.3.2: dual-write ECU fields for dashboard ES|QL" --body "Fixes elastic/elasticsearch-chargeback#99. Follow-up to #18269 (merged). Closes dashboard Unknown column [total_ecu] / [conf_ecu_rate] on 0.3.1 lookups."
```

Keep **one changelog bugfix** under `0.3.2` if that version is not published yet; use `0.3.3` only if 0.3.2 is already shipped externally.

### elasticsearch-chargeback companion PR (pairs with integrations PR)

**Yes, this works in parallel** — merge order is a process preference, not a technical blocker:

| Order | Works? | Notes |
|-------|--------|--------|
| Both PRs open together | Yes | Zip is built from the **same integrations branch commit** as the package PR (e.g. #19196). Reviewers see matching code + artifact + E2E proof. |
| Integrations merged first | Preferred | Then merge chargeback PR (zip already matches). If integrations gets a fixup commit, re-run `./scripts/release_chargeback.sh --skip-e2e` and push to the chargeback PR. |
| Chargeback merged first | OK | Zip still valid if built from integrations PR head; link #19196 in the chargeback PR body. |

The chargeback repo does **not** block integrations merge. It ships the **downloadable zip** and **E2E evidence** for Field/customers.

1. On integrations branch (e.g. `fix/chargeback-99-esql-dual-write`): merge-ready package commit.
2. Run `./scripts/release_chargeback.sh --cleanup-first --replace-dashboard` from elasticsearch-chargeback.
3. Open chargeback PR: `integration/assets/0.3.2/chargeback-0.3.2.zip`, scripts, troubleshooting — link **integrations #19196** and **#99**.
4. Paste E2E step 12 output into **both** PR descriptions (written locally to `/tmp/chargeback_e2e_issue_99_proof.log` by `release_chargeback.sh`; do not commit).

---

## Automation (build → copy → E2E)

From **elasticsearch-chargeback** repo root:

```bash
# Full pipeline: build package in integrations, copy zip, run E2E (requires stack up)
./scripts/release_chargeback.sh --cleanup-first --replace-dashboard

# Build + copy only (no Docker / stack)
./scripts/release_chargeback.sh --skip-e2e

# Copy an existing integrations build artifact
./scripts/release_chargeback.sh --skip-e2e --skip-build
```

Steps performed:

1. `scripts/sync_chargeback_from_integrations.sh` — `elastic-package build` in `integrations/packages/chargeback`, copy to `integration/assets/<version>/chargeback-<version>.zip`, print SHA-256.
2. `scripts/run_e2e_tests.sh` — install integrations + E2E; **step 12** = proof for [#99](https://github.com/elastic/elasticsearch-chargeback/issues/99) (dual-write + dashboard COALESCE ES|QL).

Prerequisites: [scripts/README.md](README.md) (Docker, `elastic-package`, stack `9.2.2`).

---

## Manual checklist (if not using automation)

### 1. Integrations — package changes

- Commit under `packages/chargeback/`
- PR base: `wip-johannes-chargeback`

### 2. Build

```bash
cd integrations/packages/chargeback
elastic-package build
# → integrations/build/packages/chargeback-<version>.zip
```

### 3. Copy zip to elasticsearch-chargeback

```bash
./scripts/sync_chargeback_from_integrations.sh
```

### 4. E2E proof for PR

```bash
REPLACE_CHARGEBACK_DASHBOARD=1 ./scripts/run_e2e_tests.sh
# Paste "Step 12 — Issue #99 proof: PASS" into PR description
```

---

## Summary

| Repo | Branch | Action |
|------|--------|--------|
| **integrations** | `fix/chargeback-99-…` → `wip-johannes-chargeback` | New PR for dual-write (#99); close #18102; #18269 already merged |
| **elasticsearch-chargeback** | your release branch → `main` | `./scripts/release_chargeback.sh`, commit zip + scripts + docs, PR |
