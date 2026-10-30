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
| `serverless_project_status_lookup` | `metrics-chargeback_serverless.usage-*` (`doc_type: project_status`) | `@timestamp` (1d), `project_id` | Daily project discovery & harvest health status (`ok`, `empty_or_unauthorized`) |

## Generator

All Kibana saved objects (`dashboard/`, `alerting_rule_template/`, `index_pattern/`) are generated deterministically by:

```bash
python3 scripts/generate_dashboards.py
```
