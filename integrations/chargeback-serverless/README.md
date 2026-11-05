# Chargeback Serverless integration overview

The Chargeback Serverless integration (`chargeback_serverless` `0.3.15`) provides native FinOps cost attribution and workload showback for **Elastic Cloud Serverless** organizations—without requiring a dedicated Elastic Cloud Hosted monitoring cluster. Running directly on a Serverless FinOps hub project through a Fleet-managed Elastic Agent, the integration uses a single unified Elastic Cloud API key to collect organization-wide billing history (including deleted projects and deployments) and per-project data stream, AI model, and Synthetics telemetry.

## How Chargeback Serverless works

Chargeback Serverless collects billing and usage telemetry once per day after `02:00 UTC` for completed calendar days up to yesterday (`D-1` UTC), enriches the records through seven Fleet-managed Elasticsearch transforms into `LOOKUP`-mode indices, and visualizes costs across five linked Kibana dashboards using Elasticsearch Query Language (ES|QL) `LOOKUP JOIN` queries.

```mermaid
flowchart LR
    subgraph Cloud["Elastic Cloud Organization"]
        BillingAPI["Elastic Cloud Billing API<br/>(/costs, /history, /hourly)<br/>Active + deleted projects"]:::navy
        CtrlPlane["Control Plane Discovery<br/>(/api/v1/serverless/projects)<br/>All Serverless project types"]:::navy
        Projects["Serverless Projects<br/>(_resolve/index, .inference,<br/>.synthetics-monitors-*)"]:::navy
    end

    subgraph Agent["Elastic Agent (CEL Input)"]
        BStream["billing stream<br/>metrics-chargeback_serverless.billing-*<br/>(365d retention)"]:::blue
        UStream["usage stream<br/>metrics-chargeback_serverless.usage-*<br/>(365d retention)"]:::blue
    end

    subgraph Transforms["7 Fleet-Managed Lookup Transforms (365d retention)"]
        L1["serverless_billing_cost_lookup<br/>Daily SKU cost & classification"]:::mint
        L2["serverless_billing_realized_pool_lookup<br/>Allocatable SKU pools by workload"]:::mint
        L3["serverless_project_contribution_lookup<br/>Project totals"]:::mint
        L4["serverless_datastream_contribution_lookup<br/>Data stream & namespace shares"]:::mint
        L5["serverless_ai_model_contribution_lookup<br/>AI & ML endpoint shares"]:::mint
        L6["serverless_synthetics_monitor_contribution_lookup<br/>Browser & ping monitor shares"]:::mint
        L7["serverless_project_status_lookup<br/>Project harvest audit registry"]:::mint
    end

    subgraph Kibana["Kibana FinOps Experience"]
        Dash["5 Linked ES|QL Dashboards"]:::pink
        Rules["10 FinOps Spend & Data Trust<br/>ES|QL Alerting Rules"]:::pink
    end

    BillingAPI --> BStream
    CtrlPlane --> BStream & UStream
    Projects --> UStream
    BStream --> L1 & L2
    UStream --> L3 & L4 & L5 & L6 & L7
    L1 & L2 & L3 & L4 & L5 & L6 & L7 --> Dash & Rules

    classDef navy fill:#153385,stroke:#153385,color:#FFFFFF
    classDef blue fill:#0B64DD,stroke:#0B64DD,color:#FFFFFF
    classDef mint fill:#48EFCF,stroke:#153385,color:#1C1E23
    classDef yellow fill:#FEC514,stroke:#153385,color:#1C1E23
    classDef pink fill:#F04E98,stroke:#153385,color:#FFFFFF
```

### Key capabilities

- **Single unified Elastic Cloud API key (`cloud_api_key`)**: One API key configured with **Billing admin** and **Cloud resource access** (`Cloud, Elasticsearch, and Kibana API`) collects organization-wide billing history (including deleted deployments and projects via `/api/v1/billing/organization/{org_id}/history`), dynamically discovers all active Serverless projects (`elasticsearch`, `observability`, `security`, and future project types), and harvests usage telemetry from each project.
- **Exact SKU-to-workload pool reconciliation**: Unlike Hosted clusters that blend indexing, querying, and storage across shared node hardware, Elastic Cloud Serverless bills separate Stock Keeping Units (SKUs) for each resource driver:
  - **Indexing pool (`ingest-vcu` SKUs)**: Allocated across data streams proportionally by indexing time (`indexing.index_time` harvested from `_cat/indices`), with proportional fallback to storage size (`store_size_bytes` / `dataset.size`) when indexing time is zero.
  - **Search and query pool (`search-vcu` SKUs)**: Allocated across data streams proportionally by query execution time (`search.query_time` harvested from `_cat/indices`), with proportional fallback to storage size (`store_size_bytes` / `dataset.size`) when query time is zero.
  - **Storage pool (`retained` SKUs)**: Allocated across data streams by daily `store_size_bytes`.
  - **AI and Machine Learning pool (`ml-vcu` and inference SKUs)**: Allocated across `.inference` endpoints and models in `serverless_ai_model_contribution_lookup`.
  - **Synthetics pools (`observability-synthetic-executions` and `observability-synthetic-pings` SKUs)**: Allocated across `browser` monitors and lightweight (`http`, `icmp`, `tcp`, `dns`) monitors in `serverless_synthetics_monitor_contribution_lookup`.

> [!NOTE]
> Telemetry metrics from `_cat/indices` (`indexing.index_time`, `search.query_time`) and `_ml/trained_models/_stats` (`inference_count`) are cumulative lifetime counters per backing index or model. Allocating daily VCU pools by each data stream's relative share of these counters cancels out snapshot frequency and avoids negative deltas. If indexing or query time across a project is zero, dashboards automatically fall back to proportional storage allocation (`dataset.size`).

