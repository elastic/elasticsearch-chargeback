# Deployment, Backfill, and Testing Scripts

Scripts for deploying Chargeback integrations, backfilling historical Serverless billing and usage data, and running local E2E developer tests.

## Published Integration & Optional Backfill vs. Maintainer Workflows

### 1. Published Kibana Integration & Optional Usage Backfill
Install **Chargeback Serverless** (`0.1.0+`) directly from the **Kibana Integrations** page (**Project settings -> Management -> Integrations -> Chargeback Serverless**, or search **Integrations**; see [Add an integration to an Elastic Agent policy](https://www.elastic.co/docs/reference/fleet/add-integration-to-policy#add-integration-to-policy-steps)). Initial publication to the Elastic Package Registry reset package versioning from internal `0.3.x`/`0.4.0` pre-releases to `0.1.0`, so `0.1.0+` in Kibana Integrations supersedes `chargeback_serverless-0.4.0.zip`.

If you want to backfill historical per-project usage snapshots without overwriting live Elastic Agent billing data, run [`run_chargeback_serverless_collector.py`](run_chargeback_serverless_collector.py) with `--usage-only`. It uses only the Python `3.9+` standard library (`urllib`, `json`, `datetime`) and works across Linux, Windows/WSL, and macOS via environment variables or a `.env` file.

### 2. Upgrade Helper (`migrate_to_060.sh`)
When upgrading **Chargeback** from pre-release `0.4.x` or `0.5.0` to `0.6.0+` (from Kibana Integrations), run [`./migrate_to_060.sh`](migrate_to_060.sh) (`--dry-run` first) to verify all `chargeback_lookup.*` aliases, carry over legacy `chargeback_conf_lookup` settings if needed, and delete retired concrete `*_lookup` indices.

### 3. Historical Local CLI Deployment (`deploy_chargeback_serverless.sh`)
Developers and maintainers deploying a local pre-release `.zip` end-to-end from the CLI (see [`../integrations/chargeback-serverless/Instructions.md`](../integrations/chargeback-serverless/Instructions.md)) require:
- **`curl`**, **`jq`**, and **`python3` (`3.9+`) with `PyYAML`**:
  - On macOS with Homebrew Python (PEP 668 `externally-managed-environment`), create a virtual environment before running `deploy_chargeback_serverless.sh`:
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    pip install pyyaml
    ```

### 4. Package Maintainers (Rebuilding `.zip` Packages & Running Local Stack E2E Tests)
Required **only** when editing integration package templates in `elastic/integrations` and running `run_e2e_tests.sh` or historical `.zip` builds:
- **`elastic/integrations` repo** (sibling of `elasticsearch-chargeback`, or set `INTEGRATIONS_REPO`)
- **`elastic-package`** (`go install github.com/elastic/elastic-package@latest`)
- **Docker** (4GB+ memory) for local stack E2E tests (`run_e2e_tests.sh`)

---

## Chargeback Serverless (Backfill & Historical Local Deployment)

### Cross-Platform Environment Variables (Linux, Windows/WSL, CI/CD, macOS)

Both `run_chargeback_serverless_collector.py` and `deploy_chargeback_serverless.sh` read standard environment variables (or a `.env` file in the repository root):

```bash
export ORGANIZATION_ID="<YOUR_ORGANIZATION_ID>"
export CLOUD_API_KEY="<YOUR_UNIFIED_CLOUD_API_KEY>"
export ES_URL="https://<your-hub-project>.es.<region>.<csp>.elastic.cloud"
export KIBANA_URL="https://<your-hub-project>.kb.<region>.<csp>.elastic.cloud"
export HUB_ES_API_KEY="<YOUR_HUB_PROJECT_API_KEY>"
export ES_API_KEY="$HUB_ES_API_KEY"
export KIBANA_API_KEY="$HUB_ES_API_KEY"
```

### Running the Standalone Backfill or Historical Local Deployment

```bash
# Option A (If you want to backfill historical usage after Kibana Integrations install):
python3 scripts/run_chargeback_serverless_collector.py --usage-only

# Option B (Developer / Maintainer historical local .zip deployment - requires PyYAML):
python3 -m venv .venv
source .venv/bin/activate
pip install pyyaml

# Deploy archived dist/chargeback_serverless-0.4.0.zip, create dedicated Fleet policy,
# provision lookup-mode index templates & 7 transforms, and run the Python backfill collector:
./scripts/deploy_chargeback_serverless.sh

# Upload package without creating/updating Fleet package policy
./scripts/deploy_chargeback_serverless.sh --skip-policy

# Verification and health check only (prints data stream & lookup counts + ES|QL checks)
./scripts/deploy_chargeback_serverless.sh --verify-only

# MAINTAINER ONLY: Rebuild package zip from elastic/integrations before deploying
./scripts/deploy_chargeback_serverless.sh --build
```

Modular components:
- [`run_chargeback_serverless_collector.py`](run_chargeback_serverless_collector.py): Cross-platform standalone Python collector (stdlib only) with `--usage-only`, `--billing-only`, and chunked `_bulk` writes; supports `0.1.0+` versioned `serverless_*_lookup-1` write aliases and backfills up to 60 days (up to 365 via `--lookbehind`).
- [`deploy_chargeback_serverless.sh`](deploy_chargeback_serverless.sh): Historical local CLI deployer for archived `.zip` packages, lookup templates, transforms, and E2E verification.
- [`configure_chargeback_serverless.sh`](configure_chargeback_serverless.sh): Updates the Fleet package policy settings (Cloud Org ID, unified Cloud API key, optional overrides, currency rates) on a dedicated `'Chargeback Serverless Policy'` via Kibana Fleet API with zero plaintext secret indices.
- [`keychain.sh`](keychain.sh): Optional macOS Keychain credential manager for local macOS developers. Securely stores, loads, lists, and manages environment variables in Keychain (`elasticsearch-chargeback` service) instead of a plaintext `.env`.
- [`install_chargeback_serverless.sh`](install_chargeback_serverless.sh): Legacy installer (deprecated in favor of Kibana Integrations).

### Optional: Managing Credentials with macOS Keychain (macOS Local Developers)

On macOS, local developers can optionally store credentials in the macOS Keychain instead of exporting environment variables or using a `.env` file:

```bash
# Store required credentials for Chargeback Serverless (prompts securely when value is omitted)
./scripts/keychain.sh set ORGANIZATION_ID <YOUR_ORGANIZATION_ID>
./scripts/keychain.sh set CLOUD_API_KEY
./scripts/keychain.sh set ES_URL "https://<your-hub-project>.es.<region>.<csp>.elastic.cloud"
./scripts/keychain.sh set ES_API_KEY
./scripts/keychain.sh set KIBANA_URL "https://<your-hub-project>.kb.<region>.<csp>.elastic.cloud"
./scripts/keychain.sh set KIBANA_API_KEY

# List stored credentials
./scripts/keychain.sh list

# Inspect a credential
./scripts/keychain.sh get ORGANIZATION_ID

# Load credentials into your current shell session
source scripts/keychain.sh
```

---

## Release automation (integrations → zip → E2E)

After editing `packages/chargeback` in **elastic/integrations**:

```bash
# From elasticsearch-chargeback repo root
./scripts/release_chargeback.sh --cleanup-first --replace-dashboard
```

This runs: **build** (`elastic-package build`) → **copy zip** to `integrations/chargeback/assets/dist/<version>/` → **E2E** (including step 12 proof for [issue #99](https://github.com/elastic/elasticsearch-chargeback/issues/99)).

After that PR merges to `main`, GitHub Actions creates the `integration-<version>` tag and a GitHub Release (zip attached, pre-release). To backfill versions that already have a zip but no release:

```bash
./scripts/create_github_release.sh --dry-run --missing
./scripts/create_github_release.sh --missing          # from main after merge
```

Options: `--skip-e2e`, `--skip-build`, `--skip-sync`, `--cleanup-first`, `--replace-dashboard`. See [PR_AND_RELEASE_CHECKLIST.md](PR_AND_RELEASE_CHECKLIST.md) for which integrations PR to use (#18269 merged; new PR for #99; close #18102).

---

## Primary: Automated E2E script

From the **elasticsearch-chargeback** repo root, with the stack already running:

```bash
./scripts/run_e2e_tests.sh
# Install an archived zip under integrations/chargeback/assets/dist/<version>/ instead of building packages/chargeback:
./scripts/run_e2e_tests.sh --from-zip
# Or: CHARGEBACK_INSTALL_FROM=zip CHARGEBACK_VERSION=0.5.0 ./scripts/run_e2e_tests.sh
```

**What it does:** Installs **Elasticsearch** integration -> **On-Prem Billing** -> **Chargeback**, in that order. **On-Prem Billing** is installed from `elastic/integrations` when `packages/onprem_billing` exists, otherwise from the archived zip. Post-install uses a **12 ERU test scenario**: three deployments (dev, prod, monitoring), two deployment groups (product, monitoring); organisation config (12 ERU licence), deployment configs (dev 2 ERU, prod 8 ERU, monitoring 2 ERU), both enrich policies, `calculate_cost` pipeline, and billing transform. Then it seeds **monitoring-indices** and **billing** (plus **0.6.0+** `config_snapshot`, `credit_line_item`, and `node_stats`), resets and starts Chargeback transforms, prints **evidence tables**, runs **step 12** (#99/#104 aliases), and runs **step 13** (`0.5.0+` canonical usage source, tier-preference order, Serverless billing classification; `0.6.0+` versioned `chargeback_lookup.*` aliases, `_id: config_onprem`, and On-Prem `mERU` dual-rate ES|QL), **step 14** (legacy `0.5.0` Serverless usage collector add-on: pipeline, staging index, project registry, pipeline simulate), and **step 15** (Chargeback companion Agent Builder add-on + live ES|QL tool query execution; uses `--skip-workflows` when the Kibana Workflows API is absent on local stacks).

**Env:** `CHARGEBACK_VERSION` (default: integrations `manifest.yml`, else `0.6.0`), `CHARGEBACK_INSTALL_FROM=package|zip` (or `--from-zip` / `--from-package`).

**Before running:** Start the stack from the integrations repo (first time can take 5–10 min):

```bash
cd /path/to/integrations
go run github.com/elastic/elastic-package stack status   # or stack up
```

**After the script:** Run asset tests:

```bash
cd /path/to/integrations/packages/chargeback
go run github.com/elastic/elastic-package test
```

**Step 12 (0.3.2+ / issues #99 and #104):** After lookup indices are populated, the script checks legacy-to-canonical alias mappings (`total_ecu` -> `total_chargeable_units`, `conf_ecu_rate` -> `conf_chargeable_unit_rate`, `conf_ecu_rate_unit` -> `conf_chargeable_unit_rate_unit`) and runs a representative Usage dashboard ES|QL query that uses chargeable-unit field names only (`conf_chargeable_unit_rate`, no legacy `COALESCE` fallbacks) via `POST _query`. The script **exits non-zero** if verification fails.

```bash
REPLACE_CHARGEBACK_DASHBOARD=1 ./scripts/run_e2e_tests.sh
```

**Env (optional):** `INTEGRATIONS_REPO`, `KIBANA_HOST`, `ES_HOST`, `ELASTIC_USER`, `ELASTIC_PASSWORD` (defaults: `https://127.0.0.1:5601`, `https://127.0.0.1:9200`, `elastic`, `changeme`).

---

## Cleanup script

Run before a fresh E2E run or to reset the stack:

```bash
./scripts/cleanup_testing_env.sh
```

Uninstalls Chargeback, On-Prem Billing, and Elasticsearch; removes the Fleet package policy `elasticsearch-monitoring`; deletes the testing indices (lookup indices, monitoring-indices, onprem_billing_config); removes both On-Prem enrich policies (`onprem_billing_config_enrich_policy`, `onprem_billing_org_config_policy`) and the `calculate_cost` pipeline; re-enables legacy monitoring. **On-Prem package version** is discovered from the Fleet API (no hardcoded version).

---

## Why are all lookup indices empty?

Chargeback transforms **read from other indices**. If those sources are empty, the lookups stay at 0 docs:

- **monitoring-indices** ← filled by the Elasticsearch **index_pivot** transform (which needs metrics from the Fleet agent / Elasticsearch integration).
- **cluster_*_contribution_lookup** ← Chargeback transforms read from **monitoring-indices**.
- **chargeback_conf_lookup** ← bootstrap transform reads from **cluster_deployment_contribution_lookup**.
- **billing_cluster_cost_lookup** ← reads from **metrics-ess_billing.billing-\*** (On-Prem or ESS Billing).

The E2E script uses a **12 ERU test setup**: three deployments (**dev**, **prod**, **monitoring**) and two deployment groups (**product**: dev + prod; **monitoring**). It seeds `monitoring-indices` with one doc per deployment (if empty), creates organization config (12 ERU licence), configures each deployment (dev 2 ERU, prod 8 ERU, monitoring 2 ERU), and seeds **metrics-ess_billing.billing-onprem** with **one billing doc per deployment** (3 docs). After resetting and starting all Chargeback transforms, every lookup index is populated with **docs for each deployment**.

---

## Quick test (build + asset test only)

When you only need to **build the package and run asset tests** (no full install of dependencies):

```bash
cd /path/to/integrations
go run github.com/elastic/elastic-package stack status   # or stack up
cd packages/chargeback
go run github.com/elastic/elastic-package build --skip-validation
go run github.com/elastic/elastic-package test
```

Result: package builds, asset test runs. Does **not** install Elasticsearch integration, On-Prem Billing, or Chargeback.

---

## Manual setup (existing stack or when not using the script)

Use when you already have a monitoring cluster or can’t use the script. **Order:** Elasticsearch integration → On-Prem Billing (or ESS Billing) → Chargeback.

1. **Install Elasticsearch integration** (version from integrations repo), then start the transform `logs-elasticsearch.index_pivot-default-*` in **Stack Management → Transforms**.
2. **Install On-Prem Billing** or **ESS Billing**; run post-install steps; ensure billing data in `metrics-ess_billing.billing-*`.
3. **Install Chargeback** (e.g. **Integrations → Create new integration → Upload it as a .zip**). Use the built zip from the integrations repo (`cd packages/chargeback && elastic-package build`) or a zip from this repo’s `integrations/chargeback/assets/dist/<version>/chargeback-<version>.zip` (use the version you built or the one under `integrations/chargeback/assets/dist/`).

Chargeback will create `chargeback_conf_lookup` and start its transforms. Verify in **Stack Management → Transforms** (filter `chargeback`).

---

## Verification

- **Transforms:** `billing_cluster_cost`, `billing_realized_pool`, `chargeback_conf_lookup`, `cluster_capacity_utilization`, `cluster_deployment_contribution`, `cluster_datastream_contribution`, `cluster_tier_contribution`, `cluster_tier_and_ds_contribution`.
- **Lookup indices:** `billing_cluster_cost_lookup`, `billing_realized_pool_lookup`, `chargeback_conf_lookup`, `cluster_capacity_utilization_lookup`, `cluster_*_contribution_lookup`.
- **Dashboards:** **Dashboards** → `[Chargeback] Billing Components Overview`, `[Chargeback] Usage & Cost Allocation`, and `[Chargeback] Configuration`.

---

## Troubleshooting

- **Stack:** First start 5–10 min. Unhealthy → `docker ps` / `docker logs <container>`; try `elastic-package stack down` then `stack up`. Package not in Kibana → check registry URL in Fleet settings.
- **Transforms:** Stack 9.2+ required. No data → ensure `logs-elasticsearch.index_pivot-default-*` is running and source integrations have data (`metrics-ess_billing.billing-*`, monitoring indices).
- **Dashboard:** Use a Chargeback version that supports TO_DOUBLE() / chargeable units if you see integer-division issues. Missing `chargeback_conf_lookup` → bootstrap transform needs `cluster_deployment_contribution_lookup` (Elasticsearch transform running first).
