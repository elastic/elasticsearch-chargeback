# Chargeback for Elastic Cloud Serverless — setup

This **add-on** extends pure Chargeback with per-index usage collection for Elastic Cloud Serverless projects. Install it only when you need datastream or tier showback for Serverless. The Fleet zip (0.5.0+) already makes Serverless **billing** allocatable; this add-on fills usage.

Assets install **on the monitoring cluster** (where Chargeback and ESS Billing already run), not on the Serverless project.

Prefer [`scripts/install_serverless_usage.sh`](../../../scripts/install_serverless_usage.sh) from the repository root. The steps below match what that script does.

## Prerequisites

- Chargeback integration **0.5.0+** on the monitoring cluster. Earlier versions do not classify Serverless SKUs as allocatable.
- [Elasticsearch Service Billing](https://www.elastic.co/docs/reference/integrations/ess_billing/) covering the organization that owns the Serverless project. Verify with:

  ```
  GET metrics-ess_billing.billing-*/_search
  {
    "size": 0,
    "query": { "term": { "ess.billing.deployment_type": "elasticsearch" } },
    "aggs": { "projects": { "terms": { "field": "ess.billing.deployment_id" } } }
  }
  ```

- An API key **on each Serverless project** with cluster `monitor` and index `read` + `view_index_metadata`.

## Install with the script

```bash
export ES_URL="https://<monitoring>.es.<region>.<csp>.cloud.es.io"
export ES_API_KEY="<monitoring es api key>"
export KIBANA_URL="https://<monitoring>.kb.<region>.<csp>.cloud.es.io"
export KIBANA_API_KEY="<monitoring kibana api key>"

# Optional: register one project in the same run
export PROJECT_ID="<ess.billing.deployment_id>"
export SL_URL="https://<project>.es.<region>.<csp>.elastic.cloud"
export SL_API_KEY="<serverless api key>"

./scripts/install_serverless_usage.sh
```

The script installs:

1. Ingest pipeline `chargeback-serverless-usage`
2. Staging index `monitoring-indices-serverless` (matches `monitoring-indices*`)
3. Project registry `.chargeback-serverless-projects` (`sl_api_key` is `keyword` with `index: false` and `doc_values: false`)
4. One **looping** collector workflow (no secrets in consts)

## Register or replace a project

`project_id` **must** equal `ess.billing.deployment_id`, or usage will not join billing on `composite_key`. Re-PUT the same `_id` to replace credentials.

```
PUT .chargeback-serverless-projects/_doc/<project_id>
{
  "project_id": "<project_id>",
  "sl_url": "https://<project>.es.<region>.<csp>.elastic.cloud",
  "sl_api_key": "<encoded key>"
}
```

Do not put API keys in the workflow YAML. Do not copy the workflow per project.

## RBAC

Split privileges:

- **Write** on `.chargeback-serverless-projects` only for operators who register projects.
- **Read** on that index only for the identity that runs the collector workflow.
- Deny the project index to users who can view workflows, to agents, and to Discover/ES|QL where practical.

`GET /api/workflows` returns the full definition. Secrets must stay in the project index, not in workflow consts. Treat workflow run history as sensitive if an HTTP step ever echoed a key.

## Verify

Run the workflow once from **Workflows**, then:

```
GET monitoring-indices-serverless/_count

GET cluster_datastream_contribution_lookup/_search
{ "query": { "term": { "cluster_name": "<SERVERLESS_PROJECT_ID>" } } }
```

## Known limitations

- Blank `deployment_name` for many Serverless billing rows; label with `deployment_id`.
- `tier: serverless` is its own bucket (no hot/warm/cold).
- No utilization discount (correct for auto-scaling).
- ML VCU is billed but not in the allocatable pool (same as Hosted ML as platform).
- About one day of billing lag versus usage.
- `_data_stream/_stats`, `<index>/_stats`, `_nodes/stats`, and `_metering/stats` return HTTP 410 on Serverless; `_cat/indices` with `dataset.size` is the collection path.
