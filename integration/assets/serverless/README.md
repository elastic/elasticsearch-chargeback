# Chargeback for Elastic Cloud Serverless (add-on)

Adds Elastic Cloud Serverless **usage** collection so a monitoring cluster can allocate cost to data streams for Hosted and Serverless side by side in the existing dashboards.

This is an **add-on**, not part of the Fleet zip. Pure Chargeback (0.5.0+) already recognizes Serverless billing SKUs in the realized pool. Install this add-on when you need per-index / datastream showback for Serverless.

## Version

Requires Chargeback integration **0.5.0+**.

## Install

From the repository root, run [`scripts/install_serverless_usage.sh`](../../../scripts/install_serverless_usage.sh). See [Instructions.md](Instructions.md).

## Why this is separate from the integration ZIP

The collector needs each Serverless project's URL and API key. Those are per-environment values that cannot ship inside a Fleet package. See [Instructions.md](Instructions.md) and [`scripts/install_serverless_usage.sh`](../../../scripts/install_serverless_usage.sh).

## How it works

```
.chargeback-serverless-projects   (one doc per project; secrets stay here)
   │
   ▼
Kibana Workflow  "Chargeback - Serverless usage collector"   (hourly, looping)
   │  GET <sl_url>/_cat/indices per project
   ▼
ingest pipeline  chargeback-serverless-usage
   │
   ▼
monitoring-indices-serverless     ◄── matches monitoring-indices*
   │
   ▼
stock Chargeback contribution transforms
```

Cost still comes from ESS Billing. Integration 0.5.0 extends `billing_realized_pool` for Serverless VCU and retained storage.

## Assets

| Path | Installed as |
| --- | --- |
| [`assets/pipelines/chargeback-serverless-usage.json`](assets/pipelines/chargeback-serverless-usage.json) | `PUT _ingest/pipeline/chargeback-serverless-usage` |
| [`assets/indices/monitoring-indices-serverless.json`](assets/indices/monitoring-indices-serverless.json) | `PUT monitoring-indices-serverless` |
| [`assets/indices/chargeback-serverless-projects.json`](assets/indices/chargeback-serverless-projects.json) | `PUT .chargeback-serverless-projects` |
| [`assets/workflows/chargeback-serverless-usage-collector.yml`](assets/workflows/chargeback-serverless-usage-collector.yml) | One looping workflow (not one copy per project) |

## Limitations

See [Instructions.md — Known limitations](Instructions.md#known-limitations).
