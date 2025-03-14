# Chargeback Module Setup Instructions

Follow these steps in order using the Kibana Dev Console.

## 1. Create Pipelines

### Set Composite Key Pipeline
This standardises `deployment_id` from `cluster_name` and generates a `composite_key`.

File: [`set_composite_key_pipeline.json`](./assets/pipelines/set_composite_key_pipeline.json)

### Set Composite Tier Key Pipeline
Extends `set_composite_key` by adding the tier value to `composite_key`.

File: [`set_composite_tier_key_pipeline.json`](./assets/pipelines/set_composite_tier_key_pipeline.json)

## 2. Create Billing Transform
Aggregates ECU consumption per deployment per day. Runs hourly, processing `metrics-ess_billing.billing-default`.

File: [`billing_cluster_cost_transform.json`](./assets/transforms/billing_cluster_cost_transform.json)

```sh
POST _transform/billing_cluster_cost/_start
```

## 3. Create Consumption Transforms

### Per Deployment
Aggregates query time, indexing time, and storage size per deployment per day.

File: [`cluster_deployment_contribution_transform.json`](./assets/transforms/cluster_deployment_contribution_transform.json)

```sh
POST _transform/cluster_deployment_contribution/_start
```

### Per Data Stream
Aggregates query time, indexing time, and storage size per deployment, per tier, per day. Runs hourly with a 24-hour delay to ensure completeness.

Note: Since the enrichment policies and pipelines are interdependent on the data stream transform, we first create the transform without the final pipeline.

File: [`cluster_datastreams_contribution-placeholder.json`](./assets/transforms/cluster_datastreams_contribution-placeholder.json)

```sh
POST _transform/cluster_datastreams_contribution/_start
```

## 4. Create Enrichment Policies

### Cluster Cost
Joins `total_ecu` and `deployment_name` from Billing integration with usage data.

File: [`cluster_cost_enrich_policy.json`](./assets/enrich/cluster_cost_enrich_policy.json)

```sh
POST /_enrich/policy/cluster_cost_enrich_policy/_execute
```

### Cluster Contribution
Joins `sum_query_time`, `sum_indexing_time`, `sum_store_size`, `sum_data_set_store_size`, and `tier` from Elasticsearch integration with usage data.

File: [`cluster_contribution_enrich_policy.json`](./assets/enrich/cluster_contribution_enrich_policy.json)

```sh
POST /_enrich/policy/cluster_contribution_enrich_policy/_execute
```

## 5. Create Enrichment Ingest Pipeline
This pipeline enriches consumption data with billing data, using the results from previous transforms.

File: [`cluster_cost_enrichment_pipeline.json`](./assets/pipelines/cluster_cost_enrichment_pipeline.json)

## 6. Recreate data stream Transform
First, clean up:

```sh
POST _transform/cluster_datastreams_contribution/_stop
DELETE cluster_datastreams_contribution
DELETE _transform/cluster_datastreams_contribution
```

Then, recreate and start the transform with the correct piplines.

File: [`cluster_datastreams_contribution.json`](./assets/transforms/cluster_datastreams_contribution.json)

```sh
POST _transform/cluster_datastreams_contribution/_start
```

## 7. Add Runtime Fields for Blended Cost Calculation
Create a runtime field on `cluster_datastreams_contribution` with default weights:
- **Indexing**: 20 (only for hot tier)
- **Querying**: 20
- **Storage**: 40

Weights can be adjusted based on requirements.

File: [`cluster_datastreams_contribution_mapping.json`](./assets/mappings/cluster_datastreams_contribution_mapping.json)

## 8. Automate Enrich Policy Refreshing
Two Watchers will execute daily to refresh enrich data. Follow these steps:
- Create a role with minimal privileges.
- Create a user with these privileges (choose your own password).
- Set up Watchers for (add password and endpoint):
  - `execute_cluster_cost_enrich_policy`
  - `execute_cluster_contribution_enrich_policy`

Files: 
- [`enrichment_policy_role.json`](./assets/security/enrichment_policy_role.json)
- [`cf-watcher-user.json`](./assets/security/cf-watcher-user.json)
- [`execute_cluster_contribution_enrich_policy_watcher.json`](./assets/watchers/execute_cluster_contribution_enrich_policy_watcher.json)
- [`execute_cluster_cost_enrich_policy_watcher.json`](./assets/watchers/execute_cluster_cost_enrich_policy_watcher.json)

## 9. Load the Dashboard

- Navigate to _Deployment > Stack Management > Saved objects_
- Click *Import* and upload the ndjson file
- Select _Check for existing objects_

File: [`chargeback_module.ndjson`](./assets/saved_objects/chargeback_module.ndjson)