# Chargeback Serverless Dashboards — Design (`0.3.7`)

**Platform:** Kibana custom panels (Liquid HTML + ES|QL) and Lens ES|QL visualizations on Elastic Cloud Serverless hubs (Observability Complete recommended).

**Architecture:** All credentials, remote project harvesting API keys, and currency settings (`unit_rate`, `currency_unit`, `currency_symbol`) are managed in the Fleet Integration Policy. Daily collection runs via native Elastic Agent CEL streams (`metrics-chargeback_serverless.billing-default` and `metrics-chargeback_serverless.usage-default`) and aggregates into 6 Fleet-managed lookup indices via continuous transforms.

## Dashboards

| Dashboard | ID | Purpose |
| --- | --- | --- |
| **[Chargeback Serverless] Billing Components Overview** | `chargeback_serverless-b6cecac0-ebe7-43de-bfc6-1ff287ad860a` | Organization spend KPIs, daily stacked cost by project and cost category (with embedded header legends), spend flow (`cost_category → cost_type → project_type`), unallocatable & pass-through charges, and invoice reconciliation detail |
| **[Chargeback Serverless] Usage & Cost Allocation** | `chargeback_serverless-21588d0e-fb6a-4f76-ad2f-cd7b3d7a3d7c` | Collapsible sections for (1) Workload drivers & project allocation and (2) Data stream cost attribution & usage (`ds_namespace`, `ds_type`, data stream tables) |
| **[Chargeback Serverless] AI & Machine Learning** | `chargeback_serverless-5fb3f4c6-5591-5c5c-9625-1d829944c5b2` | AI & ML FinOps KPIs (AI pool spend, inference volume, active models, avg latency, unit cost per 1k inferences), daily attributed AI cost by model, attributed AI cost by project, AI model compute & spend breakdown, and dedicated AI/Inference/Agent Builder billing line items |
| **[Chargeback Serverless] Synthetics** | `chargeback_serverless-c188156b-9f97-5e72-975f-022f3c862a1d` | Synthetics FinOps KPIs (direct Synthetics SKU spend, billed check quantity, ECU, unit cost per 1k checks), Synthetics data stream attribution KPIs (`synthetics-*` indexing, search, storage), daily Synthetics SKU cost by project, Synthetics data stream cost by namespace, SKU line items, and Synthetics data stream attribution table |
| **[Chargeback Serverless] Configuration** | `chargeback_serverless-7ec9743a-ccfa-43d2-8311-2f25893dadae` | Unified Fleet Configuration, Policy Settings & Serverless Project Registry panel (status banner, 5 KPIs, 3-step setup guide, and paginated project harvest registry) |

All five dashboards share a horizontal `links` navigation bar (`useCurrentDateRange: true`, `useCurrentFilters: false`) and default to `now-30d` to `now`.

## Lookup Indices & Data Sources

| Lookup Index | Source | Natural Key (`group_by`) | Purpose |
| --- | --- | --- | --- |
| `serverless_billing_cost_lookup` | `metrics-chargeback_serverless.billing-*` | `@timestamp` (1d), `project_id`, `sku` | Daily billed ECU and quantity per project and SKU, classified by `cost_category`, `cost_type`, `allocation_driver`, and `is_allocatable` |
| `serverless_billing_realized_pool_lookup` | `serverless_billing_cost_lookup` | `@timestamp` (1d), `project_id` | Daily allocatable ECU pools (`indexing_ecu`, `query_ecu`, `storage_ecu`, `ai_ecu`) per project with `composite_key` (`YYYY-MM-DD_project_id`) |
| `serverless_datastream_contribution_lookup` | `metrics-chargeback_serverless.usage-*` (`doc_type: datastream`) | `@timestamp` (1d), `project_id`, `datastream` | Daily indexing time, query time, and dataset size per data stream, parsed into `ds_type` and `ds_namespace` |
| `serverless_project_contribution_lookup` | `serverless_datastream_contribution_lookup` | `@timestamp` (1d), `project_id` | Daily project-wide denominator totals (`deployment_sum_indexing_time`, `deployment_sum_query_time`, `deployment_sum_data_set_store_size`) for `LOOKUP JOIN` attribution |
| `serverless_ai_model_contribution_lookup` | `metrics-chargeback_serverless.usage-*` (`doc_type: ai_model`) | `@timestamp` (1d), `project_id`, `model_id`, `task_type` | Daily AI/ML model inference count, latency, and total compute time |
| `serverless_synthetics_monitor_contribution_lookup` | `metrics-chargeback_serverless.usage-*` (`doc_type: synthetics_monitor`) | `@timestamp` (1d), `project_id`, `monitor_id`, `monitor_type` | Daily Synthetics monitor check count, availability, duration, and locations |
| `serverless_project_status_lookup` | `metrics-chargeback_serverless.usage-*` (`doc_type: project_status`) | `@timestamp` (1d), `project_id` | Daily project discovery & harvest health status (`ok`, `empty_or_unauthorized`, `inactive`) |

