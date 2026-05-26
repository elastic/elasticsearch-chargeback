# Chargeback troubleshooting

This guide helps when the **[Chargeback] Cost and Consumption breakdown** dashboard is empty or incomplete, even though transforms appear to be running. It applies to the **Chargeback integration** installed from this repository (current release asset: **`chargeback-0.3.2.zip`** in [`integration/assets/0.3.2/`](../assets/0.3.2/)).

For prerequisites, installation, and configuration, see [integration README](../README.md) and [Instructions.md](../Instructions.md).

## Before you start

Confirm these three facts (most empty-dashboard cases fail one of them):

1. **Correct cluster** — Chargeback is installed on the **central monitoring cluster** that ingests **both** ESS Billing and Elasticsearch usage/monitoring data for the deployments you want to charge back.
2. **Stack version** — Elasticsearch on that cluster is **9.2.0 or newer** (required for conditional [ES|QL LOOKUP JOIN](https://www.elastic.co/docs/reference/query-languages/esql/esql-lookup-join) in transforms and dashboard queries).
3. **Upstream data** — Billing and usage **source** indices have documents **before** debugging Chargeback lookup indices.

## Data flow (what must work)

```text
ESS Billing integration
  → metrics-ess_billing.billing-*
  → billing_cluster_cost transform
  → billing_cluster_cost_lookup

Elasticsearch integration + index_pivot transform (must be started manually)
  → monitoring-indices*  (default destination: monitoring-indices)
  → cluster_*_contribution transforms
  → cluster_*_contribution_lookup indices

chargeback_conf_lookup (config; date range for cost conversion)
  → joined in dashboard ES|QL

Dashboard ES|QL
  → LOOKUP JOIN on billing + config (+ usage lookups for blended panels)
```

Chargeback does **not** collect billing or usage by itself. If upstream integrations or `index_pivot` are missing, Chargeback transforms may run but produce empty or non-joinable lookup indices.

## Required versions

| Component | Minimum (documented) | Notes |
|-----------|----------------------|--------|
| Monitoring cluster (Elasticsearch) | **9.2.0+** | See [Instructions.md](../Instructions.md) |
| Chargeback integration | **0.3.2** | ZIP in `integration/assets/0.3.2/` |
| Elasticsearch Service Billing | **1.4.1+** | **1.7.0+** if using `chargeback_group` deployment tags |
| Elasticsearch integration | **1.16.0+** | Usage / stack monitoring collection |
| `logs-elasticsearch.index_pivot-default-{VERSION}` | Must be **started** | Not started by default in the Elasticsearch integration |

### Package upgrade notes

| Installed version | Risk |
|-------------------|------|
| **< 0.2.8** | Chargeback transforms may not auto-start (`start: true` added in 0.2.8). |
| **< 0.2.10** | `chargeback_conf_lookup` may be missing unless created manually. |
| **0.3.0 → 0.3.1** | Output fields renamed to `conf_chargeable_unit_rate`, `total_chargeable_units`; see [Unknown column errors (0.3.1)](#unknown-column-total_ecu-or-conf_ecu_rate-031). |
| **0.3.1 → 0.3.2** | Fixes unknown-column dashboard errors by dual-writing legacy ECU field names on lookups; update lookup index mappings or reset transforms (restart alone is not enough). |
| **< 0.3.2** | Usage transforms read index `monitoring-indices` only; **0.3.2+** uses `monitoring-indices*` (and `*:monitoring-indices*` for CCS). Custom `index_pivot` destinations must match that pattern. |

## Step 1 — Verify upstream source data

### ESS Billing

```json
GET metrics-ess_billing.billing-*/_count
```

```json
GET metrics-ess_billing.billing-*/_search
{
  "size": 1,
  "sort": [{ "@timestamp": "desc" }],
  "_source": ["@timestamp", "ess.billing.deployment_id", "ess.billing.deployment_name", "ess.billing.total_ecu"]
}
```

- Expect a non-zero count and recent `@timestamp`.
- The `billing_cluster_cost` transform only includes documents where **`ess.billing.total_ecu` > 0**.

Also open the **[Metrics ESS Billing] Billing** dashboard. If it is empty, fix ESS Billing Fleet policies and API access first.

### Elasticsearch usage (`index_pivot`)

```json
GET monitoring-indices*/_count
```

In **Stack Management → Transforms**, find:

`logs-elasticsearch.index_pivot-default-{VERSION}`

- State must be **`started`** (the Elasticsearch integration installs this transform with **`start: false`**).
- Destination index is **`monitoring-indices`** by default.

Open **[Elasticsearch] Indices & data streams usage**. If it is empty, fix Elasticsearch integration monitoring collection before Chargeback.

## Step 2 — Verify Chargeback transforms

In **Stack Management → Transforms**, filter by **`chargeback`**. The integration installs **six** transforms (from **0.2.8** onward they are configured to auto-start on install):

| Transform (asset name) | Destination lookup index | Schedule |
|------------------------|--------------------------|----------|
| `chargeback_conf_lookup` | `chargeback_conf_lookup` | No periodic `frequency`; runs when started / on package update |
| `billing_cluster_cost` | `billing_cluster_cost_lookup` | `frequency: 60m`, sync delay **1h** on `event.ingested` |
| `cluster_deployment_contribution` | `cluster_deployment_contribution_lookup` | `frequency: 60m`, sync delay **1h** on `@timestamp` |
| `cluster_datastream_contribution` | `cluster_datastream_contribution_lookup` | `frequency: 60m`, sync delay **1h** on `@timestamp` |
| `cluster_tier_contribution` | `cluster_tier_contribution_lookup` | `frequency: 60m`, sync delay **1h** on `@timestamp` |
| `cluster_tier_and_ds_contribution` | `cluster_tier_and_ds_contribution_lookup` | `frequency: 60m`, sync delay **1h** on `@timestamp` |

Fleet transform IDs typically look like:

`logs-chargeback.<transform_name>-default-<fleet_transform_version>`

Example: `logs-chargeback.billing_cluster_cost-default-0.3.2`

Check each transform:

- **State**: `started`
- **Reason** (if failed): read the failure message in the UI or `GET _transform/<transform_id>/_stats`

The **[Chargeback] Transform health monitoring** alert template watches **five** transforms (`billing_cluster_cost` and the four `cluster_*` contribution transforms). It does **not** include `chargeback_conf_lookup`; check that index separately (Step 3).

If transforms are stopped after upgrade, start them manually.

### First-run timing

After install or a full reprocess, lookup indices may stay empty for **one to two hours** because:

- `billing_cluster_cost` runs every **60 minutes** with a **1 hour** ingest delay.
- Contribution transforms use a **1 hour** sync delay on `@timestamp`.

To trigger an immediate run during testing (replace `<transform_id>`):

```json
POST _transform/<transform_id>/_start
POST _transform/<transform_id>/_schedule_now
```

## Step 3 — Verify lookup indices

```json
GET billing_cluster_cost_lookup/_count
GET cluster_deployment_contribution_lookup/_count
GET cluster_datastream_contribution_lookup/_count
GET chargeback_conf_lookup/_search
```

| Lookup index | If count is 0 |
|--------------|----------------|
| `billing_cluster_cost_lookup` | No billing data, all `total_ecu` <= 0, transform stopped/failed, or still within sync/frequency window |
| `cluster_*_contribution_lookup` | No `monitoring-indices*` data, `index_pivot` not started, or transform error |
| `chargeback_conf_lookup` | Bootstrap transform not run yet, no billing docs in source, or transform failed |

Sample billing document fields (0.3.1+): `@timestamp`, `deployment_id`, `deployment_name`, `total_chargeable_units`, `composite_key`.

## Step 4 — Configuration date range (`chargeback_conf_lookup`)

Dashboard panels join config with:

```esql
LOOKUP JOIN chargeback_conf_lookup ON @timestamp >= conf_start_date AND @timestamp <= conf_end_date
```

Inspect config:

```json
GET chargeback_conf_lookup/_search
```

**Package bootstrap (0.2.10+)** sets wide defaults: `conf_start_date` ≈ 2010-01-01, `conf_end_date` ≈ 2046-12-31.

If config was created manually from older examples (e.g. only **2024**), billing timestamps in **2025/2026** will **not** match any config row. Panels that depend on the config join will look empty even when `billing_cluster_cost_lookup` has data.

Extend the range (0.3.1+ field names); see [Instructions.md — Configuration](../Instructions.md#configuration):

```json
POST chargeback_conf_lookup/_update/config
{
  "doc": {
    "conf_start_date": "2025-01-01T00:00:00.000Z",
    "conf_end_date": "2026-12-31T23:59:59.999Z",
    "conf_chargeable_unit_rate": 0.85,
    "conf_chargeable_unit_rate_unit": "EUR"
  }
}
```

On **0.3.0** or older lookup documents, use `conf_ecu_rate` / `conf_ecu_rate_unit` instead.

## Step 5 — Reproduce dashboard queries (ES|QL)

Run in **Discover → ES|QL** (or Dev Tools where ES|QL is enabled).

**Billing + config (matches most cost panels):**

```esql
FROM billing_cluster_cost_lookup
| LOOKUP JOIN chargeback_conf_lookup ON @timestamp >= conf_start_date AND @timestamp <= conf_end_date
| STATS rows = COUNT(*) BY deployment_name
| SORT rows DESC
| LIMIT 20
```

- **No rows** → fix Steps 1–4 (data, transforms, config dates).
- **Rows here but empty dashboard** → Kibana time range, dashboard filters (deployment / deployment group), or package/dashboard version mismatch.

**Billing + usage correlation (blended / tier / data stream panels):**

```esql
FROM billing_cluster_cost_lookup
| LOOKUP JOIN cluster_deployment_contribution_lookup ON composite_key
| STATS rows = COUNT(*) BY composite_key
| LIMIT 20
```

`composite_key` is built as `<date>_<deployment_id>` in the Chargeback package ingest pipelines (billing and usage).

- **Billing lookup has rows; this join returns none** → `deployment_id` on billing docs (`ess.billing.deployment_id`) does not match usage `deployment_id` (derived from `cluster_name` / `elasticsearch.cluster.name`). Align monitoring cluster naming with ESS deployment IDs, or expect billing-only panels to work and blended panels to stay empty.

## Step 6 — Dashboard-specific checks

- Dashboard name: **[Chargeback] Cost and Consumption breakdown**
- Time picker must include dates present in `billing_cluster_cost_lookup` (`@timestamp` is daily, midnight UTC).
- Clear **Deployment name** and **Deployment group** controls (or set explicitly).
- Panels with **`Unknown column [total_ecu]`** / **`[conf_ecu_rate]`** → see [Unknown column errors (0.3.1)](#unknown-column-total_ecu-or-conf_ecu_rate-031).

## Unknown column `total_ecu` or `conf_ecu_rate` (0.3.1)

### Symptom

Panels (often under **Indexing details**) show `verification_exception` with `Unknown column [total_ecu]` and/or `Unknown column [conf_ecu_rate]`.

The bundled dashboard is **Fleet/Kibana managed**—you cannot fix this by editing individual Lens panels in the UI.

### Cause

**0.3.1** lookup indices expose `total_chargeable_units` and `conf_chargeable_unit_rate`. Bundled dashboard ES|QL still references `total_ecu` and `conf_ecu_rate` inside `COALESCE`. ES|QL requires **both** names to exist in the index mapping, even inside `COALESCE`.

### Fix: upgrade to 0.3.2

**0.3.2** dual-writes legacy ECU field names in transforms, mappings, and ingest pipelines so bundled dashboard `COALESCE` queries validate. After upgrade:

1. Install **chargeback 0.3.2** (from this repo’s `integration/assets/0.3.2/chargeback-0.3.2.zip` or the integrations package build).
2. **Update lookup mappings or recreate indices.** Restarting transforms does **not** add new fields to existing destination index mappings. For `billing_cluster_cost_lookup` and `chargeback_conf_lookup`, either add mappings for `total_ecu`, `conf_ecu_rate`, and `conf_ecu_rate_unit` (`PUT <index>/_mapping`, same types as 0.3.2 field definitions), or delete each lookup index and **reset** the corresponding transform so it is recreated with 0.3.2 mappings (reprocesses history; plan for load).
3. Start or schedule **`billing_cluster_cost`** and **`chargeback_conf_lookup`** (`POST _transform/<transform_id>/_schedule_now` during testing) so new documents receive dual-written fields.
4. If the dashboard was not replaced on upgrade, delete the Chargeback dashboard and `chargeback_integration` data view saved objects, then reinstall so Kibana re-imports the managed dashboard.

There is no supported workaround on **0.3.1** other than upgrading the package.

## Symptom → likely cause

| Symptom | Likely cause |
|---------|----------------|
| All panels empty; Chargeback transforms “started” | `logs-elasticsearch.index_pivot-default-*` not started, or `monitoring-indices*` empty |
| ESS Billing dashboard has data; Chargeback empty | `billing_cluster_cost` not finished first run (60m + 1h delay), transform failed, or `total_ecu` not > 0 |
| `billing_cluster_cost_lookup` has docs; cost panels empty | `chargeback_conf_lookup` date range does not cover billing `@timestamp` |
| Cost panels OK; tier / data stream / blended panels empty | `cluster_*_contribution_lookup` empty or `composite_key` mismatch between billing and usage |
| Deployment group filter always empty | ESS Billing **< 1.7.0** or **Add deployment tags** disabled on the billing Fleet policy |
| `Unknown column [total_ecu]` / `[conf_ecu_rate]` on managed dashboard | **0.3.1** mapping/query mismatch; upgrade to **0.3.2**, add legacy field mappings or reset billing/config transforms |
| Worked on legacy “module”, fails on integration | Module targeted **8.17.1+**; integration requires **9.2.0+** — different install path and stack requirement |

## Related dashboards and alerts

| Asset | Purpose |
|-------|---------|
| [Metrics ESS Billing] Billing | Confirm billing ingestion |
| [Elasticsearch] Indices & data streams usage | Confirm `index_pivot` / usage path |
| [Chargeback] Transform health monitoring | Alert on failed/stopped Chargeback transforms (five transforms; see Step 2) |
| [Chargeback] Deployment with Chargeback Group Missing Usage Data | Deployments tagged but no usage in lookups |

Install alert templates from the Chargeback integration page (requires Kibana **9.2.0+**). For group-related rules, Chargeback transforms must already be populating lookup indices.

## Information to collect for support

Chargeback is supported by **Field** (Customer Engineering), not standard Elastic Support while in technical preview. See [repository README — Support](../../README.md#support).

When contacting Field, include:

1. Chargeback package version (integration UI or transform `_meta.fleet_transform_version`)
2. Elasticsearch and Kibana versions (`GET /`)
3. Output of `_count` for `metrics-ess_billing.billing-*`, `monitoring-indices*`, and the lookup indices in Step 3
4. State of `logs-elasticsearch.index_pivot-default-*` and all `logs-chargeback.*` transforms
5. One sample document from `billing_cluster_cost_lookup` and `GET chargeback_conf_lookup/_search`
6. Result of the ES|QL query in Step 5 (row count or error message)

## Legacy Chargeback module

The older **Chargeback module** in this repository ([`module/`](../module/README.md), v0.2.0, Stack **8.17.1+**) is **not** maintained. Use the **integration** on **9.2.0+** for current fixes and documentation. Module installs include a **[Tech Preview] Chargeback - Meta Data** dashboard useful for date-range debugging on module deployments only.
