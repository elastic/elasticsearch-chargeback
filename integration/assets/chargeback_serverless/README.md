# Chargeback Serverless (native hub)

Native Chargeback for **Serverless-only** organizations. Runs on a Serverless hub project (recommended: Observability Complete). Collects billing and usage through Kibana Workflows. No CCS, no CPS, no ESS Billing package, no `.monitoring-es-*` emulation.

The Fleet package lives in the integrations repository (`packages/chargeback_serverless` on branch `wip-johannes-chargeback-serverless`). This directory holds indices, pipelines, workflows, and the installer.

## Install

```bash
export ES_URL="https://<hub>.es.<region>.aws.elastic.cloud"
export ES_API_KEY="<hub es api key>"
export KIBANA_URL="https://<hub>.kb.<region>.aws.elastic.cloud"
export KIBANA_API_KEY="<hub kibana api key>"
export ORGANIZATION_ID="<org id>"
export BILLING_API_KEY="<cloud api key: billing read>"
export USAGE_API_KEY="<cloud api key: list projects + ES monitor>"

./scripts/install_chargeback_serverless.sh
```

Then install the `chargeback_serverless` Fleet package on the hub and run both workflows once.

## Assets

| Path | Installed as |
| --- | --- |
| `pipelines/chargeback-serverless-billing.json` | Ingest pipeline `chargeback-serverless-billing` |
| `indices/*.json` | Config, state, and lookup indices (lookup mode) |
| `workflows/chargeback-serverless-usage.yml` | Hourly usage collector |
| `workflows/chargeback-serverless-billing.yml` | Daily billing collector |

## Related

- Hosted monitoring cluster + Serverless sources: [`../serverless/`](../serverless/) (existing add-on)
- Tracking: [elasticsearch-chargeback#123](https://github.com/elastic/elasticsearch-chargeback/issues/123)

See [Instructions.md](Instructions.md).
