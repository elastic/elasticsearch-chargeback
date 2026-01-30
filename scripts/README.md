# Local Testing Scripts

Scripts for running Chargeback integration tests locally. **Use the automated E2E script** for full install and verification.

## Prerequisites

- **elastic-package** (e.g. `go install github.com/elastic/elastic-package` or use from the integrations repo: `go run github.com/elastic/elastic-package`)
- **integrations** repo (sibling of `elasticsearch-chargeback`, or set `INTEGRATIONS_REPO`)
- **Docker** (4GB+ memory). Stack: Elasticsearch 9.2+ (for ES|QL LOOKUP JOIN), Kibana, Package Registry

---

## Primary: Automated E2E script

From the **elasticsearch-chargeback** repo root, with the stack already running:

```bash
./scripts/run_e2e_tests.sh
```

**What it does:** Installs **Elasticsearch** integration → **On-Prem Billing** (from zip on `feature/onprem-billing-integration`, if present) → **Chargeback**, in that order. Uses **version-agnostic** logic: package versions are read from the integrations repo (Elasticsearch) and from the Fleet API (On-Prem uninstall). It disables legacy monitoring; starts the Elasticsearch `index_pivot` transform; optionally adds an Elasticsearch package policy to Fleet; seeds source data if needed (monitoring-indices, billing); resets and starts all Chargeback transforms; waits for transforms; then verifies document counts and prints **evidence tables** (per-index samples and a cross-verification table) so you can confirm data matches across all lookup indices.

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

**Env (optional):** `INTEGRATIONS_REPO`, `KIBANA_HOST`, `ES_HOST`, `ELASTIC_USER`, `ELASTIC_PASSWORD` (defaults: `https://127.0.0.1:5601`, `https://127.0.0.1:9200`, `elastic`, `changeme`).

---

## Cleanup script

Run before a fresh E2E run or to reset the stack:

```bash
./scripts/cleanup_testing_env.sh
```

Uninstalls Chargeback, On-Prem Billing, and Elasticsearch; removes the Fleet package policy `elasticsearch-monitoring`; deletes the testing indices (lookup indices, monitoring-indices, onprem_billing_config); removes the On-Prem enrich policy and `calculate_cost` pipeline; re-enables legacy monitoring. **On-Prem package version** is discovered from the Fleet API (no hardcoded version).

---

## Why are all lookup indices empty?

Chargeback transforms **read from other indices**. If those sources are empty, the lookups stay at 0 docs:

- **monitoring-indices** ← filled by the Elasticsearch **index_pivot** transform (which needs metrics from the Fleet agent / Elasticsearch integration).
- **cluster_*_contribution_lookup** ← Chargeback transforms read from **monitoring-indices**.
- **chargeback_conf_lookup** ← bootstrap transform reads from **cluster_deployment_contribution_lookup**.
- **billing_cluster_cost_lookup** ← reads from **metrics-ess_billing.billing-\*** (On-Prem or ESS Billing).

The E2E script seeds `monitoring-indices` (if empty) and **metrics-ess_billing.billing-onprem** (one billing doc), then resets and starts all Chargeback transforms so every lookup index is populated.

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
3. **Install Chargeback** (e.g. **Integrations → Create new integration → Upload it as a .zip**). Use the built zip from the integrations repo (`cd packages/chargeback && elastic-package build`) or a zip from this repo’s `integration/assets/<version>/chargeback-<version>.zip` (use the version you built or the one under `integration/assets/`).

Chargeback will create `chargeback_conf_lookup` and start its transforms. Verify in **Stack Management → Transforms** (filter `chargeback`).

---

## Verification

- **Transforms:** `billing_cluster_cost`, `chargeback_conf_lookup`, `cluster_deployment_contribution`, `cluster_datastream_contribution`, `cluster_tier_contribution`, `cluster_tier_and_ds_contribution`.
- **Lookup indices:** `billing_cluster_cost_lookup`, `chargeback_conf_lookup`, `cluster_*_contribution_lookup`.
- **Dashboard:** **Dashboards** → `[Chargeback] Cost and Consumption breakdown`. Newer Chargeback versions use TO_DOUBLE() for chargeable units (avoids integer-division errors).

---

## Troubleshooting

- **Stack:** First start 5–10 min. Unhealthy → `docker ps` / `docker logs <container>`; try `elastic-package stack down` then `stack up`. Package not in Kibana → check registry URL in Fleet settings.
- **Transforms:** Stack 9.2+ required. No data → ensure `logs-elasticsearch.index_pivot-default-*` is running and source integrations have data (`metrics-ess_billing.billing-*`, monitoring indices).
- **Dashboard:** Use a Chargeback version that supports TO_DOUBLE() / chargeable units if you see integer-division issues. Missing `chargeback_conf_lookup` → bootstrap transform needs `cluster_deployment_contribution_lookup` (Elasticsearch transform running first).
