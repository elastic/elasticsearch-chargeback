# Chargeback Serverless configuration reference

Use this reference to configure the **Chargeback Serverless (`chargeback_serverless` `0.4.0`)** Fleet integration policy, create the required Elastic Cloud API key, customize currency conversion settings, and tune FinOps alerting thresholds.

## Single unified Elastic Cloud API key

Chargeback Serverless requires **one** Elastic Cloud API key (`cloud_api_key`) inside the Fleet integration policy to collect organization billing records (including deleted Serverless projects), discover active Serverless projects, and harvest per-project usage telemetry.

### Create the API key in Elastic Cloud Console

1. Sign in to the [Elastic Cloud Console API keys page](https://cloud.elastic.co/deployment-features/keys) (**Organization → API keys**) and click **Create API key**.
2. Enter a descriptive name (for example, `chargeback-serverless-collector`) and select an expiration period.
3. Configure both organization and project-level access:
   - **Organization role**: Select **`Billing Admin`**. This grants access to organization invoices, hourly SKU costs, and `GET /api/v2/billing/organizations/{org_id}/costs/instances` so the collector captures costs from both active and deleted Serverless projects.
   - **Enable access to Cloud resources**: Toggle **ON**, select **All projects** (or the specific Serverless projects you want to monitor), and in the separate per-project-type **Roles / API Access** dropdowns configure **`Cloud, Elasticsearch, and Kibana API`** (`Admin`) across **`Elasticsearch`**, **`Observability`**, and **`Security`** so the same key can list Serverless projects and read `GET /_cat/indices`, `_ml/trained_models/_stats`, `_inference`, and `.ds-synthetics-*` across projects.
4. Click **Create API key** and copy the key value.

### Hub project API keys for optional backfill & developer CLI scripts

For **Customer Fleet-Only Installations**, you only configure `cloud_api_key` in the Kibana Fleet policy UI.

When running the optional historical backfill collector ([`scripts/run_chargeback_serverless_collector.py`](../../scripts/run_chargeback_serverless_collector.py)) or the developer/maintainer automated deployment script ([`scripts/deploy_chargeback_serverless.sh`](../../scripts/deploy_chargeback_serverless.sh)) from the CLI, the scripts also authenticate directly to your Serverless FinOps hub project's Elasticsearch (`ES_URL`) and Kibana (`KIBANA_URL`) endpoints using `ES_API_KEY` and `KIBANA_API_KEY` (or `HUB_ES_API_KEY`):
- In Kibana on your Serverless hub project, navigate to **Management → Security → API keys → Create API key** and copy the encoded Base64 API key.
- Export it in your shell or `.env` file (cross-platform on Linux, Windows/WSL, and macOS):
  ```bash
  export ORGANIZATION_ID="<YOUR_ORGANIZATION_ID>"
  export CLOUD_API_KEY="<YOUR_UNIFIED_CLOUD_API_KEY>"
  export ES_URL="https://<your-hub-project>.es.<region>.<csp>.elastic.cloud"
  export KIBANA_URL="https://<your-hub-project>.kb.<region>.<csp>.elastic.cloud"
  export HUB_ES_API_KEY="<YOUR_HUB_PROJECT_API_KEY>"
  export ES_API_KEY="$HUB_ES_API_KEY"
  export KIBANA_API_KEY="$HUB_ES_API_KEY"
  ```
- *(Optional on macOS)* Local developers on macOS can alternatively store these variables in the macOS Keychain via `./scripts/keychain.sh set <VAR_NAME>`.

---

## Fleet integration policy parameters

Configure these user-facing fields in Kibana under **Management → Integrations → Chargeback Serverless → Configure integration** (or automate them with [`scripts/configure_chargeback_serverless.sh`](../../scripts/configure_chargeback_serverless.sh)):

| Policy parameter | Variable name | Required | Default | Description |
| --- | --- | --- | --- | --- |
| **Organization ID** | `organization_id` | Yes | — | Your numeric Elastic Cloud organization identifier (`<YOUR_ORGANIZATION_ID>`, found in the [Elastic Cloud Console](https://cloud.elastic.co) URL or **Organization → Settings**). |
| **Elastic Cloud API Key** | `cloud_api_key` | Yes | — | Unified Elastic Cloud API key (`secret: true`) with **Billing Admin** and **Cloud resource access** (`Cloud, Elasticsearch, and Kibana API`). |
| **Billing Lookbehind Days** | `lookbehind` | Yes | `60` | Number of historical UTC days of billing data to backfill on initial install. |
| **Currency Unit** | `currency_unit` | No | `USD` | Target display currency unit code across dashboards and alerts (for example, `USD`, `EUR`, `ZAR`, `GBP`, `ECU`). |
| **Currency Symbol** | `currency_symbol` | No | `$` | Target display currency symbol on KPI scorecards and tables (for example, `$`, `€`, `R`, `£`, `ECU`). |
| **Chargeable Unit Rate** | `unit_rate` | No | `1.0` | Multiplier from `1.0` chargeable unit (ECU) to your target contract currency (for example, `1.0`, `0.85`, `18.5`). |

> [!NOTE]
> - **Collection schedule & daily cursor gating**: The Elastic Agent CEL `billing` and `usage` streams poll hourly (`interval: 1h`) and use daily UTC cursor gating to ingest completed calendar days once per day after `02:00 UTC`.
> - **Advanced hidden variables (`show_user: false` in `manifest.yml`)**:
>   - `billing_api_base` (default `https://billing.elastic-cloud.com`) and `cloud_api_base` (default `https://api.elastic-cloud.com`) define the Elastic Cloud Billing and Serverless Control Plane API base URLs.
>   - `hub_es_api_key` and `project_api_keys` (`<project_id>:<encoded_api_key>` comma-separated string) are optional credential overrides; they remain hidden by default because a single unified `cloud_api_key` handles both organization billing and per-project usage harvesting across `Elasticsearch`, `Observability`, and `Security` projects.
> - **Serverless-only billing scope**: Both the Elastic Agent CEL streams and `run_chargeback_serverless_collector.py` automatically filter out Elastic Cloud Hosted (ECH) deployment SKUs (`Cloud-Enterprise_*`, `.es.datahot`, `.es.kibana`, etc.) so Hosted deployments do not inflate unallocatable `other` spend on Serverless dashboards. For Elastic Cloud Hosted clusters, use the [Hosted Chargeback integration](../chargeback/README.md) (or set `INCLUDE_HOSTED_BILLING=1` when running `run_chargeback_serverless_collector.py` if you intentionally want Hosted line items included).

---

## Currency conversion configuration

In Chargeback Serverless `0.4.0`, currency conversion rates and symbols are managed directly through Fleet policy variables (`unit_rate`, `currency_unit`, `currency_symbol`) without requiring manual Elasticsearch lookup index seeding.

- **Dynamic stamping**: The Elastic Agent CEL streams stamp `conf_chargeable_unit_rate`, `conf_chargeable_unit_rate_unit`, and `conf_currency_symbol` directly onto every ingested billing and usage record.
- **Continuous aggregation**: Fleet-managed continuous transforms propagate these values into all `serverless_*_lookup` indices (`max` / `terms` aggregation).
- **ES|QL evaluation**: Dashboards and alerting rules read `conf_chargeable_unit_rate` and `conf_currency_symbol` directly from the lookup indices (`LOOKUP JOIN serverless_project_status_lookup ON composite_key`) to evaluate monetary spend (`cost = units * conf_chargeable_unit_rate`).

To update currency rates or change your contract currency, edit the Fleet integration policy settings in Kibana (**Management → Integrations → Installed → Chargeback Serverless → Policies**). The next collection pass automatically applies the updated currency settings.

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

Each of the 10 prebuilt ES|QL alerting rule templates in [`assets/kibana/alerting_rule_template/`](assets/kibana/alerting_rule_template/) (**Stack Management → Rules → Create rule**, search `[Chargeback Serverless]`) defines its threshold and optional entity filter on **line 2** of the ES|QL query using an `EVAL` statement so you can adjust thresholds without rewriting the query logic:

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
