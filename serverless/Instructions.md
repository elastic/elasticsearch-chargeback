# Chargeback for Elastic Cloud Serverless — setup

These assets extend Chargeback to Elastic Cloud Serverless projects. They are installed
separately from the integration ZIP because the collector needs per-project credentials,
which cannot be baked into a Fleet package.

Everything here is installed **on the monitoring cluster** (the Elastic Cloud Hosted
deployment where the Chargeback integration and the ESS Billing integration already run),
not on the Serverless project itself.

## Prerequisites

- Chargeback integration **0.5.0+** installed on the monitoring cluster. Earlier versions
  do not classify Serverless SKUs as allocatable, so no cost can be attributed.
- The [Elasticsearch Service Billing](https://www.elastic.co/docs/reference/integrations/ess_billing/)
  integration running and covering the organization that owns the Serverless project.
  Serverless projects appear in `metrics-ess_billing.billing-*` with
  `ess.billing.deployment_type: elasticsearch`. No extra setup is needed — verify with:

  ```
  GET metrics-ess_billing.billing-*/_search
  {
    "size": 0,
    "query": { "term": { "ess.billing.deployment_type": "elasticsearch" } },
    "aggs": { "projects": { "terms": { "field": "ess.billing.deployment_id" } } }
  }
  ```

- An API key **on the Serverless project** with cluster `monitor` and index `read` +
  `view_index_metadata`. That is all `_cat/indices` requires.

## 1. Install the ingest pipeline

```
PUT _ingest/pipeline/chargeback-serverless-usage
```

with the body from [`assets/pipelines/chargeback-serverless-usage.json`](assets/pipelines/chargeback-serverless-usage.json).

It reshapes a raw `_cat/indices` row into the `.monitoring-es-*` document shape the
Chargeback contribution transforms already expect, converts the string values to `long`,
and stamps `elasticsearch.index.tier: serverless`.

## 2. Create the staging index

```
PUT monitoring-indices-serverless
```

with the body from [`assets/indices/monitoring-indices-serverless.json`](assets/indices/monitoring-indices-serverless.json).

The name matters: the contribution transforms source `monitoring-indices*`, so this index
is picked up with no transform configuration change. The definition sets
`default_pipeline: chargeback-serverless-usage`, so the workflow can post raw rows.

## 3. Import the workflow

Edit [`assets/workflows/chargeback-serverless-usage-collector.yml`](assets/workflows/chargeback-serverless-usage-collector.yml)
and replace `sl_url`, `sl_api_key`, and `project_id` in the `consts` block. `project_id`
**must** equal `ess.billing.deployment_id` for the project, or the usage rows will not join
to the billing rows on `composite_key`.

Then either paste it into **Workflows → Create workflow** in Kibana, or:

```bash
python -c "import json,sys; print(json.dumps({'yaml': open(sys.argv[1]).read()}))" \
  assets/workflows/chargeback-serverless-usage-collector.yml > /tmp/wf.json

curl -X POST "$KIBANA_URL/api/workflows/workflow" \
  -H "Authorization: ApiKey $MONITORING_CLUSTER_API_KEY" \
  -H 'kbn-xsrf: true' -H 'Content-Type: application/json' \
  -d @/tmp/wf.json
```

Run one copy per Serverless project. Duplicate the workflow and change `sl_url`,
`sl_api_key`, and `project_id`.

## 4. Verify

Run the workflow once manually, then confirm documents landed and that the stock
transforms aggregate them:

```
GET monitoring-indices-serverless/_count

POST _transform/logs-chargeback.cluster_datastream_contribution-default-0.5.0/_preview
```

The preview should contain rows whose `cluster_name` is your project ID. Then check the
lookup indices:

```
GET cluster_datastream_contribution_lookup/_search
{ "query": { "term": { "cluster_name": "<SERVERLESS_PROJECT_ID>" } } }
```

## What Serverless can and cannot report

`_data_stream/_stats`, `<index>/_stats`, `_nodes/stats` and `_metering/stats` all return
**HTTP 410 — not available when running in serverless mode**. `_cat/indices` is the only
usable source, which is why the collector uses it.

| Signal | Availability |
| --- | --- |
| `dataset.size` | Complete and reliable — the storage figure for every index |
| `docs.count` | Complete |
| `indexing.index_time` / `index_total` | Present but sparse — stateless shards recycle, so counters cover only the current shard generation |
| `search.query_time` / `query_total` | Present but sparse, same reason |
| `store.size`, `pri.store.size` | Always `0` — object storage. Do not use |

Storage carries weight 40 of 80 in the blended cost, so allocation stays meaningful even
where the indexing and query counters read zero.

## Known limitations

- **Serverless projects have no `ess.billing.deployment_name`**, so they render with a
  blank deployment name on the dashboards. Group by `deployment_id` instead, or set a
  `chargeback_group:<name>` tag if your organization supports tagging projects.
- **No data tiers.** All serverless usage is reported as `tier: serverless`, which appears
  as its own bucket next to `hot/content`, `warm`, and `cold` in the tier panels.
- **No utilization discount.** Serverless auto-scales, so there is no idle provisioned
  capacity to discount. `cluster_capacity_utilization_lookup` has no rows for serverless
  projects, and the dashboards already `COALESCE(heap_used_pct_p95, 100)`, which yields a
  utilization score of 1.0 and charges the full pool. This is intentional, not missing data.
- **ML VCU is excluded from the allocatable pool.** It is billed but not attributed to any
  data stream, matching how ECH treats ML as `cost_category: platform`.
- **One day of lag.** Billing for day D arrives on D+1, so allocation panels populate a day
  behind. This is inherent to the billing feed and applies to ECH equally.
