# Deployment, Backfill, and Testing Scripts

Scripts for deploying Chargeback integrations, backfilling historical Serverless billing and usage data, and running local E2E developer tests.

## Prerequisites: Users vs. Developers

### For Users (Installing & Backfilling Prebuilt Packages)
You do **not** need the `elastic/integrations` repository, `elastic-package`, or Docker to deploy or backfill Chargeback Serverless:
- **Prebuilt package `.zip`**: Included in the repository at [`../integrations/chargeback-serverless/assets/dist/chargeback_serverless-0.3.15.zip`](../integrations/chargeback-serverless/assets/dist/chargeback_serverless-0.3.15.zip).
- **`curl`**, **`jq`**, and **`python3` (`3.9+`) with `PyYAML`**:
  - On macOS with Homebrew Python (PEP 668 `externally-managed-environment`), create a virtual environment before running `deploy_chargeback_serverless.sh`:
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    pip install pyyaml
    ```

### For Developers (Rebuilding `.zip` Packages & Running Local Stack E2E Tests)
Required **only** when editing integration package templates and building new `.zip` archives (`--build`) or running `run_e2e_tests.sh`:
- **`elastic/integrations` repo** (sibling of `elasticsearch-chargeback`, or set `INTEGRATIONS_REPO`)
- **`elastic-package`** (`go install github.com/elastic/elastic-package@latest`)
- **Docker** (4GB+ memory) for local stack E2E tests (`run_e2e_tests.sh`)

---

## Chargeback Serverless (`0.3.15` — User Deployment & Backfill)

For deploying and backfilling the **Chargeback Serverless hub integration** ([`chargeback_serverless-0.3.15.zip`](../integrations/chargeback-serverless/assets/dist/chargeback_serverless-0.3.15.zip)):

```bash
# 1. Activate a Python venv with PyYAML (required by deploy_chargeback_serverless.sh)
python3 -m venv .venv
source .venv/bin/activate
pip install pyyaml

# 2. Deploy prebuilt dist/chargeback_serverless-0.3.15.zip, create dedicated Fleet policy,
#    provision lookup-mode indices & 7 transforms, and run the Python backfill collector:
./scripts/deploy_chargeback_serverless.sh

# Or if you already uploaded chargeback_serverless-0.3.15.zip in the Kibana UI,
# run the standalone Python collector directly (never builds a zip):
source scripts/keychain.sh
python3 scripts/run_chargeback_serverless_collector.py

# Upload package without creating/updating Fleet package policy
./scripts/deploy_chargeback_serverless.sh --skip-policy

# Verification and health check only (prints data stream & lookup counts + ES|QL checks)
./scripts/deploy_chargeback_serverless.sh --verify-only

# DEVELOPER ONLY: Rebuild package zip from elastic/integrations before deploying
./scripts/deploy_chargeback_serverless.sh --build
```

Modular components:
- [`keychain.sh`](keychain.sh): macOS Keychain credential manager. Securely stores, loads, lists, and manages environment variables in Keychain (`elasticsearch-chargeback` service) instead of a plaintext `.env`.
- [`deploy_chargeback_serverless.sh`](deploy_chargeback_serverless.sh): Installs the prebuilt `chargeback_serverless-0.3.15.zip` (unless `--build` is passed), installs persistent `mode: lookup` index templates and starts all 7 Fleet transforms, creates/updates a dedicated `'Chargeback Serverless Policy'` in Fleet, runs `run_chargeback_serverless_collector.py`, and verifies all lookup indices and dashboards.
- [`run_chargeback_serverless_collector.py`](run_chargeback_serverless_collector.py): Standalone Python collector (stdlib only) that backfills up to 60 days of Serverless billing and per-project data stream, AI model, and Synthetics telemetry and refreshes the 7 `serverless_*_lookup` indices.
- [`configure_chargeback_serverless.sh`](configure_chargeback_serverless.sh): Updates the Fleet package policy settings (Cloud Org ID, unified Cloud API key, optional overrides, currency rates) on a dedicated `'Chargeback Serverless Policy'` via Kibana Fleet API with zero plaintext secret indices.
- [`install_chargeback_serverless.sh`](install_chargeback_serverless.sh): Legacy installer (deprecated in favor of native Fleet EPM and `deploy_chargeback_serverless.sh`).

### Managing Credentials with macOS Keychain

Instead of storing credentials in a plaintext `.env` file, store all required credentials in the macOS Keychain:

```bash
# Store required credentials for Chargeback Serverless (prompts securely when value is omitted)
./scripts/keychain.sh set ORGANIZATION_ID 2001809318
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
# Install the committed zip under integrations/chargeback/assets/dist/<version>/ instead of building packages/chargeback:
./scripts/run_e2e_tests.sh --from-zip
# Or: CHARGEBACK_INSTALL_FROM=zip CHARGEBACK_VERSION=0.5.0 ./scripts/run_e2e_tests.sh
```

**What it does:** Installs **Elasticsearch** integration → **On-Prem Billing** → **Chargeback**, in that order. **On-Prem Billing** is installed from the integrations repo when `packages/onprem_billing` exists (e.g. branch `wip/onprem-billing-integration`), otherwise from a zip on a chargeback repo branch. Post-install uses a **12 ERU test scenario**: three deployments (dev, prod, monitoring), two deployment groups (product, monitoring); organization config (12 ERU licence), deployment configs (dev 2 ERU, prod 8 ERU, monitoring 2 ERU), both enrich policies, `calculate_cost` pipeline, and billing transform. Then seeds **monitoring-indices** and **billing**, resets and starts Chargeback transforms, prints **evidence tables**, runs **step 12** (#99/#104 aliases), and for **0.5.0+** runs **step 13** (canonical usage source, tier-preference order, Serverless billing classification), **step 14** (Serverless usage collector add-on: pipeline, staging index, project registry, pipeline simulate), and **step 15** (Chargeback companion Agent Builder add-on; uses `--skip-workflows` when the Kibana Workflows API is absent on local stacks).

**Env:** `CHARGEBACK_VERSION` (default: integrations `manifest.yml`, else `0.5.0`), `CHARGEBACK_INSTALL_FROM=package|zip` (or `--from-zip` / `--from-package`).

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
