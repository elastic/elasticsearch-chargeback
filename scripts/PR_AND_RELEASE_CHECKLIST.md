# PR and release checklist (Chargeback)

Use this when cutting a release across **elastic/integrations** and **elastic/elasticsearch-chargeback**.

## Branch line (integrations)

**Base branch for Chargeback PRs:** `wip-johannes-chargeback` (or `main` when merging package updates upstream).

### Suggested integrations workflow

```bash
cd /path/to/integrations
git fetch origin
git checkout -b feat/chargeback-<version>-<topic> origin/wip-johannes-chargeback

# Apply package changes under packages/chargeback/ (manifest.yml, transforms, pipelines, dashboards, changelog.yml)
git add packages/chargeback/
git commit -m "feat(chargeback): <summary> (<version>)"
git push -u origin feat/chargeback-<version>-<topic>
gh pr create --base wip-johannes-chargeback --title "Chargeback <version>: <summary>"
```

### elasticsearch-chargeback companion PR (pairs with integrations PR)

**Yes, this works in parallel** — merge order is a process preference, not a technical blocker:

| Order | Works? | Notes |
|-------|--------|--------|
| Both PRs open together | Yes | Zip is built from the **same integrations branch commit** as the package PR. Reviewers see matching code + artifact + E2E proof. |
| Integrations merged first | Preferred | Then merge chargeback PR (zip already matches). If integrations gets a fixup commit, re-run `./scripts/release_chargeback.sh --skip-e2e` and push to the chargeback PR. |
| Chargeback merged first | OK | Zip still valid if built from integrations PR head; link the integrations PR in the chargeback PR body. |

The chargeback repo does **not** block integrations merge. It ships the **downloadable zip**, **add-ons**, and **E2E evidence** for Field/customers.

1. On integrations branch: merge-ready package commit.
2. Run `./scripts/release_chargeback.sh --cleanup-first --replace-dashboard` from `elasticsearch-chargeback`.
3. Open chargeback PR: `integration/assets/<version>/chargeback-<version>.zip`, `CHANGELOG.md`, `integration/README.md`, `integration/Instructions.md`, `integration/docs/troubleshooting.md`, scripts.
4. Paste E2E output (Steps 12–15) into the PR description.

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
2. `scripts/run_e2e_tests.sh` — install integrations + E2E; **step 12** = proof for [#99](https://github.com/elastic/elasticsearch-chargeback/issues/99) (legacy aliases + dashboard COALESCE ES|QL).

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

## GitHub tag and release

Each package version needs a git tag and a GitHub Release so people can subscribe to releases and download the zip from the Releases page.

Convention (matches existing releases):

- Tag: `integration-<version>` (for example `integration-0.5.0`)
- Title: `Chargeback Integration <version>`
- Asset: `integration/assets/<version>/chargeback-<version>.zip`
- Marked as a pre-release until Chargeback is generally available

**Automatic:** after the chargeback PR merges to `main` with a new (or updated) zip, [`.github/workflows/release.yml`](../.github/workflows/release.yml) creates the tag and Release from `CHANGELOG.md`.

**Manual / backfill** (from `main`, after merge):

```bash
./scripts/create_github_release.sh --dry-run --version 0.5.0
./scripts/create_github_release.sh --version 0.5.0
./scripts/create_github_release.sh --missing
```

You can also run **Actions → Release Chargeback package → Run workflow**. Leave **Version** empty to create Releases for every zip that does not have one yet (0.3.0 through current, skipping versions that already have a Release).

Do not tag a feature branch. The script refuses to run off `main` unless you pass `--dry-run` or `--force`.

---

## Summary

| Repo | Branch | Action |
|------|--------|--------|
| **integrations** | `feat/chargeback-<version>-…` → `wip-johannes-chargeback` | Package changes under `packages/chargeback/` |
| **elasticsearch-chargeback** | your release branch → `main` | `./scripts/release_chargeback.sh`, commit zip + scripts + docs, PR. Tag and GitHub Release are created on merge. |

