# Chargeback Serverless configuration reference

Use this reference to configure the **Chargeback Serverless (`chargeback_serverless` `0.3.15`)** Fleet integration policy, create the required Elastic Cloud API key, manage currency conversion rates in `serverless_chargeback_conf_lookup`, and tune FinOps alerting thresholds.

## Single unified Elastic Cloud API key

Chargeback Serverless requires **one** Elastic Cloud API key (`cloud_api_key`) to collect organization billing records (including deleted projects and deployments), discover active Serverless projects, and harvest per-project usage telemetry.

### Create the API key in Elastic Cloud Console

1. Sign in to the [Elastic Cloud Console API keys page](https://cloud.elastic.co/deployment-features/keys) and click **Create API key**.
2. Enter a descriptive name (for example, `chargeback-serverless-collector`) and select an expiration period.
3. Under **Assign roles**, turn on both of the following toggles:
   - **Billing admin**: Grants access to organization invoices, hourly SKU costs, and `/api/v1/billing/organization/{organization_id}/history` so the collector captures costs from both active and deleted deployments and Serverless projects.
   - **Cloud resource access**: Select **All resources** (or the specific Serverless projects you want to monitor) and choose **`Cloud, Elasticsearch, and Kibana API`** so the same key can list Serverless projects and read `_resolve/index/*`, `_cat/indices`, `.inference`, and `.synthetics-monitors-*` across projects.
4. Click **Create API key** and copy the key value.

---

## Fleet integration policy parameters

Configure these fields in Kibana under **Management → Integrations → Chargeback Serverless → Configure integration** (or automate them with [`scripts/configure_chargeback_serverless.sh`](../../../scripts/configure_chargeback_serverless.sh)):

| Policy parameter | Variable name | Required | Default | Description |
| --- | --- | --- | --- | --- |
| **Elastic Cloud Organization ID** | `organization_id` | Yes | — | Your numeric Elastic Cloud organization identifier (found in the [Elastic Cloud Console](https://cloud.elastic.co) URL or **Organization → Settings**). |
| **Elastic Cloud API Key** | `cloud_api_key` | Yes | — | Unified Elastic Cloud API key (`secret: true`) with **Billing admin** and **Cloud resource access** (`Cloud, Elasticsearch, and Kibana API`). |
| **Elastic Cloud API Base URL** | `api_url` | No | `https://api.elastic-cloud.com` | Base URL for the Elastic Cloud Billing and Serverless Control Plane APIs. |
| **Collection Interval** | `collection_interval` | No | `24h` | Poll frequency for the `billing` and `usage` Custom Enterprise Language (CEL) streams. Completed calendar days up to yesterday (`D-1` UTC) are finalized after `02:00 UTC`. |
| **Initial Billing Lookbehind Days** | `initial_lookbehind_days` | No | `60` | Number of historical UTC days (`1`–`90`) to backfill on the first billing collection run. |

> [!NOTE]
> Advanced override fields (`hub_es_api_key` and `project_api_keys`) are hidden by default (`show_user: false` in `manifest.yml`) because `cloud_api_key` handles both organization billing and per-project usage harvesting when created with **Billing admin** and **Cloud resource access** (`Cloud, Elasticsearch, and Kibana API`).

---

## Currency conversion rates (`serverless_chargeback_conf_lookup`)

Dashboards and alerting rules join daily Elastic Consumption Unit (ECU) metrics with the `serverless_chargeback_conf_lookup` index (`index.mode: lookup`) to calculate monetary spend in your local currency across contract periods.

### Schema and default values

| Field | Type | Default value | Description |
| --- | --- | --- | --- |
| `conf_key` | `keyword` | `"1"` | Constant join key (`"1"`) matched by dashboard ES|QL `LOOKUP JOIN` queries. |
| `conf_chargeable_unit_rate` | `double` | `0.85` | Monetary value of `1.0` ECU in your contract currency. |
| `conf_chargeable_unit_rate_unit` | `keyword` | `"EUR"` | Three-letter currency code displayed across dashboards and alerts (for example, `"USD"`, `"EUR"`, `"GBP"`). |
| `conf_start_date` | `date` | `"2010-01-01T00:00:00.000Z"` | Start timestamp (inclusive) for this conversion rate window. |
| `conf_end_date` | `date` | `"2046-12-31T23:59:59.999Z"` | End timestamp (inclusive) for this conversion rate window. |

### Update the default currency rate in Dev Tools

Open **Management → Dev Tools** in Kibana and run:

```json
PUT serverless_chargeback_conf_lookup/_doc/config_default
{
  "conf_key": "1",
  "conf_chargeable_unit_rate": 1.00,
  "conf_chargeable_unit_rate_unit": "USD",
  "conf_start_date": "2010-01-01T00:00:00.000Z",
  "conf_end_date": "2046-12-31T23:59:59.999Z"
}
```

### Configure multiple contract rate windows

If your ECU contract price changes over time, insert separate documents with non-overlapping `conf_start_date` and `conf_end_date` ranges:

```json
PUT serverless_chargeback_conf_lookup/_doc/config_2025
{
  "conf_key": "1",
  "conf_chargeable_unit_rate": 0.85,
  "conf_chargeable_unit_rate_unit": "EUR",
  "conf_start_date": "2025-01-01T00:00:00.000Z",
  "conf_end_date": "2025-12-31T23:59:59.999Z"
}

PUT serverless_chargeback_conf_lookup/_doc/config_2026
{
  "conf_key": "1",
  "conf_chargeable_unit_rate": 0.92,
  "conf_chargeable_unit_rate_unit": "EUR",
  "conf_start_date": "2026-01-01T00:00:00.000Z",
  "conf_end_date": "2046-12-31T23:59:59.999Z"
}
```

---

## Data retention settings

Chargeback Serverless enforces a **365-day** retention policy across all raw data streams and lookup indices:

| Resource type | Pattern or index names | Retention setting |
| --- | --- | --- |
| **Raw metrics data streams** | `metrics-chargeback_serverless.billing-*`<br/>`metrics-chargeback_serverless.usage-*` | Data Stream Lifecycle (DLM) `data_retention: "365d"` |
| **Transform lookup indices** | `serverless_billing_cost_lookup`<br/>`serverless_billing_realized_pool_lookup`<br/>`serverless_project_contribution_lookup`<br/>`serverless_datastream_contribution_lookup`<br/>`serverless_ai_model_contribution_lookup`<br/>`serverless_synthetics_monitor_contribution_lookup`<br/>`serverless_project_status_lookup` | Transform `retention_policy.time.max_age: "365d"` on `@timestamp` |

To override the retention period when running the automated deployment script, set `DATA_RETENTION` (for example, `DATA_RETENTION=730d ./scripts/deploy_chargeback_serverless.sh`).

---

## FinOps alerting rule thresholds

Each of the 10 prebuilt ES|QL alerting rule templates in [`kibana/alerting_rule_template/`](kibana/alerting_rule_template/) (**Stack Management → Rules**) defines its threshold and optional entity filter on **line 2** of the ES|QL query using an `EVAL` statement so you can adjust thresholds without rewriting the query logic:

| Alert rule title | Line-2 `EVAL` parameters | Default values |
| --- | --- | --- |
| **`[Chargeback Serverless] Total daily spend threshold exceeded`** | `EVAL threshold_spend = 1000.0` | `1000.0` |
| **`[Chargeback Serverless] Rolling 30-day total spend budget exceeded`** | `EVAL threshold_budget = 25000.0` | `25000.0` |
| **`[Chargeback Serverless] Cost category daily spend threshold exceeded`** | `EVAL threshold_spend = 300.0, filter_category = "*"` | `300.0`, all categories |
| **`[Chargeback Serverless] Workload driver daily spend threshold exceeded`** | `EVAL threshold_spend = 250.0, filter_driver = "*"` | `250.0`, all drivers |
| **`[Chargeback Serverless] Project daily spend threshold exceeded`** | `EVAL threshold_spend = 200.0, filter_project = "*"` | `200.0`, all projects |
| **`[Chargeback Serverless] Project daily spend spike vs. 7-day baseline`** | `EVAL threshold_pct_increase = 50.0, min_daily_spend = 25.0, filter_project = "*"` | `+50.0%`, `25.0` min, all projects |
| **`[Chargeback Serverless] Namespace attributed daily spend threshold exceeded`** | `EVAL threshold_spend = 100.0, filter_namespace = "*"` | `100.0`, all namespaces |
| **`[Chargeback Serverless] AI model attributed daily spend threshold exceeded`** | `EVAL threshold_spend = 50.0, filter_model = "*"` | `50.0`, all models |
| **`[Chargeback Serverless] Synthetics monitor attributed daily spend threshold exceeded`** | `EVAL threshold_spend = 25.0, filter_monitor = "*"` | `25.0`, all monitors |
| **`[Chargeback Serverless] Data trust & project harvest health issue`** | `EVAL threshold_unallocated_spend = 100.0, threshold_unallocated_pct = 25.0` | `100.0` and `25.0%` |

## Related pages

- [Chargeback Serverless overview](README.md)
- [Install and configure Chargeback Serverless](Instructions.md)
- [Dashboard and alert design reference](dashboards/DESIGN.md)