## Alerting Rule Templates

The generator builds 10 ES|QL alerting rule templates (`integration/assets/chargeback_serverless/kibana/alerting_rule_template/*.json`):

- **Line-2 `EVAL` Parameter Convention:** Every alert places user-tunable thresholds (`threshold_spend`, `threshold_budget`, `threshold_pct_increase`, etc.) and entity filters (`filter_category`, `filter_driver`, `filter_project`, `filter_namespace`, `filter_model`, `filter_monitor`) on **Line 2** immediately after `FROM <lookup_index>`, before `COST_PRELUDE_BODY` / `USAGE_JOIN_BODY` / `AI_JOIN_BODY` / `SYNTHETICS_JOIN_BODY` joins and currency resolution.
- **Latest Complete Billed Day Scoping:** Single-day rules use a `3d` lookback window (to tolerate Cloud Billing API latency) and scope to the latest complete billed day via `| INLINE STATS latest_day = MAX(@timestamp) | WHERE @timestamp == latest_day` so multi-day lookbacks never double-count daily spend.
- **Structured Row Preservation:** Each rule ends with `| KEEP result, ...` rather than collapsing rows with `VALUES(result)`, preserving both the formatted `result` message and structured metrics (`day`, `spend`, `ecu`, `threshold`, `unit`, etc.) per breached entity.

| Alert ID | Title | Window | Source Index | Line-2 `EVAL` Defaults |
| --- | --- | --- | --- | --- |
| `chargeback_serverless-alert-total-daily-spend` | `[Chargeback Serverless] Total daily spend threshold exceeded` | `3d` | `serverless_billing_cost_lookup` | `threshold_spend = 1000.0` |
| `chargeback_serverless-alert-total-monthly-spend` | `[Chargeback Serverless] Rolling 30-day total spend budget exceeded` | `30d` | `serverless_billing_cost_lookup` | `threshold_budget = 25000.0` |
| `chargeback_serverless-alert-category-spend` | `[Chargeback Serverless] Cost category daily spend threshold exceeded` | `3d` | `serverless_billing_cost_lookup` | `threshold_spend = 300.0, filter_category = "*"` |
| `chargeback_serverless-alert-workload-driver-spend` | `[Chargeback Serverless] Workload driver daily spend threshold exceeded` | `3d` | `serverless_billing_cost_lookup` | `threshold_spend = 250.0, filter_driver = "*"` |
| `chargeback_serverless-alert-project-spend` | `[Chargeback Serverless] Project daily spend threshold exceeded` | `3d` | `serverless_billing_cost_lookup` | `threshold_spend = 200.0, filter_project = "*"` |
| `chargeback_serverless-alert-project-spend-spike` | `[Chargeback Serverless] Project daily spend spike vs. 7-day baseline` | `10d` | `serverless_billing_cost_lookup` | `threshold_pct_increase = 50.0, min_daily_spend = 25.0, filter_project = "*"` |
| `chargeback_serverless-alert-namespace-spend` | `[Chargeback Serverless] Namespace attributed daily spend threshold exceeded` | `3d` | `serverless_datastream_contribution_lookup` | `threshold_spend = 100.0, filter_namespace = "*"` |
| `chargeback_serverless-alert-ai-model-spend` | `[Chargeback Serverless] AI model attributed daily spend threshold exceeded` | `3d` | `serverless_ai_model_contribution_lookup` | `threshold_spend = 50.0, filter_model = "*"` |
| `chargeback_serverless-alert-synthetics-monitor-spend` | `[Chargeback Serverless] Synthetics monitor attributed daily spend threshold exceeded` | `3d` | `serverless_synthetics_monitor_contribution_lookup` | `threshold_spend = 25.0, filter_monitor = "*"` |
| `chargeback_serverless-alert-data-trust-health` | `[Chargeback Serverless] Data trust & project harvest health issue` | `3d` | `serverless_billing_cost_lookup` | `threshold_unallocated_spend = 100.0, threshold_unallocated_pct = 25.0` |

## Generator

All Kibana saved objects (`dashboard/`, `alerting_rule_template/`, `index_pattern/`) are generated deterministically by:

```bash
python3 scripts/generate_dashboards.py
```