- **Automatic 365-day retention**: Both raw metrics data streams (`metrics-chargeback_serverless.billing-*` and `metrics-chargeback_serverless.usage-*`) and all seven lookup transforms enforce a 365-day retention policy (`data_retention: "365d"` and `retention_policy.time.max_age: 365d`).
- **Zero plaintext secret indices**: All credentials reside exclusively in the encrypted Kibana Fleet integration policy (`secret: true`). No API keys are stored in Elasticsearch indices.

## Dashboards

The package installs five linked Kibana dashboards with a shared top navigation bar:

| Dashboard title | Purpose |
| --- | --- |
| **`[Chargeback Serverless] Billing Components Overview`** | Reconciles your organization's full Elastic Cloud invoice across active and deleted projects, SKU line items, cost categories, and allocatable versus unallocatable spend. |
| **`[Chargeback Serverless] Usage & Cost Allocation`** | Attributes Indexing, Query, Storage, AI, and Synthetics SKU pools across projects, data stream types (`ds_type`), datasets (`ds_dataset`), namespaces (`ds_namespace`), and individual data streams. |
| **`[Chargeback Serverless] AI & Machine Learning`** | Breaks down AI and Machine Learning (ML) Virtual Compute Unit (VCU) consumption by project, task type, model provider, and `.inference` endpoint. |
| **`[Chargeback Serverless] Synthetics`** | Tracks Synthetics spend and check volume by monitor type (`browser`, `http`, `icmp`, `tcp`, `dns`), monitor name, namespace, and project. |
| **`[Chargeback Serverless] Configuration`** | Displays active currency settings configured in the Fleet policy, step-by-step setup documentation, and the live project harvest audit registry (`serverless_project_status_lookup`). |

## Prebuilt FinOps alerting rules

Chargeback Serverless includes 10 ES|QL alerting rule templates in [`kibana/alerting_rule_template/`](kibana/alerting_rule_template/) with second-line `EVAL` threshold and filter variables that you can customize in Kibana (**Stack Management → Rules**):

1. **`[Chargeback Serverless] Total daily spend threshold exceeded`**: Alerts when total organization daily spend exceeds `threshold_spend` (default `1000.0`).
2. **`[Chargeback Serverless] Rolling 30-day total spend budget exceeded`**: Alerts when cumulative 30-day spend exceeds `threshold_budget` (default `25000.0`).
3. **`[Chargeback Serverless] Cost category daily spend threshold exceeded`**: Alerts when any `cost_category` matching `filter_category` exceeds `threshold_spend` (default `300.0`).
4. **`[Chargeback Serverless] Workload driver daily spend threshold exceeded`**: Alerts when Indexing, Query, Storage, AI, or Synthetics spend matching `filter_driver` exceeds `threshold_spend` (default `250.0`).
5. **`[Chargeback Serverless] Project daily spend threshold exceeded`**: Alerts when any single project matching `filter_project` exceeds `threshold_spend` (default `200.0`).
6. **`[Chargeback Serverless] Project daily spend spike vs. 7-day baseline`**: Alerts when a project's daily spend increases by more than `threshold_pct_increase` (default `50.0%`) over its 7-day baseline and exceeds `min_daily_spend` (default `25.0`).
7. **`[Chargeback Serverless] Namespace attributed daily spend threshold exceeded`**: Alerts when any `ds_namespace` matching `filter_namespace` exceeds `threshold_spend` (default `100.0`).
8. **`[Chargeback Serverless] AI model attributed daily spend threshold exceeded`**: Alerts when any `.inference` model matching `filter_model` exceeds `threshold_spend` (default `50.0`).
9. **`[Chargeback Serverless] Synthetics monitor attributed daily spend threshold exceeded`**: Alerts when any Synthetics monitor matching `filter_monitor` exceeds `threshold_spend` (default `25.0`).
10. **`[Chargeback Serverless] Data trust & project harvest health issue`**: Alerts when any discovered Serverless project fails usage harvesting (`status != "ok"`) or unallocated spend exceeds `threshold_unallocated_spend` (default `100.0`) and `threshold_unallocated_pct` (default `25.0%`).

## Package assets in this directory

| Path | Description |
| --- | --- |
| [`dist/chargeback_serverless-0.3.15.zip`](dist/chargeback_serverless-0.3.15.zip) | Built Fleet integration package ready to upload in Kibana (**Management → Integrations → Upload it as a .zip**). |
| [`Instructions.md`](Instructions.md) | Step-by-step installation, API key creation, and verification guide. |
| [`CONFIG.md`](CONFIG.md) | Reference for Fleet policy settings, currency conversion configuration, and alerting thresholds. |
| [`dashboards/DESIGN.md`](dashboards/DESIGN.md) | Detailed design reference for the five dashboards, filter controls, lookup indices, and 10 alerting rule templates. |
| [`kibana/dashboard/`](kibana/dashboard/) | Saved object JSON definitions for all five Chargeback Serverless dashboards. |
| [`kibana/alerting_rule_template/`](kibana/alerting_rule_template/) | 10 ES|QL FinOps spend and data-trust alerting rule template definitions. |

## Next steps

- Follow [Install and configure Chargeback Serverless](Instructions.md) to create your unified Elastic Cloud API key and deploy the integration.
- Review the [Configuration reference](CONFIG.md) to customize currency rates and alerting thresholds.
