# On-Premises Billing Integration

## Version

On-Premises Billing integration: 0.1.0

## Overview

The On-Premises Billing integration generates billing-compatible data for self-managed Elasticsearch deployments, enabling cost allocation through the Chargeback integration.

This integration produces data in the same format as the [Elasticsearch Service Billing](https://www.elastic.co/docs/reference/integrations/ess_billing/) integration, writing to `metrics-ess_billing.billing-onprem`.

## Prerequisites

- **Stack Version**: 9.2.0+ (required for Chargeback dashboard compatibility)
- **Elasticsearch Integration**: The [Elasticsearch](https://www.elastic.co/docs/reference/integrations/elasticsearch/) integration (v1.16.0+) must be installed with the `logs-elasticsearch.index_pivot-default-{VERSION}` transform running to produce the `monitoring-indices` index.

## Installation

1. Navigate to **Integrations** in Kibana
2. Click **Upload integration**
3. Upload the `onprem_billing-0.1.0.zip` from the `assets/0.1.0/` folder

## Setup

After installation:

1. Configure deployments in `onprem_billing_config` (name, daily ECU, chargeback group tags)
2. Create the enrich policy and ingest pipeline
3. Start the billing transform

See [Instructions.md](Instructions.md) for detailed setup steps.

## Chargeback Integration

Once billing data is flowing to `metrics-ess_billing.billing-onprem`, install the [Chargeback integration](../integration/README.md) to enable cost allocation dashboards and analysis.

The Chargeback integration will automatically pick up on-premises billing data alongside any Elastic Cloud billing data.
