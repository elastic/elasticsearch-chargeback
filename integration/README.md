# Elasticsearch Chargeback integration

## Version

Chargeback integration: 0.1.3

## Dependencies

This process must be set up on the **Monitoring cluster**, where all monitoring data is collected.

### Requirements

Either one of the following conditions must be true:

#### A: The Elasticsearch integration: 
- Elasticsearch integration (version 1.16.0+) must collect data from all deployments sending data to the Monitoring cluster.
- The Transform `logs-elasticsearch.index_pivot-default-{VERSION}` must be running on the Monitoring cluster.

#### B: Stack Monitoring:
- Stack Monitoring must be enabled and sending metrics to the Monitoring cluster.

#### All of the following conditions must be met:
- Monitoring cluster needs to be on 8.18.0+ to be able to use the ES|QL LOOKUP JOIN feature.
- The Monitoring cluster must be hosted on Elastic Cloud (ECH).
- Elasticsearch Service Billing integration (version 1.0.0+) must be installed on the Monitoring cluster.

## Setup instructions

Please see [Integration `Instructions.md`](Instructions.md) to install the integration.

## Data flow

The Chargeback Module is building on two distinct data sets: 
- The output of the Elasticsearch Service Billing integration, i.e. `metrics-ess_billing.billing-default` index.
- The output of the Elasticsearch integration usage data, specifically that of the `logs-elasticsearch.index_pivot-default-{VERSION}` transform, ie. `monitoring-indices` index.

The first layer of processing that we do, is five transforms: 

- From the billing data, we get one value, namely the total ECU (cost), per deployment per day.
- From the usage data, we get values for indexing, querying and storage:
    - per deployment per day.
    - per tier per day.
    - per deployment, per datastream per day.
    - per tier, per datastream per day.

![Transforms](assets/img/Transforms.png)

All of the transforms create their own lookup index. There is also a lookup index for the configuration.

![Lookup Indices](assets/img/LookupIndices.png)

To be able to take indexing, querying and storage into consideration in a weighted fashion, we use the following weights (see  [Integration `Instructions.md`](Instructions.md) on how to change these):
- indexing: 20 (only considered for the hot tier)
- querying: 20
- storage: 40

This means that storage will contribute the most to the blended cost calculation, and that indexing will only contribute to this blended cost on the hot tier. You should consider these weights, and adjust these based on your own best judgement. 

![Chargeback flow](assets/img/ChargebackFlow.png)
![data_flow](assets/img/data_flow.png)

## Dashboards

Once you have uploaded the integration, you can navigate to the `[Chargeback] Cost and Consumption breakdown` dashboard that provides the Chargeback insight into deployments, data streams and data tiers.

## Sample dashboard

![Chargeback](<assets/img/[Chargeback] Cost and Consumption breakdown.png>)