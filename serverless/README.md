# Chargeback for Elastic Cloud Serverless

Adds Elastic Cloud Serverless projects to Chargeback, so a single monitoring cluster can
report cost and usage allocation for Hosted deployments and Serverless projects side by
side in the existing dashboards.

## Version

Requires Chargeback integration **0.5.0+**.

## Why this is separate from the integration ZIP

The collector needs the Serverless project's URL, an API key, and its project ID. Those are
per-environment values that cannot ship inside a Fleet package, so these assets are
installed directly against the monitoring cluster. See [Instructions.md](Instructions.md).

## How it works

Serverless does not expose the stats APIs the Elasticsearch integration relies on, and it
has no Metricbeat-based `.monitoring-es-*` stream. So there is no usage data for the
Chargeback usage transforms to read.

Rather than reimplement the aggregation, a Kibana Workflow collects per-index usage from
the Serverless project and writes it in the **same document shape** as `.monitoring-es-*`
into an index named `monitoring-indices-serverless`. The Chargeback contribution transforms
already source `monitoring-indices*`, so they pick it up unchanged and perform the data
stream name parsing, tier mapping, day bucketing, and `composite_key` construction
themselves. Output is therefore identical in schema to Hosted data by construction, and the
existing dashboards require no modification.

```
Serverless project
   │  GET _cat/indices  (the only usable stats API; _stats returns HTTP 410)
   ▼
Kibana Workflow  "Chargeback - Serverless usage collector"   (hourly, on the monitoring cluster)
   │  POST monitoring-indices-serverless/_doc
   ▼
ingest pipeline  chargeback-serverless-usage
   │  reshapes into .monitoring-es-* form, converts types, sets tier: serverless
   ▼
monitoring-indices-serverless          ◄── matches the transforms' existing monitoring-indices* pattern
   │
   ▼
stock Chargeback contribution transforms (unmodified)
   │
   ▼
cluster_{deployment,tier,datastream,tier_and_ds}_contribution_lookup
   │
   ▼
existing [Chargeback] dashboards
```

Cost comes from the ESS Billing integration, which already covers Serverless projects — no
extra collection is needed for the billing side. Integration 0.5.0 extends
`billing_realized_pool` to recognise Serverless VCU and retained-storage SKUs so that cost
becomes allocatable; before 0.5.0 every serverless row was `is_allocatable: false`.

## Assets

| Path | Installed as |
| --- | --- |
| [`assets/pipelines/chargeback-serverless-usage.json`](assets/pipelines/chargeback-serverless-usage.json) | `PUT _ingest/pipeline/chargeback-serverless-usage` |
| [`assets/indices/monitoring-indices-serverless.json`](assets/indices/monitoring-indices-serverless.json) | `PUT monitoring-indices-serverless` |
| [`assets/workflows/chargeback-serverless-usage-collector.yml`](assets/workflows/chargeback-serverless-usage-collector.yml) | `POST /api/workflows/workflow` (one per project) |

## Limitations

See [Instructions.md — Known limitations](Instructions.md#known-limitations). The main ones:
blank deployment name for serverless projects, `tier: serverless` as its own bucket, no
utilization discount (correct for an auto-scaling platform), ML VCU billed but not
allocated, and one day of billing lag.
