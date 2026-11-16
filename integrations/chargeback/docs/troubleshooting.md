# Chargeback troubleshooting

This guide helps when Chargeback dashboards are empty or incomplete, even though transforms appear to be running. It applies to the **Chargeback integration** installed from this repository (current release asset: **`chargeback-0.6.0.zip`** in [`integrations/chargeback/assets/dist/0.6.0/`](../assets/dist/0.6.0/)).

For prerequisites, installation, and configuration, see [integration README](../README.md) and [Instructions.md](../Instructions.md).

## Before you start

Confirm these three facts (most empty-dashboard cases fail one of them):

1. **Correct cluster** — Chargeback is installed on the **central monitoring cluster** (Hub) that either runs a **Chargeback** Elastic Agent policy (`0.6.0+`) or ingests ESS/On-Premises Billing and Elasticsearch usage/monitoring data for the deployments you want to charge back.
2. **Stack version** — Elasticsearch on that cluster is **9.2.0 or newer** (required for conditional [ES|QL LOOKUP JOIN](https://www.elastic.co/docs/reference/query-languages/esql/esql-lookup-join) in transforms and dashboard queries).
3. **Upstream data** — Billing and usage **source** indices (`metrics-chargeback.*` and/or `metrics-ess_billing.billing-*` + `monitoring-indices*`) have documents **before** debugging Chargeback lookup indices.

## Data flow (what must work)

```text
Chargeback CEL billing (metrics-chargeback.billing-*) and/or ESS / On-Premises Billing (metrics-ess_billing.billing-*)
  → billing_cluster_cost + billing_realized_pool + billing_credits transforms
  → chargeback_lookup.billing_cluster_cost + chargeback_lookup.billing_realized_pool + chargeback_lookup.billing_credits
    (backed by chargeback_lookup.dest_*-1)

Chargeback CEL usage (metrics-chargeback.usage-*) and/or Elasticsearch index_pivot (monitoring-indices*)
  → cluster_*_contribution transforms
  → chargeback_lookup.cluster_*_contribution aliases (backed by chargeback_lookup.dest_*-1)

Node stats (metrics-chargeback.node_stats-* / metrics-elasticsearch.stack_monitoring.node_stats-* / .monitoring-es-* / metricbeat-*)
  → cluster_capacity_utilization transform
  → cluster_capacity_utilization_lookup

chargeback_conf_lookup (Fleet config_snapshot sync or manual _id: config / config_onprem; COALESCE defaults when empty)
  → joined in dashboard ES|QL

Dashboard ES|QL
  → LOOKUP JOIN on chargeback_lookup.* + chargeback_conf_lookup (+ usage / utilization lookups for blended panels)
```

In **0.6.0+**, Chargeback collects Elastic Cloud Hosted and Serverless billing, usage, and node stats directly when attached to a Fleet Agent policy. On assets-only installations (or self-managed / On-Premises clusters), upstream producers (`On-Premises Billing`, `ESS Billing`, and `Elasticsearch` `index_pivot`) supply the source data.

## Required versions

| Component | Minimum (documented) | Notes |
|-----------|----------------------|--------|
| Monitoring cluster (Elasticsearch) | **9.2.0+** | See [Instructions.md](../Instructions.md) |
| Chargeback integration | **0.6.0** | ZIP in `integrations/chargeback/assets/dist/0.6.0/` |
| Elasticsearch Service Billing | **Optional** (`1.7.0+` if used) | Built-in `chargeback.billing` CEL stream in `0.6.0+`; On-Premises Billing required for self-managed `mERU` |
| Elasticsearch integration | **Optional** (`1.16.0+` if used) | Built-in `chargeback.usage` and `chargeback.node_stats` CEL streams in `0.6.0+` |
| `logs-elasticsearch.index_pivot-default-{VERSION}` | Started if using `monitoring-indices*` | Only needed when not running `chargeback.usage` CEL collection |

### Package upgrade notes

| Installed version | Risk |
|-------------------|------|
| **< 0.2.8** | Chargeback transforms may not auto-start (`start: true` added in 0.2.8). |
| **< 0.2.10** | `chargeback_conf_lookup` may be missing unless created manually. |
| **0.3.0 → 0.3.1** | Output fields renamed to `conf_chargeable_unit_rate`, `total_chargeable_units`; see [Unknown column errors (0.3.1)](#unknown-column-total_ecu-or-conf_ecu_rate-031). |
| **0.3.1 → 0.3.2** | Fixes unknown-column dashboard errors by dual-writing legacy ECU field names on lookups; update lookup index mappings or reset transforms (restart alone is not enough). |
| **< 0.3.2** | Usage transforms read index `monitoring-indices` only; **0.3.2+** uses `monitoring-indices*` (and `*:monitoring-indices*` for CCS). Custom `index_pivot` destinations must match that pattern. |
| **0.5.0 → 0.6.0** | Seven lookup destinations moved to versioned `chargeback_lookup.dest_*-1` indices behind `chargeback_lookup.*` aliases, `billing_credits` was added (9 transforms total), and `chargeback_conf_lookup` supports dual Cloud (`_id: config`) and On-Premises (`_id: config_onprem`) rates synced from Fleet `config_snapshot` events. |

## Step 1 — Verify upstream source data

### Built-in Chargeback CEL collection (`0.6.0+` with Fleet policy)

```json
GET metrics-chargeback.billing-*/_count
GET metrics-chargeback.usage-*/_count
GET metrics-chargeback.node_stats-*/_count
```

```json
GET metrics-chargeback.billing-*/_search
{
  "size": 1,
  "sort": [{ "@timestamp": "desc" }],
  "_source": ["@timestamp", "ess.billing.deployment_id", "ess.billing.deployment_name", "ess.billing.total_ecu", "event.dataset"]
}
```

- Expect non-zero counts and recent `@timestamp` when a Chargeback integration policy is attached to an Elastic Agent.
- `metrics-chargeback.billing-*` carries `config_snapshot`, `line_item`, and `credit_line_item` documents.

### ESS Billing / On-Premises Billing (assets-only or self-managed)

```json
GET metrics-ess_billing.billing-*/_count
```

```json
GET metrics-ess_billing.billing-*/_search
{
  "size": 1,
  "sort": [{ "@timestamp": "desc" }],
  "_source": ["@timestamp", "ess.billing.deployment_id", "ess.billing.deployment_name", "ess.billing.total_ecu", "ess.billing.chargeable_unit"]
}
```

- Expect a non-zero count and recent `@timestamp` when relying on ESS Billing or On-Premises Billing.
- The `billing_cluster_cost` transform only includes line-item documents where **`ess.billing.total_ecu` > 0** (or `total_chargeable_units` > 0).

### Elasticsearch usage (`index_pivot` — when not using `chargeback.usage`)

```json
GET monitoring-indices*/_count
```

In **Stack Management → Transforms**, find:

`logs-elasticsearch.index_pivot-default-{VERSION}`

- State must be **`started`** (the Elasticsearch integration installs this transform with **`start: false`**).
- Destination index is **`monitoring-indices`** by default.

Open **[Elasticsearch] Indices & data streams usage**. If it is empty and you are not running `chargeback.usage`, fix Elasticsearch integration monitoring collection before Chargeback.

## Step 2 — Verify Chargeback transforms

In **Stack Management → Transforms**, filter by **`chargeback`**. In **0.6.0+**, the integration installs **nine** transforms (configured to auto-start on install):

| Transform (asset name) | Destination lookup alias / index | Schedule |
|------------------------|----------------------------------|----------|
| `chargeback_conf_lookup` | `chargeback_conf_lookup` | `frequency: 5m`, sync delay **60s** on `cb_sync_ts` (`event.ingested`) |
| `billing_cluster_cost` | `chargeback_lookup.billing_cluster_cost` (`chargeback_lookup.dest_billing_cluster_cost-1`) | `frequency: 60m`, sync delay **1h** on `@timestamp` |
| `billing_realized_pool` | `chargeback_lookup.billing_realized_pool` (`chargeback_lookup.dest_billing_realized_pool-1`) | `frequency: 60m`, sync delay **1h** on `@timestamp` |
| `billing_credits` | `chargeback_lookup.billing_credits` (`chargeback_lookup.dest_billing_credits-1`) | `frequency: 60m`, sync delay **1h** on `@timestamp` |
| `cluster_capacity_utilization` | `cluster_capacity_utilization_lookup` | `frequency: 60m`, sync delay **1h** on `@timestamp` |
| `cluster_deployment_contribution` | `chargeback_lookup.cluster_deployment_contribution` (`chargeback_lookup.dest_cluster_deployment_contribution-1`) | `frequency: 60m`, sync delay **1h** on `@timestamp` |
| `cluster_datastream_contribution` | `chargeback_lookup.cluster_datastream_contribution` (`chargeback_lookup.dest_cluster_datastream_contribution-1`) | `frequency: 60m`, sync delay **1h** on `@timestamp` |
| `cluster_tier_contribution` | `chargeback_lookup.cluster_tier_contribution` (`chargeback_lookup.dest_cluster_tier_contribution-1`) | `frequency: 60m`, sync delay **1h** on `@timestamp` |
| `cluster_tier_and_ds_contribution` | `chargeback_lookup.cluster_tier_and_datastream_contribution` (`chargeback_lookup.dest_cluster_tier_and_datastream_contribution-1`) | `frequency: 60m`, sync delay **1h** on `@timestamp` |

Fleet transform IDs typically look like:

`logs-chargeback.<transform_name>-default-<fleet_transform_version>`

Example: `logs-chargeback.billing_cluster_cost-default-0.6.0`

Check each transform:

- **State**: `started`
- **Reason** (if failed): read the failure message in the UI or `GET _transform/<transform_id>/_stats`

In **0.6.0+**, the **[Chargeback] Transform health monitoring** alert template watches all **nine** Chargeback transforms.

If transforms are stopped after upgrade, start them manually.

### First-run timing

After install or a full reprocess, lookup indices may stay empty for **one to two hours** because:

- `billing_cluster_cost`, `billing_realized_pool`, and `billing_credits` run every **60 minutes** with a **1 hour** sync delay on `@timestamp`.
- Contribution and utilization transforms use a **1 hour** sync delay on `@timestamp`.

To trigger an immediate run during testing (replace `<transform_id>`):

```json
POST _transform/<transform_id>/_start
POST _transform/<transform_id>/_schedule_now
```

## Step 3 — Verify lookup indices

```json
GET chargeback_lookup.billing_cluster_cost/_count
GET chargeback_lookup.billing_realized_pool/_count
GET chargeback_lookup.billing_credits/_count
GET cluster_capacity_utilization_lookup/_count
GET chargeback_lookup.cluster_deployment_contribution/_count
GET chargeback_lookup.cluster_datastream_contribution/_count
GET chargeback_lookup.chargeback_conf/_search
```

| Lookup alias / index | If count is 0 |
|----------------------|----------------|
| `chargeback_lookup.billing_cluster_cost` / `chargeback_lookup.billing_realized_pool` | No billing data, all `total_ecu` <= 0, transform stopped/failed, or still within sync/frequency window |
| `chargeback_lookup.billing_credits` | Expected `0` when the organisation has no Cloud credit drawdowns (`credit_line_item`) |
| `cluster_capacity_utilization_lookup` | No `node_stats` data (or Serverless-only environment; utilization defaults to 100%) |
| `chargeback_lookup.cluster_*_contribution` | No `metrics-chargeback.usage-*` or `monitoring-indices*` data, `index_pivot` not started, or transform error |
| `chargeback_lookup.chargeback_conf` | Assets-only install without Fleet `config_snapshot` or manual config upsert; dashboards fall back to `COALESCE` defaults (`0.85` / `0.00085` `EUR`) |

Sample billing document fields (0.3.1+): `@timestamp`, `deployment_id`, `deployment_name`, `total_chargeable_units`, `composite_key`.

## Step 4 — Configuration date range and dual rates (`chargeback_lookup.chargeback_conf`)

In **0.6.0+**, `chargeback_lookup.chargeback_conf` (`chargeback_lookup.dest_chargeback_conf-1`) holds separate Cloud (`_id: config`, `config_join_key: "chargeback_config"`) and On-Premises (`_id: config_onprem`, `config_join_key: "chargeback_config_onprem"`) rate rows synced from Fleet `config_snapshot` events (and automatically carries over any pre-0.6.0 `chargeback_conf_lookup` custom row when no Fleet `config_snapshot` exists). Dashboard panels join config by date window and `config_join_key`, applying `COALESCE` defaults (`0.85` `EUR` per ECU and `0.00085` `EUR` per `mERU`) when `chargeback_lookup.chargeback_conf` is empty:

```esql
EVAL cloud_cfg_key = "chargeback_config"
| LOOKUP JOIN chargeback_lookup.chargeback_conf ON @timestamp >= conf_start_date AND @timestamp <= conf_end_date AND cloud_cfg_key == config_join_key
```

Inspect config:

```json
GET chargeback_lookup.chargeback_conf/_search
```

When using a Chargeback Fleet policy (`0.6.0+`), update the rate, currency, or contract dates directly in the Fleet integration policy settings. On assets-only installs (or to override manually), upsert `_id: config` (and optionally `_id: config_onprem`) with `doc_as_upsert: true` — see [Instructions.md — Configuration](../Instructions.md#configuration):

```json
POST chargeback_lookup.chargeback_conf/_update/config
{
  "doc": {
    "config_join_key": "chargeback_config",
    "conf_start_date": "2025-01-01T00:00:00.000Z",
    "conf_end_date": "2046-12-31T23:59:59.999Z",
    "conf_chargeable_unit_rate": 0.85,
    "conf_chargeable_unit_rate_unit": "EUR"
  },
  "doc_as_upsert": true
}
```

If manual config dates do not cover billing `@timestamp` (for example, `conf_end_date` set to `2024`), extend `conf_start_date` / `conf_end_date` so custom rates apply instead of falling back to defaults.

On **0.3.0** or older lookup documents, use `conf_ecu_rate` / `conf_ecu_rate_unit` instead.

## Step 5 — Reproduce dashboard queries (ES|QL)

Run in **Discover → ES|QL** (or Dev Tools where ES|QL is enabled).

**Billing + config (matches most cost panels):**

```esql
FROM chargeback_lookup.billing_cluster_cost
| EVAL cloud_cfg_key = "chargeback_config"
| LOOKUP JOIN chargeback_lookup.chargeback_conf ON @timestamp >= conf_start_date AND @timestamp <= conf_end_date AND cloud_cfg_key == config_join_key
| EVAL unit_rate = COALESCE(conf_chargeable_unit_rate, 0.85)
| STATS rows = COUNT(*), total_units = SUM(total_chargeable_units) BY deployment_name, unit_rate
| SORT rows DESC
| LIMIT 20
```

- **No rows** → fix Steps 1–3 (upstream source data and transforms).
- **Rows here but empty dashboard** → Kibana time range, dashboard filters (deployment / deployment group), or package/dashboard version mismatch.

**Billing + usage correlation (blended / tier / data stream panels):**

```esql
FROM chargeback_lookup.billing_realized_pool
| LOOKUP JOIN chargeback_lookup.cluster_deployment_contribution ON composite_key
| STATS rows = COUNT(*) BY composite_key
| LIMIT 20
```

`composite_key` is built as `<date>_<deployment_id>` in the Chargeback transforms and ingest pipelines (billing and usage).

- **Billing lookup has rows; this join returns none** → `deployment_id` on billing docs (`ess.billing.deployment_id`) does not match usage `deployment_id` (derived from `cluster_name` / `elasticsearch.cluster.name` or target endpoint). Align monitoring cluster naming with ESS deployment IDs, or expect billing-only panels to work and blended panels to stay empty.

## Step 6 — Dashboard-specific checks

- Dashboards (0.4.0+): **[Chargeback] Billing Components Overview**, **[Chargeback] Usage & Cost Allocation**, and **[Chargeback] Configuration** (replacing the pre-0.4.0 **[Chargeback] Cost and Consumption breakdown** dashboard)
- Time picker must include dates present in `chargeback_lookup.billing_cluster_cost` / `chargeback_lookup.billing_realized_pool` (`@timestamp` is daily, midnight UTC).
- Clear **Deployment name** and **Deployment group** controls (or set explicitly).
- Panels with **`Unknown column [total_ecu]`** / **`[conf_ecu_rate]`** → see [Unknown column errors (0.3.1)](#unknown-column-total_ecu-or-conf_ecu_rate-031). On **0.4.2**, upgrade to **0.4.3+** so Billing and Usage panels stop referencing those legacy names in ES|QL.

## Small differences versus the ESS Billing dashboard

Chargeback billing and usage transforms aggregate on a **calendar day** with a sync delay (typically about one hour). The ESS Billing dashboard can show near-real-time or partial-day totals for the current UTC day. Small differences for today or windows that cross midnight are expected. When reconciling, compare **completed UTC days** (or extend the billing time range across the day boundary).

## `chargeback_conf_lookup` empty or fails on pre-0.6.0 first install

In **0.6.0+**, `chargeback_conf_lookup` sources `metrics-chargeback.billing-*` (`config_snapshot` documents synced every 5 minutes) and dashboards apply `COALESCE` defaults when `chargeback_conf_lookup` is empty, so assets-only installs work without errors. On **0.5.0 and earlier**, the bootstrap transform sourced `metrics-ess_billing.billing-*` and could fail until a concrete billing backing index existed.

## Unknown column `total_ecu` or `conf_ecu_rate` (0.3.1)

### Symptom

Panels (often under **Indexing details**) show `verification_exception` with `Unknown column [total_ecu]` and/or `Unknown column [conf_ecu_rate]`.

The bundled dashboard is **Fleet/Kibana managed**—you cannot fix this by editing individual Lens panels in the UI.

### Cause

**0.3.1** lookup indices expose `total_chargeable_units` and `conf_chargeable_unit_rate`. Bundled dashboard ES|QL still references `total_ecu` and `conf_ecu_rate` inside `COALESCE`. ES|QL requires **both** names to exist in the index mapping, even inside `COALESCE`.

### Fix: upgrade to 0.3.2

**0.3.2** adds legacy ECU names as lookup **field aliases** so bundled dashboard `COALESCE` queries validate without duplicating values. After upgrade:

1. Install **chargeback 0.3.2** (from this repo’s `integrations/chargeback/assets/dist/0.3.2/chargeback-0.3.2.zip` or the integrations package build).
2. **Recreate lookup indices to pick up 0.3.2 mappings.** Restarting transforms does **not** change existing destination index mappings. Delete `billing_cluster_cost_lookup` and `chargeback_conf_lookup`, then **reset** the corresponding transforms so those indices are recreated with 0.3.2 alias mappings (`total_ecu` -> `total_chargeable_units`, `conf_ecu_rate` -> `conf_chargeable_unit_rate`, `conf_ecu_rate_unit` -> `conf_chargeable_unit_rate_unit`).
3. Start or schedule **`billing_cluster_cost`** and **`chargeback_conf_lookup`** (`POST _transform/<transform_id>/_schedule_now` during testing) so data is repopulated after the reset.
4. If the dashboard was not replaced on upgrade, delete the Chargeback dashboard and `chargeback_integration` data view saved objects, then reinstall so Kibana re-imports the managed dashboard.

There is no supported workaround on **0.3.1** other than upgrading the package.

## Symptom → likely cause

| Symptom | Likely cause |
|---------|----------------|
| All panels empty; Chargeback transforms “started” | Upstream billing/usage source indices empty, or `logs-elasticsearch.index_pivot-default-*` not started on assets-only installs |
| ESS Billing dashboard has data; Chargeback empty | `billing_cluster_cost` not finished first run (60m + 1h delay), transform failed, or `total_ecu` / `total_chargeable_units` not > 0 |
| ESS Billing total slightly differs from Chargeback for "today" | Expected: Chargeback uses **calendar-day** pivots with sync delay; compare **completed UTC days** (see above) |
| `chargeback_lookup.billing_cluster_cost` has docs; custom rate not applied | `chargeback_lookup.chargeback_conf` date range does not cover billing `@timestamp`, or `config_join_key` missing on manual upsert |
| Cost panels OK; tier / data stream / blended panels empty | `chargeback_lookup.cluster_*_contribution` empty or `composite_key` mismatch between billing and usage |
| Deployment group filter always empty | Deployment tags not configured on Elastic Cloud deployments, or ESS Billing **< 1.7.0** / **Add deployment tags** disabled |
| `Unknown column [total_ecu]` / `[conf_ecu_rate]` on managed dashboard | **0.3.1** mapping/query mismatch → upgrade to **0.3.2+** and recreate lookups; on **0.4.2** Billing/Usage panels → upgrade to **0.4.3+** (dashboard ES\|QL only) |
| Worked on legacy “module”, fails on integration | Module targeted **8.17.1+**; integration requires **9.2.0+** — different install path and stack requirement |

## Related dashboards and alerts

| Asset | Purpose |
|-------|---------|
| [Metrics ESS Billing] Billing | Confirm legacy ESS billing ingestion (when used) |
| [Elasticsearch] Indices & data streams usage | Confirm `index_pivot` / usage path (when used) |
| [Chargeback] Transform health monitoring | Alert on failed/stopped Chargeback transforms (all nine transforms in `0.6.0+`; see Step 2) |
| [Chargeback] Deployment with chargeback group missing usage data | Deployments tagged with a chargeback group that have no usage data in lookups |
| [Chargeback] New chargeback group detected | Alert when a new `deployment_group` appears in `chargeback_lookup.billing_cluster_cost` |

Install alert templates from the Chargeback integration page (requires Kibana **9.2.0+**). For group-related rules, Chargeback transforms must already be populating lookup indices.

## Information to collect for support

Chargeback is supported by **Field** (Customer Engineering), not standard Elastic Support while in technical preview. See [repository README — Support](../../README.md#support).

When contacting Field, include:

1. Chargeback package version (integration UI or transform `_meta.fleet_transform_version`)
2. Elasticsearch and Kibana versions (`GET /`)
3. Output of `_count` for `metrics-chargeback.*`, `metrics-ess_billing.billing-*`, `monitoring-indices*`, and the lookup aliases/indices in Step 3
4. State of `logs-elasticsearch.index_pivot-default-*` (if used) and all `logs-chargeback.*` transforms
5. One sample document from `chargeback_lookup.billing_cluster_cost` and `GET chargeback_lookup.chargeback_conf/_search`
6. Result of the ES|QL query in Step 5 (row count or error message)

## Legacy Chargeback module

The older **Chargeback module** in this repository ([`module/`](../module/README.md), v0.2.0, Stack **8.17.1+**) is **not** maintained. Use the **integration** on **9.2.0+** for current fixes and documentation. Module installs include a **[Tech Preview] Chargeback - Meta Data** dashboard useful for date-range debugging on module deployments only.
