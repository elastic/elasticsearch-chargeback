# Chargeback module

FinOps is an operational framework and cultural practice designed to maximize the business value of cloud usage. It enables timely, data-driven decision-making and fosters financial accountability through collaboration between engineering, finance, and business teams.

The **Chargeback Module** helps users answer a key question: How is my organisation consuming the Elastic solution, and to which tenants can I allocate these costs?

The Chargeback Module is based on the **Elasticsearch Service Billing** and **Elasticsearch** integrations. Its purpose is to provide data for chargeback calculations, offering a breakdown of Elastic Consumption Units (ECU) per:
- Deployment
- Data tier
- Data stream
- Day

Version 0.2.0

## Dependencies

This process must be set up on the **Monitoring cluster**, where all monitoring data is collected.

### Requirements
- The Monitoring cluster must be running Elastic Stack 8.17.1 or higher.
- The Monitoring cluster must be hosted on Elastic Cloud (ECH).
- **Elasticsearch Service Billing** integration (version 1.0.0+) must be installed on the Monitoring cluster.
- **Elasticsearch** integration (version 1.16.0+) must collect data from all deployments sending data to the Monitoring cluster.
- The **Transform**  `logs-elasticsearch.index_pivot-default-{VERSION}` must be running on the Monitoring cluster.

## Setup instructions

Please see [`Instructions.md`](Instructions.md)

## Data flow

The Chargeback Module is building on two distinct data sets: 
- The output of the Elasticsearch Service Billing integration, i.e. `metrics-ess_billing.billing-default` index.
- The output of the Elasticsearch integration usage data, specifically that of the `logs-elasticsearch.index_pivot-default-{VERSION}` transform, ie. `monitoring-indices` index.

The first layer of processing that we do, is two transforms: 
- From the billing data, we get one value, namely the total ECU (cost), per deployment per day.
- From the usage data, we get values for indexing, querying and storage, per deployment per day.

Once we have these two new data sets, i.e. the billing data per day and the usage data per day, we perform another transform on the usage data, now also looking at the data stream and data tier, can use these two new data sets to enrich the data tier usage data. The end result is that we know how much indexing, querying and storage is used per deployment, per data tier and per data stream per day. This data is available in the `cluster_datastreams_contribution` index.

Since the enrichments are done on dynamic data, i.e. both the billing and usage data gets refreshed each day, the enrichment policies need to be executed once a day to be populated with the new data.

One catch is that, if the two integrations have not been running for at least 24 hours prior to the setup of this Chargeback Module, the enrichment data will be empty, and you will have to wait for 24 hours before the indices are populated.

To be able to take indexing, querying and storage into consideration in a weighted fashion, we create a runtime field on the `cluster_datastreams_contribution` index with the following default weights (which you can change):
- indexing: 20 (only considered for the hot tier)
- querying: 20
- storage: 40

This means that storage will contribute the most to the blended cost calculation, and that indexing will only contribute to this blended cost on the hot tier. You should consider these weights, and adjust these based on your own best judgement. 

![Chargeback flow](assets/img/Chargeback%20flow.png)

## Dashboards

Once you have loaded the dashboards, you can navigate to the `[Tech Preview] Chargeback (0.2.0)` dashboard that provides the Chargeback insight into deployments, data streams and data tiers.

The dashboard also links out to:
- `[Tech Preview] Chargeback - Meta Data (0.2.0)` which can be helpful when troubleshooting, as this provides the date ranges that have been parsed, etc.
- `[Metrics ESS Billing] Billing dashboard`, the dashboard for the Billing integration.
- `[Elasticsearch] Indices & data streams usage (Technical Preview/Beta)`, the dashboard for the Elasticsearch integration usage data.

## Sample dashboard

![Chargeback](assets/img/[Tech%20Preview]%20Chargeback%20(0.2.0).png)
